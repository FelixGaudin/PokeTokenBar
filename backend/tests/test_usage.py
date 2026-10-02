import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.readers import claude_code as cc
from app.usage import UsageService, enrichment_scan_start, start_of_month, start_of_week

TZ = ZoneInfo("UTC")


def _line(msg_id, request_id, ts, *, out=100, inp=10, cw=0, cr=0, model="claude-opus-5"):
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "requestId": request_id,
            "message": {
                "id": msg_id,
                "model": model,
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_creation_input_tokens": cw,
                    "cache_read_input_tokens": cr,
                },
            },
        }
    )


def test_parses_assistant_line():
    entry = cc.parse_line(_line("m1", "r1", "2026-08-19T10:00:00.000Z", cw=5, cr=7), TZ)
    assert entry is not None
    assert entry.id == "m1|r1"
    assert (entry.input, entry.output, entry.cache_write, entry.cache_read) == (10, 100, 5, 7)
    assert entry.total == 122
    assert entry.local_day == "2026-08-19"


def test_ignores_non_assistant_and_malformed_lines():
    assert cc.parse_line('{"type":"user","message":{}}', TZ) is None
    assert cc.parse_line("not json at all", TZ) is None
    # Missing usage, missing timestamp, and explicit nulls all mean "no entry".
    assert cc.parse_line('{"type":"assistant","message":{"id":"a"}}', TZ) is None
    assert cc.parse_line('{"type":"assistant","timestamp":null,"message":{"usage":{}}}', TZ) is None


def test_null_token_value_reads_as_zero_not_missing():
    entry = cc.parse_line(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T10:00:00Z",
                "requestId": "r",
                "message": {"id": "m", "model": "x", "usage": {"input_tokens": None, "output_tokens": 4}},
            }
        ),
        TZ,
    )
    assert entry is not None and entry.input == 0 and entry.output == 4


def test_absurd_token_value_is_clamped_not_fatal():
    entry = cc.parse_line(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T10:00:00Z",
                "requestId": "r",
                "message": {"id": "m", "model": "x", "usage": {"output_tokens": 1e30}},
            }
        ),
        TZ,
    )
    assert entry is not None
    assert entry.output == cc.MAX_PARSED_TOKEN_VALUE


def test_dedupe_keeps_the_largest_total():
    """Streaming re-logs a growing message; the completed one must win."""
    partial = cc.parse_line(_line("m1", "r1", "2026-08-19T10:00:00Z", out=50), TZ)
    complete = cc.parse_line(_line("m1", "r1", "2026-08-19T10:00:00Z", out=900), TZ)
    assert partial and complete
    kept = cc.dedupe_keep_max([complete, partial])
    assert len(kept) == 1
    assert kept[0].output == 900
    # Order must not matter.
    assert cc.dedupe_keep_max([partial, complete])[0].output == 900


def test_scan_start_absorbs_month_and_midnight_boundaries():
    """On the 1st, this week starts in the previous month — the floor must reach back."""
    now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
    floor = enrichment_scan_start(now, TZ)
    assert floor <= now - timedelta(hours=5)
    assert floor.date() <= start_of_week(now.date())
    assert floor.date() <= start_of_month(now.date())


def _write_session(root, name, lines):
    path = root / name
    path.write_text("\n".join(lines) + "\n")
    return path


def test_snapshot_aggregates_windows_and_dedupes_across_files(tmp_path):
    root = tmp_path / "projects" / "proj"
    root.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # The same turn appears in two files (session resume) and must be counted once.
    _write_session(root, "a.jsonl", [_line("m1", "r1", today, out=1000)])
    _write_session(root, "b.jsonl", [_line("m1", "r1", today, out=1000), _line("m2", "r2", today, out=500)])

    svc = UsageService([tmp_path / "projects"], TZ)
    snap = svc.snapshot(now=now)

    assert snap.today.output == 1500
    assert snap.today.total_tokens == 1500 + 20  # two entries' input
    assert snap.block is not None
    assert snap.block.total_tokens == snap.today.total_tokens


def test_burn_tier_thresholds():
    from app.usage import BlockUsage, PeriodUsage, UsageSnapshot

    def tier(tpm):
        snap = UsageSnapshot(
            today_date="2026-08-19",
            today=PeriodUsage(0, 0.0),
            week=PeriodUsage(0, 0.0),
            month=PeriodUsage(0, 0.0),
            block=BlockUsage("s", "e", 0, 0.0, tpm),
            month_daily=[],
            scanned_files=0,
            generated_at=datetime.now(timezone.utc),
        )
        return snap.burn_tier

    assert tier(0) == "idle"
    assert tier(1_000) == "idle"
    assert tier(1_001) == "normal"
    assert tier(99_999) == "normal"
    assert tier(100_000) == "fast"
    assert tier(399_999) == "fast"
    assert tier(400_000) == "blazing"


