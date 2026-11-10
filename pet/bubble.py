"""The popups above the pet: the hover/event callout, the speech bubble, the hearts.

The callout and the speech bubble use the same widget — an undecorated popup with a
Cairo-drawn rounded rect and a tail — because they differ only in content and layout.
The speech bubble (limit alerts, agent questions and results) has a fixed text column
with a one-line bold title and at most two body lines, so untrusted agent text can
never grow it.
"""

from __future__ import annotations

import math

import cairo

# isort: off
from . import gtk3  # noqa: F401  (pins GI versions; must precede gi.repository)
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

# isort: on
from .petting import HEART_ART, HEART_COLOURS, HEART_HEIGHT, HEART_PIXEL, HEART_WIDTH

PADDING_X = 11
PADDING_Y = 8
RADIUS = 9.0
TAIL = 7
GAP = 6  # space between the pet and the bubble
MAX_WIDTH = 300
FONT = "Sans 9"

SPEECH_WIDTH = 164  # the fixed text column
SPEECH_TITLE_FONT = "Sans Bold 9"
SPEECH_BODY_FONT = "Sans 8.5"
SPEECH_BODY_LINES = 2
SPEECH_SPACING = 2  # title to body

BG = (0.08, 0.09, 0.12, 0.95)
BORDER = (1.0, 1.0, 1.0, 0.16)
TEXT = (0.91, 0.93, 0.97, 1.0)
SECONDARY = (0.70, 0.74, 0.80, 1.0)


def _popup() -> Gtk.Window:
    window = Gtk.Window(type=Gtk.WindowType.POPUP)
    window.set_app_paintable(True)
    window.set_decorated(False)
    window.set_keep_above(True)
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    screen = Gdk.Screen.get_default()
    visual = screen.get_rgba_visual() if screen is not None else None
    if visual is not None:
        window.set_visual(visual)
    window.stick()
    return window


def _monitor_at(x: int, y: int) -> Gdk.Rectangle | None:
    display = Gdk.Display.get_default()
    if display is None:
        return None
    monitor = display.get_monitor_at_point(x, y)
    return monitor.get_geometry() if monitor is not None else None


