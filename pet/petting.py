"""Stroking the pet: the wiggle detector, the heart bubble's pixel art, and where on the
sprite the bubble sits.

Pure (no gi import), so it is testable with plain pytest; sprite.py feeds it raw pixbuf
bytes.
"""

from __future__ import annotations

from collections.abc import Sequence

REQUIRED_REVERSALS = 3
WINDOW = 1.2  # seconds: reversals older than this no longer count, and a pause starts over
HEART_DURATION_MS = 1800  # every stroke restarts it


def min_stroke(pet_size: float) -> float:
    """How far the pointer must travel before a turn counts, in px."""
    return max(10.0, pet_size * 0.15)


class PettingDetector:
    """Spots a back-and-forth wiggle over the pet from pointer-move samples.

    Fed the root-window x only: the pet's own coordinates would shift if the window
    moved mid-gesture. y is ignored. Every reversal from the third within the window
    fires, so a continuing wiggle keeps the hearts up.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last_t: float | None = None
        self._extreme_x = 0.0
        self._direction = 0
        self._reversals: list[float] = []

    def feed(self, x: float, t: float, stroke: float) -> bool:
        if self._last_t is None or t - self._last_t > WINDOW:
            self.reset()
            self._last_t = t
            self._extreme_x = x
            return False
        self._last_t = t
        self._reversals = [r for r in self._reversals if t - r <= WINDOW]
        if self._direction == 0:
            if abs(x - self._extreme_x) < stroke:
                return False
            self._direction = 1 if x > self._extreme_x else -1
            self._extreme_x = x
            return False
        if (x - self._extreme_x) * self._direction > 0:  # still going the same way
            self._extreme_x = x
            return False
        if (self._extreme_x - x) * self._direction < stroke:  # not far enough back yet
            return False
        self._direction = -self._direction
        self._extreme_x = x
        self._reversals.append(t)
        return len(self._reversals) >= REQUIRED_REVERSALS


# One character per pixel, drawn at HEART_PIXEL px a cell (an integer, so edges stay
# crisp). The same colours in light and dark themes.
HEART_ART = (
    "..OOOOOOOOOOOOO..",
    ".OWWWWWWWWWWWWWO.",
    "OWWWWWWWWWWWWWWWO",
    "OWWWWRRRWRRRWWWWO",
    "OWWWRHHRRRRRRWWWO",
    "OWWWRHRRRRRRDWWWO",
    "OWWWRRRRRRRRDWWWO",
    "OWWWWRRRRRRDWWWWO",
    "OWWWWWRRRRDWWWWWO",
    "OWWWWWWRRDWWWWWWO",
    "OWWWWWWWDWWWWWWWO",
    "OWWWWWWWWWWWWWWWO",
    ".OWWWWWWWWWWWWWO.",
    "..OOOOOWWWOOOOO..",
    ".......OWO.......",
    "........O........",
)
HEART_COLOURS: dict[str, tuple[int, int, int]] = {
    "O": (0x20, 0x20, 0x20),  # outline
    "W": (0xFF, 0xFF, 0xFF),  # fill
    "R": (0xE0, 0x30, 0x3C),  # heart
    "D": (0xA8, 0x20, 0x2C),  # shade
    "H": (0xFF, 0x9A, 0xA2),  # highlight
}
HEART_PIXEL = 2
HEART_WIDTH = len(HEART_ART[0]) * HEART_PIXEL
HEART_HEIGHT = len(HEART_ART) * HEART_PIXEL
TAIL_OVERLAP = 3  # px of the tail tip that lands on the sprite's visible top
ALPHA_THRESHOLD = 16


def opaque_top_fraction(
    pixels: bytes, width: int, height: int, rowstride: int, channels: int = 4
) -> float:
    """First row from the top with a visible pixel, as a fraction of the height.

    Only the middle of the image decides (35-65% of the width), so raised arms or a tail
    at the sides do not lift the bubble off the head; the full width is the fallback for
    art with an empty middle. 0 when nothing is visible.
    """
    if width <= 0 or height <= 0 or channels < 4:
        return 0.0
    start = int(width * 0.35)
    end = max(start + 1, int(width * 0.65))
    for first, last in ((start, end), (0, width)):
        for row in range(height):
            base = row * rowstride
            alphas = pixels[base + first * channels + 3 : base + last * channels : channels]
            if alphas and max(alphas) > ALPHA_THRESHOLD:
                return row / height
    return 0.0


def anchor_fraction(fractions: Sequence[float]) -> float:
    """The median frame's top, so frames that jump to the canvas top do not lift it."""
    if not fractions:
        return 0.0
    return sorted(fractions)[len(fractions) // 2]


def bubble_top_offset(
    visible_top: float, bubble_height: int = HEART_HEIGHT, overlap: int = TAIL_OVERLAP
) -> int:
    """The bubble's top relative to the pet's box, at whole px."""
    return round(visible_top + overlap - bubble_height)
