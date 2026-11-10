"""Prayers, sulking, the event feed and growth % (upstream 2.5.7)."""

from datetime import UTC, datetime, timedelta

import pytest

from app import balance as B
from app.balance import Rarity
from app.companion import hatch_text
from app.pokeapi import BaseSpecies
from app.state import MonState, PrayerRecord, SaveState, sanitize

from .test_companion import (
    NoDisguiseRNG,
    feed,
    make_service,
    single_stage_line,
    three_stage_line,
)

HOUR = 3600.0


def later():
    """A test clock after the hatch, which saw today's total rise at the real time."""
    return datetime.now(UTC) + timedelta(days=1)


async def hatched(tmp_path, rng=None):
    svc = make_service(tmp_path, {1: three_stage_line()}, rng=rng)
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active is not None
    # The hatch flourish outranks every mood for a few seconds.
    svc.event = svc.event_until = None
    return svc


# ------------------------------------------------------------------- prayers


def test_praying_counts_over_the_egg_only(tmp_path):
    svc = make_service(tmp_path, {})
    assert [svc.pray() for _ in range(3)] == [1, 2, 3]
    assert svc.state.egg_prayers.count == 3
    svc.state.active = MonState(base_id=1, current_id=1, path_ids=[1])
    with pytest.raises(ValueError):
        svc.pray()


