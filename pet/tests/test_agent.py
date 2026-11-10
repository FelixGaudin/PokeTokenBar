"""Agent activity: upstream's AgentActivityTests and FloatingPetAgentTests tables."""

from __future__ import annotations

from pet.agent import (
    AWAY,
    DONE_DURATION,
    HOME,
    MAX_AGENTS,
    RETURNING,
    AgentActivityTracker,
    AgentBubble,
    AgentFeed,
    LimitBubble,
    Note,
    Summary,
    agent_bubble_content,
    agent_title,
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

T0 = 1000.0


def apply(tracker: AgentActivityTracker, state: str, at: float = T0, label=None, message=None):
    tracker.apply(state, label, message, at)


# ------------------------------------------------------------------- the tracker


def test_working_expires_after_ten_minutes_without_news():
    tracker = AgentActivityTracker()
    apply(tracker, "working")
    assert tracker.deadline == T0 + 600
    assert not tracker.expire(T0 + 599)
    assert tracker.activity == ("working", None)
    assert tracker.expire(T0 + 600)
    assert tracker.activity == ("idle", None)
    assert tracker.deadline is None
    assert not tracker.expire(T0 + 10_000), "idle has nothing to expire"


def test_another_working_event_pushes_the_timeout_back():
    tracker = AgentActivityTracker()
    apply(tracker, "working")
    apply(tracker, "working", T0 + 500)
    assert not tracker.expire(T0 + 700)
    assert tracker.activity == ("working", None)


def test_done_shows_for_six_seconds():
    tracker = AgentActivityTracker()
    apply(tracker, "done", message="ok")
    assert not tracker.expire(T0 + 5.9)
    assert tracker.activity == ("done", "ok")
    assert tracker.expire(T0 + 6)
    assert tracker.activity == ("idle", None)


def test_waiting_stays_until_the_next_event_or_a_click():
    tracker = AgentActivityTracker()
    apply(tracker, "working")
    apply(tracker, "waiting", T0 + 1)
    assert tracker.deadline is None, "waiting must not inherit the working timeout"
    assert not tracker.expire(T0 + 86_400)
    assert tracker.activity == ("waiting", None)

    assert tracker.dismiss_bubble()
    assert tracker.activity == ("idle", None)

    apply(tracker, "waiting")
    apply(tracker, "working", T0 + 2)
    assert tracker.activity == ("working", None)


def test_click_dismisses_only_bubbles():
    tracker = AgentActivityTracker()
    apply(tracker, "working")
    assert not tracker.dismiss_bubble(), "a click does not stop work"
    assert tracker.activity == ("working", None)
    assert tracker.deadline is not None

    apply(tracker, "done")
    assert tracker.dismiss_bubble()
    assert tracker.activity == ("idle", None)
    assert tracker.deadline is None
    assert not tracker.dismiss_bubble()


def test_idle_event_clears_everything():
    tracker = AgentActivityTracker()
    apply(tracker, "working")
    apply(tracker, "idle")
    assert tracker.agents == {}
    assert tracker.deadline is None
    assert tracker.activity == ("idle", None)


def test_message_is_kept_only_for_bubble_states():
    tracker = AgentActivityTracker()
    apply(tracker, "working", message="ignored")
    assert tracker.agents[""].message is None
    apply(tracker, "waiting", message="kept")
    assert tracker.activity == ("waiting", "kept")


def test_each_label_keeps_its_own_state():
    tracker = AgentActivityTracker()
    apply(tracker, "working", label="web")
    apply(tracker, "done", T0 + 1, label="api")
    assert tracker.summary.working, "api's done must not end web's work"
    assert tracker.summary.done == Note("api", None)

    assert tracker.expire(T0 + 7)
    assert tracker.summary.done is None
    assert tracker.summary.working
    assert tracker.activity == ("working", None)


def test_unlabeled_events_still_share_one_agent():
    tracker = AgentActivityTracker()
    apply(tracker, "working")
    apply(tracker, "done")
    assert not tracker.summary.working, "without labels a done ends the work"
    assert len(tracker.agents) == 1


def test_waiting_outranks_working_and_counts_the_others():
    tracker = AgentActivityTracker()
    apply(tracker, "waiting", label="a", message="first")
    apply(tracker, "working", label="b")
    apply(tracker, "waiting", label="c", message="latest")
    summary = tracker.summary
    assert summary.waiting == Note("c", "latest"), "the most recent question first"
    assert summary.other_waiting_count == 1
    assert summary.working
    assert tracker.activity == ("waiting", "latest")

    apply(tracker, "working", label="c")  # c's question was answered
    assert tracker.summary.waiting == Note("a", "first")
    assert tracker.summary.other_waiting_count == 0


def test_done_overlays_a_waiting_agent_only_for_its_few_seconds():
    tracker = AgentActivityTracker()
    apply(tracker, "waiting", label="a")
    apply(tracker, "done", label="b")
    assert tracker.activity == ("done", None)
    assert tracker.summary.waiting.label == "a", "the question is still there underneath"
    tracker.expire(T0 + DONE_DURATION)
    assert tracker.activity == ("waiting", None)


def test_overlapping_dones_show_the_latest():
    tracker = AgentActivityTracker()
    apply(tracker, "done", T0, label="a", message="a")
    apply(tracker, "done", T0 + 2, label="b", message="b")
    apply(tracker, "done", T0 + 1, label="c", message="c")
    assert tracker.summary.done == Note("c", "c"), "latest event, not latest clock"
    tracker.expire(T0 + DONE_DURATION + 1)
    assert tracker.summary.done == Note("b", "b")


def test_each_label_expires_on_its_own_clock():
    tracker = AgentActivityTracker()
    apply(tracker, "working", T0, label="a")
    apply(tracker, "working", T0 + 300, label="b")
    assert tracker.deadline == T0 + 600, "the earliest deadline arms the timer"
    assert tracker.expire(T0 + 600)
    assert list(tracker.agents) == ["b"]
    assert tracker.deadline == T0 + 900


def test_click_clears_every_question_and_result_but_not_work():
    tracker = AgentActivityTracker()
    apply(tracker, "waiting", label="a")
    apply(tracker, "waiting", label="b")
    apply(tracker, "done", label="c")
    apply(tracker, "working", label="d")
    assert tracker.dismiss_bubble()
    assert list(tracker.agents) == ["d"]
    assert not tracker.dismiss_bubble()


def test_idle_clears_only_its_own_label():
    tracker = AgentActivityTracker()
    apply(tracker, "working", label="a")
    apply(tracker, "working", label="b")
    apply(tracker, "idle", label="a")
    assert list(tracker.agents) == ["b"]


def test_the_number_of_agents_is_capped():
    tracker = AgentActivityTracker()
    for index in range(50):
        apply(tracker, "working", T0 + index, label=f"agent{index}")
    assert len(tracker.agents) == MAX_AGENTS
    assert set(tracker.agents) == {f"agent{i}" for i in range(42, 50)}, "the oldest go first"

    apply(tracker, "working", T0 + 60, label="agent42")  # refresh, no eviction
    assert len(tracker.agents) == MAX_AGENTS
    apply(tracker, "waiting", T0 + 61, label="new")
    assert "agent43" not in tracker.agents, "agent42 was refreshed, so agent43 is the oldest"
    assert "agent42" in tracker.agents


def test_reset_forgets_every_agent():
    tracker = AgentActivityTracker()
    apply(tracker, "working", label="a")
    apply(tracker, "waiting", label="b")
    tracker.reset()
    assert tracker.agents == {}
    assert tracker.deadline is None


# ------------------------------------------------------------------- the feed


def feed(epoch: str, latest: int, *seqs: int, state: str = "working") -> dict:
    events = [{"seq": s, "state": state, "label": None, "message": None} for s in seqs]
    return {"epoch": epoch, "latest_seq": latest, "events": events}


def test_first_poll_drops_the_backlog_and_later_polls_ask_for_news():
    cursor = AgentFeed()
    assert cursor.query() == ""
    assert cursor.take(feed("e1", 5, 4, 5)) == [], "events from before the pet are not news"
    assert cursor.query() == "?agent_after=5"
    fresh = cursor.take(feed("e1", 7, 7, 6))
    assert [e["seq"] for e in fresh] == [6, 7], "oldest first"
    assert cursor.query() == "?agent_after=7"


def test_a_backend_restart_resyncs_without_replaying():
    cursor = AgentFeed()
    cursor.take(feed("e1", 9))
    assert cursor.take(feed("e2", 3, 1, 2, 3)) == []
    assert cursor.query() == "?agent_after=3"
    assert [e["seq"] for e in cursor.take(feed("e2", 4, 4))] == [4]


def test_the_feed_ignores_malformed_input():
    cursor = AgentFeed()
    assert cursor.take(None) == []
    assert cursor.take({"epoch": 1, "latest_seq": "x"}) == []
    assert cursor.query() == ""
    cursor.take(feed("e1", 0))
    bad = {"epoch": "e1", "latest_seq": 2, "events": [{"seq": 1, "state": "dancing"}, "x"]}
    assert cursor.take(bad) == []


# -------------------------------------------------------------------- bubbles


def test_limit_alert_outranks_agent_bubbles():
    waiting = Summary(waiting=Note("a", "x"), other_waiting_count=2)
    both = Summary(done=Note("b", None), waiting=Note("a", "x"))
    assert bubble_for("alert", waiting) == LimitBubble("alert")
    assert bubble_for("alert", Summary()) == LimitBubble("alert")
    assert bubble_for(None, waiting) == AgentBubble("waiting", "a", "x", 2)
    assert bubble_for(None, both) == AgentBubble("done", "b", None), "a done overlays a question"
    assert bubble_for(None, Summary(working=True)) is None
    assert bubble_for(None, Summary()) is None


def test_agent_speech_outranks_hearts_and_event_callouts():
    for agent in (Summary(waiting=Note("web", None)), Summary(done=Note("api", None))):
        showing = is_showing_bubble(False, False, agent)
        assert showing
        assert not hearts_showing(False, agent)
        assert not should_pop_event(2, 1, True, showing)
    working = Summary(working=True)
    assert hearts_showing(False, working), "working only bobs: it must not block stroking"
    assert is_showing_bubble(False, True, working)
    assert not is_showing_bubble(False, False, working)
    assert not hearts_showing(True, working)
    assert is_showing_bubble(True, False, Summary())


def test_events_pop_only_when_new_visible_and_unobstructed():
    assert should_pop_event(1, None, True, False), "the first event"
    assert should_pop_event(2, 1, True, False), "a newer event"
    assert not should_pop_event(1, 1, True, False), "the same event is not popped twice"
    assert not should_pop_event(None, None, True, False), "no event"
    assert not should_pop_event(2, 1, False, False), "the pet is hidden"
    assert not should_pop_event(2, 1, True, True), "speech or hearts win"


def test_latest_event_is_the_highest_seq():
    events = [{"seq": 3, "text": "c"}, {"seq": 5, "text": "e"}, {"seq": 4}, "junk", {"seq": "x"}]
    assert latest_event(events)["text"] == "e"
    assert latest_event([]) is None
    assert latest_event(None) is None


def chars(limit: int):
    return lambda text: len(text) <= limit


def test_agent_bubble_uses_defaults_without_a_message():
    fits = chars(40)
    assert agent_bubble_content(AgentBubble("waiting", None, None), fits) == (
        "Your agent needs you",
        "It's waiting for your input.",
    )
    assert agent_bubble_content(AgentBubble("done", None, "Tests pass"), fits) == (
        "Done!",
        "Tests pass",
    )
    assert agent_bubble_content(AgentBubble("done", None, None), fits)[1] == (
        "Your agent finished its task."
    )


def test_labeled_titles_name_the_agent_and_count_the_rest():
    fits = chars(40)
    assert agent_bubble_content(AgentBubble("waiting", "web", None), fits)[0] == "web · waiting"
    assert agent_bubble_content(AgentBubble("waiting", "web", None, 2), fits)[0] == (
        "web · waiting +2"
    )
    assert agent_bubble_content(AgentBubble("done", "api", None), fits)[0] == "api · done"


def test_a_long_title_is_cut_between_words_and_keeps_its_count():
    sentence = "Your coding agent is waiting for an answer from you"
    cut = agent_title(None, sentence, "", "+3", chars(30))
    assert cut.endswith("… +3"), cut
    assert len(cut) <= 30
    assert sentence.startswith(cut.removesuffix("… +3") + " "), f"cut between words: {cut}"


def test_a_long_label_is_cut_and_the_status_and_count_stay():
    wide = "W" * 24
    title = agent_bubble_content(AgentBubble("waiting", wide, None, 7), chars(26))[0]
    assert title != f"{wide} · waiting +7"
    assert title.startswith("WWW") and title.endswith("… · waiting +7"), title
    assert len(title) <= 26


def test_title_cut_is_as_long_as_possible():
    title = agent_title("abcdefghij", "", "done", None, chars(12))
    assert title == "abcd… · done"
    assert not chars(12)("abcde… · done"), "one more character must not fit"
    assert agent_title("short", "", "done", None, chars(40)) == "short · done"


# --------------------------------------------------------------------- motion


def test_only_home_or_a_drag_persists_the_origin():
    assert resolve_move(HOME, False) == (HOME, True)
    assert resolve_move(HOME, True) == (HOME, True)
    assert resolve_move(AWAY, False) == (AWAY, False), "frames of the run must not become home"
    assert resolve_move(RETURNING, False) == (RETURNING, False)
    assert resolve_move(AWAY, True) == (HOME, True), "a drag cancels the return: new home"
    assert resolve_move(RETURNING, True) == (HOME, True)


WORKAREA = (0, 0, 1440, 875)
PANEL = (180, 168)


def inside(origin, workarea, panel=PANEL) -> bool:
    x, y = origin
    wx, wy, ww, wh = workarea
    return wx <= x and wy <= y and x + panel[0] <= wx + ww and y + panel[1] <= wy + wh


def covers(origin, cursor, panel=PANEL) -> bool:
    x, y = origin
    return x <= cursor[0] < x + panel[0] and y <= cursor[1] < y + panel[1]


def test_run_lands_below_right_of_the_cursor():
    origin = run_panel_origin((600, 400), PANEL, WORKAREA, 16)
    assert origin == (616, 416)
    assert not covers(origin, (600, 400))


def test_run_flips_at_screen_edges_instead_of_covering_the_pointer():
    for cursor in ((1430, 400), (600, 860), (1435, 870), (5, 5)):
        origin = run_panel_origin(cursor, PANEL, WORKAREA, 16)
        assert inside(origin, WORKAREA), (cursor, origin)
        assert not covers(origin, cursor), (cursor, origin)
    # Flipped, not just clamped.
    assert run_panel_origin((1430, 400), PANEL, WORKAREA, 16) == (1430 - 16 - 180, 416)
    assert run_panel_origin((600, 860), PANEL, WORKAREA, 16) == (616, 860 - 16 - 168)


def test_run_stays_on_the_cursor_screen():
    secondary = (-1920, 0, 1920, 1080)
    assert inside(run_panel_origin((-10, 5), PANEL, secondary), secondary)


def test_oversized_panel_keeps_the_pet_visible():
    small = (0, 0, 300, 200)
    huge = (384, 456)
    origin = run_panel_origin((150, 100), huge, small)
    assert origin == (0, 200 - 456), "left and bottom win: the pet sits at the panel bottom"


def test_a_second_done_does_not_run_again_while_away():
    assert runs_to_cursor(HOME, True, True)
    assert runs_to_cursor(RETURNING, True, True), "a done on the way back turns the pet around"
    assert not runs_to_cursor(AWAY, True, True)
    assert not runs_to_cursor(HOME, False, True)
    assert not runs_to_cursor(HOME, True, False)


def test_motion_follows_power_saver_animations_and_the_static_pet():
    assert allows_motion(True, False, True)
    assert not allows_motion(False, False, True), "static pet"
    assert not allows_motion(True, True, True), "power saver"
    assert not allows_motion(True, False, False), "desktop animations off"
    assert should_bob(True, True)
    assert not should_bob(True, False)
    assert not should_bob(False, True), "nothing working must not loop"


def test_ease_in_out_is_clamped_and_symmetric():
    assert ease_in_out(-1) == 0
    assert ease_in_out(0) == 0
    assert ease_in_out(0.5) == 0.5
    assert ease_in_out(1) == 1
    assert ease_in_out(2) == 1
    assert abs(ease_in_out(0.25) + ease_in_out(0.75) - 1) < 1e-9
