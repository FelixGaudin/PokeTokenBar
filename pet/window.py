"""The pet itself: a borderless, always-on-top, click-through-where-transparent window.

The X11/GTK3 recipe that makes this behave like a desktop pet rather than an ordinary
window is in _configure_window. The one people forget is set_accept_focus(False) --
without it the pet steals focus from your editor every time you click it.
"""

from __future__ import annotations

import logging
import math
import os
import random
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cairo

# isort: off
from . import gtk3  # noqa: F401  (pins GI versions; must precede gi.repository)
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo

# isort: on
from . import fmt
from .agent import (
    AWAY,
    BOB_INTERVAL_MS,
    BOB_SCALE,
    HOME,
    RETURNING,
    RUN_DURATION,
    AgentActivityTracker,
    AgentBubble,
    SpeechBubble,
    agent_bubble_content,
    allows_motion,
    bubble_for,
    ease_in_out,
    hearts_showing,
    is_showing_bubble,
    latest_event,
    resolve_move,
    run_panel_origin,
    runs_to_cursor,
    should_bob,
    should_pop_event,
)
from .bubble import GAP as BUBBLE_GAP
from .bubble import Bubble, HeartBubble
from .client import ReconnectWatcher, SpriteKey, SpriteLoad, StateClient
from .config import SIZES, Config, autostart_file
from .petting import (
    HEART_DURATION_MS,
    HEART_WIDTH,
    PettingDetector,
    bubble_top_offset,
    min_stroke,
)
from .prayer import (
    PARTICLE_GLYPH,
    PARTICLE_LIFETIME,
    PetClickRouter,
    PrayerBurst,
    particle_frame,
    prayer_hover_line,
)
from .sprite import NOMINAL, Sprite, alpha_region

log = logging.getLogger(__name__)

# The Gen-V sprites are authored at 100ms a frame, so ticking faster than 10fps buys
# nothing visible and costs CPU linearly: measured 2.3% of a core at 15fps against 1.5%
# at 10fps, for an identical-looking pet.
DEFAULT_FPS = 10
# Only while a prayer's emoji flies and the egg shakes: the shake's keyframes are 60-100ms
# apart, which 10fps would skip.
FAST_TICK_MS = 33
RUN_FRAME_MS = 16
DRAG_THRESHOLD = 4  # px of movement that turns a click into a drag
HOVER_DELAY_MS = 220
ALERT_MS = 6000
EVENT_POP_MS = 6000
HOP_DURATION = 0.7  # seconds
LIMIT_ALERT_AT = 90.0
LIMIT_REARM_AT = 85.0  # hysteresis, so a wobbling number cannot re-trigger
ACCOUNT_TITLE_MAX = 14  # characters of an account title in the callout
EGG_WOBBLE_PERIOD = 3.0  # seconds, matching the web UI's egg animation
EGG_ANGLE_STEP = 0.02  # radians

# Progress bar. Sizes are px at the 96px nominal and scale with the pet. It sits below
# the sprite because above is where the hover callout goes.
BAR_HEIGHT = 5.0
BAR_GAP = 5.0  # between the ground line and the bar
BAR_WIDTH_RATIO = 0.60  # about the width of the sprite, so the bar reads as attached
BAR_TRACK = (0.10, 0.11, 0.14, 0.88)
BAR_EDGE = (1.0, 1.0, 1.0, 0.22)
BAR_PAD = 3  # room in the cached surface for the neon stroke to bleed into

# The games' EXP bar, not the web UI's flat accent: a bright cyan, drawn as the two-tone
# light-over-dark band Gen V uses, with a gloss and a neon edge. Shiny keeps a gold
# version of the same treatment.
EXP_LIGHT = (0.435, 0.914, 1.0)  # #6fe9ff
EXP_DARK = (0.071, 0.647, 0.878)  # #12a5e0
SHINY_LIGHT = (1.0, 0.894, 0.502)  # #ffe480
SHINY_DARK = (0.949, 0.663, 0.231)  # #f2a93b


@dataclass(frozen=True)
class Behaviour:
    """How a display_state reads as motion. Amplitudes are px at the 96px nominal size."""

    bob: float
    period: float
    alpha: float = 1.0
    glow: bool = False


# "sulk" has no entry on purpose: it gets FALLBACK, and in particular is not asleep.
BEHAVIOUR: dict[str, Behaviour] = {
    "egg": Behaviour(bob=1.5, period=3.0),
    "sleep": Behaviour(bob=1.5, period=4.5, alpha=0.55),
    "idle": Behaviour(bob=2.0, period=3.0),
    "working": Behaviour(bob=3.0, period=1.5),
    "focus": Behaviour(bob=4.0, period=0.8, glow=True),
    "tired": Behaviour(bob=1.5, period=3.5, alpha=0.85),
    "levelUp": Behaviour(bob=5.0, period=0.5, glow=True),
}
FALLBACK = Behaviour(bob=2.0, period=3.0)

RARITY_LABEL = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "legendary": "Legendary",
}