async def test_prayers_follow_the_egg_into_the_pokemon_and_the_dex(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    for _ in range(31):
        svc.pray()
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active.prayers.count == 31
    assert svc.state.egg_prayers is None
    hatch = next(e for e in svc.recent_events() if e.kind == "hatch")
    assert hatch.text == "31 prayers later: Mon1. Heaven must be swamped today."

    svc.state.used_since_install = 5_000_000_000
    svc.buy_fresh_egg(None)
    assert svc.state.dex[-1].prayers.count == 31
    assert svc.state.egg_prayers is None


async def test_praying_never_changes_what_hatches(tmp_path):
    quiet = await hatched(tmp_path / "a", rng=NoDisguiseRNG(7))
    devout = make_service(tmp_path / "b", {1: three_stage_line()}, rng=NoDisguiseRNG(7))
    await feed(devout, 1_000)
    for _ in range(300):
        devout.pray()
    await feed(devout, 1_000 + B.EGG_HATCH_THRESHOLD)
    a, b = quiet.state.active, devout.state.active
    assert (a.current_id, a.is_shiny, a.nature, a.profile.ivs) == (
        b.current_id,
        b.is_shiny,
        b.nature,
        b.profile.ivs,
    )


def test_sanitize_drops_leftover_prayers_and_clamps_the_count():
    state = SaveState(egg_prayers=PrayerRecord(count=-4))
    sanitize(state)
    assert state.egg_prayers.count == 0
    state = SaveState(
        egg_prayers=PrayerRecord(count=9),
        active=MonState(base_id=1, current_id=1, path_ids=[1]),
    )
    sanitize(state)
    assert state.egg_prayers is None


def test_damaged_prayer_records_decode_as_none():
    state = SaveState.model_validate(
        {
            "egg_prayers": "oops",
            "active": {"base_id": 1, "current_id": 1, "prayers": {"count": 2, "wasFutile": True}},
        }
    )
    assert state.egg_prayers is None
    assert state.active.prayers.count == 2


@pytest.mark.parametrize(
    ("shiny", "rarity", "prayers", "expected"),
    [
        (False, Rarity.COMMON, 0, "Pidgey hatched from the egg!"),
        (True, Rarity.COMMON, 0, "A shiny Pidgey hatched! (1 in 64)"),
        (True, Rarity.LEGENDARY, 5, "5 prayers… heaven went all in. Shiny Pidgey!"),
        (False, Rarity.LEGENDARY, 5, "5 prayers… it worked?! Pidgey hatched!"),
        (True, Rarity.RARE, 1, "1 prayers AND it's shiny?! Pidgey hatched!"),
        (False, Rarity.COMMON, 2, "2 prayers later: Pidgey. Heaven must be swamped today."),
        (False, Rarity.UNCOMMON, 2, "2 prayers later: Pidgey. Let's call it half a miracle."),
        (False, Rarity.RARE, 2, "2 prayers later: Pidgey. Let's call it half a miracle."),
    ],
)
def test_hatch_lines(shiny, rarity, prayers, expected):
    assert hatch_text("Pidgey", shiny, 64, rarity, prayers) == expected


# ------------------------------------------------------------------- sulking


async def observe(svc, total, *, idle, now, day="2026-08-19", limit=False):
    await svc.refresh(
        today_by_provider={"claude_code": total},
        today_date=day,
        burn_tier="idle",
        limit_warning=limit,
        has_usage_data=True,
        time_since_last_usage=idle,
        now=now,
    )


async def test_three_idle_hours_make_it_sulk(tmp_path):
    svc = await hatched(tmp_path)
    total = 1_000 + B.EGG_HATCH_THRESHOLD
    now = later()
    await observe(svc, total, idle=3 * HOUR - 1, now=now)
    assert svc.display_state != "sulk" and svc.sulk_idle == 0
    await observe(svc, total, idle=3 * HOUR, now=now)
    assert svc.display_state == "sulk" and svc.sulk_idle == 3 * HOUR
    await observe(svc, total, idle=None, now=now)
    assert svc.display_state != "sulk", "unknown idle time never sulks"


async def test_a_limit_warning_outranks_sulking(tmp_path):
    svc = await hatched(tmp_path)
    await observe(
        svc, 1_000 + B.EGG_HATCH_THRESHOLD, idle=5 * HOUR, now=datetime.now(UTC), limit=True
    )
    assert svc.display_state == "tired"


async def test_an_egg_never_sulks(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await observe(svc, 1_000, idle=10 * HOUR, now=datetime.now(UTC))
    await observe(svc, 1_000, idle=10 * HOUR, now=datetime.now(UTC))
    assert svc.display_state == "egg"


async def test_usage_seen_today_ends_the_sulk_before_the_scan_catches_up(tmp_path):
    svc = await hatched(tmp_path)
    total = 1_000 + B.EGG_HATCH_THRESHOLD
    t0 = later()
    await observe(svc, total, idle=5 * HOUR, now=t0)
    assert svc.display_state == "sulk"
    # Today's total rose, but the scan still reports the old idle time.
    await observe(svc, total + 10, idle=5 * HOUR, now=t0 + timedelta(minutes=1))
    assert svc.display_state != "sulk"
    # That observed usage ages like any other.
    await observe(svc, total + 10, idle=8 * HOUR, now=t0 + timedelta(hours=3, minutes=1))
    assert svc.display_state == "sulk"


async def test_a_new_day_is_a_baseline_not_usage(tmp_path):
    svc = await hatched(tmp_path)
    t0 = later()
    await observe(svc, 1_000 + B.EGG_HATCH_THRESHOLD, idle=5 * HOUR, now=t0)
    # A larger total than yesterday's, but on a new day: not fresh usage.
    await observe(svc, 6_000_000, idle=5 * HOUR, now=t0 + timedelta(hours=2), day="2026-08-20")
    assert svc.display_state == "sulk"


# ---------------------------------------------------------------- event feed


async def test_lifecycle_events_carry_one_line_and_a_rising_sequence(tmp_path):
    svc = await hatched(tmp_path)
    threshold = B.phase_threshold(Rarity.COMMON, 3, 0)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD + threshold)
    events = svc.recent_events()
    assert [e.kind for e in events] == ["hatch", "evolve"]
    assert events[0].text == "Mon1 hatched from the egg!"
    assert events[1].text == "Mon1 evolved into Mon2!"
    assert events[0].seq < events[1].seq
    # Reading never consumes.
    assert svc.recent_events() == events
    assert svc.recent_events(datetime.now(UTC) + timedelta(minutes=3)) == []


async def test_graduation_announces_the_new_egg(tmp_path):
    svc = make_service(tmp_path, {50: single_stage_line()}, index=[BaseSpecies(50, 45)])
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD + B.graduation_total(Rarity.RARE))
    graduate = next(e for e in svc.recent_events() if e.kind == "graduate")
    assert graduate.text == "Solo50 — saved to your Pokédex! A new egg has arrived."


# -------------------------------------------------------------------- growth


@pytest.mark.parametrize(
    ("credits", "percent"),
    [
        (0, 0),
        (1_449_999, 28),
        (1_450_000, 29),
        (1_499_999, 29),
        (1_500_000, 30),
        (4_999_999, 99),
        (5_000_000, 100),
        (5_000_001, 100),
    ],
)
def test_egg_growth_floors_with_integer_math(tmp_path, credits, percent):
    svc = make_service(tmp_path, {})
    svc.state.egg_usage = credits
    assert svc.growth() == ("hatch", percent)


async def test_hatched_growth_tracks_the_current_stage(tmp_path):
    svc = await hatched(tmp_path)
    a = svc.state.active
    threshold = svc.stage_threshold_of(a)
    assert threshold % 100 == 0, "a threshold divisible by 100 makes 29% exact"
    exact = threshold * 29 // 100
    a.used_at_stage = exact - 1
    assert svc.growth() == ("growth", 28)
    a.used_at_stage = exact
    assert svc.growth() == ("growth", 29)
    a.used_at_stage = threshold * 30 // 100
    assert svc.growth() == ("growth", 30)
