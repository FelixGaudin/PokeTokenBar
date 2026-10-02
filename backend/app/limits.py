"""Official Claude usage limits, read via the Claude Code OAuth credential.

This is the one outbound call that needs a secret. The credential file is mounted
read-only and only the access token is used, as a bearer token against the usage
endpoint. Failures degrade to "limits unavailable" and never affect token accounting.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
OAUTH_BETA = "oauth-2025-04-20"

FIVE_HOURS = 5 * 3600
SEVEN_DAYS = 7 * 24 * 3600
# Identity lookups are cached per token; failures are not, so a switched account
# never shows the previous one's label.
PROFILE_CACHE_MAX = 8


class LimitsUnavailable(Exception):
    """Limits could not be fetched. Carries a short reason for the UI."""

    def __init__(self, reason: str, *, auth_expired: bool = False, retry_after: float | None = None):
        super().__init__(reason)
        self.reason = reason
        self.auth_expired = auth_expired
        self.retry_after = retry_after


@dataclass(frozen=True)
class Credential:
    access_token: str
    expires_at: float | None
    subscription_type: str | None
    rate_limit_tier: str | None

    @property
    def is_expired(self) -> bool:
        # Treat a token inside its last minute as already gone.
        return self.expires_at is not None and self.expires_at <= time.time() + 60


@dataclass
class LimitWindow:
    key: str
    name: str
    kind: str  # "session" | "weekly" | "weekly_scoped"
    utilization: float | None
    resets_at: str | None = None

    @property
    def span_seconds(self) -> int | None:
        """How long this window lasts, for the pace marker. None when unknown."""
        if self.kind == "session":
            return FIVE_HOURS
        if self.kind in ("weekly", "weekly_all", "weekly_scoped"):
            return SEVEN_DAYS
        return None


@dataclass
class LimitStatus:
    windows: list[LimitWindow] = field(default_factory=list)
    plan: str | None = None
    fetched_at: float = 0.0
    account_email: str | None = None
    account_org: str | None = None

    @property
    def account(self) -> str | None:
        return account_display(self.account_email, self.account_org)

    @property
    def max_utilization(self) -> float:
        return max((w.utilization or 0.0) for w in self.windows) if self.windows else 0.0


def _expires_at(raw: object) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # Values above ~year 2286 in seconds are milliseconds.
    return value / 1000.0 if value > 10_000_000_000 else value


def read_credential(path: Path) -> Credential:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise LimitsUnavailable("no credentials file mounted") from None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LimitsUnavailable(f"credentials file unreadable: {exc}") from None

    if not isinstance(raw, dict):
        raise LimitsUnavailable("credentials file has an unexpected shape")

    # An explicit JSON null means logged out; a missing key can mean the file only
    # holds MCP OAuth state. Both need a re-login, so they read the same to the user.
    oauth = raw.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise LimitsUnavailable("no Claude account OAuth in credentials — run /login", auth_expired=True)

    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        raise LimitsUnavailable("credentials carry no access token", auth_expired=True)

    return Credential(
        access_token=token,
        expires_at=_expires_at(oauth.get("expiresAt")),
        subscription_type=oauth.get("subscriptionType"),
        rate_limit_tier=oauth.get("rateLimitTier"),
    )


def account_display(email: str | None, org: str | None) -> str | None:
    """Personal plans get a generated "<email>'s Organization", which adds nothing."""
    if not email:
        return None
    if not org or email in org:
        return email
    return f"{email} · {org}"


def parse_profile(payload: object) -> tuple[str, str | None] | None:
    if not isinstance(payload, dict):
        return None
    account = payload.get("account")
    email = account.get("email") if isinstance(account, dict) else None
    if not isinstance(email, str) or not email:
        return None
    org = payload.get("organization")
    name = org.get("name") if isinstance(org, dict) else None
    return email, (name if isinstance(name, str) and name else None)


def saved_identity(claude_json: Path) -> tuple[str, str | None] | None:
    """The login Claude Code saved in `.claude.json`, used when the profile call fails."""
    try:
        raw = json.loads(claude_json.read_text())
    except (OSError, ValueError):
        return None
    account = raw.get("oauthAccount") if isinstance(raw, dict) else None
    if not isinstance(account, dict):
        return None
    email = account.get("emailAddress")
    if not isinstance(email, str) or not email:
        return None
    org = account.get("organizationName")
    return email, (org if isinstance(org, str) and org else None)


def _tier_multiplier(tier: str | None) -> str | None:
    """Pull a "20x"/"5x" style multiplier out of the rate-limit tier string."""
    if not tier:
        return None
    for part in tier.split("_"):
        if part.endswith("x") and part[:-1].isdigit():
            return part
    return None


def plan_display(cred: Credential) -> str | None:
    if not cred.subscription_type:
        return None
    base = cred.subscription_type[:1].upper() + cred.subscription_type[1:]
    multiplier = _tier_multiplier(cred.rate_limit_tier)
    return f"{base} {multiplier}" if multiplier else base


def _window(raw: object, key: str, name: str, kind: str) -> LimitWindow | None:
    if not isinstance(raw, dict):
        return None
    utilization = raw.get("utilization")
    return LimitWindow(
        key=key,
        name=name,
        kind=kind,
        utilization=float(utilization) if isinstance(utilization, (int, float)) else None,
        resets_at=raw.get("resets_at") if isinstance(raw.get("resets_at"), str) else None,
    )


def parse_status(payload: dict, cred: Credential) -> LimitStatus:
    windows: list[LimitWindow] = []
    legacy = [
        ("five_hour", "session", "5-hour session", "session"),
        ("seven_day", "weekly_all", "Weekly", "weekly"),
        ("seven_day_opus", "weekly_opus", "Weekly (Opus)", "weekly_scoped"),
        ("seven_day_sonnet", "weekly_sonnet", "Weekly (Sonnet)", "weekly_scoped"),
    ]
    for field_name, key, label, kind in legacy:
        w = _window(payload.get(field_name), key, label, kind)
        if w is not None and w.utilization is not None:
            windows.append(w)

    have_legacy = any(w.key in {"session", "weekly_all"} for w in windows)
    for entry in payload.get("limits") or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind") or "unknown"
        # The legacy fields already cover session and weekly_all; the newer list is
        # the only source for model-scoped weekly windows.
        if have_legacy and kind in {"session", "weekly_all"}:
            continue
        percent = entry.get("percent")
        model_name = (((entry.get("scope") or {}).get("model") or {}).get("display_name"))
        group = entry.get("group")
        label = model_name or group or str(kind).replace("_", " ").title()
        windows.append(
            LimitWindow(
                key=f"{kind}:{model_name or group or 'all'}",
                name=label,
                kind=str(kind),
                utilization=float(percent) if isinstance(percent, (int, float)) else None,
                resets_at=entry.get("resets_at") if isinstance(entry.get("resets_at"), str) else None,
            )
        )

    return LimitStatus(
        windows=[w for w in windows if w.utilization is not None],
        plan=plan_display(cred),
        fetched_at=time.time(),
    )


class LimitsProvider:
    def __init__(self, credentials_file: Path, claude_json: Path | None = None) -> None:
        self.credentials_file = credentials_file
        self.claude_json = claude_json
        self._last_good: Credential | None = None
        self._profiles: dict[str, tuple[str, str | None]] = {}

    def _credential(self) -> Credential:
        """Re-read the file every time, so a /login to another account is picked up.

        Only when the file vanished or was logged out does the last good token stand
        in, until it expires.
        """
        try:
            cred = read_credential(self.credentials_file)
        except LimitsUnavailable:
            if self._last_good is not None and not self._last_good.is_expired:
                return self._last_good
            raise
        if not cred.is_expired:
            self._last_good = cred
        elif self._last_good is not None and not self._last_good.is_expired:
            return self._last_good
        return cred

    async def _identity(
        self, client: httpx.AsyncClient, token: str
    ) -> tuple[str, str | None] | None:
        cached = self._profiles.get(token)
        if cached is not None:
            return cached
        try:
            resp = await client.get(
                PROFILE_URL,
                headers={"Authorization": f"Bearer {token}", "anthropic-beta": OAUTH_BETA},
                timeout=15.0,
            )
            identity = parse_profile(resp.json()) if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            identity = None
        if identity is not None:
            if len(self._profiles) >= PROFILE_CACHE_MAX:
                self._profiles.clear()
            self._profiles[token] = identity
        return identity

    def fallback_identity(self) -> tuple[str, str | None] | None:
        return saved_identity(self.claude_json) if self.claude_json else None

    async def fetch(self, client: httpx.AsyncClient) -> LimitStatus:
        cred = self._credential()
        if cred.is_expired:
            raise LimitsUnavailable("OAuth token expired — run /login", auth_expired=True)

        try:
            resp = await client.get(
                USAGE_URL,
                headers={
                    "Authorization": f"Bearer {cred.access_token}",
                    "anthropic-beta": OAUTH_BETA,
                },
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise LimitsUnavailable(f"network error: {exc}") from None

        if resp.status_code == 429:
            raise LimitsUnavailable(
                "rate limited by the usage endpoint",
                retry_after=_retry_after_seconds(resp),
            )
        if resp.status_code in (401, 403):
            raise LimitsUnavailable(
                "OAuth token rejected — run /login", auth_expired=True
            )
        if resp.status_code != 200:
            raise LimitsUnavailable(f"usage endpoint returned HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise LimitsUnavailable(f"usage endpoint returned invalid JSON: {exc}") from None
        if not isinstance(payload, dict):
            raise LimitsUnavailable("usage endpoint returned an unexpected shape")

        status = parse_status(payload, cred)
        # Best effort: a failed lookup only drops the label.
        identity = await self._identity(client, cred.access_token) or self.fallback_identity()
        if identity is not None:
            status.account_email, status.account_org = identity
        return status


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Seconds-form Retry-After only; HTTP-date and junk fall back to the default."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return min(seconds, 3600.0) if seconds > 0 else None
