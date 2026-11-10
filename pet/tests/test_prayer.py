"""Egg prayers: upstream's EggPrayerTests tables for the pet's side."""

from __future__ import annotations

from pet.prayer import (
    PARTICLE_LIFETIME,
    PRAYER_REFRESH_INTERVAL,
    PRAYERS_PER_REFRESH,
    QUIPS,
    WOBBLE_DURATION,
    PetClickRouter,
    PrayerBurst,
    click_action,
    emoji_edge,
    particle_frame,
    prayer_hover_line,
    prayer_quip_index,
    refresh_milestone,
    should_refresh_on_prayer,
    wobble_degrees,
)

NOW = 1000.0


def test_quip_index_advances_every_thirty_prayers_and_stops_at_the_last():
    assert prayer_quip_index(0) is None, "nothing to say before the first prayer"
    assert prayer_quip_index(-1) is None
    expected = [(1, 0), (29, 0), (30, 1), (59, 1), (60, 2), (299, 9), (300, 10), (10_000, 10)]
    for prayers, index in expected:
        assert prayer_quip_index(prayers) == index, prayers
    assert len(QUIPS) == 11
    assert len(set(QUIPS)) == 11 and all(QUIPS), "eleven different quips"


def test_hover_line_copy():
    assert prayer_hover_line(0) == ""
    assert prayer_hover_line(1) == "1 prayer · just trying it out"
    assert prayer_hover_line(31) == "31 prayers · heaven hasn't checked yet"
    assert prayer_hover_line(120) == "120 prayers · heaven's servers are lagging"
    assert prayer_hover_line(180) == "180 prayers · your palms are wearing thin"
    assert prayer_hover_line(300) == "300 prayers · if a legendary hatches, that's pure skill"


def test_click_prays_over_an_egg_and_opens_the_web_ui_for_a_pokemon():
    assert click_action(True) == "pray"
    assert click_action(False) == "open"


def test_milestones_are_every_thirty_prayers():
    milestones = {0: 0, 1: 0, 29: 0, 30: 1, 59: 1, 60: 2, 89: 2, 90: 3, 120: 4, -5: 0}
    for count, milestone in milestones.items():
        assert refresh_milestone(count) == milestone, count
    assert PRAYERS_PER_REFRESH == 30
    huge = 2**63 - 1
    assert refresh_milestone(huge) == huge // 30


def test_should_refresh_on_prayer_decision():
    later = NOW + PRAYER_REFRESH_INTERVAL
    assert not should_refresh_on_prayer(29, 0, None, NOW)
    assert should_refresh_on_prayer(30, 0, None, NOW)
    assert not should_refresh_on_prayer(30, 1, None, NOW), "already refreshed at this milestone"
    assert not should_refresh_on_prayer(30, 0, NOW, NOW), "inside the floor"
    assert should_refresh_on_prayer(30, 0, NOW, later)
    assert should_refresh_on_prayer(60, 1, NOW, later)


class Calls:
    """What the router did, and the backend's count for the current egg."""

    def __init__(self) -> None:
        self.prays = self.spawns = self.refreshes = self.opens = self.did_pray = 0
        self.egg_prayers = 0
        self.now = NOW


def make_router(calls: Calls) -> PetClickRouter:
    router: PetClickRouter

    def pray() -> None:
        # The backend answers with the new count; in the pet that arrives later.
        calls.prays += 1
        calls.egg_prayers += 1
        router.prayed(calls.egg_prayers)

    def spawn() -> None:
        calls.spawns += 1

    def did_pray(_count: int) -> None:
        calls.did_pray += 1

    def refresh() -> None:
        calls.refreshes += 1

    def open_ui() -> None:
        calls.opens += 1

    router = PetClickRouter(
        pray=pray,
        spawn=spawn,
        did_pray=did_pray,
        refresh=refresh,
        open_ui=open_ui,
        clock=lambda: calls.now,
    )
    return router


def pray(router: PetClickRouter, times: int) -> None:
    for _ in range(times):
        router.handle(True)


def test_prayer_refresh_follows_the_milestones():
    calls = Calls()
    router = make_router(calls)
    pray(router, 29)
    assert calls.refreshes == 0, "29 prayers: no refresh"
    pray(router, 1)
    assert calls.refreshes == 1, "the 30th prayer refreshes"
    calls.now = NOW + PRAYER_REFRESH_INTERVAL
    pray(router, 29)
    assert calls.refreshes == 1
    pray(router, 1)
    assert calls.refreshes == 2, "the 60th prayer refreshes"
    calls.now = NOW + PRAYER_REFRESH_INTERVAL * 2
    pray(router, 30)
    assert calls.refreshes == 3, "the 90th prayer refreshes"
    assert calls.spawns == calls.prays, "every click still spawns its emoji"
    assert calls.did_pray == calls.prays, "every click rebuilds the hover text"


