import json
import time

import pytest

from app import limits as L
from app.pricing import cost, rate_for

MILLION = 1_000_000


def test_published_rates_for_current_models():
    """Input / output / cache-write / cache-read, in USD per million tokens."""
    expected = {
        "claude-opus-5": (5, 25, 6.25, 0.5),
        "claude-opus-4-8": (5, 25, 6.25, 0.5),
        "claude-sonnet-5": (3, 15, 3.75, 0.3),
        "claude-sonnet-4-6": (3, 15, 3.75, 0.3),
        "claude-haiku-4-5": (1, 5, 1.25, 0.1),
        "claude-fable-5": (10, 50, 12.5, 1.0),
    }
    for model, (inp, out, cw, cr) in expected.items():
        r = rate_for(model)
        assert r.input * MILLION == pytest.approx(inp)
        assert r.output * MILLION == pytest.approx(out)
        assert r.cache_write * MILLION == pytest.approx(cw)
        assert r.cache_read * MILLION == pytest.approx(cr)


def test_cache_multipliers_hold():
    """Cache write is 1.25x input (5-minute TTL); cache read is 0.1x input."""
    for model in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5"):
        r = rate_for(model)
        assert r.cache_write == pytest.approx(r.input * 1.25)
        assert r.cache_read == pytest.approx(r.input * 0.1)


def test_family_fallback_prices_unknown_point_releases():
    assert rate_for("claude-opus-9-9").input == rate_for("claude-opus-5").input
    assert rate_for("claude-sonnet-9").output == rate_for("claude-sonnet-5").output


def test_subscription_billed_sources_price_at_zero():
    assert rate_for("grok-4").input == 0
    assert rate_for("antigravity/claude-sonnet-4-6").input == 0
    assert rate_for("totally-unknown-model").input == 0


def test_cost_arithmetic():
    # 1M input, 1M output on Opus: $5 + $25.
    assert cost("claude-opus-5", input=MILLION, output=MILLION, cache_write=0, cache_read=0) == pytest.approx(30.0)
    # Cache-heavy turn: 22.02M reads at $0.50/M is a little over $11.
    assert cost("claude-opus-5", input=0, output=0, cache_write=0, cache_read=22_020_000) == pytest.approx(11.01)


def _creds(**oauth):
    base = {
        "accessToken": "sk-ant-oat01-test",
        "expiresAt": (time.time() + 3600) * 1000,
        "subscriptionType": "max",
        "rateLimitTier": "default_claude_max_20x",
    }
    base.update(oauth)
    return {"claudeAiOauth": base}


def test_reads_credential_and_formats_the_plan(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps(_creds()))
    cred = L.read_credential(path)
    assert cred.access_token == "sk-ant-oat01-test"
    assert not cred.is_expired
    assert L.plan_display(cred) == "Max 20x"


def test_plan_display_without_a_multiplier():
    cred = L.Credential("t", None, "pro", "default_claude_pro")
    assert L.plan_display(cred) == "Pro"


def test_expiry_accepts_seconds_or_milliseconds(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps(_creds(expiresAt=time.time() + 3600)))
    assert not L.read_credential(path).is_expired
    path.write_text(json.dumps(_creds(expiresAt=(time.time() - 10) * 1000)))
    assert L.read_credential(path).is_expired


def test_a_logged_out_credential_asks_for_a_relogin(tmp_path):
    path = tmp_path / "creds.json"
    # An explicit null must not be mistaken for "present".
    path.write_text(json.dumps({"claudeAiOauth": None, "mcpOAuth": {}}))
    with pytest.raises(L.LimitsUnavailable) as exc:
        L.read_credential(path)
    assert exc.value.auth_expired


def test_a_missing_file_is_reported_but_not_an_auth_problem(tmp_path):
    with pytest.raises(L.LimitsUnavailable) as exc:
        L.read_credential(tmp_path / "nope.json")
    assert not exc.value.auth_expired


def test_parses_legacy_and_scoped_limit_windows():
    cred = L.Credential("t", None, "max", "default_claude_max_20x")
    payload = {
        "five_hour": {"utilization": 62, "resets_at": "2026-08-19T18:00:00Z"},
        "seven_day": {"utilization": 31},
        "seven_day_opus": None,
        "limits": [
            {"kind": "session", "percent": 62},
            {"kind": "weekly_all", "percent": 31},
            {
                "kind": "weekly_scoped",
                "percent": 44,
                "scope": {"model": {"display_name": "Claude Opus 5"}},
            },
        ],
    }
    status = L.parse_status(payload, cred)
    keys = {w.key for w in status.windows}
    # Legacy rows win for session/weekly_all; the scoped row is added on top.
    assert "session" in keys and "weekly_all" in keys
    assert sum(1 for w in status.windows if w.utilization == 62) == 1
    scoped = next(w for w in status.windows if w.kind == "weekly_scoped")
    assert scoped.name == "Claude Opus 5"
    assert scoped.utilization == 44
    assert status.max_utilization == 62
    assert status.plan == "Max 20x"


def test_windows_without_a_percentage_are_dropped():
    cred = L.Credential("t", None, None, None)
    status = L.parse_status({"five_hour": {"resets_at": "x"}, "limits": []}, cred)
    assert status.windows == []
    assert status.max_utilization == 0.0


def test_stored_language_maps_back_to_the_short_code():
    """The UI's <select> options use short codes; PokéAPI stores ja as ja-Hrkt.

    Without this mapping the select can never match its own option value, so it
    silently falls back to showing English regardless of what is saved.
    """
    from app.runtime import short_language

    assert short_language("ja-Hrkt") == "ja"
    # Everything else is already a short code and must pass through untouched.
    for code in ("en", "ko", "es", "fr", "de"):
        assert short_language(code) == code


