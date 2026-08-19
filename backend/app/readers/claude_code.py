"""Parse Claude Code session logs into normalised usage entries.

Reads `<root>/**/*.jsonl` and keeps the `type: "assistant"` lines, which carry
`message.usage` (four token counts), `message.model`, `message.id`, `requestId`
and `timestamp`.

Session resume and sidechains write the same message into more than one file, so
entries are de-duplicated on `(message.id, requestId)`. Streaming also re-logs a
growing message: `cache_read`/`input` stay fixed while `output` climbs, so the
largest total wins — keeping the first occurrence would badly under-count cost.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from ..pricing import cost

# Parsing ceiling. Real usage is in the billions; this is 100,000x that, so it never
# clips a legitimate value. Logs are written outside this app (hand edits, upstream
# bugs, truncated transfers) and a bad value stays on disk, so clamping beats
# crashing on every refresh until the user deletes the file by hand.
MAX_PARSED_TOKEN_VALUE = 1_000_000_000_000_000

PROVIDER_ID = "claude_code"


@dataclass
class Entry:
    id: str
    date: datetime
    local_day: str
    model: str
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read


@dataclass
class Bucket:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    cost: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def add(self, e: Entry) -> None:
        self.input += e.input
        self.output += e.output
        self.cache_write += e.cache_write
        self.cache_read += e.cache_read
        self.cost += cost(
            e.model,
            input=e.input,
            output=e.output,
            cache_write=e.cache_write,
            cache_read=e.cache_read,
        )
        self.by_model[e.model] = self.by_model.get(e.model, 0) + e.total


def _int_or_none(v: object) -> int | None:
    """Numbers only. JSON null, strings and missing keys are all absent.

    Checking `!= None` on the raw dict lets an explicit `null` pass as "present"
    and collapse to 0, which silently under-counts.
    """
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return max(-MAX_PARSED_TOKEN_VALUE, min(MAX_PARSED_TOKEN_VALUE, v))
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return max(-MAX_PARSED_TOKEN_VALUE, min(MAX_PARSED_TOKEN_VALUE, int(v)))
    return None


def _int(v: object) -> int:
    return _int_or_none(v) or 0


def parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def local_day(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d")


def parse_line(line: str, tz: ZoneInfo) -> Entry | None:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    dt = parse_timestamp(obj.get("timestamp"))
    if dt is None:
        return None

    return Entry(
        id=f"{msg.get('id') or ''}|{obj.get('requestId') or ''}",
        date=dt,
        local_day=local_day(dt, tz),
        model=msg.get("model") or "unknown",
        input=_int(usage.get("input_tokens")),
        output=_int(usage.get("output_tokens")),
        cache_write=_int(usage.get("cache_creation_input_tokens")),
        cache_read=_int(usage.get("cache_read_input_tokens")),
    )


def dedupe_keep_max(entries: Iterable[Entry]) -> list[Entry]:
    """One entry per id, keeping the largest total (the completed message)."""
    by_id: dict[str, Entry] = {}
    for e in entries:
        existing = by_id.get(e.id)
        if existing is None or e.total > existing.total:
            by_id[e.id] = e
    return list(by_id.values())


def parse_file(path: Path, tz: ZoneInfo) -> list[Entry]:
    """Parse one session file, de-duplicated within the file."""
    out: list[Entry] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # Cheap pre-filter: most lines are user turns or tool results.
                if '"usage"' not in line or '"assistant"' not in line:
                    continue
                entry = parse_line(line, tz)
                if entry is not None:
                    out.append(entry)
    except OSError:
        return []
    return dedupe_keep_max(out)


def jsonl_files(roots: Iterable[Path], modified_since: float | None = None) -> Iterator[Path]:
    """Session files under any root, optionally limited by mtime.

    Logs are append-only, so a file last modified before a window's start holds no
    entries inside that window and can be skipped entirely.
    """
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                if not name.endswith(".jsonl"):
                    continue
                path = Path(dirpath) / name
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                if modified_since is not None:
                    try:
                        if path.stat().st_mtime < modified_since:
                            continue
                    except OSError:
                        continue
                seen.add(resolved)
                yield path
