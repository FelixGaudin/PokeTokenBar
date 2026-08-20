"""HTTP access to the backend.

Every network call happens on a worker thread and is marshalled back to the GTK main
loop with GLib.idle_add — a blocking fetch on the main loop would stall the animation.
Only stdlib urllib is used, so the pet needs no pip install to run.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from . import gtk3  # noqa: F401  (pins GI versions)
from gi.repository import GLib

log = logging.getLogger(__name__)

TIMEOUT = 8.0
MAX_BACKOFF = 60.0


class StateClient:
    """Polls /api/state and fetches sprites, both off the main loop."""

    def __init__(
        self,
        base_url: str,
        interval: float,
        on_state: Callable[[dict[str, Any]], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.interval = interval
        self.on_state = on_state
        self.on_error = on_error
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._sprites: dict[tuple[int, bool, bool], bytes] = {}
        self._sprites_lock = threading.Lock()
        self._inflight: set[tuple[int, bool, bool]] = set()

    # ------------------------------------------------------------------ polling

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="ptb-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
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
                state = self._get_json("/api/state")
                backoff = self.interval
                GLib.idle_add(self._deliver_state, state)
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

    def _deliver_state(self, state: dict[str, Any]) -> bool:
        self.on_state(state)
        return False

    def _deliver_error(self, message: str) -> bool:
        self.on_error(message)
        return False

    # ------------------------------------------------------------------ sprites

    def sprite(
        self,
        species_id: int,
        *,
        animated: bool,
        shiny: bool,
        on_ready: Callable[[bytes], None],
    ) -> bytes | None:
        """Return cached sprite bytes, or fetch in the background and call on_ready."""
        key = (species_id, animated, shiny)
        with self._sprites_lock:
            cached = self._sprites.get(key)
            if cached is not None:
                return cached
            if key in self._inflight:
                return None
            self._inflight.add(key)

        def work() -> None:
            try:
                data = self._get_bytes(
                    f"/api/sprite/{species_id}?animated={int(animated)}&shiny={int(shiny)}"
                )
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                log.debug("sprite %s failed: %s", species_id, exc)
                with self._sprites_lock:
                    self._inflight.discard(key)
                return
            with self._sprites_lock:
                self._sprites[key] = data
                self._inflight.discard(key)
            GLib.idle_add(lambda: (on_ready(data), False)[1])

        threading.Thread(target=work, daemon=True).start()
        return None

    # ------------------------------------------------------------------- plumbing

    def _get_json(self, path: str) -> dict[str, Any]:
        raw = self._get_bytes(path)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("unexpected response shape")
        return parsed

    def _get_bytes(self, path: str) -> bytes:
        request = urllib.request.Request(f"{self.base_url}{path}", method="GET")
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()

    def _post(self, path: str) -> bytes:
        request = urllib.request.Request(f"{self.base_url}{path}", method="POST", data=b"")
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
