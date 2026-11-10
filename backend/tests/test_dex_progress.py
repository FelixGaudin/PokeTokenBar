import json

import pytest

from app import balance as B
from app import dex_progress as D
from app.balance import Rarity
from app.pokeapi import BaseSpecies
from app.state import DexEntry, MonState, SaveState, StateStore

from .test_companion import feed, make_service, single_stage_line

UNOWN_LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)] + ["exclamation", "question"]


def owned(*ids, shiny=False):
    return {i: shiny for i in ids}


def region(key):
    return next(r for r in D.REGIONS if r.key == key)


def entry(chain, *, shiny=False, form=None, released=False, n=0):
    return DexEntry(
        id=f"e{chain[0]}-{chain[-1]}-{form}-{n}",
        base_id=chain[0],
        final_id=chain[-1],
        chain_order=list(chain),
        is_shiny=shiny,
        form=form,
        released_at="2026-09-01T00:00:00Z" if released else None,
    )


# ------------------------------------------------------------------- regions


def test_region_boundaries_map_to_the_expected_region():
    cases = {1: "kanto", 151: "kanto", 152: "johto", 251: "johto", 252: "hoenn", 386: "hoenn",
             387: "sinnoh", 493: "sinnoh", 494: "unova", 649: "unova"}
    for species_id, key in cases.items():
        assert D.region_for(species_id).key == key


def test_no_region_outside_gen_1_to_5():
    for species_id in (0, -5, 650, 10_000):
        assert D.region_for(species_id) is None


def test_region_totals_and_ranges():
    assert [r.total for r in D.REGIONS] == [151, 100, 135, 107, 156]
    assert sum(r.total for r in D.REGIONS) == D.NATIONAL.total == 649
    assert D.REGIONS[0].first == 1 and D.REGIONS[-1].last == 649
    for a, b in zip(D.REGIONS, D.REGIONS[1:], strict=False):
        assert a.last + 1 == b.first


def test_partial_completion_counts_owned_species_in_range():
    kanto, johto = D.region_completions(owned(1, 4, 7, 152, 999))[:2]
    assert (kanto.owned, kanto.total, kanto.is_complete) == (3, 151, False)
    assert johto.owned == 1


def test_a_complete_region_is_not_shiny_complete_without_every_shiny():
    johto = region("johto")
    ids = range(johto.first, johto.last + 1)
    plain = D.completion(johto, owned(*ids))
    assert plain.owned == 100 and plain.is_complete and not plain.is_shiny_complete
    almost = {i: i != johto.last for i in ids}
    c = D.completion(johto, almost)
    assert c.is_complete and c.shiny_owned == 99 and not c.is_shiny_complete


# -------------------------------------------------------------------- badges


def keys(badges):
    return [b.key for b in badges]


def test_no_badges_until_something_is_complete():
    assert D.earned_badges(owned(1, 2), 0) == []


def test_completing_a_region_earns_its_badge_only():
    kanto = owned(*range(1, 152))
    assert keys(D.earned_badges(kanto, 0)) == ["region.kanto"]
    shiny = owned(*range(1, 152), shiny=True)
    assert keys(D.earned_badges(shiny, 0)) == ["region.kanto", "regionShiny.kanto"]


def test_the_unown_badge_needs_all_28_forms():
    assert D.UNOWN_TOTAL == 28
    assert D.earned_badges({}, 27) == []
    assert keys(D.earned_badges({}, 28)) == ["unown"]


def test_national_badges_need_every_species():
    every = owned(*range(1, 650))
    got = keys(D.earned_badges(every, 0))
    assert "national" in got and "nationalShiny" not in got
    assert all(f"region.{r.key}" in got for r in D.REGIONS)
    shiny = keys(D.earned_badges(owned(*range(1, 650), shiny=True), 0))
    assert shiny[-2:] == ["national", "nationalShiny"]


def test_newly_earned_keeps_order_and_skips_announced_ones():
    earned = D.earned_badges(owned(*range(1, 650)), 0)
    assert D.newly_earned(earned, []) == earned
    assert "region.kanto" not in keys(D.newly_earned(earned, ["region.kanto"]))
    assert D.newly_earned(earned, keys(earned)) == []


def test_badge_keys_are_stable():
    """They are written to the save."""
    kanto = region("kanto")
    badges = [
        D.DexBadge("region", kanto),
        D.DexBadge("regionShiny", kanto),
        D.DexBadge("unown"),
        D.DexBadge("national"),
        D.DexBadge("nationalShiny"),
    ]
    assert keys(badges) == ["region.kanto", "regionShiny.kanto", "unown", "national",
                            "nationalShiny"]
    assert badges[0].title == "🏅 Kanto Dex complete!"
    assert badges[1].body == "You've caught a shiny of every Pokémon in Kanto!"


