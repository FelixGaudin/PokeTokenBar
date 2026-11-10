from datetime import date, datetime, timezone

import pytest

from app.recap import (
    activity_grid,
    activity_level,
    current_streak,
    make_recap,
    merge_ledger,
    prune_ledger,
)
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


# ---------------------------------------------------------------- current streak


def active_on(*days):
    return SaveState(token_ledger={d: 100 for d in days})


def test_a_run_ending_today_counts_today():
    s = active_on("2026-09-16", "2026-09-17", "2026-09-18")
    assert current_streak(s, TODAY) == (3, True)


def test_an_unused_today_does_not_break_the_run():
    s = active_on("2026-09-16", "2026-09-17")
    assert current_streak(s, TODAY) == (2, False)


def test_a_whole_day_without_usage_ends_the_run():
    s = active_on("2026-09-14", "2026-09-15", "2026-09-17", "2026-09-18")
    assert current_streak(s, TODAY) == (2, True)
    assert current_streak(active_on("2026-09-15", "2026-09-16"), TODAY) == (0, False)


def test_the_run_carries_across_the_first_of_the_month():
    s = active_on("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01")
    assert current_streak(s, date(2026, 10, 1)) == (4, True)


def test_the_streak_agrees_with_the_recaps_best_streak():
    s = SaveState(
        token_ledger={d: 100 for d in ("2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18")},
        token_ledger_since="2026-09-01",
    )
    assert current_streak(s, TODAY).days == recap(s, "week").best_streak == 4


# ---------------------------------------------------------------- 16-week grid


def _dates(grid):
    return [c.date for week in grid.weeks for c in week]


@pytest.mark.parametrize("today", ["2026-10-01", "2026-10-19", "2026-10-31", "2026-11-03"])
def test_the_grid_is_sixteen_monday_weeks_ending_with_todays_week(today):
    day = date.fromisoformat(today)
    grid = activity_grid(SaveState(), [], day)
    assert len(grid.weeks) == 16
    assert all(len(week) == 7 for week in grid.weeks)
    dates = [date.fromisoformat(d) for d in _dates(grid)]
    assert len(set(dates)) == 112
    assert all((b - a).days == 1 for a, b in zip(dates, dates[1:], strict=False))
    assert dates[0].weekday() == 0, "Monday first, like the week totals and the recap"
    assert today in [c.date for c in grid.weeks[-1]]
    in_range = [c.date for week in grid.weeks for c in week if c.in_range]
    assert in_range == [today], "with no history only today is in range"


def test_the_first_of_the_month_keeps_last_months_days():
    s = SaveState(
        token_ledger={"2026-08-20": 50, "2026-09-30": 70},
        token_ledger_since="2026-08-01",
    )
    grid = activity_grid(s, [("2026-10-01", 30)], date(2026, 10, 1))
    cells = {c.date: c for week in grid.weeks for c in week}
    assert cells["2026-09-30"].tokens == 70 and cells["2026-09-30"].in_range
    assert cells["2026-08-20"].tokens == 50
    assert cells["2026-10-01"].tokens == 30
    assert grid.used_days == 3
    assert grid.peak == 70


def test_days_before_recording_began_are_outside_the_range():
    live = [(f"2026-10-{d:02d}", 10) for d in range(1, 20)]
    grid = activity_grid(SaveState(), live, date(2026, 10, 19))
    cells = {c.date: c for week in grid.weeks for c in week}
    assert not cells["2026-09-30"].in_range
    assert cells["2026-10-01"].in_range
    assert not cells["2026-10-20"].in_range, "days after today are outside too"
    first = next(c for week in grid.weeks for c in week if c.in_range)
    assert first.date == "2026-10-01"


def test_the_ledger_coverage_start_bounds_the_range():
    s = SaveState(token_ledger={}, token_ledger_since="2026-09-15")
    grid = activity_grid(s, [], date(2026, 10, 19))
    cells = {c.date: c for week in grid.weeks for c in week}
    assert not cells["2026-09-14"].in_range
    assert cells["2026-09-15"].in_range and cells["2026-09-15"].tokens == 0


def test_the_live_series_wins_only_when_higher():
    s = SaveState(token_ledger={"2026-10-18": 500}, token_ledger_since="2026-10-01")
    higher = activity_grid(s, [("2026-10-18", 2000)], date(2026, 10, 19))
    lower = activity_grid(s, [("2026-10-18", 100)], date(2026, 10, 19))
    assert higher.peak == 2000
    assert lower.peak == 500


def test_levels_split_used_days_into_quartiles_of_the_peak():
    peak = 100_000_000
    assert [activity_level(t, peak) for t in (0, 1, 25_000_000, 25_000_001)] == [0, 1, 1, 2]
    assert [activity_level(t, peak) for t in (50_000_000, 75_000_000, peak)] == [2, 3, 4]
    assert activity_level(0, 0) == 0
