"""Coding-agent activity on the pet: the state machine, run geometry, and which of the
speech bubble, hearts and hover/event callout gets to show above the pet.

Hooks POST to the backend's /api/agent, which sanitizes and buffers the events; the pet
picks them up from its /api/state poll and runs the state machine here, on its own
monotonic clock from receipt (upstream keeps the tracker in the floating pet as well).

Pure on purpose -- no gi import -- so it is testable with plain pytest. Time is always
passed in. Coordinates are GTK's: pixels, y pointing down.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

WORKING_TIMEOUT = 600.0  # working falls back to idle after 10 minutes without news
DONE_DURATION = 6.0  # how long a done is shown
MAX_AGENTS = 8  # labels kept; a new label evicts the oldest
BOB_INTERVAL_MS = 500  # half a bob cycle
BOB_SCALE = (1.03, 0.94)  # squash about the ground line, so the feet stay put
RUN_DURATION = 0.6  # seconds, to the cursor and back
RUN_CURSOR_GAP = 16  # px between the pointer and the pet

STATES = ("working", "waiting", "done", "idle")

WAITING_TITLE = "Your agent needs you"
WAITING_DEFAULT_BODY = "It's waiting for your input."
WAITING_STATUS = "waiting"
DONE_TITLE = "Done!"
DONE_DEFAULT_BODY = "Your agent finished its task."
DONE_STATUS = "done"

ELLIPSIS = "…"

Phase = Literal["home", "away", "returning"]
HOME: Phase = "home"
AWAY: Phase = "away"
RETURNING: Phase = "returning"


# ---------------------------------------------------------------------- the tracker


@dataclass(frozen=True)
class Note:
    label: str | None
    message: str | None


@dataclass(frozen=True)
class Summary:
    done: Note | None = None
    waiting: Note | None = None
    other_waiting_count: int = 0
    working: bool = False


@dataclass(frozen=True)
class Entry:
    activity: str  # working, waiting or done; idle is never stored
    message: str | None
    deadline: float | None
    sequence: int


class AgentActivityTracker:
    """Per-label agent state. An event replaces only its own label's state.

    Without labels every event shares the key "", so a done after working ends the
    work, as a single session would. With labels, one project's done leaves another's
    work alone.
    """

    def __init__(self) -> None:
        self.agents: dict[str, Entry] = {}
        self._sequence = 0

    def apply(self, state: str, label: str | None, message: str | None, now: float) -> None:
        key = label or ""
        self.expire(now)
        if state == "idle":
            self.agents.pop(key, None)
            return
        if state == "working":
            deadline: float | None = now + WORKING_TIMEOUT
        elif state == "done":
            deadline = now + DONE_DURATION
        elif state == "waiting":
            deadline = None  # stays until that label's next event or a click
        else:
            return
        if key not in self.agents and len(self.agents) >= MAX_AGENTS:
            oldest = min(self.agents, key=lambda k: self.agents[k].sequence)
            del self.agents[oldest]
        self._sequence += 1
        kept = message if state in ("waiting", "done") else None
        self.agents[key] = Entry(state, kept, deadline, self._sequence)

    def expire(self, now: float) -> bool:
        """Drop entries whose deadline has passed. True if anything changed."""
        gone = [k for k, e in self.agents.items() if e.deadline is not None and e.deadline <= now]
        for key in gone:
            del self.agents[key]
        return bool(gone)

    @property
    def deadline(self) -> float | None:
        """The earliest deadline: the one timer to arm. None while idle or only waiting."""
        deadlines = [e.deadline for e in self.agents.values() if e.deadline is not None]
        return min(deadlines) if deadlines else None

    def dismiss_bubble(self) -> bool:
        """A click answers every question and acknowledges every result; work goes on."""
        kept = {k: e for k, e in self.agents.items() if e.activity == "working"}
        changed = len(kept) != len(self.agents)
        self.agents = kept
        return changed

    def reset(self) -> None:
        self.agents.clear()

    @property
    def summary(self) -> Summary:
        done = self._latest("done")
        waiting = self._latest("waiting")
        waiting_count = sum(1 for e in self.agents.values() if e.activity == "waiting")
        return Summary(
            done=done,
            waiting=waiting,
            other_waiting_count=max(0, waiting_count - 1),
            working=any(e.activity == "working" for e in self.agents.values()),
        )

    @property
    def activity(self) -> tuple[str, str | None]:
        """One state for the whole pet: done, then waiting, then working, then idle."""
        summary = self.summary
        if summary.done is not None:
            return "done", summary.done.message
        if summary.waiting is not None:
            return "waiting", summary.waiting.message
        return ("working", None) if summary.working else ("idle", None)

    def _latest(self, activity: str) -> Note | None:
        # Latest by event order, not by clock: overlapping dones show the newest event.
        matches = [(k, e) for k, e in self.agents.items() if e.activity == activity]
        if not matches:
            return None
        key, entry = max(matches, key=lambda item: item[1].sequence)
        return Note(key or None, entry.message)


class AgentFeed:
    """Which events of /api/state's `agent` feed are news.

    The first poll and a backend restart (new epoch) start from the latest sequence
    without replaying anything: like upstream, events from before the pet was there
    are dropped.
    """

    def __init__(self) -> None:
        self.epoch: str | None = None
        self.after: int | None = None

    def query(self) -> str:
        return "" if self.after is None else f"?agent_after={self.after}"

    def take(self, feed: Mapping[str, object] | None) -> list[dict]:
        if not isinstance(feed, Mapping):
            return []
        epoch = feed.get("epoch")
        latest = feed.get("latest_seq")
        if not isinstance(epoch, str) or not isinstance(latest, int):
            return []
        fresh: list[dict] = []
        if epoch == self.epoch and self.after is not None:
            events = feed.get("events")
            for event in events if isinstance(events, list) else []:
                if (
                    isinstance(event, dict)
                    and isinstance(event.get("seq"), int)
                    and event["seq"] > self.after
                    and event.get("state") in STATES
                ):
                    fresh.append(event)
            fresh.sort(key=lambda e: e["seq"])
        self.epoch = epoch
        self.after = latest
        return fresh


# --------------------------------------------------------------------------- bubbles


@dataclass(frozen=True)
class LimitBubble:
    alert: object


@dataclass(frozen=True)
class AgentBubble:
    status: Literal["waiting", "done"]
    label: str | None
    message: str | None
    others: int = 0


SpeechBubble = LimitBubble | AgentBubble


def bubble_for(limit_alert: object | None, summary: Summary) -> SpeechBubble | None:
    """Limit alert, then a fresh done, then the latest question. Working only bobs."""
    if limit_alert is not None:
        return LimitBubble(limit_alert)
    if summary.done is not None:
        return AgentBubble("done", summary.done.label, summary.done.message)
    if summary.waiting is not None:
        return AgentBubble(
            "waiting", summary.waiting.label, summary.waiting.message, summary.other_waiting_count
        )
    return None


def is_showing_bubble(alert_active: bool, hearts_showing: bool, summary: Summary) -> bool:
    """Speech or hearts above the pet: the hover/event callout stays out of the way."""
    return alert_active or hearts_showing or bubble_for(None, summary) is not None


def hearts_showing(alert_active: bool, summary: Summary) -> bool:
    """A stroke shows hearts unless something is being said. Working does not count."""
    return not alert_active and bubble_for(None, summary) is None


def should_pop_event(
    event_seq: int | None, last_seen_seq: int | None, pet_visible: bool, bubble_showing: bool
) -> bool:
    fresh = event_seq is not None and event_seq != last_seen_seq
    return fresh and pet_visible and not bubble_showing


def agent_title(
    label: str | None,
    unlabeled: str,
    status: str,
    count: str | None,
    fits: Callable[[str], bool],
) -> str:
    """The title on one line. The label is cut; the status and the count never are."""
    lead = label if label is not None else unlabeled
    suffix = "" if label is None else " · " + status
    if count:
        suffix += " " + count
    if fits(lead + suffix):
        return lead + suffix

    def cut(n: int) -> str:
        return lead[:n].strip() + ELLIPSIS + suffix

    low, high = 0, len(lead)  # invariant: low fits (or is the shortest cut), high does not
    while high - low > 1:
        mid = (low + high) // 2
        if fits(cut(mid)):
            low = mid
        else:
            high = mid
    # Prefer a word boundary ("needs…", not "needs y…"); folder names have none.
    space = lead.rfind(" ", 0, low)
    if space >= 0 and space >= low / 2:
        return cut(space)
    return cut(low)


def agent_bubble_content(bubble: AgentBubble, fits: Callable[[str], bool]) -> tuple[str, str]:
    """(title, body) as plain text. The body's two-line cut is left to the renderer."""
    if bubble.status == "waiting":
        count = f"+{bubble.others}" if bubble.others > 0 else None
        title = agent_title(bubble.label, WAITING_TITLE, WAITING_STATUS, count, fits)
        return title, bubble.message or WAITING_DEFAULT_BODY
    title = agent_title(bubble.label, DONE_TITLE, DONE_STATUS, None, fits)
    return title, bubble.message or DONE_DEFAULT_BODY