# ----------------------------------------------------------------- ownership


def test_every_chain_member_counts_released_ones_too():
    state = SaveState(dex=[entry([1, 2, 3]), entry([4], released=True)])
    assert set(D.owned_species(state)) == {1, 2, 3, 4}


def test_an_unrevealed_shiny_ditto_does_not_count_as_shiny():
    state = SaveState(
        active=MonState(base_id=10, current_id=10, path_ids=[10], is_shiny=True, ditto_disguise=10)
    )
    assert D.owned_species(state) == {10: False}


def test_forms_count_unown_letters_without_inflating_species():
    state = SaveState(
        dex=[entry([201], form=f, n=i) for i, f in enumerate(UNOWN_LETTERS[:27])]
        + [entry([351], form=f) for f in ("normal", "sunny", "rainy", "snowy")]
    )
    assert D.unown_forms(state) == 27
    assert "unown" not in keys(D.state_badges(state))
    state.active = MonState(base_id=201, current_id=201, path_ids=[201], form="question")
    assert D.unown_forms(state) == 28
    assert "unown" in keys(D.state_badges(state))
    johto, hoenn = D.region_completions(D.owned_species(state))[1:3]
    assert (johto.owned, hoenn.owned) == (1, 1)
    assert D.completion(D.NATIONAL, D.owned_species(state)).owned == 2


# ---------------------------------------------------------------- announcing


def test_a_fresh_save_is_seeded_with_nothing_to_announce(tmp_path):
    svc = make_service(tmp_path, {})
    assert svc.state.dex_completions_seeded
    assert svc.state.notified_dex_completions == []
    assert not (tmp_path / "state.json").exists(), "seeding alone writes no file"


def test_completions_from_before_the_feature_are_seeded_silently(tmp_path):
    (tmp_path / "state.json").write_text(
        SaveState(dex=[entry(list(range(1, 152)))]).model_dump_json()
    )
    svc = make_service(tmp_path, {})
    assert svc.state.notified_dex_completions == ["region.kanto"]
    svc.save()
    assert svc.recent_events() == []
    on_disk = json.loads((tmp_path / "state.json").read_text())
    assert on_disk["dex_completions_seeded"] is True


async def test_a_hatch_that_completes_a_region_is_announced_once(tmp_path):
    (tmp_path / "state.json").write_text(
        SaveState(dex=[entry(list(range(152, 251)))]).model_dump_json()
    )
    svc = make_service(
        tmp_path,
        {251: single_stage_line(251, Rarity.COMMON)},
        index=[BaseSpecies(251, 45)],
    )
    assert svc.state.notified_dex_completions == []
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active is not None and svc.state.active.current_id == 251

    completions = [e for e in svc.recent_events() if e.kind == "dex_completion"]
    assert [e.badge for e in completions] == ["region.johto"]
    assert completions[0].title == "🏅 Johto Dex complete!"
    assert "region.johto" in svc.state.notified_dex_completions
    svc.save()
    assert len([e for e in svc.recent_events() if e.kind == "dex_completion"]) == 1


@pytest.mark.parametrize(("lower", "expected"), [(1, ["region.kanto"]), (152, [])])
def test_an_imported_legacy_save_is_seeded_and_persisted(tmp_path, lower, expected):
    svc = make_service(tmp_path, {})
    legacy = json.loads(SaveState(dex=[entry(list(range(lower, 251)))]).model_dump_json())
    del legacy["dex_completions_seeded"], legacy["notified_dex_completions"]
    svc.store.replace(SaveState.model_validate(legacy))
    assert not svc.state.dex_completions_seeded
    svc.reset_after_import()
    assert svc.state.dex_completions_seeded
    assert svc.state.notified_dex_completions == expected
    assert json.loads((tmp_path / "state.json").read_text())["dex_completions_seeded"] is True


def test_a_damaged_announcement_list_loads_as_empty():
    state = SaveState.model_validate({"notified_dex_completions": "oops",
                                      "dex_completions_seeded": "yes"})
    assert state.notified_dex_completions == []
    assert state.dex_completions_seeded is False


def test_the_store_round_trips_the_announcement_history(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.state.notified_dex_completions = ["unown", "region.kanto"]
    store.state.dex_completions_seeded = True
    store.save()
    again = StateStore(tmp_path / "state.json").state
    assert again.notified_dex_completions == ["region.kanto", "unown"]
    assert again.dex_completions_seeded
