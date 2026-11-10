"""Praying over the egg: click routing, the usage-refresh milestones, quips and the
floating 🙏 with its one-shot wobble.

Prayers are a placebo. The backend counts them and never reads them in any roll; all
the pet does is make clicking the egg feel like something. Pure (no gi import), with
time passed in, so it is testable with plain pytest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

PRAYERS_PER_QUIP = 30
PRAYERS_PER_REFRESH = 30
PRAYER_REFRESH_INTERVAL = 30.0  # seconds: a refresh also hits the provider limit endpoints

QUIPS = (
    "just trying it out",
    "heaven hasn't checked yet",
    "the egg feels a little pressured",
    "your praying form is getting pro",
    "heaven's servers are lagging",
    "you're #3,812 in heaven's queue",
    "your palms are wearing thin",
    "at this point the egg is praying with you",
    'heaven: "received, we\'ll get back to you"',
    'the egg just whispered "please stop"',
    "if a legendary hatches, that's pure skill",
)

PARTICLE_FLIGHT = 1.0  # seconds from the egg to the top of the pet
PARTICLE_LIFETIME = 1.1  # a little longer than the flight, then it is dropped
PARTICLE_GLYPH = "🙏"
# (duration s, angle reached in degrees), linear from 0 degrees. Never loops.
WOBBLE_KEYFRAMES = ((0.06, -9.0), (0.10, 9.0), (0.08, -5.0), (0.06, 0.0))
WOBBLE_DURATION = sum(duration for duration, _ in WOBBLE_KEYFRAMES)


def prayer_quip_index(prayers: int) -> int | None:
    """The joke changes every 30 prayers and stays on the last one from 300 on."""
    if prayers <= 0:
        return None
    return min(prayers // PRAYERS_PER_QUIP, len(QUIPS) - 1)


def prayer_hover_line(prayers: int) -> str:
    index = prayer_quip_index(prayers)
    if index is None:
        return ""
    count = "1 prayer" if prayers == 1 else f"{prayers} prayers"
    return f"{count} · {QUIPS[index]}"


def refresh_milestone(count: int) -> int:
    return max(0, count) // PRAYERS_PER_REFRESH


def should_refresh_on_prayer(
    count: int, last_refreshed_milestone: int, last_refresh_at: float | None, now: float
) -> bool:
    """Refresh at the 30th, 60th, 90th... prayer, at most once per interval."""
    if refresh_milestone(count) <= last_refreshed_milestone:
        return False
    return last_refresh_at is None or now - last_refresh_at >= PRAYER_REFRESH_INTERVAL


def click_action(is_egg: bool) -> str:
    return "pray" if is_egg else "open"


class PetClickRouter:
    """Routes a plain click: pray over an egg, open the web UI over a Pokemon.

    The particle spawns at once; the count comes back from the backend later and is
    handed to `prayed`, which rebuilds the hover text and, at a milestone, refreshes
    usage. A milestone held back by the interval is refreshed by the first prayer after
    it, neither lost nor doubled. The bookkeeping is in memory only: after a restart one
    extra refresh can happen, which is harmless.
    """

    def __init__(
        self,
        *,
        pray: Callable[[], None],
        spawn: Callable[[], None],
        did_pray: Callable[[int], None],
        refresh: Callable[[], None],
        open_ui: Callable[[], None],
        clock: Callable[[], float],
    ) -> None:
        self._pray = pray
        self._spawn = spawn
        self._did_pray = did_pray
        self._refresh = refresh
        self._open_ui = open_ui
        self._clock = clock
        self.last_refreshed_milestone = 0
        self.last_refresh_at: float | None = None

    def handle(self, is_egg: bool) -> None:
        if click_action(is_egg) == "pray":
            self._spawn()
            self._pray()
        else:
            self._open_ui()

    def prayed(self, count: int) -> None:
        self._did_pray(count)
        milestone = refresh_milestone(count)
        # Below the last milestone means a new egg started over.
        self.last_refreshed_milestone = min(self.last_refreshed_milestone, milestone)
        now = self._clock()
        last_milestone, last_at = self.last_refreshed_milestone, self.last_refresh_at
        if should_refresh_on_prayer(count, last_milestone, last_at, now):
            self.last_refreshed_milestone = milestone
            self.last_refresh_at = now
            self._refresh()


# ------------------------------------------------------------------------ the burst


@dataclass(frozen=True)
class Particle:
    x_fraction: float
    born: float


@dataclass(frozen=True)
class ParticleFrame:
    cx: float
    cy: float
    edge: float
    opacity: float


def emoji_edge(pet_size: float) -> float:
    return min(32.0, max(14.0, pet_size * 0.25))


def ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) * (1 - t)


def particle_frame(pet_size: float, x_fraction: float, age: float) -> ParticleFrame:
    """Where a particle is `age` seconds after its prayer, in pet-box coordinates.

    It rises from 70% of the box down to touching the top, always fully inside the box
    horizontally, and fades out as it goes.
    """
    progress = ease_out(age / PARTICLE_FLIGHT)
    edge = emoji_edge(pet_size)
    cx = edge / 2 + x_fraction * (pet_size - edge)
    start = pet_size * 0.7
    cy = start + (edge / 2 - start) * progress
    return ParticleFrame(cx, cy, edge, 1.0 - progress)


def wobble_degrees(elapsed: float) -> float:
    """The egg's one-shot shake, `elapsed` seconds after a prayer. 0 outside it."""
    if elapsed <= 0 or elapsed >= WOBBLE_DURATION:
        return 0.0
    angle = 0.0
    for duration, target in WOBBLE_KEYFRAMES:
        if elapsed < duration:
            return angle + (target - angle) * elapsed / duration
        elapsed -= duration
        angle = target
    return 0.0


class PrayerBurst:
    """The particles in flight and the latest wobble. Rapid clicks stack particles."""

    def __init__(self) -> None:
        self.particles: list[Particle] = []
        self.wobble_count = 0
        self._wobble_started: float | None = None

    def spawn(self, now: float, x_fraction: float) -> None:
        self.particles.append(Particle(min(1.0, max(0.0, x_fraction)), now))
        self.wobble_count += 1
        self._wobble_started = now

    def prune(self, now: float) -> None:
        self.particles = [p for p in self.particles if now - p.born < PARTICLE_LIFETIME]

    def wobble(self, now: float) -> float:
        return 0.0 if self._wobble_started is None else wobble_degrees(now - self._wobble_started)

    def active(self, now: float) -> bool:
        wobbling = self._wobble_started is not None and now - self._wobble_started < WOBBLE_DURATION
        return wobbling or any(now - p.born < PARTICLE_LIFETIME for p in self.particles)
