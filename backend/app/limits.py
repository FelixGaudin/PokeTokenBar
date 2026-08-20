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
OAUTH_BETA = "oauth-2025-04-20"


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


@dataclass
class LimitStatus:
    windows: list[LimitWindow] = field(default_factory=list)
    plan: str | None = None
    fetched_at: float = 0.0

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
    def __init__(self, credentials_file: Path) -> None:
        self.credentials_file = credentials_file

    async def fetch(self, client: httpx.AsyncClient) -> LimitStatus:
        cred = read_credential(self.credentials_file)
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

        return parse_status(payload, cred)


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
