"""Pet preferences, stored under XDG config so nothing lands in the repo."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

SIZES = (48, 96, 128, 192, 256, 320, 384)

DEFAULTS: dict[str, object] = {
    "x": 80,
    "y": 80,
    "size": 96,
    "animated": True,
    "keep_above": True,
    "show_bar": True,
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "poketokenbar"


def autostart_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "autostart" / "poketokenbar-pet.desktop"


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "pet.json")
        self.data: dict[str, object] = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable config %s: %s", self.path, exc)
            return
        if isinstance(loaded, dict):
            # Only known keys, so a hand-edited file can't inject surprises.
            self.data.update({k: v for k, v in loaded.items() if k in DEFAULTS})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, indent=2) + "\n")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not save config: %s", exc)

    def get(self, key: str, default: object = None) -> object:
        return self.data.get(key, default)

    @property
    def size(self) -> int:
        value = self.data.get("size", 96)
        return value if isinstance(value, int) and value in SIZES else 96

    @property
    def position(self) -> tuple[int, int]:
        x, y = self.data.get("x", 80), self.data.get("y", 80)
        return (int(x) if isinstance(x, (int, float)) else 80, int(y) if isinstance(y, (int, float)) else 80)

    def set(self, **values: object) -> None:
        self.data.update(values)
        self.save()