def esc(text: object) -> str:
    return GLib.markup_escape_text(str(text))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class PetWindow(Gtk.Window):
    def __init__(
        self,
        base_url: str,
        interval: float,
        config: Config,
        shape: bool = True,
        fps: int = DEFAULT_FPS,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.base_url = base_url.rstrip("/")
        self.frame_ms = max(1, round(1000 / max(fps, 1)))
        self.config = config
        self.size = config.size
        self.show_bar = bool(config.get("show_bar", True))
        self.use_shape = shape

        self.state: dict[str, Any] | None = None
        self.error: str | None = None
        self.sprite: Sprite | None = None
        self._sprite_key: SpriteKey | None = None
        self._sprite_load: SpriteLoad | None = None
        self._shape_key: tuple[int, int, int, bool] | None = None
        self._next_frame_at = 0.0
        self._started = time.monotonic()
        self._frame_serial = 0
        self._lift_px = 0
        self._egg_rotation = 0.0
        self._egg_wobble = 0.0
        self._render_key: tuple | None = None
        self._bar_cache: tuple[tuple, cairo.ImageSurface] | None = None
        self._particle_layout: tuple[float, Pango.Layout] | None = None
        # Artwork bounds in window coordinates. The sprite occupies only the lower part
        # of the box, so the callout must anchor to this rather than to the window edge.
        self._art_top = 0
        self._art_bottom = self.size
        self._hop_until = 0.0

        # click/drag discrimination
        self._press: tuple[float, float, int] | None = None
        # Set when a drag is handed to the window manager. The WM then owns the pointer
        # and the release may never reach us, so it is cleared on the next press instead.
        self._dragging = False
        self._save_source: int | None = None
        self._pending_save: tuple[int, int] | None = None
        self._hover_source: int | None = None
        self._hovering = False
        self._placed = False
        self._target = (0, 0)
        self._place_attempts = 0

        # Limit alerts (edge-triggered) and companion event pops.
        self._limits_alerted: set[str] = set()
        self._limit_alert: str | None = None
        self._limit_source: int | None = None
        self._event_seen: int | None = None
        self._event_text: str | None = None
        self._event_source: int | None = None
        self._first_state = True

        # Coding agents: bob while working, run to the pointer when done.
        self.tracker = AgentActivityTracker()
        self._agent_source: int | None = None
        self._bob_source: int | None = None
        self._squashed = False
        self.run_phase = HOME
        self._run_source: int | None = None
        self._run_generation = 0

        # Petting and prayers.
        self.petting = PettingDetector()
        self._hearts_up = False
        self._hearts_source: int | None = None
        self.burst = PrayerBurst()
        self._fast_source: int | None = None
        self.router = PetClickRouter(
            pray=self._pray,
            spawn=self._spawn_prayer,
            did_pray=self._prayed,
            refresh=lambda: self.client.refresh_now(),
            open_ui=self._open_ui,
            clock=time.monotonic,
        )

        self.bubble = Bubble()  # hover stats and event pops
        self.speech = Bubble()  # limit alerts and agent questions/results
        self.hearts = HeartBubble()
        self._configure_window()
        self._build_ui()
        self._apply_geometry()

        self.client = StateClient(
            self.base_url, interval, self._on_state, self._on_error, self._on_agent
        )
        self.reconnect = ReconnectWatcher(self.client.refresh_now)
        self._power: Gio.PowerProfileMonitor | None = None
        self._watch_motion_settings()

    # -------------------------------------------------------------- window setup

    def _configure_window(self) -> None:
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(bool(self.config.get("keep_above", True)))
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)  # never take focus from the window underneath
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_title("PokeTokenBar Pet")

        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual() if screen is not None else None
        if visual is not None:
            self.set_visual(visual)
        else:
            log.warning("no RGBA visual: the pet will have an opaque background")
        self.stick()  # follow me across workspaces

    def _build_ui(self) -> None:
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.size, self.box_height)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.area.connect("draw", self._on_draw)
        self.area.connect("button-press-event", self._on_press)
        self.area.connect("button-release-event", self._on_release)
        self.area.connect("motion-notify-event", self._on_motion)
        self.area.connect("enter-notify-event", self._on_enter)
        self.area.connect("leave-notify-event", self._on_leave)
        self.add(self.area)
        self.connect("configure-event", self._on_configure)
        self.connect("destroy", lambda *_: self.quit())

    @property
    def scale(self) -> float:
        return self.size / NOMINAL

    @property
    def bar_height(self) -> int:
        return max(3, round(BAR_HEIGHT * self.scale))

    @property
    def bar_gap(self) -> int:
        return max(2, round(BAR_GAP * self.scale))

    @property
    def bar_block(self) -> int:
        """Extra window height the bar needs: gap, bar, and room for its 1px stroke."""
        return self.bar_gap + self.bar_height + 1 if self.show_bar else 0

    @property
    def box_height(self) -> int:
        """Window height: the square sprite stage, plus the bar's strip below it."""
        return self.size + self.bar_block

    def _apply_geometry(self) -> None:
        self.area.set_size_request(self.size, self.box_height)
        self.resize(self.size, self.box_height)

    def _apply_saved_position(self) -> None:
        """Move to the saved spot, and keep insisting until it takes.

        A single move() -- before or after show_all() -- loses a race with the window
        manager's own placement policy, which then wins. Worse, our configure handler
        would persist the WM's guess as the user's chosen position, so the pet wanders a
        little further from home on every restart. Re-asserting until the position
        actually reads back correct is the only thing that survives muffin.
        """
        self._target = self._home()
        self._place_attempts = 0
        GLib.timeout_add(30, self._enforce_position)

    def _enforce_position(self) -> bool:
        target_x, target_y = self._target
        self.move(target_x, target_y)
        self._place_attempts += 1
        x, y = self.get_position()
        settled = abs(x - target_x) <= 2 and abs(y - target_y) <= 2
        if settled or self._place_attempts >= 12:
            if not settled:
                log.debug("placement did not settle: wanted %s, got %s", self._target, (x, y))
            self._placed = True  # only now may a move be treated as the user's doing
            self._sync_overlays()
            return False
        return True

    def _home(self) -> tuple[int, int]:
        return self._clamped(*self.config.position)

    def _clamped(self, x: int, y: int) -> tuple[int, int]:
        """Keep the pet on a monitor that still exists, after a display change."""
        display = Gdk.Display.get_default()
        if display is None:
            return x, y
        monitor = display.get_monitor_at_point(x, y) or display.get_primary_monitor()
        if monitor is None:
            return x, y
        g = monitor.get_geometry()
        return (
            max(g.x, min(x, g.x + g.width - self.size)),
            max(g.y, min(y, g.y + g.height - self.box_height)),
        )

    def _visible(self) -> bool:
        return self.get_mapped()

    def run(self) -> None:
        self.connect("map-event", self._on_map)
        self.show_all()
        self.client.start()
        self.reconnect.start()
        GLib.timeout_add(self.frame_ms, self._tick)

    def quit(self) -> None:
        self.reconnect.stop()
        self.client.stop()
        if self._sprite_load is not None:
            self._sprite_load.cancel()
        self.bubble.destroy()
        self.speech.destroy()
        self.hearts.destroy()
        Gtk.main_quit()

    # ------------------------------------------------------------------ data flow

    def _on_state(self, state: dict[str, Any]) -> None:
        self.error = None
        self.state = state
        companion = state.get("companion", {})
        self._sync_sprite(companion)
        self._check_limits(state)
        self._check_events(companion)
        self._first_state = False
        self.area.queue_draw()

    def _on_error(self, message: str) -> None:
        self.error = message
        self.area.queue_draw()

    def _active(self) -> dict[str, Any] | None:
        active = (self.state or {}).get("companion", {}).get("active")
        return active if isinstance(active, dict) else None

    def _is_egg(self) -> bool:
        """Known to be an egg. Before the first state nothing is known, so no prayer."""
        return self.state is not None and self._active() is None

    def _sync_sprite(self, companion: dict[str, Any]) -> None:
        active = companion.get("active")
        if not active:
            self._cancel_sprite_load()
            self.sprite = None
            self._sprite_key = None
            return
        key = SpriteKey(
            species_id=int(active["species_id"]),
            animated=bool(self.config.get("animated", True)),
            shiny=bool(active["is_shiny"]),
            form=active.get("form"),
            back=bool(companion.get("faces_back")),
        )
        if key == self._sprite_key:
            return
        self._sprite_key = key
        self._cancel_sprite_load()
        cached = self.client.cached_sprite(key)
        if cached is not None:
            self._install_sprite(key, cached)
            return
        # The previous sprite stays up only while the first request is in flight; the
        # loader hands over a stand-in (or nothing) as soon as that request fails.
        self._sprite_load = self.client.load_sprite(key, lambda data: self._on_sprite(key, data))

    def _cancel_sprite_load(self) -> None:
        if self._sprite_load is not None:
            self._sprite_load.cancel()
            self._sprite_load = None

    def _on_sprite(self, key: SpriteKey, data: bytes | None) -> None:
        if data is None:
            # Not even the static image came through. Show nothing rather than the
            # previous species; the load keeps retrying.
            self.sprite = None
            self._shape_key = None
            self.area.queue_draw()
            return
        self._install_sprite(key, data)

    def _install_sprite(self, key: SpriteKey, data: bytes) -> None:
        try:
            self.sprite = Sprite(data)
        except (ValueError, GLib.Error) as exc:
            log.warning("could not decode sprite %s: %s", key.species_id, exc)
            self.sprite = None
        self._shape_key = None
        self._next_frame_at = 0.0
        self.area.queue_draw()

    # ------------------------------------------------------- limit alerts, events

    def _check_limits(self, state: dict[str, Any]) -> None:
        alerts: list[str] = []
        for window in state.get("limits", {}).get("windows", []):
            key = str(window.get("key", ""))
            util = float(window.get("utilization", 0.0))
            if util >= LIMIT_ALERT_AT and key not in self._limits_alerted:
                self._limits_alerted.add(key)
                alerts.append(f"<b>{esc(window.get('name', key))}</b>\nat {util:.0f}% -- ease off?")
            elif util < LIMIT_REARM_AT:
                self._limits_alerted.discard(key)
        if not alerts:
            return
        self._hop_until = time.monotonic() + HOP_DURATION
        self._limit_alert = "\n".join(alerts)
        self._limit_source = self._restart(self._limit_source, ALERT_MS, self._limit_expired)
        self._sync_overlays()

    def _limit_expired(self) -> bool:
        self._limit_source = None
        self._limit_alert = None
        self._sync_overlays()
        return False

    def _check_events(self, companion: dict[str, Any]) -> None:
        """Pop the callout for a hatch, evolution, graduation, Ditto reveal...

        `companion.events` is never consumed by reading, so the pet and a browser tab
        both see every event. Whatever is already there on the first poll is not news;
        an event that arrives while a speech bubble or hearts show is dropped, not
        queued.
        """
        event = latest_event(companion.get("events"))
        if event is None:
            return
        seq = event["seq"]
        if self._first_state:
            self._event_seen = seq
            return
        if seq == self._event_seen:
            return
        pop = should_pop_event(seq, self._event_seen, self._visible(), self._showing_bubble())
        self._event_seen = seq
        text = _text(event.get("text"))
        if not pop or text is None:
            return
        self._event_text = text
        self._hop_until = time.monotonic() + HOP_DURATION
        self._event_source = self._restart(self._event_source, EVENT_POP_MS, self._event_expired)
        self._show_callout()

    def _event_expired(self) -> bool:
        self._event_source = None
        self._event_text = None
        if self._hovering:
            self._show_callout()
        else:
            self.bubble.hide()
        return False

    @staticmethod
    def _restart(source: int | None, ms: int, callback: Callable[[], bool]) -> int:
        """A one-shot timer that each new trigger pushes back."""
        if source is not None:
            GLib.source_remove(source)
        return GLib.timeout_add(ms, callback)

    # ------------------------------------------------------- what shows above the pet

    def _showing_bubble(self) -> bool:
        alert_active = self._limit_alert is not None
        return is_showing_bubble(alert_active, self._hearts_up, self.tracker.summary)

    def _sync_overlays(self) -> None:
        """Speech bubble over hearts over the hover/event callout."""
        visible = self._visible()
        speech = bubble_for(self._limit_alert, self.tracker.summary)
        if speech is not None and visible:
            self._show_speech(speech)
            self.hearts.hide()
            self._hide_callout()
            return
        self.speech.hide()
        if self._hearts_up and visible:
            self._place_hearts()
            self._hide_callout()
        else:
            self.hearts.hide()

    def _show_speech(self, speech: SpeechBubble) -> None:
        anchor_x, top, bottom = self._anchor()
        if isinstance(speech, AgentBubble):
            # Labels and messages come from any local process: escape before Pango sees them.
            title, body = agent_bubble_content(speech, lambda t: self.speech.title_fits(esc(t)))
            self.speech.show_speech(
                esc(title), esc(body), anchor_x=anchor_x, above_y=top, below_y=bottom
            )
        else:
            self.speech.show(str(speech.alert), anchor_x=anchor_x, above_y=top, below_y=bottom)

    def _follow_overlays(self) -> None:
        if not (self.speech.visible or self.bubble.visible or self.hearts.visible):
            return
        anchor_x, top, bottom = self._anchor()
        self.speech.reposition(anchor_x=anchor_x, above_y=top, below_y=bottom)
        self.bubble.reposition(anchor_x=anchor_x, above_y=top, below_y=bottom)
        if self.hearts.visible:
            self._place_hearts()

    def _anchor(self) -> tuple[int, int, int]:
        """Screen-space anchor for the callouts: centre, top of art, bottom of art."""
        x, y = self.get_position()
        return x + self.size // 2, y + self._art_top, y + self._art_bottom

    def _show_bubble(self, markup: str, timeout_ms: int | None) -> None:
        anchor_x, top, bottom = self._anchor()
        self.bubble.show(
            markup, anchor_x=anchor_x, above_y=top, below_y=bottom, timeout_ms=timeout_ms
        )

    def _show_callout(self) -> None:
        """The hover/event callout, unless a speech bubble or hearts have the space."""
        if self._showing_bubble() or not self._visible():
            self._hide_callout()
            return
        self._show_bubble(self._hover_markup(), timeout_ms=None)

    def _hide_callout(self) -> None:
        self._cancel_hover()
        self.bubble.hide()

    def _hover_markup(self) -> str:
        if self.state is None:
            reason = self.error or "waiting for the backend"
            return f"<b>PokeTokenBar</b>\n<span size='small'>{esc(reason)}</span>"

        state = self.state
        usage, limits = state.get("usage", {}), state.get("limits", {})
        companion = state.get("companion", {})
        active = companion.get("active")
        lines: list[str] = []
        # The server's integer floor: rounding here would read 99.6% as 100% before an
        # evolution, disagreeing with the web UI.
        growth = companion.get("growth") or {}
        percent = growth.get("percent")
        if not isinstance(percent, int):
            source = active if active else companion.get("egg", {})
            percent = math.floor(float(source.get("progress") or 0.0) * 100)

        if active:
            marks = " <span foreground='#ffd76e'>SHINY</span>" if active.get("is_shiny") else ""
            lines.append(f"<b>{esc(active['name'])}</b>{marks}")
            bits = [RARITY_LABEL.get(active.get("rarity", ""), str(active.get("rarity", "")))]
            if active.get("nature_label"):
                bits.append(str(active["nature_label"]))
            lines.append(f"<span size='small' foreground='#9aa3b2'>{esc(' - '.join(bits))}</span>")
            stage = f"Form {int(active['stage_index']) + 1} of {int(active['total_forms'])}"
            if active.get("is_final"):
                target = f"{fmt.tokens(active['tokens_to_next'])} to graduation"
            else:
                target = f"{fmt.tokens(active['tokens_to_next'])} to next form"
            lines.append(
                f"<span size='small'>{esc(stage)} - Growth {percent}%\n{esc(target)}</span>"
            )
        else:
            egg = companion.get("egg", {})
            lines.append("<b>Egg</b>")
            lines.append(
                f"<span size='small'>{fmt.tokens(egg.get('tokens_to_hatch', 0))} to hatch"
                f" - Hatch {percent}%</span>"
            )

        today = usage.get("today", {})
        sulk = fmt.sulking(float(companion.get("sulk_idle_seconds") or 0.0))
        sulk_part = f"<span size='small' foreground='#9aa3b2'> · {esc(sulk)}</span>" if sulk else ""
        lines.append(
            "<span size='small' foreground='#9aa3b2'>Today  </span>"
            f"<span size='small'><b>{fmt.tokens(today.get('total_tokens', 0))}</b>"
            f"  {fmt.cost(float(today.get('cost', 0.0)))}</span>{sulk_part}"
        )
        lines.append(
            "<span size='small' foreground='#9aa3b2'>Burn   </span>"
            f"<span size='small'>{fmt.rate(float(usage.get('burn_per_minute', 0.0)))}"
            f" ({esc(usage.get('burn_tier', '-'))})</span>"
        )
        accounts = limits.get("accounts") or []
        if len(accounts) > 1:
            # One line per account, labelled, so equal window names stay distinguishable.
            for account in accounts:
                windows = account.get("windows", [])
                if not windows:
                    continue
                title = str(account.get("title", ""))
                if len(title) > ACCOUNT_TITLE_MAX:
                    title = title[: ACCOUNT_TITLE_MAX - 1] + "…"
                parts = " - ".join(
                    f"{esc(w['name'])} {float(w['utilization']):.0f}%" for w in windows
                )
                stale = " <span foreground='#e0a34a'>(stale)</span>" if account.get("stale") else ""
                lines.append(
                    f"<span size='small'><b>{esc(title)}</b></span>"
                    f" <span size='small' foreground='#9aa3b2'>{parts}</span>{stale}"
                )
        else:
            windows = limits.get("windows", [])
            if windows:
                parts = " - ".join(
                    f"{esc(w['name'])} {float(w['utilization']):.0f}%" for w in windows
                )
                stale = " <span foreground='#e0a34a'>(stale)</span>" if limits.get("stale") else ""
                lines.append(f"<span size='small' foreground='#9aa3b2'>{parts}</span>{stale}")
        if self._event_text:
            lines.append(f"<b>{esc(self._event_text)}</b>")
        if not active:
            prayers = companion.get("egg", {}).get("prayers") or 0
            if isinstance(prayers, int) and prayers > 0:
                lines.append(f"<span size='small'>{esc(prayer_hover_line(prayers))}</span>")
        if self.error:
            lines.append(f"<span size='small' foreground='#e0a34a'>{esc(self.error)}</span>")
        return "\n".join(lines)

    # -------------------------------------------------------------- agent activity

    def _agent_enabled(self) -> bool:
        return bool(self.config.get("agent_activity", True))

    def _on_agent(self, events: list[dict]) -> None:
        if not self._agent_enabled():
            return
        now = time.monotonic()
        for event in events:
            self.tracker.apply(
                event["state"], _text(event.get("label")), _text(event.get("message")), now
            )
        self._agent_did_change()

    def _agent_did_change(self) -> None:
        summary = self.tracker.summary
        motion = self._motion_allowed()
        self._set_bob(should_bob(summary.working, motion))
        self._schedule_agent_expiry()
        self._sync_overlays()
        if summary.done is not None:
            if runs_to_cursor(self.run_phase, motion, self._visible()):
                self._run_to_cursor()
        else:
            self._return_home()

    def _schedule_agent_expiry(self) -> None:
        """Exactly one timer, at the tracker's deadline; none while idle or waiting."""
        if self._agent_source is not None:
            GLib.source_remove(self._agent_source)
            self._agent_source = None
        deadline = self.tracker.deadline
        if deadline is None:
            return
        delay = max(0, math.ceil((deadline - time.monotonic()) * 1000))
        self._agent_source = GLib.timeout_add(delay, self._expire_agent)

    def _expire_agent(self) -> bool:
        self._agent_source = None
        if self.tracker.expire(time.monotonic()):
            self._agent_did_change()
        else:
            self._schedule_agent_expiry()  # fired early: arm again rather than get stuck
        return False

    def _acknowledge_agents(self) -> None:
        """A click answers waiting agents and acknowledges results. Work goes on."""
        if self.tracker.dismiss_bubble():
            self._agent_did_change()

    def _reset_agent(self) -> None:
        self.tracker.reset()
        self._schedule_agent_expiry()
        self._set_bob(False)
        self._stop_run()
        if self.run_phase != HOME:
            self.run_phase = HOME
            self.move(*self._home())
        self._sync_overlays()

    def _set_bob(self, on: bool) -> None:
        """The squash loop exists only while bobbing: idle leaves no timer behind."""
        if on and self._bob_source is None:
            self._bob_source = GLib.timeout_add(BOB_INTERVAL_MS, self._toggle_bob)
        elif not on and self._bob_source is not None:
            GLib.source_remove(self._bob_source)
            self._bob_source = None
            self._squashed = False

    def _toggle_bob(self) -> bool:
        self._squashed = not self._squashed
        self._tick()  # draw the new pose now rather than at the next frame
        return True

    def _run_to_cursor(self) -> None:
        display = Gdk.Display.get_default()
        seat = display.get_default_seat() if display is not None else None
        pointer = seat.get_pointer() if seat is not None else None
        if pointer is None:
            return
        _screen, cx, cy = pointer.get_position()
        monitor = display.get_monitor_at_point(cx, cy)
        if monitor is None:
            return
        area = monitor.get_workarea()
        # The speech bubble sits above the pet, so it counts as part of what must fit.
        bubble_w, bubble_h = self.speech.size if self.speech.visible else (0, 0)
        headroom = max(0, bubble_h + BUBBLE_GAP - self._art_top)
        width, height = max(self.size, bubble_w), self.box_height + headroom
        workarea = (area.x, area.y, area.width, area.height)
        x, y = run_panel_origin((cx, cy), (width, height), workarea)
        self._dragging = False
        self.run_phase = AWAY
        self._hide_callout()
        self._animate_to((x + (width - self.size) // 2, y + headroom))

    def _return_home(self) -> None:
        if self.run_phase != AWAY:
            return  # home already, or on the way
        if not (self._visible() and self._motion_allowed()):
            self._stop_run()
            self.run_phase = HOME
            self.move(*self._home())
            return
        self.run_phase = RETURNING

        def arrived() -> None:
            if self.run_phase == RETURNING:
                self.run_phase = HOME

        self._animate_to(self._home(), arrived)

    def _animate_to(
        self, target: tuple[int, int], on_done: Callable[[], None] | None = None
    ) -> None:
        self._stop_run()
        generation = self._run_generation
        start_x, start_y = self.get_position()
        target_x, target_y = target
        started = time.monotonic()

        def step() -> bool:
            if generation != self._run_generation:
                return False
            t = (time.monotonic() - started) / RUN_DURATION
            eased = ease_in_out(t)
            self.move(
                round(start_x + (target_x - start_x) * eased),
                round(start_y + (target_y - start_y) * eased),
            )
            if t < 1:
                return True
            self._run_source = None
            if on_done is not None:
                on_done()
            return False

        self._run_source = GLib.timeout_add(RUN_FRAME_MS, step)

    def _stop_run(self) -> None:
        """Stop an in-flight run or return; a stale step can then do nothing."""
        self._run_generation += 1
        if self._run_source is not None:
            GLib.source_remove(self._run_source)
            self._run_source = None

    # -------------------------------------------------------------- motion gating

    def _watch_motion_settings(self) -> None:
        try:
            self._power = Gio.PowerProfileMonitor.get_default()
        except (AttributeError, GLib.Error) as exc:  # GLib < 2.70
            log.debug("no power profile monitor: %s", exc)
            self._power = None
        if self._power is not None:
            self._power.connect("notify::power-saver-enabled", self._on_motion_changed)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.connect("notify::gtk-enable-animations", self._on_motion_changed)

    def _motion_allowed(self) -> bool:
        """Power saver and turned-off desktop animations still the bob and the run."""
        power_saver = self._power is not None and bool(self._power.get_power_saver_enabled())
        settings = Gtk.Settings.get_default()
        animations = settings is None or bool(settings.props.gtk_enable_animations)
        return allows_motion(bool(self.config.get("animated", True)), power_saver, animations)

    def _on_motion_changed(self, *_args: object) -> None:
        self._set_bob(should_bob(self.tracker.summary.working, self._motion_allowed()))

    # ---------------------------------------------------------- petting, prayers

    def _pet_stroked(self) -> None:
        if not hearts_showing(self._limit_alert is not None, self.tracker.summary):
            return
        self._hearts_up = True
        self._hearts_source = self._restart(
            self._hearts_source, HEART_DURATION_MS, self._hearts_expired
        )
        self._hide_callout()
        self._sync_overlays()

    def _hearts_expired(self) -> bool:
        self._hearts_source = None
        self._hearts_up = False
        self._sync_overlays()
        if self._hovering:
            self._show_callout()
        return False

    def _place_hearts(self) -> None:
        x, y = self.get_position()
        self.hearts.show(
            x + self.size // 2 - HEART_WIDTH // 2, y + bubble_top_offset(self._visible_top())
        )

    def _visible_top(self) -> float:
        """Where the art visibly starts, in window coordinates: the hearts' tail goes there."""
        if self.sprite is None:
            return float(self._art_top)
        pixbuf = self.sprite.scaled(self.size)
        _x, y = Sprite.placement(pixbuf, self.size)
        return y - self._lift_px + self.sprite.opaque_top_fraction() * pixbuf.get_height()

    def _pray(self) -> None:
        self.client.post_json("/api/companion/pray", None, self._on_prayer_counted)

    def _on_prayer_counted(self, result: dict[str, Any]) -> None:
        count = result.get("count")
        if isinstance(count, int):
            self.router.prayed(count)

    def _spawn_prayer(self) -> None:
        self.burst.spawn(time.monotonic(), random.random())
        if self._fast_source is None:
            self._fast_source = GLib.timeout_add(FAST_TICK_MS, self._fast_tick)

    def _prayed(self, count: int) -> None:
        """Show the new count at once instead of on the next poll."""
        egg = (self.state or {}).get("companion", {}).get("egg")
        if isinstance(egg, dict) and self._is_egg():
            egg["prayers"] = count
        if self.bubble.visible:
            self._show_callout()

    def _fast_tick(self) -> bool:
        now = time.monotonic()
        self.burst.prune(now)
        self._tick()
        if self.burst.active(now):
            return True
        self._fast_source = None
        return False

    # ------------------------------------------------------------------- painting

    def _progress(self) -> float:
        """How far the companion is toward its next form, or the egg toward hatching."""
        if self.state is None:
            return 0.0
        companion = self.state.get("companion", {})
        active = companion.get("active")
        raw = active.get("progress") if active else companion.get("egg", {}).get("progress")
        return min(1.0, max(0.0, float(raw or 0.0)))

    def _bar_colours(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        active = self._active()
        if active and active.get("is_shiny"):
            return SHINY_LIGHT, SHINY_DARK
        return EXP_LIGHT, EXP_DARK

    def _asleep(self) -> bool:
        if self.state is None:
            return True
        return str(self.state.get("companion", {}).get("display_state", "")) == "sleep"

    def _behaviour(self) -> Behaviour:
        if self.state is None:
            return BEHAVIOUR["sleep"]
        key = str(self.state.get("companion", {}).get("display_state", "idle"))
        return BEHAVIOUR.get(key, FALLBACK)

    def _offsets(self, behaviour: Behaviour) -> int:
        """Vertical bob plus any level-up hop, as whole pixels at the current size.

        Rounded to integers on purpose: it keeps the sprite from being resampled at a
        fractional offset (which softens pixel art), and it means most frames are
        identical to the last one, so the redraw can be skipped entirely.
        """
        scale = self.size / NOMINAL
        now = time.monotonic()
        phase = (now - self._started) / max(behaviour.period, 0.1) * 2 * math.pi
        bob = (math.sin(phase) * 0.5 + 0.5) * behaviour.bob * scale
        hop = 0.0
        if now < self._hop_until:
            progress = 1.0 - (self._hop_until - now) / HOP_DURATION
            hop = abs(math.sin(progress * 3 * math.pi)) * 7 * scale * (1.0 - progress)
        return round(bob + hop)

    def _egg_angle(self) -> float:
        """Wobble, on the same 3s period as the web UI's egg.

        Quantised coarsely on purpose: the egg is redrawn from scratch each time this
        changes (arcs, fills, a stroke), so a finer step made the egg state cost more
        CPU than a fully animated Pokemon. 0.02rad is about a degree -- invisible.
        """
        phase = math.sin((time.monotonic() - self._started) / EGG_WOBBLE_PERIOD)
        return round(phase * 0.09 / EGG_ANGLE_STEP) * EGG_ANGLE_STEP

    def _on_draw(self, _area: Gtk.DrawingArea, cr) -> bool:
        cr.set_operator(cairo.Operator.SOURCE)  # start from genuine transparency
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.Operator.OVER)

        behaviour = self._behaviour()
        alpha = behaviour.alpha if self.error is None else min(behaviour.alpha, 0.45)
        lift = self._lift_px

        self._draw_shadow(cr, lift)
        if behaviour.glow:
            self._draw_glow(cr, lift)
        if self.show_bar:
            self._draw_bar(cr, alpha)

        if self.sprite is not None:
            pixbuf = self.sprite.scaled(self.size)
            x, y = Sprite.placement(pixbuf, self.size)
            cr.save()
            self._squash(cr, y - lift + pixbuf.get_height())
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, x, y - lift)
            if self._squashed:
                cr.get_source().set_filter(cairo.Filter.NEAREST)  # keep the pixel art crisp
            cr.paint_with_alpha(alpha)
            cr.restore()
            self._art_top = max(0, int(y - lift))
            self._art_bottom = max(
                self.box_height if self.show_bar else 0, int(y - lift + pixbuf.get_height())
            )
            self._update_shape(pixbuf, x, y, behaviour)
        elif self._active() is None:
            cr.save()
            self._squash(cr, self.size - 4 * self.scale - lift)
            self._draw_egg(cr, lift, alpha)
            self._draw_particles(cr)
            cr.restore()
            self._art_top = max(0, int(self.size - (58 * self.scale) - lift))
            self._art_bottom = self.box_height
            self._clear_shape()
        else:
            # A Pokemon whose sprite has not arrived: nothing, rather than an egg or the
            # previous species. The whole box stays clickable meanwhile.
            self._clear_shape()
        return False

    def _squash(self, cr, ground_y: float) -> None:
        """The agent bob's squashed half, about the ground line so the feet stay put."""
        if not self._squashed:
            return
        centre = self.size / 2
        cr.translate(centre, ground_y)
        cr.scale(*BOB_SCALE)
        cr.translate(-centre, -ground_y)

    def _draw_particles(self, cr) -> None:
        now = time.monotonic()
        for particle in self.burst.particles:
            age = now - particle.born
            if age >= PARTICLE_LIFETIME:
                continue
            frame = particle_frame(self.size, particle.x_fraction, age)
            if frame.opacity <= 0:
                continue
            layout = self._prayer_layout(frame.edge)
            width, height = layout.get_pixel_size()
            x, y = frame.cx - width / 2, frame.cy - height / 2
            cr.save()
            # A colour emoji ignores the source alpha, so fade it through a group,
            # clipped to the glyph to keep the group small.
            cr.rectangle(x, y, width, height)
            cr.clip()
            cr.push_group()
            cr.move_to(x, y)
            PangoCairo.show_layout(cr, layout)
            cr.pop_group_to_source()
            cr.paint_with_alpha(frame.opacity)
            cr.restore()

    def _prayer_layout(self, edge: float) -> Pango.Layout:
        if self._particle_layout is None or self._particle_layout[0] != edge:
            layout = self.area.create_pango_layout(PARTICLE_GLYPH)
            font = Pango.FontDescription("Sans")
            font.set_absolute_size(edge * 0.8 * Pango.SCALE)
            layout.set_font_description(font)
            self._particle_layout = (edge, layout)
        return self._particle_layout[1]

    def _bar_rect(self) -> tuple[int, int, int, int]:
        """Whole-pixel bounds, so the cached surface can be blitted without resampling."""
        width = round(self.size * BAR_WIDTH_RATIO)
        return (self.size - width) // 2, self.size + self.bar_gap, width, self.bar_height

    @staticmethod
    def _pill(cr, x: float, y: float, width: float, height: float) -> None:
        radius = height / 2
        cr.new_path()
        cr.arc(x + radius, y + radius, radius, 0.5 * math.pi, 1.5 * math.pi)
        cr.arc(x + width - radius, y + radius, radius, 1.5 * math.pi, 0.5 * math.pi)
        cr.close_path()

    def _draw_bar(self, cr, alpha: float) -> None:
        """Blit the bar from a cached surface.

        Repainting it per frame cost ~0.7% of a core, because the sprite animation
        invalidates the whole window even though the bar only changes when progress
        does -- about once per poll. Caching keyed on the filled width makes it a blit.
        """
        x, y, width, height = self._bar_rect()
        progress = self._progress()
        # Never narrower than the pill is round, or the end caps cross over.
        filled = 0 if progress <= 0 else round(max(height, width * progress))
        light, dark = self._bar_colours()
        key = (width, height, filled, light, round(alpha, 2))

        if self._bar_cache is None or self._bar_cache[0] != key:
            surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, width + 2 * BAR_PAD, height + 2 * BAR_PAD
            )
            self._paint_bar(cairo.Context(surface), width, height, filled, light, dark, alpha)
            surface.flush()
            self._bar_cache = (key, surface)

        # fill(), not paint(): paint() covers the whole clip region -- the entire window --
        # which cost more than drawing the bar from scratch did.
        cr.set_source_surface(self._bar_cache[1], x - BAR_PAD, y - BAR_PAD)
        cr.rectangle(x - BAR_PAD, y - BAR_PAD, width + 2 * BAR_PAD, height + 2 * BAR_PAD)
        cr.fill()

    def _paint_bar(
        self,
        cr,
        width: int,
        height: int,
        filled: int,
        light: tuple[float, float, float],
        dark: tuple[float, float, float],
        alpha: float,
    ) -> None:
        # Half-pixel offset puts the 1px stroke on the pixel grid rather than across it.
        x = y = BAR_PAD + 0.5
        span, tall = width - 1, height - 1

        self._pill(cr, x, y, span, tall)
        cr.set_source_rgba(*BAR_TRACK[:3], BAR_TRACK[3] * alpha)
        cr.fill_preserve()
        cr.set_source_rgba(*BAR_EDGE[:3], BAR_EDGE[3] * alpha)
        cr.set_line_width(1.0)
        cr.stroke()
        if filled <= 0:
            return

        run = min(float(filled), span)
        # Neon edge first, so the fill sits on top of its own glow.
        self._pill(cr, x, y, run, tall)
        cr.set_source_rgba(*light, 0.45 * alpha)
        cr.set_line_width(2.5)
        cr.stroke()

        # Light over dark, the way the games shade the band.
        gradient = cairo.LinearGradient(x, y, x, y + tall)
        gradient.add_color_stop_rgba(0.0, *light, alpha)
        gradient.add_color_stop_rgba(0.55, *dark, alpha)
        gradient.add_color_stop_rgba(1.0, *dark, alpha)
        self._pill(cr, x, y, run, tall)
        cr.set_source(gradient)
        cr.fill()

        # Gloss: a thin bright band across the top third.
        cr.save()
        self._pill(cr, x, y, run, tall)
        cr.clip()
        self._pill(cr, x + 0.5, y + 0.5, run - 1, max(1.0, tall / 3))
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.42 * alpha)
        cr.fill()
        cr.restore()

    def _draw_shadow(self, cr, lift: int) -> None:
        scale = self.size / NOMINAL
        cx, cy = self.size / 2, self.size - 3 * scale
        rx = 15 * scale
        ry = max(2.0, 4 * scale)
        cr.save()
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.set_source_rgba(0, 0, 0, max(0.05, 0.22 - lift * 0.012))
        cr.fill()

    def _draw_glow(self, cr, lift: int) -> None:
        scale = self.size / NOMINAL
        cx = self.size / 2
        cy = self.size - 20 * scale - lift
        radius = 26 * scale
        gradient = cairo.RadialGradient(cx, cy, 0, cx, cy, radius)
        gradient.add_color_stop_rgba(0.0, 0.45, 0.72, 1.0, 0.20)
        gradient.add_color_stop_rgba(1.0, 0.45, 0.72, 1.0, 0.0)
        cr.set_source(gradient)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()

    def _draw_egg(self, cr, lift: int, alpha: float) -> None:
        """Drawn rather than an emoji: no font dependency, and it can wobble properly."""
        scale = self.size / NOMINAL
        cr.save()
        base_x, base_y = self.size / 2, self.size - 4 * scale
        cr.translate(base_x, base_y - lift)
        cr.rotate(self._egg_rotation)  # the idle rock, about the base
        rx, ry = 21 * scale, 27 * scale
        if self._egg_wobble:
            # A prayer's shake turns about the egg's centre, on top of the rock.
            cr.translate(0, -ry)
            cr.rotate(self._egg_wobble)
            cr.translate(0, ry)
        cr.save()
        cr.translate(0, -ry)
        cr.scale(rx, ry)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.set_source_rgba(0.97, 0.94, 0.86, alpha)
        cr.fill_preserve()
        cr.set_source_rgba(0.62, 0.55, 0.44, alpha * 0.8)
        cr.set_line_width(max(1.0, 1.2 * scale))
        cr.stroke()
        for dx, dy, r in ((-0.30, -0.42, 0.17), (0.34, -0.62, 0.13), (0.02, -0.22, 0.15)):
            cr.arc(dx * rx, dy * ry * 2, r * rx, 0, 2 * math.pi)
            cr.set_source_rgba(0.85, 0.72, 0.55, alpha * 0.85)
            cr.fill()
        cr.restore()

    # ---------------------------------------------------------------- input shape

    def _update_shape(self, pixbuf, x: int, y: int, behaviour: Behaviour) -> None:
        """Shape input to the artwork so clicks in the empty corners fall through.

        Recomputed only when the sprite or size changes -- scanning alpha every frame at
        192px would cost more than the animation itself.
        """
        if not self.use_shape:
            return
        key = (self.size, id(self.sprite), int(behaviour.bob), self.show_bar)
        if key == self._shape_key:
            return
        window = self.get_window()
        if window is None:
            return
        travel = math.ceil(behaviour.bob * self.scale) + 8
        region = alpha_region(pixbuf, x, y - travel)
        if region.is_empty():
            return  # never shape to nothing: that would make the pet unclickable
        # Grow downward so the hit area covers the whole bob, not one frozen frame.
        grown = region.copy()
        grown.translate(0, travel)
        region.union(grown)
        if self.show_bar:
            bx, by, bw, bh = self._bar_rect()
            region.union(cairo.RectangleInt(bx, by, bw, bh))
        window.input_shape_combine_region(region, 0, 0)
        self._shape_key = key

    def _clear_shape(self) -> None:
        if not self.use_shape:
            return
        window = self.get_window()
        if window is not None and self._shape_key is not None:
            window.input_shape_combine_region(None, 0, 0)
            self._shape_key = None

    # -------------------------------------------------------------------- the tick

    def _tick(self) -> bool:
        window = self.get_window()
        if window is not None:
            blocked = Gdk.WindowState.WITHDRAWN | Gdk.WindowState.ICONIFIED
            if window.get_state() & blocked:
                return True  # nothing to animate while hidden

        behaviour = self._behaviour()
        now = time.monotonic()
        # Holding a single frame while asleep reads better than a fidgeting sleeper, and
        # it drops the pet to almost nothing exactly when nobody is working.
        animating = self.sprite is not None and self.sprite.is_animated and not self._asleep()
        if animating and now >= self._next_frame_at:
            self.sprite.advance()
            self._frame_serial += 1
            delay = self.sprite.delay_ms()
            self._next_frame_at = now + (delay / 1000.0 if delay > 0 else 0.1)

        egg = self.sprite is None and self._active() is None
        self._lift_px = self._offsets(behaviour)
        self._egg_rotation = self._egg_angle() if egg else 0.0
        self._egg_wobble = math.radians(self.burst.wobble(now)) if egg else 0.0
        key = (
            self._lift_px,
            self._frame_serial,
            self._egg_rotation,
            self._egg_wobble,
            self._squashed,
            # Particles move every frame while they fly.
            round(now * 1000 / FAST_TICK_MS) if self.burst.particles else None,
            behaviour.alpha,
            behaviour.glow,
            self.error is not None,
            round(self._progress(), 4) if self.show_bar else None,
        )
        if key != self._render_key:
            self._render_key = key
            self.area.queue_draw()
        return True

    # ------------------------------------------------------------------ interaction

    def _on_press(self, _w, event) -> bool:
        self.petting.reset()
        if event.button == 3:
            self._menu().popup_at_pointer(event)
            return True
        if event.button == 1:
            self._press = (event.x_root, event.y_root, event.time)
            self._dragging = False
        return True

    def _on_motion(self, _w, event) -> bool:
        if self._press is None:
            # Stroking: plain pointer moves over the pet, no button held.
            if not event.state & Gdk.ModifierType.BUTTON1_MASK:
                stroke = min_stroke(self.size)
                if self.petting.feed(event.x_root, event.time / 1000, stroke):
                    self._pet_stroked()
            return False
        if self._dragging:
            return False
        px, py, _ = self._press
        if math.hypot(event.x_root - px, event.y_root - py) > DRAG_THRESHOLD:
            self._dragging = True
            self.petting.reset()
            self.bubble.hide()
            self._cancel_hover()
            # Hand the move to the window manager: smoother than tracking motion, and
            # it cooperates with Cinnamon's edge snapping. The WM keeps the release, so
            # forget the press now or the next stroke would still look like a drag.
            self.begin_move_drag(1, int(event.x_root), int(event.y_root), event.time)
            self._press = None
        return True

    def _on_release(self, _w, event) -> bool:
        if event.button == 1 and self._press is not None and not self._dragging:
            self._acknowledge_agents()
            self.router.handle(self._is_egg())
        self._press = None
        return True

    def _on_enter(self, _w, _event) -> bool:
        self._hovering = True
        self._cancel_hover()
        self._hover_source = GLib.timeout_add(HOVER_DELAY_MS, self._show_hover)
        return False

    def _on_leave(self, _w, _event) -> bool:
        self._hovering = False
        self.petting.reset()
        self._hide_callout()
        return False

    def _show_hover(self) -> bool:
        self._hover_source = None
        self._show_callout()
        return False

    def _cancel_hover(self) -> None:
        if self._hover_source is not None:
            GLib.source_remove(self._hover_source)
            self._hover_source = None

    def _on_map(self, _w, _event) -> bool:
        self._apply_saved_position()
        return False

    def _on_configure(self, _w, _event) -> bool:
        self._follow_overlays()
        if not self._placed:
            return False  # still settling into the saved position
        # The run's own frames must never become home; a user drag always does, and
        # cancels a run or return in progress.
        phase, persist = resolve_move(self.run_phase, self._dragging)
        if phase != self.run_phase:
            self.run_phase = phase
            self._stop_run()
        if not persist:
            return False
        self._pending_save = self.get_position()
        # Debounced: a WM-driven drag emits a configure event per pixel.
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(600, self._save_position)
        return False

    def _save_position(self) -> bool:
        self._save_source = None
        if self._pending_save is not None:
            x, y = self._pending_save
            self.config.set(x=x, y=y)
            self._pending_save = None
        return False

    # ------------------------------------------------------------------- the menu

    def _menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        menu.set_reserve_toggle_size(False)

        def item(label: str, handler) -> None:
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", handler)
            menu.append(entry)

        def check(label: str, active: bool, handler) -> None:
            entry = Gtk.CheckMenuItem(label=label)
            entry.set_active(active)
            entry.connect("toggled", handler)
            menu.append(entry)

        item("Open PokeTokenBar", lambda *_: self._open_from_menu())
        item("Refresh now", lambda *_: self.client.refresh_now())
        menu.append(Gtk.SeparatorMenuItem())

        sizes = Gtk.Menu()
        group: Gtk.RadioMenuItem | None = None
        for size in SIZES:
            entry = Gtk.RadioMenuItem(label=f"{size} px", group=group)
            group = group or entry
            entry.set_active(size == self.size)
            entry.connect("toggled", self._on_size_chosen, size)
            sizes.append(entry)
        size_item = Gtk.MenuItem(label="Size")
        size_item.set_submenu(sizes)
        menu.append(size_item)

        meta = (self.state or {}).get("meta", {})
        check("Always on top", bool(self.config.get("keep_above", True)), self._on_keep_above)
        check("Animated sprite", bool(self.config.get("animated", True)), self._on_animated)
        check("Progress bar", self.show_bar, self._on_show_bar)
        check("React to agent activity", self._agent_enabled(), self._on_agent_activity)
        check("Show back view", bool(meta.get("always_faces_back")), self._on_back_view)
        check("Start with session", autostart_file().exists(), self._on_autostart)
        menu.append(Gtk.SeparatorMenuItem())
        item("Quit", lambda *_: self.quit())

        menu.show_all()
        return menu

    def _open_from_menu(self) -> None:
        """Open always opens, even over an egg; it still answers waiting agents."""
        self._acknowledge_agents()
        self._open_ui()

    def _on_size_chosen(self, entry: Gtk.RadioMenuItem, size: int) -> None:
        if not entry.get_active() or size == self.size:
            return
        self.size = size
        self._shape_key = None
        self.config.set(size=size)
        self._apply_geometry()
        x, y = self.get_position()
        self.move(*self._clamped(x, y))
        self.area.queue_draw()

    def _on_show_bar(self, entry: Gtk.CheckMenuItem) -> None:
        self.show_bar = entry.get_active()
        self.config.set(show_bar=self.show_bar)
        self._shape_key = None
        self._apply_geometry()  # the window has to grow or shrink with the bar
        self.area.queue_draw()

    def _on_keep_above(self, entry: Gtk.CheckMenuItem) -> None:
        self.set_keep_above(entry.get_active())
        self.config.set(keep_above=entry.get_active())

    def _on_animated(self, entry: Gtk.CheckMenuItem) -> None:
        self.config.set(animated=entry.get_active())
        self._sprite_key = None
        if self.state is not None:
            self._sync_sprite(self.state.get("companion", {}))
        self._on_motion_changed()  # a static pet does not bob or run either

    def _on_agent_activity(self, entry: Gtk.CheckMenuItem) -> None:
        self.config.set(agent_activity=entry.get_active())
        if not entry.get_active():
            self._reset_agent()

    def _on_back_view(self, entry: Gtk.CheckMenuItem) -> None:
        self.client.post_json(
            "/api/settings/back-view",
            {"enabled": entry.get_active()},
            lambda _result: self.client.poll_now(),
        )

    def _on_autostart(self, entry: Gtk.CheckMenuItem) -> None:
        path = autostart_file()
        if not entry.get_active():
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=PokeTokenBar Pet\n"
            f"Exec=/usr/bin/python3 -m pet --url {self.base_url}\n"
            f"Path={os.getcwd()}\nX-GNOME-Autostart-Delay=5\nTerminal=false\n"
        )

    def _open_ui(self) -> None:
        try:
            Gtk.show_uri_on_window(None, self.base_url, Gdk.CURRENT_TIME)
        except GLib.Error as exc:
            log.debug("show_uri failed (%s), falling back to xdg-open", exc)
            subprocess.Popen(["xdg-open", self.base_url])
