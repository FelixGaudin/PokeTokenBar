"""Usage recap for any calendar week, month or year.

Logs only ever cover the current month, so each refresh copies daily token totals
into a ledger kept in the save. The recap reads that ledger, which also records the
first day it covers — so "no usage" can be told apart from "not recorded yet".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from .state import DexEntry, SaveState

MAX_OFFSET = {"week": 5200, "month": 1200, "year": 100}


def _parse_day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def merge_ledger(state: SaveState, series: list[tuple[str, int]]) -> bool:
    """Fold this month's daily totals in. A day's count never goes down."""
    changed = False
    for day, tokens in series:
        if tokens > 0 and tokens > state.token_ledger.get(day, 0):
            state.token_ledger[day] = tokens
            changed = True
    if series:
        first = min(day for day, _ in series)
        if state.token_ledger_since is None or first < state.token_ledger_since:
            state.token_ledger_since = first
            changed = True
    return changed


def prune_ledger(state: SaveState, today: date) -> bool:
    """Keep this year and last year."""
    cutoff = date(today.year - 1, 1, 1).isoformat()
    stale = [k for k in state.token_ledger if k < cutoff or _parse_day(k) is None]
    for k in stale:
        del state.token_ledger[k]
    changed = bool(stale)
    since = state.token_ledger_since
    if since is not None and (_parse_day(since) is None or since < cutoff):
        state.token_ledger_since = cutoff if _parse_day(since) is not None else None
        changed = True
    return changed


def _covers(state: SaveState, day: date) -> bool:
    since = state.token_ledger_since
    return since is not None and day.isoformat() >= since


def period_bounds(scope: str, offset: int, today: date) -> tuple[date, date]:
    """[start, end) of the week (Monday-based), month or year `offset` periods back."""
    if scope == "week":
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        return start, start + timedelta(days=7)
    if scope == "month":
        months = today.year * 12 + (today.month - 1) + offset
        start = date(months // 12, months % 12 + 1, 1)
        nxt = months + 1
        return start, date(nxt // 12, nxt % 12 + 1, 1)
    if scope == "year":
        start = date(today.year + offset, 1, 1)
        return start, date(start.year + 1, 1, 1)
    raise ValueError(f"unknown scope: {scope}")


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days)]


@dataclass
class RecapBucket:
    key: str
    tokens: int
    is_current: bool
    has_data: bool


@dataclass
class Recap:
    scope: str
    offset: int
    start: str
    end: str  # exclusive
    total: int
    buckets: list[RecapBucket]
    is_in_progress: bool
    previous_total: int | None
    delta: float | None
    best_day: str | None
    best_day_tokens: int
    active_days: int
    counted_days: int
    best_streak: int
    graduated: list[DexEntry] = field(default_factory=list)
    can_go_back: bool = False


def make_recap(state: SaveState, scope: str, offset: int, today: date, tz: tzinfo) -> Recap:
    # Far enough back for any real ledger, near enough that dates stay valid.
    offset = max(-MAX_OFFSET[scope] if scope in MAX_OFFSET else 0, min(0, offset))
    start, end = period_bounds(scope, offset, today)
    ledger = state.token_ledger

    def tokens(d: date) -> int:
        return ledger.get(d.isoformat(), 0)

    def known(d: date) -> bool:
        return tokens(d) > 0 or _covers(state, d)

    days = _days(start, end)
    elapsed = [d for d in days if d <= today]
    total = sum(tokens(d) for d in elapsed)

    best_day: date | None = None
    for d in elapsed:
        if tokens(d) > 0 and (best_day is None or tokens(d) > tokens(best_day)):
            best_day = d
    active_days = sum(1 for d in elapsed if tokens(d) > 0)
    counted_days = sum(1 for d in elapsed if known(d))
    best_streak = run = 0
    for d in elapsed:
        run = run + 1 if tokens(d) > 0 else 0
        best_streak = max(best_streak, run)

    if scope == "year":
        buckets = []
        for m in range(1, 13):
            m_start, m_end = period_bounds("month", 0, date(start.year, m, 1))
            m_days = [d for d in _days(m_start, m_end) if d <= today]
            buckets.append(
                RecapBucket(
                    key=m_start.isoformat(),
                    tokens=sum(tokens(d) for d in m_days),
                    is_current=m_start <= today < m_end,
                    has_data=any(known(d) for d in m_days),
                )
            )
    else:
        buckets = [
            RecapBucket(
                key=d.isoformat(),
                tokens=tokens(d),
                is_current=d == today,
                has_data=d <= today and known(d),
            )
            for d in days
        ]

    is_in_progress = start <= today < end
    p_start, p_end = period_bounds(scope, offset - 1, today)
    previous_days = _days(p_start, p_end)
    compared = previous_days[: len(elapsed)] if is_in_progress else previous_days
    # Coverage is judged on the oldest compared day: judging on the newest one
    # inflated the change when the ledger started mid-period.
    previous_total = (
        sum(tokens(d) for d in compared) if compared and _covers(state, compared[0]) else None
    )
    delta = (total - previous_total) / previous_total if previous_total else None

    graduated = sorted(
        (
            e
            for e in state.dex
            if not e.is_released
            and e.caught_at is not None
            and start <= e.caught_at.astimezone(tz).date() < end
        ),
        key=lambda e: e.caught_at or datetime.min,
        reverse=True,
    )

    return Recap(
        scope=scope,
        offset=offset,
        start=start.isoformat(),
        end=end.isoformat(),
        total=total,
        buckets=buckets,
        is_in_progress=is_in_progress,
        previous_total=previous_total,
        delta=delta,
        best_day=best_day.isoformat() if best_day else None,
        best_day_tokens=tokens(best_day) if best_day else 0,
        active_days=active_days,
        counted_days=counted_days,
        best_streak=best_streak,
        graduated=graduated,
        can_go_back=_covers(state, previous_days[-1]),
    )
