"""Relay for coding-agent activity events, picked up by the floating pet.

Upstream receives these through a `poketokenbar://agent` URL. Linux has no such scheme
here, so hooks POST to `/api/agent` and the pet reads new events from its regular
`/api/state` poll. The backend only cleans and buffers them; the pet runs the state
machine (working, waiting, done) on its own clock.

Labels and messages are untrusted: any local process, and anything that can reach the
port, can send them. They are sanitized here, so raw input is never stored. Lengths are
counted in code points rather than grapheme clusters: stricter, so the caps always hold,
at the price of possibly cutting inside a long emoji sequence.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections import deque
from dataclasses import dataclass

AGENT_STATES = ("working", "waiting", "done", "idle")
MAX_LABEL_LENGTH = 24
MAX_MESSAGE_LENGTH = 80
BUFFER_SIZE = 64
ZERO_WIDTH_JOINER = "‍"
ELLIPSIS = "…"


def sanitize(raw: str | None, max_length: int) -> str | None:
    """Controls become spaces, invisible format characters go, whitespace collapses."""
    if raw is None:
        return None
    kept: list[str] = []
    for ch in raw:
        category = unicodedata.category(ch)
        if category == "Cc":
            kept.append(" ")
        elif category == "Cf" and ch != ZERO_WIDTH_JOINER:
            continue  # bidi overrides, zero-width spaces; ZWJ keeps emoji sequences joined
        else:
            kept.append(ch)
    text = " ".join("".join(kept).split())
    if not text:
        return None
    if len(text) > max_length:
        text = text[: max_length - 1].strip() + ELLIPSIS
    return text


@dataclass(frozen=True)
class AgentEvent:
    seq: int
    state: str
    label: str | None
    message: str | None


class AgentRelay:
    """The latest events, in order. A new epoch per process tells clients to resync."""

    def __init__(self) -> None:
        self.epoch = uuid.uuid4().hex
        self.latest_seq = 0
        self._events: deque[AgentEvent] = deque(maxlen=BUFFER_SIZE)

    def post(self, state: str, label: str | None, message: str | None) -> AgentEvent:
        state = state.lower()
        if state not in AGENT_STATES:
            raise ValueError(f"unknown state: {state!r} (expected {', '.join(AGENT_STATES)})")
        self.latest_seq += 1
        event = AgentEvent(
            seq=self.latest_seq,
            state=state,
            label=sanitize(label, MAX_LABEL_LENGTH),
            # Only a question or a result has something to say.
            message=sanitize(message, MAX_MESSAGE_LENGTH) if state in ("waiting", "done") else None,
        )
        self._events.append(event)
        return event

    def after(self, seq: int) -> list[AgentEvent]:
        return [e for e in self._events if e.seq > seq]
