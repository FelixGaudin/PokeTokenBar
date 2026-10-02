from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import balance as B
from app.balance import ItemKind, Rarity
from app.config import Settings
from app.runtime import AppRuntime
from app.state import DexEntry

from .test_companion import StubPoke, three_stage_line
from .test_game_rules import slowpoke


def settings(tmp_path: Path, **kw) -> Settings:
    (tmp_path / "claude" / "projects").mkdir(parents=True)
    return Settings(
        data_dir=tmp_path / "data",
        claude_roots=[tmp_path / "claude" / "projects"],
        credentials_file=tmp_path / "claude" / ".credentials.json",
        poll_interval=60,
        limits_enabled=False,
        limits_interval=300,
        timezone=ZoneInfo("UTC"),
        **kw,
    )


@pytest.fixture
def rt(tmp_path) -> AppRuntime:
    runtime = AppRuntime(settings(tmp_path))
    stub = StubPoke({1: three_stage_line()})
    stub.details[3] = slowpoke()
    stub.details[2] = slowpoke()
    runtime.poke = stub
    runtime.companion.poke = stub
    return runtime


async def test_the_full_state_view_builds_with_an_active_pokemon_and_a_dex(rt):
    await rt.refresh()
    state = rt.store.state
    state.used_since_install = 20_000_000_000
    state.egg_usage = B.EGG_HATCH_THRESHOLD
    await rt.companion._hatch()
    state.dex.append(
        DexEntry(
            id="old",
            base_id=1,
            final_id=3,
            chain_order=[1, 2, 3],
            rarity=Rarity.COMMON,
            caught_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            is_shiny=True,
            names={"3": {"en": "Mon3", "fr": "Monfr"}},
        )
    )
    state.inventory[ItemKind.RARE_CANDY.value] = 3
    view = rt.build_state()

    assert view.collection.catch_log[0].is_active
    species = {s.species_id: s for s in view.collection.pokedex}
    assert set(species) == {1, 2, 3}
    assert species[1].is_raising and species[1].has_normal and species[1].is_shiny
    assert species[3].is_shiny and not species[3].has_normal
    assert "Monfr" in species[3].search_names
    candy = next(b for b in view.bag if b.kind == "rareCandy")
    assert candy.max_use == 3 and len(candy.previews) == 3
    assert all(e.buyable for e in view.shop.eggs)
    assert view.companion.active.level == 5


async def test_eggs_are_locked_during_the_egg_stage(rt):
    view = rt.build_state()
    assert all(not e.buyable and e.locked_reason for e in view.shop.eggs)


async def test_detail_lists_final_form_individuals_only(rt):
    rt.store.state.dex.append(
        DexEntry(id="g", base_id=1, final_id=3, chain_order=[1, 2, 3], rarity=Rarity.COMMON)
    )
    from app.state import migrate_profiles

    migrate_profiles(rt.store.state)
    detail = await rt.pokemon_detail(3)
    assert [i.id for i in detail.individuals] == ["g"]
    ind = detail.individuals[0]
    assert ind.level == 100 and ind.ability_name in {"oblivious", "own-tempo", "regenerator"}
    assert len(ind.stats) == 6 and ind.stat_scale >= 300
    assert (await rt.pokemon_detail(2)).individuals == []


async def test_refresh_records_the_ledger_and_takes_a_snapshot(rt, tmp_path):
    rt.store.state.used_since_install = 1
    await rt.refresh()
    assert rt.store.state.token_ledger_since is not None
    assert len(rt.store.list_snapshots()) == 1
    recap = rt.recap("week", 0)
    assert len(recap.buckets) == 7


def test_account_folders_are_discovered_and_scanned(tmp_path):
    work = tmp_path / "accounts" / ".claude-work"
    work.mkdir(parents=True)
    (work / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "me@work.example"}}')
    (tmp_path / "accounts" / ".claude-old").mkdir()
    (tmp_path / "accounts" / ".claude-old" / ".claude.json").write_text('{"oauthAccount": null}')
    rt = AppRuntime(settings(tmp_path, accounts_root=tmp_path / "accounts"))
    rt._sync_account_folders()
    assert [a.fallback_title for a in rt.extra_accounts] == [".claude-work"]
    assert rt.extra_accounts[0].title == "me@work.example"
    assert work / "projects" in rt.usage.roots


def test_snapshots_keep_the_newest_ten(tmp_path):
    from datetime import timedelta

    from app.state import StateStore

    store = StateStore(tmp_path / "state.json")
    base = datetime(2026, 9, 1, tzinfo=timezone.utc).astimezone()
    for n in range(15):
        store.state.used_since_install = n
        store.create_snapshot(base + timedelta(hours=n))
    snaps = store.list_snapshots()
    assert len(snaps) == 10
    assert snaps[0].lifetime_tokens == 14 and snaps[-1].lifetime_tokens == 5