# ---------------------------------------------------------------------------- motion


def allows_motion(animated: bool, power_saver: bool, animations_enabled: bool) -> bool:
    """The bob and the run need the animated pet, no power saver and desktop animations."""
    return animated and not power_saver and animations_enabled


def should_bob(working: bool, motion_allowed: bool) -> bool:
    return working and motion_allowed


def runs_to_cursor(phase: Phase, motion_allowed: bool, visible: bool) -> bool:
    """A second done while away only changes the bubble; a done on the way back turns."""
    return phase != AWAY and motion_allowed and visible


def resolve_move(phase: Phase, user_dragging: bool) -> tuple[Phase, bool]:
    """(new phase, persist?) for a window move.

    The run's own frames must never become the saved home; a drag during a run cancels
    the return and is the new home.
    """
    if user_dragging:
        return HOME, True
    return phase, phase == HOME


def run_panel_origin(
    cursor: tuple[int, int],
    panel_size: tuple[int, int],
    workarea: tuple[int, int, int, int],
    gap: int = RUN_CURSOR_GAP,
) -> tuple[int, int]:
    """Top-left of the pet plus its speech bubble, just below-right of the pointer.

    Flips to the other side at a screen edge rather than covering the pointer. When the
    panel is larger than the screen the left and bottom edges win, so the pet itself (at
    the panel's bottom) stays visible.
    """
    cx, cy = cursor
    w, h = panel_size
    wx, wy, ww, wh = workarea
    fits_right = cx + gap + w <= wx + ww
    fits_below = cy + gap + h <= wy + wh
    x = cx + gap if fits_right else cx - gap - w
    y = cy + gap if fits_below else cy - gap - h
    x = max(min(x, wx + ww - w), wx)
    y = min(max(y, wy), wy + wh - h)
    return x, y


def ease_in_out(t: float) -> float:
    """Smoothstep: close enough to the cubic-bezier(0.42, 0, 0.58, 1) upstream uses."""
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def latest_event(events: Sequence[object] | None) -> dict | None:
    """The newest companion event (by seq) in /api/state's `companion.events`."""
    best: dict | None = None
    for event in events or ():
        if not isinstance(event, dict) or not isinstance(event.get("seq"), int):
            continue
        if best is None or event["seq"] > best["seq"]:
            best = event
    return best
