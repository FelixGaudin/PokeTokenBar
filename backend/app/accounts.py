"""Additional Claude accounts, each kept in its own config folder.

Claude Code run with `CLAUDE_CONFIG_DIR=/path` keeps a whole login inside that
folder: `.credentials.json`, `.claude.json` (with `oauthAccount`) and `projects/`.
Each folder mounted into the container becomes one more account with its own
official limits.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AccountFolder:
    id: str
    label: str
    root: Path

    @property
    def credentials_file(self) -> Path:
        return self.root / ".credentials.json"

    @property
    def claude_json(self) -> Path:
        return self.root / ".claude.json"

    @property
    def projects(self) -> Path:
        return self.root / "projects"


def account_id(key: str) -> str:
    """Stable across restarts as long as the label (or mount point) stays put."""
    return hashlib.sha256(key.rstrip("/").encode("utf-8")).hexdigest()[:8]


def has_login(root: Path) -> bool:
    try:
        raw = json.loads((root / ".claude.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and isinstance(raw.get("oauthAccount"), dict)


def parse_entries(raw: str | None) -> list[tuple[str, Path]]:
    """`label=path` or bare `path`, separated by commas or newlines."""
    out: list[tuple[str, Path]] = []
    for part in (raw or "").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        label, sep, path = part.partition("=")
        if not sep:
            label, path = "", part
        p = Path(os.path.expanduser(path.strip()))
        if p.is_absolute():
            out.append((label.strip(), p))
    return out


def discover(
    listed: list[tuple[str, Path]], search_root: Path | None, default_roots: list[Path]
) -> list[AccountFolder]:
    """Listed folders are trusted; searched `.claude-*` folders need a saved login."""
    defaults = {str(p.resolve()) for p in default_roots if p.exists()}
    seen: set[str] = set()
    out: list[AccountFolder] = []

    def add(label: str, root: Path) -> None:
        if not root.is_dir():
            return
        resolved = str(root.resolve())
        if resolved in defaults or resolved in seen:
            return
        seen.add(resolved)
        name = label or root.name
        # The path is part of the key, so two folders sharing a label stay apart.
        key = f"{label}={root}" if label else str(root)
        out.append(AccountFolder(id=account_id(key), label=name, root=root))

    for label, root in listed:
        add(label, root)
    if search_root is not None and search_root.is_dir():
        try:
            names = sorted(os.listdir(search_root))
        except OSError:
            names = []
        for name in names:
            if name.startswith((".claude-", ".claude_")) and has_login(search_root / name):
                add("", search_root / name)
    return out