def test_snapshot_reports_a_missing_root_rather_than_a_confident_zero(tmp_path):
    """An all-zero reading must be distinguishable from "nothing to read"."""
    svc = UsageService([tmp_path / "does-not-exist"], TZ)
    snap = svc.snapshot()
    assert not snap.has_source
    assert snap.missing_roots == [str(tmp_path / "does-not-exist")]
    assert snap.present_roots == []
    assert snap.total_files == 0
    assert snap.today.total_tokens == 0


def test_an_existing_but_empty_root_counts_as_a_source(tmp_path):
    """A fresh Claude Code install legitimately has zero tokens today."""
    root = tmp_path / "projects"
    root.mkdir()
    snap = UsageService([root], TZ).snapshot()
    assert snap.has_source
    assert snap.present_roots == [str(root)]
    assert snap.total_files == 0


def test_file_count_reflects_what_was_seen(tmp_path):
    root = tmp_path / "projects" / "p"
    root.mkdir(parents=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name in ("a.jsonl", "b.jsonl", "c.jsonl"):
        _write_session(root, name, [_line(f"m{name}", f"r{name}", now)])
    snap = UsageService([tmp_path / "projects"], TZ).snapshot()
    assert snap.total_files == 3
    assert snap.has_source


def _cost_state(by_model):
    return json.dumps(
        {"type": "cost-state", "modelUsage": {m: {"costUSD": c} for m, c in by_model.items()}}
    )


def _bucket(entries):
    b = cc.Bucket()
    for e in entries:
        b.add(e)
    return b


def _parse(tmp_path, lines):
    return cc.parse_file(_write_session(tmp_path, "s.jsonl", lines), TZ)


def test_negative_token_counts_read_as_zero():
    entry = cc.parse_line(_line("m", "r", "2026-08-19T10:00:00Z", out=-50, inp=-1), TZ)
    assert entry is not None and entry.output == 0 and entry.input == 0


def test_reported_cost_prices_an_unpriced_model(tmp_path):
    ts = "2026-09-15T12:00:00Z"
    entries = _parse(
        tmp_path,
        [
            _line("a", "r-a", ts, inp=100, out=50, model="claude-future-unpriced-model"),
            _cost_state({"claude-future-unpriced-model[1m]": 12.5}),
        ],
    )
    b = _bucket(entries)
    assert b.cost == pytest.approx(12.5)
    assert b.coverage.reported and not b.coverage.unknown


def test_context_variants_pool_on_the_base_model(tmp_path):
    entries = _parse(
        tmp_path,
        [
            _line("a", "r-a", "2026-09-15T12:00:00Z"),
            _cost_state({"claude-opus-5[1m]": 4, "claude-opus-5": 1}),
        ],
    )
    assert _bucket(entries).cost == pytest.approx(5)


def test_reported_cost_overrides_the_table_and_the_last_ledger_wins(tmp_path):
    ts = "2026-09-15T12:00:00Z"
    haiku = "claude-haiku-4-5-20251001"
    entries = _parse(
        tmp_path,
        [
            _line("a", "r-a", ts, inp=1_000_000, out=0, model=haiku),
            _cost_state({haiku: 1}),
            _line("b", "r-b", ts, inp=10, out=0, model=haiku),
            _cost_state({haiku: 9}),
        ],
    )
    assert _bucket(entries).cost == pytest.approx(9)


def test_reported_cost_splits_across_days_by_tokens(tmp_path):
    entries = _parse(
        tmp_path,
        [
            _line("a", "r-a", "2026-09-15T12:00:00Z", inp=300, out=0),
            _line("b", "r-b", "2026-09-16T12:00:00Z", inp=100, out=0),
            _cost_state({"claude-opus-5": 10}),
        ],
    )
    by_day = {e.local_day: e.explicit_cost for e in entries}
    assert by_day == {"2026-09-15": pytest.approx(7.5), "2026-09-16": pytest.approx(2.5)}


def test_unattributable_reported_cost_is_dropped(tmp_path):
    entries = _parse(
        tmp_path,
        [
            _line("a", "r-a", "2026-09-15T12:00:00Z"),
            _cost_state({"claude-opus-5": 2, "claude-haiku-4-5-20251001": 98}),
        ],
    )
    assert _bucket(entries).cost == pytest.approx(2)


def test_zero_token_entries_do_not_absorb_reported_cost(tmp_path):
    entries = _parse(
        tmp_path,
        [
            _line("z", "r-z", "2026-09-15T12:00:00Z", inp=0, out=0),
            _line("a", "r-a", "2026-09-15T12:00:00Z", inp=500, out=0),
            _cost_state({"claude-opus-5": 6}),
        ],
    )
    assert _bucket(entries).cost == pytest.approx(6)
    assert [e.explicit_cost for e in entries if e.total == 0] == [None]


def test_without_a_ledger_the_table_prices_or_reports_unavailable(tmp_path):
    ts = "2026-09-15T12:00:00Z"
    known = _bucket([cc.parse_line(_line("a", "r", ts, inp=1_000_000, out=0,
                                         model="claude-haiku-4-5-20251001"), TZ)])
    assert known.cost == pytest.approx(1.0)
    assert known.coverage.estimated and not known.coverage.unknown

    unknown = _bucket([cc.parse_line(_line("b", "r", ts, inp=1_000_000, out=0,
                                           model="claude-not-a-model"), TZ)])
    assert unknown.cost == 0 and unknown.coverage.unknown and not unknown.coverage.has_known


def test_an_explicit_zero_cost_is_a_real_zero():
    e = cc.parse_line(_line("a", "r", "2026-09-15T12:00:00Z", model="claude-opus-5"), TZ)
    e.explicit_cost, e.cost_is_estimate = 0.0, False
    b = _bucket([e])
    assert b.cost == 0 and b.coverage.reported and not b.coverage.estimated


def test_a_zero_token_row_adds_no_coverage():
    ts = "2026-09-15T12:00:00Z"
    b = _bucket(
        [
            cc.parse_line(_line("a", "r", ts, inp=0, out=0, model="claude-opus-5"), TZ),
            cc.parse_line(_line("b", "r", ts, model="claude-not-a-model"), TZ),
        ]
    )
    assert b.coverage.unknown and not b.coverage.has_known


def test_a_forked_replay_keeps_the_original_time():
    """A fork replays old turns under a new timestamp; they must stay on their day."""
    source = cc.parse_line(_line("m", "r", "2026-09-10T05:00:00Z"), TZ)
    fork = cc.parse_line(_line("m", "r", "2026-09-15T15:00:00Z"), TZ)
    for order in ([source, fork], [fork, source]):
        kept = cc.dedupe_keep_max(order)
        assert len(kept) == 1
        assert kept[0].local_day == "2026-09-10"


def test_month_series_is_dense_and_sums_to_the_month(tmp_path):
    root = tmp_path / "projects" / "p"
    root.mkdir(parents=True)
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    _write_session(
        root,
        "a.jsonl",
        [
            # Last month's turn in a file that crossed the boundary must stay out.
            _line("old", "r0", "2026-08-31T12:00:00Z", out=999),
            _line("m1", "r1", "2026-09-02T12:00:00Z", out=100),
            _line("m2", "r2", "2026-09-05T08:00:00Z", out=300),
        ],
    )
    snap = UsageService([tmp_path / "projects"], TZ).snapshot(now=now)
    assert [d.date for d in snap.month_daily] == [f"2026-09-0{i}" for i in range(1, 6)]
    assert [d.total_tokens for d in snap.month_daily] == [0, 110, 0, 0, 310]
    assert sum(d.total_tokens for d in snap.month_daily) == snap.month.total_tokens
    assert sum(d.cost for d in snap.month_daily) == pytest.approx(snap.month.cost)


def test_recent_days_reach_back_past_the_month_start(tmp_path):
    """The last day of a month must still reach the recap ledger after midnight."""
    root = tmp_path / "projects" / "p"
    root.mkdir(parents=True)
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)  # a Thursday
    _write_session(
        root,
        "a.jsonl",
        [
            _line("late", "r1", "2026-09-30T23:00:00Z", out=700),
            _line("new", "r2", "2026-10-01T08:00:00Z", out=100),
        ],
    )
    snap = UsageService([tmp_path / "projects"], TZ).snapshot(now=now)
    recent = dict(snap.recent_daily)
    assert recent["2026-09-30"] == 710
    assert recent["2026-10-01"] == 110
    assert min(recent) == "2026-09-28", "from the start of the week"
