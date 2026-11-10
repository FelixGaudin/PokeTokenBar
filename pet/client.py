"""HTTP access to the backend.

Every network call happens on a worker thread and is marshalled back to the GTK main
loop with GLib.idle_add — a blocking fetch on the main loop would stall the animation.
Only stdlib urllib is used, so the pet needs no pip install to run.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# isort: off
from . import gtk3  # noqa: F401  (pins GI versions; must precede gi.repository)
from gi.repository import Gio, GLib

# isort: on
from .agent import AgentFeed

log = logging.getLogger(__name__)

TIMEOUT = 8.0
MAX_BACKOFF = 60.0
RECONNECT_COOLDOWN = 5.0
# A failed sprite is retried after 2, 4, 8, 16, 30, 30... s, with no attempt limit: one
# network blip must not freeze the pet on a static image for the rest of the session.
SPRITE_FIRST_RETRY = 2.0
SPRITE_MAX_RETRY = 30.0


def should_refresh_on_network(
    prev: bool | None, current: bool, last_triggered_at: float | None, now: float
) -> bool:
    """A reconnect is an unavailable -> available change; the first event is not one."""
    if prev is None or prev or not current:
        return False
    return last_triggered_at is None or now - last_triggered_at >= RECONNECT_COOLDOWN


class ReconnectWatcher:
    """Calls `on_reconnect` when the network comes back, via Gio.NetworkMonitor.

    The signal is delivered on the GTK main loop, so no locking is needed.
    """

    def __init__(self, on_reconnect: Callable[[], None]) -> None:
        self.on_reconnect = on_reconnect
        self._monitor: Gio.NetworkMonitor | None = None
        self._handler: int | None = None
        self._prev: bool | None = None
        self._last: float | None = None

    def start(self) -> None:
        if self._handler is not None:
            return
        self._monitor = Gio.NetworkMonitor.get_default()
        self._handler = self._monitor.connect("network-changed", self._on_changed)

    def stop(self) -> None:
        if self._monitor is not None and self._handler is not None:
            self._monitor.disconnect(self._handler)
        self._handler = None
        self._prev = None

    def _on_changed(self, _monitor: Gio.NetworkMonitor, available: bool) -> None:
        now = time.monotonic()
        trigger = should_refresh_on_network(self._prev, available, self._last, now)
        self._prev = available
        if trigger:
            self._last = now
            log.debug("network is back; refreshing")
            self.on_reconnect()


@dataclass(frozen=True)
class SpriteKey:
    species_id: int
    animated: bool
    shiny: bool
    form: str | None
    back: bool


class SpriteLoad:
    """One recovering sprite load. Once cancelled it never delivers anything.

    Cancelling and delivering both happen on the main loop, so a late response for an
    old key can never be installed over a newer one.
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep, waking early on cancel. True if cancelled."""
        return self._cancelled.wait(seconds)


class StateClient:
    """Polls /api/state, fetches sprites and posts actions, all off the main loop."""

    def __init__(
        self,
        base_url: str,
        interval: float,
        on_state: Callable[[dict[str, Any]], None],
        on_error: Callable[[str], None],
        on_agent: Callable[[list[dict]], None],
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.interval = interval
        self.on_state = on_state
        self.on_error = on_error
        self.on_agent = on_agent
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._feed = AgentFeed()  # only touched by the poll thread
        self._sprites: dict[SpriteKey, bytes] = {}
        self._sprites_lock = threading.Lock()
        # Actions run one at a time, in click order, so prayer counts come back in order.
        self._actions: queue.SimpleQueue[Callable[[], None] | None] = queue.SimpleQueue()

    # ------------------------------------------------------------------ polling

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="ptb-poll", daemon=True)
        self._thread.start()
        threading.Thread(target=self._run_actions, name="ptb-actions", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._actions.put(None)

    def poll_now(self) -> None:
        self._wake.set()

    def refresh_now(self) -> None:
        """Ask the backend to re-scan, then poll immediately."""
        threading.Thread(target=self._force_refresh, daemon=True).start()

    def _force_refresh(self) -> None:
        try:
            self._post("/api/refresh")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.debug("forced refresh failed: %s", exc)
        self._wake.set()

    def _loop(self) -> None:
        backoff = self.interval
        while not self._stop.is_set():
            try:
                state = self._get_json("/api/state" + self._feed.query())
                events = self._feed.take(state.get("agent"))
                backoff = self.interval
                GLib.idle_add(self._deliver_state, state, events)
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
                # The container may simply not be up yet; degrade quietly and back off
                # rather than filling the journal with one line every five seconds.
                message = self._describe(exc)
                log.debug("state fetch failed: %s", message)
                GLib.idle_add(self._deliver_error, message)
                backoff = min(backoff * 2, MAX_BACKOFF)
            self._wake.wait(timeout=backoff)
            self._wake.clear()

    @staticmethod
    def _describe(exc: BaseException) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            return f"backend returned {exc.code}"
        if isinstance(exc, urllib.error.URLError):
            return f"cannot reach backend ({exc.reason})"
        return str(exc) or exc.__class__.__name__

    def _deliver_state(self, state: dict[str, Any], events: list[dict]) -> bool:
        self.on_state(state)
        if events:
            self.on_agent(events)
        return False

    def _deliver_error(self, message: str) -> bool:
        self.on_error(message)
        return False

    # ------------------------------------------------------------------ actions

    def post_json(
        self,
        path: str,
        body: object | None = None,
        on_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """POST in the background; `on_result` gets the parsed reply on the main loop.

        Failures, including a 409 for an action the state no longer allows, are only
        logged: the next poll shows what actually happened.
        """

        def job() -> None:
            try:
                raw = self._post(path, body)
                result = json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                log.debug("%s answered %s", path, exc.code)
                return
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
                log.debug("%s failed: %s", path, exc)
                return
            if on_result is not None and isinstance(result, dict):
                GLib.idle_add(lambda: (on_result(result), False)[1])

        self._actions.put(job)

    def _run_actions(self) -> None:
        while (job := self._actions.get()) is not None:
            job()

    # ------------------------------------------------------------------ sprites

    def cached_sprite(self, key: SpriteKey) -> bytes | None:
        with self._sprites_lock:
            return self._sprites.get(key)

    def load_sprite(self, key: SpriteKey, on_sprite: Callable[[bytes | None], None]) -> SpriteLoad:
        """Fetch the sprite for `key`, recovering from failures until cancelled.

        `on_sprite` runs on the main loop with the final sprite, or with a stand-in
        after the first failure: the static image for an animated key, or None when not
        even that could be fetched, so the previous species never stays on screen. A
        404 (missing) settles on the stand-in; anything else is retried with backoff.
        """
        load = SpriteLoad()
        threading.Thread(
            target=self._recover, args=(key, load, on_sprite), name="ptb-sprite", daemon=True
        ).start()
        return load

    def _recover(
        self, key: SpriteKey, load: SpriteLoad, on_sprite: Callable[[bytes | None], None]
    ) -> None:
        delay = SPRITE_FIRST_RETRY
        stood_in = False
        while not load.cancelled:
            data, missing = self._attempt(key)
            if load.cancelled:
                return
            if data is not None:
                with self._sprites_lock:
                    self._sprites[key] = data
                self._deliver_sprite(load, on_sprite, data)
                return
            if not stood_in:
                stand_in = self._static_stand_in(key) if key.animated else None
                if load.cancelled:
                    return
                if missing and stand_in is not None:
                    with self._sprites_lock:
                        self._sprites[key] = stand_in  # final for this key
                self._deliver_sprite(load, on_sprite, stand_in)
                stood_in = True
            if missing or load.wait(delay):
                return
            delay = min(delay * 2, SPRITE_MAX_RETRY)

    def _attempt(self, key: SpriteKey) -> tuple[bytes | None, bool]:
        """One try at the preferred sprite: (data, confirmed missing).

        A shiny 404 falls through to the plain sprite in the same attempt. A transient
        failure does not, or one blip would settle a shiny on the plain colours. The
        server resolves back-to-front itself, and only for confirmed 404s.
        """
        for shiny in (True, False) if key.shiny else (False,):
            path = self._sprite_path(key, animated=key.animated, shiny=shiny, back=key.back)
            try:
                return self._get_bytes(path), False
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    log.debug("sprite %s answered %s", path, exc.code)
                    return None, False
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                log.debug("sprite %s failed: %s", path, exc)
                return None, False
        return None, True

    def _static_stand_in(self, key: SpriteKey) -> bytes | None:
        """The static image, giving up on back and then shiny on any failure."""
        for back in (True, False) if key.back else (False,):
            for shiny in (True, False) if key.shiny else (False,):
                path = self._sprite_path(key, animated=False, shiny=shiny, back=back)
                try:
                    return self._get_bytes(path)
                except (urllib.error.URLError, OSError, TimeoutError) as exc:
                    log.debug("static sprite %s failed: %s", path, exc)
        return None

    @staticmethod
    def _sprite_path(key: SpriteKey, *, animated: bool, shiny: bool, back: bool) -> str:
        query = f"animated={int(animated)}&shiny={int(shiny)}"
        if key.form:
            query += f"&form={urllib.parse.quote(key.form)}"
        if back:
            query += "&back=1"
        return f"/api/sprite/{key.species_id}?{query}"

    @staticmethod
    def _deliver_sprite(
        load: SpriteLoad, on_sprite: Callable[[bytes | None], None], data: bytes | None
    ) -> None:
        def run() -> bool:
            if not load.cancelled:
                on_sprite(data)
            return False

        GLib.idle_add(run)

    # ------------------------------------------------------------------- plumbing

    def _get_json(self, path: str) -> dict[str, Any]:
        raw = self._get_bytes(path)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            # ValueError, not TypeError: the poll loop treats it like any undecodable reply.
            raise ValueError("unexpected response shape")  # noqa: TRY004
        return parsed

    def _get_bytes(self, path: str) -> bytes:
        request = urllib.request.Request(f"{self.base_url}{path}", method="GET")
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()

    def _post(self, path: str, body: object | None = None) -> bytes:
        data = b"" if body is None else json.dumps(body).encode()
        headers = {} if body is None else {"Content-Type": "application/json"}
        request = urllib.request.Request(
            f"{self.base_url}{path}", method="POST", data=data, headers=headers
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
