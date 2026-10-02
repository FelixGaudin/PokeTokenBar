"""Aggregate parsed log entries into the windows the UI shows."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .readers import claude_code as cc
from .readers.claude_code import Bucket, CostCoverage, Entry

# The rolling window shared by the burn-rate block and the enrichment scan floor.
BLOCK_WINDOW = timedelta(hours=5)


@dataclass
class BlockUsage:
    start: str
    end: str
    total_tokens: int
    cost: float
    tokens_per_minute: float
    coverage: CostCoverage = field(default_factory=CostCoverage)


@dataclass
class PeriodUsage:
    total_tokens: int
    cost: float
    coverage: CostCoverage = field(default_factory=CostCoverage)
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
            coverage=bucket.coverage,
            input=bucket.input,
            output=bucket.output,
            cache_write=bucket.cache_write,
            cache_read=bucket.cache_read,
            by_model=dict(bucket.by_model),
        )


@dataclass
class DayUsage:
    date: str
    total_tokens: int
    cost: float
    coverage: CostCoverage = field(default_factory=CostCoverage)


@dataclass
class UsageSnapshot:
    today_date: str
    today: PeriodUsage
    week: PeriodUsage
    month: PeriodUsage
    block: BlockUsage | None
    # Every day from the 1st through today, empty days included, summing to `month`.
    month_daily: list[DayUsage]
    scanned_files: int
    generated_at: datetime
    # Which configured roots actually exist on disk, and how many session files
    # were seen across them. A zero here means "nothing to read", which is very
    # different from "read successfully, and the total is zero".
    present_roots: list[str] = field(default_factory=list)
    missing_roots: list[str] = field(default_factory=list)
    total_files: int = 0
    # Today's tokens per present log root.
    today_by_root: dict[str, int] = field(default_factory=dict)
    # (day, tokens) for every day the scan fully covers, which reaches back past
    # the 1st early in a month — so the end of last month still reaches the ledger.
    recent_daily: list[tuple[str, int]] = field(default_factory=list)

    @property
    def has_source(self) -> bool:
        """True when at least one log directory exists to be read."""
        return bool(self.present_roots)

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

    def _files(self):
        """Every session file with the root it belongs to; a file is listed once."""
        seen: set[Path] = set()
        for root in self.roots:
            for path in cc.jsonl_files([root], modified_since=None):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    yield path, str(root)

    def _entries(self, modified_since: float) -> tuple[list[Entry], int, int]:
        collected: list[Entry] = []
        live: set[Path] = set()
        scanned = 0
        seen_files = 0

        for path, root in self._files():
            try:
                st = path.stat()
            except OSError:
                continue
            live.add(path)
            seen_files += 1
            # Files untouched since the window opened can only hold older entries,
            # but a cached parse is free — reuse it rather than dropping the file.
            cached = self._cache.get(path)
            if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
                collected.extend(cached[2])
                continue
            if st.st_mtime < modified_since and cached is None:
                continue
            entries = [replace(e, root=root) for e in cc.parse_file(path, self.tz)]
            self._cache[path] = (st.st_mtime, st.st_size, entries)
            collected.extend(entries)
            scanned += 1

        for stale in set(self._cache) - live:
            self._cache.pop(stale, None)

        return cc.dedupe_keep_max(collected), scanned, seen_files

    def snapshot(self, now: datetime | None = None) -> UsageSnapshot:
        now = now or datetime.now(timezone.utc)
        floor = enrichment_scan_start(now, self.tz)
        entries, scanned, seen_files = self._entries(floor.timestamp())
        present = [str(r) for r in self.roots if r.is_dir()]
        missing = [str(r) for r in self.roots if not r.is_dir()]

        local_today = now.astimezone(self.tz).date()
        today_key = local_today.strftime("%Y-%m-%d")
        week_key = start_of_week(local_today).strftime("%Y-%m-%d")
        month_key = start_of_month(local_today).strftime("%Y-%m-%d")

        today_b, week_b, month_b = Bucket(), Bucket(), Bucket()
        # Calendar stepping, not +86400s, so a DST change never skips a day.
        month_days = [
            (start_of_month(local_today) + timedelta(days=n)).strftime("%Y-%m-%d")
            for n in range(local_today.day)
        ]
        per_day: dict[str, Bucket] = {d: Bucket() for d in month_days}
        today_by_root: dict[str, int] = {r: 0 for r in present}
        first_full_day = (floor.astimezone(self.tz) + timedelta(days=1)).date()
        if floor.astimezone(self.tz).time() == datetime.min.time():
            first_full_day = floor.astimezone(self.tz).date()
        recent_days = [
            (first_full_day + timedelta(days=n)).strftime("%Y-%m-%d")
            for n in range((local_today - first_full_day).days + 1)
        ]
        recent_totals: dict[str, int] = dict.fromkeys(recent_days, 0)
        window_start = now - BLOCK_WINDOW
        recent: list[Entry] = []

        for e in entries:
            day = e.local_day
            if day == today_key:
                today_b.add(e)
                today_by_root[e.root] = today_by_root.get(e.root, 0) + e.total
            if week_key <= day <= today_key:
                week_b.add(e)
            if day in recent_totals:
                recent_totals[day] += e.total
            if month_key <= day <= today_key:
                month_b.add(e)
                # Last month's turns in a file that crossed the boundary stay out.
                per_day[day].add(e)
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
                coverage=block_b.coverage,
            )

        month_daily = [
            DayUsage(date=d, total_tokens=per_day[d].total, cost=per_day[d].cost,
                     coverage=per_day[d].coverage)
            for d in month_days
        ]

        return UsageSnapshot(
            today_date=today_key,
            today=PeriodUsage.of(today_b),
            week=PeriodUsage.of(week_b),
            month=PeriodUsage.of(month_b),
            block=block,
            month_daily=month_daily,
            today_by_root=today_by_root,
            recent_daily=list(recent_totals.items()),
            scanned_files=scanned,
            generated_at=now,
            present_roots=present,
            missing_roots=missing,
            total_files=seen_files,
        )