def test_auto_snapshots_wait_twelve_hours_and_need_progress(tmp_path):
    from datetime import timedelta

    from app.state import StateStore

    store = StateStore(tmp_path / "state.json")
    now = datetime(2026, 9, 1, tzinfo=timezone.utc).astimezone()
    assert not store.auto_snapshot(now), "nothing to keep yet"
    store.state.used_since_install = 1
    assert store.auto_snapshot(now)
    assert not store.auto_snapshot(now + timedelta(hours=1))
    assert store.auto_snapshot(now + timedelta(hours=13))


def test_a_corrupt_save_is_moved_aside_and_recovered_from_a_snapshot(tmp_path):
    from app.state import StateStore

    store = StateStore(tmp_path / "state.json")
    store.state.used_since_install = 77_777
    store.save()
    store.create_snapshot()
    (tmp_path / "state.json").write_text("{not json")
    recovered = StateStore(tmp_path / "state.json")
    assert recovered.state.used_since_install == 77_777
    assert (tmp_path / "state.json.corrupt").read_text() == "{not json"


def test_a_save_that_fails_validation_is_kept_not_overwritten(tmp_path):
    from app.state import StateStore

    (tmp_path / "state.json").write_text('{"dex": "not a list"}')
    StateStore(tmp_path / "state.json").save()
    assert (tmp_path / "state.json.corrupt").read_text() == '{"dex": "not a list"}'


def _session(root: Path, name: str, msg: str, out: int) -> None:
    import json

    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = {
        "type": "assistant",
        "timestamp": ts,
        "requestId": msg,
        "message": {"id": msg, "model": "claude-opus-5", "usage": {"output_tokens": out}},
    }
    (root / name).write_text(json.dumps(line) + "\n")


async def test_an_account_folder_that_drops_out_and_returns_is_not_credited_twice(tmp_path):
    work = tmp_path / "accounts" / ".claude-work"
    work.mkdir(parents=True)
    (work / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "me@work.example"}}')
    rt = AppRuntime(settings(tmp_path, accounts_root=tmp_path / "accounts"))
    _session(tmp_path / "claude" / "projects" / "p", "a.jsonl", "m1", 1000)
    _session(work / "projects" / "p", "b.jsonl", "m2", 600)
    await rt.refresh()  # seeds the baseline
    _session(tmp_path / "claude" / "projects" / "p", "c.jsonl", "m3", 100)
    await rt.refresh()
    assert rt.store.state.used_since_install == 100

    # The folder's login file is unreadable for one refresh, then comes back.
    (work / ".claude.json").write_text("{half-written")
    await rt.refresh()
    (work / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "me@work.example"}}')
    await rt.refresh()
    assert rt.store.state.used_since_install == 100


def test_duplicate_labels_get_distinct_ids(tmp_path):
    from app.accounts import discover

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    found = discover([("work", a), ("work", b)], None, [])
    assert len({f.id for f in found}) == 2


def test_recap_survives_mixed_naive_dates_and_huge_offsets(rt):
    from app.state import sanitize

    rt.store.state.dex = [
        DexEntry(id="n", base_id=1, final_id=3, caught_at=datetime(2026, 9, 28, 10)),
        DexEntry(
            id="a", base_id=1, final_id=3, caught_at=datetime(2026, 9, 28, 11, tzinfo=timezone.utc)
        ),
    ]
    sanitize(rt.store.state)
    assert all(e.caught_at.tzinfo is not None for e in rt.store.state.dex)
    rt.recap("week", 0)
    rt.recap("year", -100_000)


def test_only_session_and_all_model_weekly_windows_pay_out(rt):
    from app.limits import LimitStatus, LimitWindow

    rt.default_account.status = LimitStatus(
        windows=[
            LimitWindow("session", "5-hour", "session", 100.0),
            LimitWindow("weekly_all", "Weekly", "weekly", 100.0),
            LimitWindow("weekly_opus", "Weekly (Opus)", "weekly_scoped", 100.0),
            LimitWindow("monthly:all", "Monthly", "monthly", 100.0),
        ]
    )
    keys = [w.key for w in rt._candy_windows([rt.default_account])]
    assert keys == ["session", "weekly_all"]


async def test_every_selectable_candy_count_has_a_preview(rt):
    from app import runtime as R

    await test_the_full_state_view_builds_with_an_active_pokemon_and_a_dex(rt)
    rt.store.state.inventory[ItemKind.RARE_CANDY.value] = 500
    rt.store.state.active.rarity = Rarity.LEGENDARY
    rt.companion.set_growth_difficulty(2.0)
    candy = next(b for b in rt.build_state().bag if b.kind == "rareCandy")
    assert candy.max_use == R.MAX_CANDY_PREVIEWS
    assert len(candy.previews) == candy.max_use
