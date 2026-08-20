"""Sprite decoding, scaling and hit-region extraction.

Two things here are less obvious than they look:

* Animated Gen-V GIFs do not share a canvas size — Pikachu's frames are 50x46,
  Ferrothorn's are 48x40 — while the static PNG fallbacks are a uniform 96x96. Fitting
  each sprite to the window box would make species visibly jump in size, so everything
  is scaled by one factor against a nominal 96px box and anchored to a common ground
  line instead.
* Pixel art must be scaled with NEAREST. Bilinear turns it to mush at 192px.
"""

from __future__ import annotations

import logging
import warnings

import cairo
from . import gtk3  # noqa: F401  (pins GI versions)
from gi.repository import GdkPixbuf

log = logging.getLogger(__name__)

# The PixbufAnimation family is deprecated in gdk-pixbuf but is still the only way to
# get frame-by-frame GIF decoding out of GTK3, and it is not going away first.
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"pet\.sprite")

NOMINAL = 96  # the static-PNG canvas; every sprite is scaled relative to this
GROUND_MARGIN = 2  # px at 96, keeps the feet off the very edge of the window


LEARN_CAP = 240  # frames; a safety net if a GIF's loop is never detected


class Sprite:
    """A decoded sprite, ready to be drawn at any size.

    Frames are decoded once and then replayed from a list. gdk-pixbuf's animation
    iterator re-composites the GIF on every advance -- about 2.7ms a frame, which at
    10fps was the pet's entire CPU cost -- so the first time through the animation each
    frame is copied out, and the loop is detected by the frame that matches frame zero.
    After that, animating is a list index and a cached blit.
    """

    def __init__(self, data: bytes) -> None:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        animation = loader.get_animation()
        if animation is None:
            raise ValueError("undecodable sprite")

        self.is_animated = not animation.is_static_image()
        self._static = None if self.is_animated else animation.get_static_image()
        self._iter = animation.get_iter(None) if self.is_animated else None
        self._frames: list[tuple[GdkPixbuf.Pixbuf, int]] = []
        self._first_pixels: bytes | None = None
        self._last_pixels: bytes | None = None
        self._index = 0
        self._learning = self.is_animated
        self._scaled: dict[tuple[int, int], GdkPixbuf.Pixbuf] = {}

        if self._iter is not None:
            first = self._iter.get_pixbuf()
            self._first_pixels = self._last_pixels = first.get_pixels()
            self._frames.append((first.copy(), max(self._iter.get_delay_time(), 20)))

    @property
    def learning(self) -> bool:
        """True until the animation's loop has been captured."""
        return self._learning

    # -------------------------------------------------------------------- frames

    def frame(self) -> GdkPixbuf.Pixbuf:
        if self._static is not None:
            return self._static
        return self._frames[self._index][0]

    def delay_ms(self) -> int:
        """Milliseconds until the next frame, or -1 for a static image."""
        if not self.is_animated:
            return -1
        return self._frames[self._index][1]

    def advance(self) -> bool:
        """Step to the next frame. True if the image changed."""
        if not self.is_animated:
            return False
        if not self._learning:
            self._index = (self._index + 1) % len(self._frames)
            return True

        assert self._iter is not None
        if not self._iter.advance(None):
            return False
        pixbuf = self._iter.get_pixbuf()
        pixels = pixbuf.get_pixels()
        if pixels == self._last_pixels:
            # advance() reports a change on its first call, and again whenever it is
            # asked before the current frame's delay has elapsed, without the image
            # actually differing. Treating those as real frames would either end the
            # loop on frame one or fill it with duplicates.
            return False
        if len(self._frames) >= 2 and pixels == self._first_pixels:
            self._stop_learning()  # back at frame zero: the loop is complete
            return True
        self._last_pixels = pixels
        self._frames.append((pixbuf.copy(), max(self._iter.get_delay_time(), 20)))
        self._index = len(self._frames) - 1
        if len(self._frames) >= LEARN_CAP:
            log.debug("no loop found after %d frames; replaying what we have", LEARN_CAP)
            self._stop_learning()
        return True

    def _stop_learning(self) -> None:
        self._learning = False
        self._iter = None  # release the decoder
        self._index = 0

    # ------------------------------------------------------------------- drawing

    def scaled(self, size: int) -> GdkPixbuf.Pixbuf:
        """The current frame at `size`, preserving each species' relative bulk."""
        key = (self._index, size)
        cached = self._scaled.get(key)
        if cached is not None:
            return cached

        source = self.frame()
        factor = size / NOMINAL
        width = max(1, round(source.get_width() * factor))
        height = max(1, round(source.get_height() * factor))
        scaled = source.scale_simple(width, height, GdkPixbuf.InterpType.NEAREST)
        if len(self._scaled) > 2 * LEARN_CAP:
            self._scaled.clear()  # a size change made the old entries dead weight
        self._scaled[key] = scaled
        return scaled

    @staticmethod
    def placement(pixbuf: GdkPixbuf.Pixbuf, size: int) -> tuple[int, int]:
        """Bottom-centre anchor inside a size x size box, so sprites share a ground line."""
        margin = round(GROUND_MARGIN * size / NOMINAL)
        x = round((size - pixbuf.get_width()) / 2)
        y = size - pixbuf.get_height() - margin
        return x, max(0, y)


def alpha_region(pixbuf: GdkPixbuf.Pixbuf, offset_x: int, offset_y: int, threshold: int = 24) -> cairo.Region:
    """Region covering the sprite's opaque pixels, for the window's input shape.

    Shaping input to the artwork rather than the window box is what lets clicks in the
    empty space around the Pokemon fall through to whatever is behind it.
    """
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if not pixbuf.get_has_alpha():
        return cairo.Region(cairo.RectangleInt(offset_x, offset_y, width, height))

    pixels = pixbuf.get_pixels()
    rowstride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()
    region = cairo.Region()
    for y in range(height):
        row = y * rowstride
        x = 0
        while x < width:
            if pixels[row + x * channels + 3] > threshold:
                start = x
                while x < width and pixels[row + x * channels + 3] > threshold:
                    x += 1
                region.union(cairo.RectangleInt(offset_x + start, offset_y + y, x - start, 1))
            else:
                x += 1
    return region
