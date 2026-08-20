"""The callout above the pet: hover stats, and alert speech bubbles.

Both use the same widget — an undecorated popup with a Cairo-drawn rounded rect and a
tail — because they differ only in content and in how long they stay up.
"""

from __future__ import annotations

import math

import cairo

from . import gtk3  # noqa: F401  (pins GI versions; must precede gi.repository)
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

PADDING_X = 11
PADDING_Y = 8
RADIUS = 9.0
TAIL = 7
GAP = 6  # space between the pet and the bubble
MAX_WIDTH = 300

BG = (0.08, 0.09, 0.12, 0.95)
BORDER = (1.0, 1.0, 1.0, 0.16)


class Bubble:
    """A transient rounded callout, anchored horizontally on a point."""

    def __init__(self) -> None:
        self.window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.window.set_app_paintable(True)
        self.window.set_decorated(False)
        self.window.set_keep_above(True)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual() if screen is not None else None
        if visual is not None:
            self.window.set_visual(visual)
        self.window.stick()

        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._on_draw)
        self.window.add(self.area)

        self._markup = ""
        self._tail_down = True
        self._tail_x = 0.0
        self._size = (0, 0)
        self._hide_source: int | None = None

    # ---------------------------------------------------------------- visibility

    def show(
        self,
        markup: str,
        *,
        anchor_x: int,
        above_y: int,
        below_y: int,
        timeout_ms: int | None = None,
    ) -> None:
        """Place the bubble above `above_y`, flipping below if it would leave the screen."""
        self._cancel_timeout()
        self._markup = markup
        width, height = self._measure(markup)

        monitor = self._monitor_at(anchor_x, above_y)
        x = anchor_x - width // 2
        if monitor is not None:
            x = max(monitor.x + 2, min(x, monitor.x + monitor.width - width - 2))

        top = above_y - GAP - height
        self._tail_down = monitor is None or top >= monitor.y + 2
        if not self._tail_down:
            top = below_y + GAP

        self._tail_x = max(RADIUS + TAIL, min(float(anchor_x - x), width - RADIUS - TAIL))
        self._size = (width, height)

        self.window.resize(width, height)
        self.window.move(x, top)
        self.window.show_all()
        self.area.queue_draw()

        if timeout_ms is not None:
            self._hide_source = GLib.timeout_add(timeout_ms, self._on_timeout)

    def hide(self) -> None:
        self._cancel_timeout()
        self.window.hide()

    @property
    def visible(self) -> bool:
        return self.window.get_visible()

    def destroy(self) -> None:
        self._cancel_timeout()
        self.window.destroy()

    def _on_timeout(self) -> bool:
        self._hide_source = None
        self.window.hide()
        return False

    def _cancel_timeout(self) -> None:
        if self._hide_source is not None:
            GLib.source_remove(self._hide_source)
            self._hide_source = None

    # ------------------------------------------------------------------ internals

    def _layout(self, markup: str) -> Pango.Layout:
        layout = self.area.create_pango_layout("")
        layout.set_font_description(Pango.FontDescription("Sans 9"))
        layout.set_markup(markup, -1)
        layout.set_width(MAX_WIDTH * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        return layout

    def _measure(self, markup: str) -> tuple[int, int]:
        text_w, text_h = self._layout(markup).get_pixel_size()
        return text_w + 2 * PADDING_X, text_h + 2 * PADDING_Y + TAIL

    @staticmethod
    def _monitor_at(x: int, y: int) -> Gdk.Rectangle | None:
        display = Gdk.Display.get_default()
        if display is None:
            return None
        monitor = display.get_monitor_at_point(x, y)
        return monitor.get_geometry() if monitor is not None else None

    def _on_draw(self, _area: Gtk.DrawingArea, cr) -> bool:
        width, height = self._size
        if width <= 0:
            return False
        cr.set_operator(cairo.Operator.SOURCE)  # start from fully transparent
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.Operator.OVER)

        body_top = 0.0 if self._tail_down else float(TAIL)
        body_height = height - TAIL

        self._trace_body(cr, width, body_top, body_height)
        cr.set_source_rgba(*BG)
        cr.fill_preserve()
        cr.set_source_rgba(*BORDER)
        cr.set_line_width(1.0)
        cr.stroke()

        layout = self._layout(self._markup)
        cr.set_source_rgba(0.91, 0.93, 0.97, 1.0)
        cr.move_to(PADDING_X, body_top + PADDING_Y)
        PangoCairo.show_layout(cr, layout)
        return False

    def _trace_body(self, cr, width: int, top: float, height: float) -> None:
        """Rounded rect with a tail, as one path so fill and stroke agree."""
        right, bottom = float(width), top + height
        tail = float(self._tail_x)
        cr.new_path()
        cr.arc(RADIUS, top + RADIUS, RADIUS, math.pi, 1.5 * math.pi)
        cr.arc(right - RADIUS, top + RADIUS, RADIUS, 1.5 * math.pi, 0)
        if self._tail_down:
            cr.arc(right - RADIUS, bottom - RADIUS, RADIUS, 0, 0.5 * math.pi)
            cr.line_to(tail + TAIL, bottom)
            cr.line_to(tail, bottom + TAIL)
            cr.line_to(tail - TAIL, bottom)
            cr.arc(RADIUS, bottom - RADIUS, RADIUS, 0.5 * math.pi, math.pi)
        else:
            cr.arc(right - RADIUS, bottom - RADIUS, RADIUS, 0, 0.5 * math.pi)
            cr.arc(RADIUS, bottom - RADIUS, RADIUS, 0.5 * math.pi, math.pi)
            cr.line_to(0, top + RADIUS)
            cr.line_to(tail - TAIL, top)
            cr.line_to(tail, top - TAIL)
            cr.line_to(tail + TAIL, top)
        cr.close_path()
