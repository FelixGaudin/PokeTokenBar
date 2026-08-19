import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
            daily_history=[],
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
