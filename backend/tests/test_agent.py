import pytest

from app.agent import BUFFER_SIZE, AgentRelay, sanitize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  line 1\n\r\tline 2  ", "line 1 line 2"),
        # A bidi override and a zero-width space vanish; a bell becomes a space.
        ("ab‮cd​ef\u0007g", "abcdef g"),
        ("family 👨‍👩‍👧", "family 👨‍👩‍👧"),
        (" \n\t ", None),
        ("", None),
        (None, None),
        ("a" * 80, "a" * 80),
    ],
)
def test_sanitize_messages(raw, expected):
    assert sanitize(raw, 80) == expected


def test_a_long_message_is_cut_without_a_space_before_the_ellipsis():
    out = sanitize("word " * 100, 80)
    assert len(out) <= 80 and out.endswith("…") and not out.endswith(" …")


def test_labels_are_capped_at_24():
    assert sanitize(" my\n project‮ ", 24) == "my project"
    assert sanitize("x" * 100, 24) == "x" * 23 + "…"


def test_states_are_checked_and_case_insensitive():
    relay = AgentRelay()
    with pytest.raises(ValueError):
        relay.post("sleeping", None, None)
    assert relay.latest_seq == 0
    assert relay.post("DONE", "api", "Build passed").state == "done"


def test_a_message_is_kept_only_for_a_question_or_a_result():
    relay = AgentRelay()
    assert relay.post("working", "api", "busy").message is None
    assert relay.post("idle", "api", "bye").message is None
    assert relay.post("waiting", "api", "Allow?").message == "Allow?"


def test_events_are_read_after_a_sequence_number():
    relay = AgentRelay()
    first = relay.post("working", "a", None)
    second = relay.post("done", "a", None)
    assert relay.after(0) == [first, second]
    assert relay.after(first.seq) == [second]
    assert relay.after(second.seq) == []


def test_the_buffer_keeps_the_newest_events():
    relay = AgentRelay()
    for _ in range(BUFFER_SIZE + 5):
        relay.post("working", None, None)
    kept = relay.after(0)
    assert len(kept) == BUFFER_SIZE
    assert kept[-1].seq == relay.latest_seq == BUFFER_SIZE + 5


def test_each_relay_has_its_own_epoch():
    assert AgentRelay().epoch != AgentRelay().epoch
