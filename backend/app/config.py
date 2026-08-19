"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def _paths(raw: str | None, default: list[Path]) -> list[Path]:
    if not raw:
        return default
    out: list[Path] = []
    for part in raw.split(","):
        p = part.strip()
        if p:
            out.append(Path(os.path.expanduser(p)))
    return out or default


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Everything the app needs to locate data and decide how often to look."""

    data_dir: Path
    claude_roots: list[Path]
    credentials_file: Path
    poll_interval: float
    limits_enabled: bool
    limits_interval: float
    timezone: ZoneInfo
    # Supplying a token directly means the credentials file is never opened.
    oauth_token: str | None = field(default=None)
    static_dir: Path | None = field(default=None)

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def sprite_dir(self) -> Path:
        return self.data_dir / "sprites"


def load_settings() -> Settings:
    home = Path(os.path.expanduser("~"))
    data_dir = Path(os.environ.get("PTB_DATA_DIR", "/data"))
    static_raw = os.environ.get("PTB_STATIC_DIR", "/app/static")
    static_dir = Path(static_raw) if static_raw else None
    tz_name = os.environ.get("PTB_TZ") or os.environ.get("TZ") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    return Settings(
        data_dir=data_dir,
        claude_roots=_paths(
            os.environ.get("PTB_CLAUDE_ROOTS"),
            [home / ".claude" / "projects", home / ".config" / "claude" / "projects"],
        ),
        credentials_file=Path(
            os.path.expanduser(
                os.environ.get("PTB_CREDENTIALS_FILE", str(home / ".claude" / ".credentials.json"))
            )
        ),
        poll_interval=float(os.environ.get("PTB_POLL_INTERVAL", "60")),
        limits_enabled=_bool(os.environ.get("PTB_LIMITS_ENABLED"), True),
        limits_interval=float(os.environ.get("PTB_LIMITS_INTERVAL", "300")),
        timezone=tz,
        oauth_token=(os.environ.get("PTB_OAUTH_TOKEN") or "").strip() or None,
        static_dir=static_dir if static_dir and static_dir.exists() else None,
    )


SETTINGS = load_settings()