def test_japanese_resolves_through_pokeapi_variant_codes():
    """PokéAPI gives a species either ja-Hrkt or ja, never reliably both."""
    from app.pokeapi import resolve_name

    assert resolve_name({"ja": "チョボマキ", "en": "Shelmet"}, "ja", 616) == "チョボマキ"
    assert resolve_name({"ja-Hrkt": "チョボマキ", "en": "Shelmet"}, "ja", 616) == "チョボマキ"
    # A save written before this fix stores the long code; it must still resolve.
    assert resolve_name({"ja": "チョボマキ", "en": "Shelmet"}, "ja-Hrkt", 616) == "チョボマキ"
    # Missing translation falls back to English, then to the dex number.
    assert resolve_name({"en": "Shelmet"}, "ko", 616) == "Shelmet"
    assert resolve_name({}, "ko", 616) == "#616"


def test_other_languages_resolve_directly():
    from app.pokeapi import resolve_name

    names = {"en": "Shelmet", "ko": "쪼마리", "fr": "Escargaume", "de": "Schnuthelm"}
    assert resolve_name(names, "ko", 616) == "쪼마리"
    assert resolve_name(names, "fr", 616) == "Escargaume"
    assert resolve_name(names, "de", 616) == "Schnuthelm"


# ------------------------------------------------------- HTTP status branching

import httpx  # noqa: E402


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps(_creds()))
    return L.LimitsProvider(path)


async def test_a_429_is_a_backoff_not_an_auth_failure(tmp_path):
    """Observed in real use: hammering the endpoint returns 429, and mistaking that
    for a bad token would tell the user to re-login for no reason."""
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "120"})

    with pytest.raises(L.LimitsUnavailable) as exc:
        async with _client(handler) as c:
            await _provider(tmp_path).fetch(c)
    assert not exc.value.auth_expired
    assert exc.value.retry_after == 120
    assert "rate limited" in exc.value.reason


async def test_a_429_without_retry_after_leaves_the_caller_to_pick_a_delay(tmp_path):
    def handler(request):
        return httpx.Response(429)

    with pytest.raises(L.LimitsUnavailable) as exc:
        async with _client(handler) as c:
            await _provider(tmp_path).fetch(c)
    # None means "use your own interval" — the runtime falls back to limits_interval.
    assert exc.value.retry_after is None
    assert not exc.value.auth_expired


async def test_an_absurd_retry_after_is_capped_at_an_hour(tmp_path):
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "999999"})

    with pytest.raises(L.LimitsUnavailable) as exc:
        async with _client(handler) as c:
            await _provider(tmp_path).fetch(c)
    assert exc.value.retry_after == 3600


async def test_an_http_date_retry_after_falls_back_rather_than_crashing(tmp_path):
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    with pytest.raises(L.LimitsUnavailable) as exc:
        async with _client(handler) as c:
            await _provider(tmp_path).fetch(c)
    assert exc.value.retry_after is None


async def test_401_and_403_ask_for_a_relogin(tmp_path):
    for status in (401, 403):
        def handler(request, status=status):
            return httpx.Response(status)

        with pytest.raises(L.LimitsUnavailable) as exc:
            async with _client(handler) as c:
                await _provider(tmp_path).fetch(c)
        assert exc.value.auth_expired, status
        assert "/login" in exc.value.reason


async def test_a_200_parses_into_windows_and_sends_the_right_headers(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx.Response(
            200,
            json={"five_hour": {"utilization": 62}, "seven_day": {"utilization": 31}},
        )

    async with _client(handler) as c:
        status = await _provider(tmp_path).fetch(c)

    assert seen["auth"] == "Bearer sk-ant-oat01-test"
    assert seen["beta"] == L.OAUTH_BETA
    assert status.max_utilization == 62
    assert {w.key for w in status.windows} == {"session", "weekly_all"}
    assert status.plan == "Max 20x"


async def test_a_network_error_is_reported_without_blaming_the_token(tmp_path):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(L.LimitsUnavailable) as exc:
        async with _client(handler) as c:
            await _provider(tmp_path).fetch(c)
    assert not exc.value.auth_expired
    assert "network error" in exc.value.reason


async def test_a_non_json_body_is_reported_clearly(tmp_path):
    def handler(request):
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(L.LimitsUnavailable) as exc:
        async with _client(handler) as c:
            await _provider(tmp_path).fetch(c)
    assert "invalid JSON" in exc.value.reason


def test_limits_backoff_grows_on_consecutive_failures():
    """A flat retry keeps re-tripping a shared endpoint's rate limit."""
    from app.runtime import LIMITS_MAX_BACKOFF

    interval = 300.0
    # The runtime computes min(interval * 2**(n-1), cap) for the nth failure.
    seq = [min(interval * (2 ** (n - 1)), LIMITS_MAX_BACKOFF) for n in range(1, 8)]
    assert seq[0] == 300
    assert seq[1] == 600
    assert seq[2] == 1200
    # Capped, and never decreasing.
    assert all(b <= LIMITS_MAX_BACKOFF for b in seq)
    assert seq == sorted(seq)
    assert seq[-1] == LIMITS_MAX_BACKOFF


def test_an_explicit_retry_after_wins_over_the_backoff_curve():
    """The server's own guidance must not be overridden by our escalation."""
    resp = httpx.Response(429, headers={"Retry-After": "45"})
    assert L._retry_after_seconds(resp) == 45
