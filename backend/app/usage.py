"""Aggregate parsed log entries into the windows the UI shows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .readers import claude_code as cc
from .readers.claude_code import Bucket, Entry

# The rolling window shared by the burn-rate block and the enrichment scan floor.
BLOCK_WINDOW = timedelta(hours=5)


@dataclass
class BlockUsage:
    start: str
    end: str
    total_tokens: int
    cost: float
    tokens_per_minute: float


@dataclass
class PeriodUsage:
    total_tokens: int
    cost: float
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def of(bucket: Bucket) -> "PeriodUsage":
        return PeriodUsage(
            total_tokens=bucket.total,
            cost=bucket.cost,
            input=bucket.input,
            output=bucket.output,
            cache_write=bucket.cache_write,
            cache_read=bucket.cache_read,
            by_model=dict(bucket.by_model),
        )


@dataclass
class UsageSnapshot:
    today_date: str
    today: PeriodUsage
    week: PeriodUsage
    month: PeriodUsage
    block: BlockUsage | None
    daily_history: list[tuple[str, int, float]]
    scanned_files: int
    generated_at: datetime

    @property
    def burn_per_minute(self) -> float:
        return self.block.tokens_per_minute if self.block else 0.0

    @property
    def burn_tier(self) -> str:
        burn = self.burn_per_minute
        if burn <= 1_000:
            return "idle"
        if burn < 100_000:
            return "normal"
        if burn < 400_000:
            return "fast"
        return "blazing"


def start_of_month(d: date) -> date:
    return d.replace(day=1)


def start_of_week(d: date) -> date:
    """Monday-based week start."""
    return d - timedelta(days=d.weekday())


def enrichment_scan_start(now: datetime, tz: ZoneInfo) -> datetime:
    """Earliest instant any displayed window begins.

    Using only the month start breaks at the start of a month, when this week began
    in the previous month, and just after midnight, when the 5-hour block reaches
    back into yesterday — session files last touched then would be skipped and the
    weekly total would under-count for days. Taking the minimum absorbs both edges.
    """
    local_now = now.astimezone(tz)
    today = local_now.date()
    candidates = [
        datetime.combine(start_of_month(today), datetime.min.time(), tzinfo=tz),
        datetime.combine(start_of_week(today), datetime.min.time(), tzinfo=tz),
        now - BLOCK_WINDOW,
    ]
    return min(candidates)


class UsageService:
    """Scans the log roots and caches parsed entries per file.

    A file's parsed entries are reused until its mtime or size changes, so a refresh
    only re-reads sessions that actually grew.
    """

    def __init__(self, roots: list[Path], tz: ZoneInfo) -> None:
        self.roots = roots
        self.tz = tz
        self._cache: dict[Path, tuple[float, int, list[Entry]]] = {}

    def _entries(self, modified_since: float) -> tuple[list[Entry], int]:
        collected: list[Entry] = []
        live: set[Path] = set()
        scanned = 0

        for path in cc.jsonl_files(self.roots, modified_since=None):
            try:
                st = path.stat()
            except OSError:
                continue
            live.add(path)
            # Files untouched since the window opened can only hold older entries,
            # but a cached parse is free — reuse it rather than dropping the file.
            cached = self._cache.get(path)
            if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
                collected.extend(cached[2])
                continue
            if st.st_mtime < modified_since and cached is None:
                continue
            entries = cc.parse_file(path, self.tz)
            self._cache[path] = (st.st_mtime, st.st_size, entries)
            collected.extend(entries)
            scanned += 1

        for stale in set(self._cache) - live:
            self._cache.pop(stale, None)

        return cc.dedupe_keep_max(collected), scanned

    def snapshot(self, now: datetime | None = None) -> UsageSnapshot:
        now = now or datetime.now(timezone.utc)
        floor = enrichment_scan_start(now, self.tz)
        entries, scanned = self._entries(floor.timestamp())

        local_today = now.astimezone(self.tz).date()
        today_key = local_today.strftime("%Y-%m-%d")
        week_key = start_of_week(local_today).strftime("%Y-%m-%d")
        month_key = start_of_month(local_today).strftime("%Y-%m-%d")

        today_b, week_b, month_b = Bucket(), Bucket(), Bucket()
        per_day: dict[str, Bucket] = {}
        window_start = now - BLOCK_WINDOW
        recent: list[Entry] = []

        for e in entries:
            day = e.local_day
            if day == today_key:
                today_b.add(e)
            if week_key <= day <= today_key:
                week_b.add(e)
            if month_key <= day <= today_key:
                month_b.add(e)
            per_day.setdefault(day, Bucket()).add(e)
            if e.date >= window_start:
                recent.append(e)

        block: BlockUsage | None = None
        if recent:
            recent.sort(key=lambda e: e.date)
            block_b = Bucket()
            for e in recent:
                block_b.add(e)
            first = recent[0].date
            minutes = max(1.0, (now - first).total_seconds() / 60.0)
            block = BlockUsage(
                start=first.isoformat(),
                end=(first + BLOCK_WINDOW).isoformat(),
                total_tokens=block_b.total,
                cost=block_b.cost,
                tokens_per_minute=block_b.total / minutes,
            )

        history = sorted(
            ((day, b.total, b.cost) for day, b in per_day.items()),
            key=lambda row: row[0],
        )[-30:]

        return UsageSnapshot(
            today_date=today_key,
            today=PeriodUsage.of(today_b),
            week=PeriodUsage.of(week_b),
            month=PeriodUsage.of(month_b),
            block=block,
            daily_history=history,
            scanned_files=scanned,
            generated_at=now,
        )
