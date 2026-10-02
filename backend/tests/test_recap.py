from datetime import date, datetime, timezone

import pytest

from app.recap import make_recap, merge_ledger, prune_ledger
from app.state import DexEntry, SaveState

UTC = timezone.utc
TODAY = date(2026, 9, 18)  # a Friday


def recap(state, scope="week", offset=0):
    return make_recap(state, scope, offset, TODAY, UTC)


def ledger(since, rows):
    return SaveState(token_ledger=dict(rows), token_ledger_since=since)


def test_merge_never_lowers_a_day():
    s = SaveState()
    merge_ledger(s, [("2026-09-01", 500), ("2026-09-02", 300)])
    merge_ledger(s, [("2026-09-01", 120), ("2026-09-02", 900)])
    assert s.token_ledger == {"2026-09-01": 500, "2026-09-02": 900}


def test_merge_records_coverage_from_the_first_day_even_if_empty():
    s = SaveState()
    merge_ledger(s, [("2026-09-01", 0), ("2026-09-02", 40)])
    assert s.token_ledger == {"2026-09-02": 40}
    assert s.token_ledger_since == "2026-09-01"
    merge_ledger(s, [("2026-09-05", 1)])
    assert s.token_ledger_since == "2026-09-01"


def test_prune_keeps_this_year_and_last():
    s = ledger("2025-06-01", {"2025-06-10": 100, "2026-02-01": 200})
    prune_ledger(s, date(2027, 3, 1))
    assert s.token_ledger == {"2026-02-01": 200}
    assert s.token_ledger_since == "2026-01-01"


def test_period_shapes():
    s = SaveState()
    week = recap(s)
    assert week.start == "2026-09-14" and len(week.buckets) == 7
    assert [b.is_current for b in week.buckets].index(True) == 4
    assert len(recap(s, "month").buckets) == 30
    assert len(recap(s, "month", -7).buckets) == 28
    s = ledger("2026-08-01", {"2026-09-10": 10, "2026-09-11": 20, "2026-08-05": 5})
    year = recap(s, "year")
    assert len(year.buckets) == 12
    assert year.buckets[8].tokens == 30 and year.buckets[7].tokens == 5
    assert year.buckets[8].is_current and year.total == 35


def test_has_data_follows_coverage():
    s = ledger("2026-09-16", {"2026-09-17": 50})
    week = recap(s)
    assert [b.has_data for b in week.buckets] == [False, False, True, True, True, False, False]
    assert week.active_days == 1 and week.counted_days == 3


def test_a_running_month_compares_the_same_days():
    s = ledger("2026-08-01", {"2026-09-10": 100, "2026-08-18": 40, "2026-08-19": 1000})
    r = recap(s, "month")
    assert r.previous_total == 40 and r.delta == pytest.approx(1.5)


def test_a_finished_month_compares_the_whole_previous_month():
    s = ledger("2026-07-01", {"2026-08-05": 300, "2026-07-31": 100})
    r = recap(s, "month", -1)
    assert r.previous_total == 100 and r.delta == pytest.approx(2.0)


def test_a_half_covered_previous_period_has_no_comparison():
    s = ledger("2026-09-09", {"2026-09-15": 10})
    r = recap(s)
    assert r.previous_total is None and r.delta is None


def test_streaks_stay_inside_the_period():
    rows = {f"2026-09-{d:02d}": 10 for d in (11, 12, 13, 14, 15, 16, 18)}
    s = ledger("2026-09-01", rows)
    assert recap(s).best_streak == 3
    assert recap(s, "month").best_streak == 6


def test_graduations_are_counted_by_period_and_releases_excluded():
    def entry(name, when, released=False):
        return DexEntry(
            id=name, base_id=1, final_id=3, caught_at=when, released_at=when if released else None
        )

    s = SaveState(
        dex=[
            entry("this-week", datetime(2026, 9, 17, 12, tzinfo=UTC)),
            entry("monday", datetime(2026, 9, 14, 0, tzinfo=UTC)),
            entry("released", datetime(2026, 9, 15, 12, tzinfo=UTC), released=True),
            entry("last-week", datetime(2026, 9, 13, 23, tzinfo=UTC)),
        ]
    )
    assert [e.id for e in recap(s).graduated] == ["this-week", "monday"]
    assert len(recap(s, "month").graduated) == 3


def test_going_back_needs_coverage():
    s = ledger("2026-09-01", {})
    assert not recap(s, "month").can_go_back
    assert recap(s, "week", -1).can_go_back
    assert not recap(s, "week", -2).can_go_back
    assert not recap(s, "year").can_go_back


def test_an_empty_ledger():
    r = recap(SaveState())
    assert len(r.buckets) == 7 and not any(b.has_data for b in r.buckets)
    assert r.total == 0 and r.best_day is None and r.previous_total is None
    assert not r.can_go_back