def test_a_milestone_inside_the_floor_is_refreshed_by_the_first_click_after_it():
    calls = Calls()
    router = make_router(calls)
    pray(router, 30)
    assert calls.refreshes == 1
    calls.now = NOW + 5
    pray(router, 30)  # reaches 60 only 5 s after the last refresh
    assert calls.refreshes == 1, "the floor holds the 60th-prayer refresh back"
    calls.now = NOW + PRAYER_REFRESH_INTERVAL
    pray(router, 1)
    assert calls.refreshes == 2, "the first click after the floor does not lose the milestone"
    pray(router, 1)
    assert calls.refreshes == 2, "and it is not refreshed twice"


def test_a_new_egg_starts_the_milestones_over():
    calls = Calls()
    router = make_router(calls)
    pray(router, 60)  # refreshes at 30; the 60th is held back by the floor
    assert calls.refreshes == 1
    calls.now = NOW + PRAYER_REFRESH_INTERVAL * 2
    calls.egg_prayers = 0  # a new egg: the backend's count starts over
    pray(router, 29)
    assert calls.refreshes == 1
    pray(router, 1)
    assert calls.refreshes == 2, "the new egg's 30th prayer refreshes again"


def test_clicking_a_hatched_pokemon_opens_without_praying_or_refreshing():
    calls = Calls()
    router = make_router(calls)
    pray(router, 29)
    for _ in range(50):
        router.handle(False)
    assert calls.opens == 50
    assert calls.prays == 29
    assert calls.did_pray == 29, "hatched clicks do not touch the prayer hover text"
    assert calls.refreshes == 0, "hatched clicks never count"
    pray(router, 1)
    assert calls.refreshes == 1, "the 30th real prayer still refreshes"


def test_burst_spawns_clamped_particles_and_drops_them_after_their_lifetime():
    burst = PrayerBurst()
    burst.spawn(NOW, -3)
    burst.spawn(NOW, 7)
    burst.spawn(NOW, 0.4)
    assert len(burst.particles) == 3, "rapid clicks stack up"
    assert burst.wobble_count == 3
    assert burst.particles[0].x_fraction == 0
    assert burst.particles[1].x_fraction == 1
    assert all(0 <= p.x_fraction <= 1 for p in burst.particles)
    assert burst.active(NOW + 1.0)
    burst.prune(NOW + PARTICLE_LIFETIME + 0.3)
    assert burst.particles == [], "each particle goes by itself"
    assert not burst.active(NOW + PARTICLE_LIFETIME + 0.3)
    assert burst.wobble_count == 3


def test_emoji_stays_inside_the_pet_frame_at_every_size():
    for size in range(48, 385, 8):
        edge = emoji_edge(size)
        assert 14 <= edge <= 32
        assert edge <= size
        for fraction in (0.0, 1.0):
            for age in (0.0, 0.5, 1.0):
                frame = particle_frame(size, fraction, age)
                assert frame.cx - edge / 2 >= 0
                assert frame.cx + edge / 2 <= size
                assert frame.cy - edge / 2 >= -1e-9
                assert frame.cy + edge / 2 <= size


def test_particles_rise_and_fade():
    start = particle_frame(96, 0.5, 0.0)
    end = particle_frame(96, 0.5, 1.0)
    assert start.cy == 96 * 0.7
    assert start.opacity == 1
    assert end.cy == emoji_edge(96) / 2
    assert end.opacity == 0
    assert particle_frame(96, 0.5, 0.5).cy < start.cy


def test_the_wobble_follows_its_keyframes_once():
    assert WOBBLE_DURATION == 0.30
    assert wobble_degrees(0) == 0
    assert abs(wobble_degrees(0.06) - -9) < 1e-9
    assert abs(wobble_degrees(0.16) - 9) < 1e-9
    assert abs(wobble_degrees(0.24) - -5) < 1e-9
    assert abs(wobble_degrees(0.03) - -4.5) < 1e-9, "linear between keyframes"
    assert wobble_degrees(0.30) == 0
    assert wobble_degrees(5) == 0, "never loops"
    burst = PrayerBurst()
    assert burst.wobble(NOW) == 0
    burst.spawn(NOW, 0.5)
    assert abs(burst.wobble(NOW + 0.06) - -9) < 1e-9
