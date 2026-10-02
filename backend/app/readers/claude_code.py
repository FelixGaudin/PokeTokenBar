"""Parse Claude Code session logs into normalised usage entries.

Reads `<root>/**/*.jsonl` and keeps the `type: "assistant"` lines, which carry
`message.usage` (four token counts), `message.model`, `message.id`, `requestId`
and `timestamp`.

Session resume and sidechains write the same message into more than one file, so
entries are de-duplicated on `(message.id, requestId)`. Streaming also re-logs a
growing message: `cache_read`/`input` stay fixed while `output` climbs, so the
largest total wins — keeping the first occurrence would badly under-count cost.

Claude Code also appends cumulative `type: "cost-state"` ledgers to each session file.
When one is present its per-model `costUSD` is spread over that model's entries by
token share, so the reported amount wins over the price table.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from ..pricing import estimated_cost, note_unpriced

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
    # Set from a cost-state ledger: the tool's own figure beats the table estimate.
    explicit_cost: float | None = None
    cost_is_estimate: bool | None = None
    # The log root the entry was read from, so each root is credited on its own.
    root: str = ""

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read


@dataclass
class CostCoverage:
    """Where a cost total came from. Flags merge with OR as totals combine."""

    reported: bool = False
    estimated: bool = False
    unknown: bool = False

    @property
    def has_known(self) -> bool:
        return self.reported or self.estimated

    def merge(self, other: "CostCoverage") -> None:
        self.reported = self.reported or other.reported
        self.estimated = self.estimated or other.estimated
        self.unknown = self.unknown or other.unknown


@dataclass
class Bucket:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    cost: float = 0.0
    coverage: CostCoverage = field(default_factory=CostCoverage)
    by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def add(self, e: Entry) -> None:
        """Reported cost first, then the table estimate, else unavailable."""
        self.input += e.input
        self.output += e.output
        self.cache_write += e.cache_write
        self.cache_read += e.cache_read
        self.by_model[e.model] = self.by_model.get(e.model, 0) + e.total
        if e.total <= 0:
            # Zero-usage replay rows say nothing about what the day cost.
            return
        explicit = e.explicit_cost
        if explicit is not None and math.isfinite(explicit) and explicit >= 0:
            self.cost += explicit
            if e.cost_is_estimate:
                self.coverage.estimated = True
            else:
                self.coverage.reported = True
            return
        est = estimated_cost(
            e.model,
            input=e.input,
            output=e.output,
            cache_write=e.cache_write,
            cache_read=e.cache_read,
        )
        if est is not None:
            self.cost += est
            self.coverage.estimated = True
            return
        note_unpriced(e.model)
        self.coverage.unknown = True


def _int_or_none(v: object) -> int | None:
    """Numbers only. JSON null, strings and missing keys are all absent.

    Checking `!= None` on the raw dict lets an explicit `null` pass as "present"
    and collapse to 0, which silently under-counts. Negative token counts do not
    exist, so they read as 0.
    """
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return max(0, min(MAX_PARSED_TOKEN_VALUE, v))
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return max(0, min(MAX_PARSED_TOKEN_VALUE, int(v)))
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
    """One entry per id: the largest total (the completed message), earliest time.

    A forked session replays earlier turns under a later timestamp. Taking the date
    from the replay would drag historical usage into today's totals.
    """
    by_id: dict[str, Entry] = {}
    for e in entries:
        existing = by_id.get(e.id)
        if existing is None:
            by_id[e.id] = e
            continue
        kept = e if e.total > existing.total else existing
        earliest = e if e.date < existing.date else existing
        if kept.date != earliest.date:
            kept = replace(kept, date=earliest.date, local_day=earliest.local_day)
        by_id[e.id] = kept
    return list(by_id.values())


def cost_model_key(model: str) -> str:
    """Drop a context suffix: cost-state keys look like `claude-opus-5[1m]`."""
    return model.split("[", 1)[0]


def parse_cost_state_line(line: str) -> dict[str, float] | None:
    """Per-model reported USD from a cost-state line, or None if it has none usable."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or obj.get("type") != "cost-state":
        return None
    usage = obj.get("modelUsage")
    if not isinstance(usage, dict):
        return None
    out: dict[str, float] = {}
    for model, fields in usage.items():
        if not isinstance(fields, dict):
            continue
        value = fields.get("costUSD")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        key = cost_model_key(str(model))
        out[key] = out.get(key, 0.0) + float(value)
    return out or None


def apply_reported_cost(entries: list[Entry], cost_by_model: dict[str, float]) -> list[Entry]:
    """Spread each model's session cost over its entries in proportion to tokens.

    The last entry absorbs the rounding remainder so the session sum is exact. A
    model the ledger prices but the file never logged is dropped, never moved onto
    another model.
    """
    indices: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        if e.total > 0:
            indices.setdefault(cost_model_key(e.model), []).append(i)
    out = list(entries)
    for model, amount in cost_by_model.items():
        idx = indices.get(model)
        if not idx:
            continue
        total_tokens = sum(out[i].total for i in idx)
        if total_tokens <= 0:
            continue
        remaining = amount
        for n, i in enumerate(idx):
            share = remaining if n == len(idx) - 1 else amount * out[i].total / total_tokens
            out[i] = replace(out[i], explicit_cost=max(0.0, share), cost_is_estimate=False)
            remaining -= share
    return out


def parse_file(path: Path, tz: ZoneInfo) -> list[Entry]:
    """Parse one session file, de-duplicated within the file."""
    out: list[Entry] = []
    # The ledger is cumulative, so the last usable one in the file wins.
    cost_state: dict[str, float] | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"cost-state"' in line:
                    parsed = parse_cost_state_line(line)
                    if parsed is not None:
                        cost_state = parsed
                    continue
                # Cheap pre-filter: most lines are user turns or tool results.
                if '"usage"' not in line or '"assistant"' not in line:
                    continue
                entry = parse_line(line, tz)
                if entry is not None:
                    out.append(entry)
    except OSError:
        return []
    entries = dedupe_keep_max(out)
    if cost_state:
        entries = apply_reported_cost(entries, cost_state)
    return entries


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