class Bubble:
    """A rounded callout, anchored horizontally on a point."""

    def __init__(self) -> None:
        self.window = _popup()
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._on_draw)
        self.window.add(self.area)

        # (layout, x, y, colour), laid out once per show and replayed by every draw.
        self._content: list[tuple[Pango.Layout, float, float, tuple[float, ...]]] = []
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
        """Free-form markup, wrapped at MAX_WIDTH."""
        layout = self._layout(markup, FONT, MAX_WIDTH)
        text_w, text_h = layout.get_pixel_size()
        self._content = [(layout, PADDING_X, PADDING_Y, TEXT)]
        self._present(text_w + 2 * PADDING_X, text_h + 2 * PADDING_Y, anchor_x, above_y, below_y)
        if timeout_ms is not None:
            self._hide_source = GLib.timeout_add(timeout_ms, self._on_timeout)

    def show_speech(
        self, title_markup: str, body_markup: str, *, anchor_x: int, above_y: int, below_y: int
    ) -> None:
        """A one-line title over at most two body lines, in the fixed column.

        Pango ellipsizes the body itself; the title must already fit (see title_fits),
        so that the agent's status and count are never the part that gets cut.
        """
        title = self._layout(title_markup, SPEECH_TITLE_FONT, SPEECH_WIDTH)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_height(-1)
        body = self._layout(body_markup, SPEECH_BODY_FONT, SPEECH_WIDTH)
        body.set_ellipsize(Pango.EllipsizeMode.END)
        body.set_height(-SPEECH_BODY_LINES)
        title_h = title.get_pixel_size()[1]
        body_h = body.get_pixel_size()[1]
        self._content = [
            (title, PADDING_X, PADDING_Y, TEXT),
            (body, PADDING_X, PADDING_Y + title_h + SPEECH_SPACING, SECONDARY),
        ]
        height = title_h + SPEECH_SPACING + body_h + 2 * PADDING_Y
        self._present(SPEECH_WIDTH + 2 * PADDING_X, height, anchor_x, above_y, below_y)

    def title_fits(self, title_markup: str) -> bool:
        """Whether a speech title fits the column on one line."""
        layout = self._layout(title_markup, SPEECH_TITLE_FONT, None)
        return layout.get_pixel_size()[0] <= SPEECH_WIDTH

    def reposition(self, *, anchor_x: int, above_y: int, below_y: int) -> None:
        """Follow the pet without laying the content out again."""
        if self.visible and self._size[0] > 0:
            width, height = self._size
            self._place(width, height, anchor_x, above_y, below_y)

    def hide(self) -> None:
        self._cancel_timeout()
        self.window.hide()

    @property
    def visible(self) -> bool:
        return self.window.get_visible()

    @property
    def size(self) -> tuple[int, int]:
        """Width and height of the last bubble shown, tail included."""
        return self._size

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

    def _layout(self, markup: str, font: str, width: int | None) -> Pango.Layout:
        layout = self.area.create_pango_layout("")
        layout.set_font_description(Pango.FontDescription(font))
        layout.set_markup(markup, -1)
        if width is not None:
            layout.set_width(width * Pango.SCALE)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        return layout

    def _present(
        self, width: int, body_height: int, anchor_x: int, above_y: int, below_y: int
    ) -> None:
        self._cancel_timeout()
        height = body_height + TAIL
        self._size = (width, height)
        self._place(width, height, anchor_x, above_y, below_y)
        self.window.show_all()
        self.area.queue_draw()

    def _place(self, width: int, height: int, anchor_x: int, above_y: int, below_y: int) -> None:
        """Above `above_y`, flipping below `below_y` if it would leave the screen."""
        monitor = _monitor_at(anchor_x, above_y)
        x = anchor_x - width // 2
        if monitor is not None:
            x = max(monitor.x + 2, min(x, monitor.x + monitor.width - width - 2))

        top = above_y - GAP - height
        tail_down = monitor is None or top >= monitor.y + 2
        if not tail_down:
            top = below_y + GAP
        tail_x = max(RADIUS + TAIL, min(float(anchor_x - x), width - RADIUS - TAIL))
        if (tail_down, tail_x) != (self._tail_down, self._tail_x):
            self._tail_down, self._tail_x = tail_down, tail_x
            self.area.queue_draw()

        self.window.resize(width, height)
        self.window.move(x, top)

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

        for layout, x, y, colour in self._content:
            cr.set_source_rgba(*colour)
            cr.move_to(x, body_top + y)
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


class HeartBubble:
    """The pixel-art heart that pops over a stroked pet.

    Its own popup because the pet's window has no headroom above the sprite. It takes
    no input at all, so a wiggle that strays onto it still reaches the pet underneath.
    """

    def __init__(self) -> None:
        self.window = _popup()
        self.window.set_size_request(HEART_WIDTH, HEART_HEIGHT)
        self.window.resize(HEART_WIDTH, HEART_HEIGHT)
        self.window.connect("realize", self._on_realize)
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._on_draw)
        self.window.add(self.area)
        self._art = self._render()

    def show(self, x: int, y: int) -> None:
        self.window.move(x, y)
        self.window.show_all()

    def hide(self) -> None:
        self.window.hide()

    @property
    def visible(self) -> bool:
        return self.window.get_visible()

    def destroy(self) -> None:
        self.window.destroy()

    def _on_realize(self, window: Gtk.Window) -> None:
        gdk_window = window.get_window()
        if gdk_window is not None:
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)

    @staticmethod
    def _render() -> cairo.ImageSurface:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, HEART_WIDTH, HEART_HEIGHT)
        cr = cairo.Context(surface)
        for row, cells in enumerate(HEART_ART):
            for column, cell in enumerate(cells):
                colour = HEART_COLOURS.get(cell)
                if colour is None:
                    continue
                cr.set_source_rgb(*(channel / 255 for channel in colour))
                cr.rectangle(column * HEART_PIXEL, row * HEART_PIXEL, HEART_PIXEL, HEART_PIXEL)
                cr.fill()
        surface.flush()
        return surface

    def _on_draw(self, _area: Gtk.DrawingArea, cr) -> bool:
        cr.set_operator(cairo.Operator.SOURCE)
        cr.set_source_surface(self._art, 0, 0)
        cr.paint()
        return False
