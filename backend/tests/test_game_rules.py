import math

import pytest

from app import balance as B
from app.balance import ItemKind, Rarity
from app.companion import CandyWindow, CompanionService
from app.pokeapi import BaseSpecies, EvoLine, EvoNode
from app.profile import (
    Ability,
    LearnMethod,
    MoveInfo,
    PokemonDetails,
    PokemonProfile,
    actual_stat,
    stat_bar_scale,
)
from app.state import DexEntry, PreferenceStore, SaveState, StateStore, migrate_profiles

from .test_companion import (
    AlwaysDisguiseRNG,
    NoDisguiseRNG,
    StubPoke,
    ditto_line,
    feed,
    single_stage_line,
    three_stage_line,
)

COMMON_STAGE_0 = 125_000_000


def make(tmp_path, lines, index=None, rng=None, growth=1.0, shop=1.0) -> CompanionService:
    store = StateStore(tmp_path / "state.json")
    prefs = PreferenceStore(tmp_path / "preferences.json")
    prefs.prefs.growth_difficulty = growth
    prefs.prefs.shop_difficulty = shop
    return CompanionService(
        store, StubPoke(lines, index), rng=rng or NoDisguiseRNG(1234), prefs=prefs
    )


async def hatched(tmp_path, **kw) -> CompanionService:
    svc = make(tmp_path, {1: three_stage_line(), B.DITTO_SPECIES_ID: ditto_line()}, **kw)
    await feed(svc, 1_000)
    await feed(svc, 1_000 + svc.egg_hatch_threshold)
    assert svc.state.active is not None
    return svc


# ---------------------------------------------------------------- difficulty


def test_clamp_and_rounding():
    assert B.clamp_difficulty(0) == B.DIFFICULTY_MIN
    assert B.clamp_difficulty(-5) == B.DIFFICULTY_MIN
    assert B.clamp_difficulty(20) == B.DIFFICULTY_MAX
    for bad in (math.nan, math.inf, -math.inf):
        assert B.clamp_difficulty(bad) == 1.0
    assert B.round_half_up(2.5) == 3  # Python's round() would give 2


async def test_half_difficulty_halves_the_threshold(tmp_path):
    svc = await hatched(tmp_path, growth=0.5)
    assert svc.stage_threshold == COMMON_STAGE_0 // 2
    svc.state.active.used_at_stage = COMMON_STAGE_0 // 2 - 1
    await svc._apply_usage(0)
    assert svc.state.active.stage_index == 0
    await svc._apply_usage(1)
    assert svc.state.active.stage_index == 1


async def test_egg_hatches_at_the_scaled_threshold(tmp_path):
    svc = make(tmp_path, {1: three_stage_line()}, growth=0.5)
    await feed(svc, 1_000)
    await feed(svc, 1_000 + 2_500_000)
    assert svc.state.active is not None


async def test_shop_difficulty_scales_prices_independently(tmp_path):
    svc = make(tmp_path, {1: three_stage_line()}, growth=0.1, shop=0.5)
    svc.state.used_since_install = B.RARE_CANDY_PRICE // 2
    svc.buy_item(ItemKind.RARE_CANDY)
    assert svc.state.spent_tokens == 250_000_000
    assert svc.egg_price(None) == 500_000_000
    assert svc.egg_price(Rarity.RARE) / svc.egg_price(None) == pytest.approx(4.0)


async def test_changing_difficulty_keeps_the_earned_fraction_and_never_evolves(tmp_path):
    svc = await hatched(tmp_path)
    svc.state.active.used_at_stage = COMMON_STAGE_0 // 2
    lifetime = svc.state.used_since_install
    for d in (0.1, 2.0, 0.5, 1.0):
        svc.set_growth_difficulty(d)
        a = svc.state.active
        assert a.stage_index == 0
        assert a.used_at_stage / svc.stage_threshold == pytest.approx(0.5, abs=1e-6)
    assert svc.state.used_since_install == lifetime


async def test_lowering_difficulty_never_completes_a_stage(tmp_path):
    svc = await hatched(tmp_path)
    svc.state.active.used_at_stage = COMMON_STAGE_0 - 1
    svc.set_growth_difficulty(0.5)
    assert svc.stage_threshold - svc.state.active.used_at_stage == 1
    await svc._apply_usage(0)
    assert svc.state.active.stage_index == 0
    await svc._apply_usage(1)
    assert svc.state.active.stage_index == 1


def test_preferences_stay_out_of_the_save(tmp_path):
    prefs = PreferenceStore(tmp_path / "preferences.json")
    prefs.prefs.growth_difficulty = 0.3
    prefs.save()
    assert PreferenceStore(tmp_path / "preferences.json").prefs.growth_difficulty == 0.3
    assert "growth_difficulty" not in SaveState().model_dump()


# ---------------------------------------------------------------- repeat boost


def test_boosted_thresholds_are_half_rounded_up():
    for rarity in Rarity:
        for k in (1, 2, 3):
            for i in range(k):
                standard = B.phase_threshold(rarity, k, i)
                assert B.phase_threshold(rarity, k, i, 2) == B.round_half_up(standard / 2)
    assert B.phase_threshold(Rarity.COMMON, 3, 0, 2) == 62_500_000


async def test_a_repeat_hatch_of_a_graduated_line_grows_twice_as_fast(tmp_path):
    svc = await hatched(tmp_path)
    assert not svc.state.active.has_growth_boost
    await feed(svc, 50 * B.graduation_total(Rarity.COMMON))
    assert svc.state.collected_finals == ["1:3"]
    await feed(svc, 50 * B.graduation_total(Rarity.COMMON) + B.EGG_HATCH_THRESHOLD)
    a = svc.state.active
    assert a is not None and a.has_growth_boost
    assert svc.stage_threshold == 62_500_000


def test_legacy_saves_have_no_boost():
    s = SaveState.model_validate({"active": {"base_id": 1, "current_id": 1, "total_forms": 3}})
    assert s.active.has_growth_boost is False
    assert s.active.phase_threshold == COMMON_STAGE_0


async def test_a_boosted_disguise_reveals_at_the_boosted_threshold_and_keeps_it(tmp_path):
    svc = make(
        tmp_path,
        {1: three_stage_line(), B.DITTO_SPECIES_ID: ditto_line()},
        rng=AlwaysDisguiseRNG(7),
    )
    svc.state.collected_finals = ["1:3"]
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    a = svc.state.active
    assert a.has_growth_boost and a.ditto_disguise is not None
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD + 62_500_000)
    a = svc.state.active
    assert a.ditto_revealed and a.has_growth_boost
    assert a.used_at_stage == 0
    assert svc.stage_threshold == 1_500_000_000


# ---------------------------------------------------------------- release


async def test_a_mid_chain_release_keeps_only_reached_forms(tmp_path):
    svc = await hatched(tmp_path)
    await svc._apply_usage(COMMON_STAGE_0)
    assert svc.state.active.stage_index == 1
    svc.state.used_since_install = 5_000_000_000
    svc.buy_fresh_egg(Rarity.RARE)
    entry = svc.state.dex[0]
    assert entry.chain_order == [1, 2] and entry.final_id == 2 and entry.is_released
    assert svc.state.collected_finals == []


async def test_releasing_a_disguised_shiny_ditto_does_not_reveal_it(tmp_path):
    svc = make(tmp_path, {1: three_stage_line()}, rng=AlwaysDisguiseRNG(7))
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active.is_shiny and svc.state.active.ditto_disguise is not None
    assert not svc.is_high_value_companion, "a disguise is never precious before the reveal"
    svc.state.used_since_install = 5_000_000_000
    svc.buy_fresh_egg(None)
    assert svc.state.dex[0].is_shiny is False


def test_legacy_dex_entries_are_not_released():
    e = DexEntry.model_validate(
        {"base_id": 1, "final_id": 3, "chain_order": [1, 2, 3], "rarity": "common"}
    )
    assert e.released_at is None and not e.is_released


async def test_high_value_companions(tmp_path):
    svc = await hatched(tmp_path)
    a = svc.state.active
    assert not svc.is_high_value_companion
    a.is_shiny = True
    assert svc.is_high_value_companion
    a.is_shiny, a.rarity = False, Rarity.LEGENDARY
    assert svc.is_high_value_companion
    a.rarity = Rarity.RARE
    assert not svc.is_high_value_companion


# ---------------------------------------------------------------- bulk candy


async def test_bulk_candy_plans_and_carries_over(tmp_path):
    svc = await hatched(tmp_path)
    svc.state.active.used_at_stage = 100_000_000
    svc.state.inventory[ItemKind.RARE_CANDY.value] = 10
    assert svc.max_candy_use == 7
    plan = svc.plan_candy_use(3)
    assert plan.evolves and not plan.graduates
    assert plan.carryover == 25_000_000 and plan.discarded == 0
    lifetime = svc.state.used_since_install
    assert await svc.use_rare_candy(3) == "evolved"
    a = svc.state.active
    assert a.stage_index == 2 and a.used_at_stage == 25_000_000
    assert svc.item_count(ItemKind.RARE_CANDY) == 7
    assert svc.state.used_since_install == lifetime


async def test_bulk_candy_caps_at_graduation_and_reports_leftover(tmp_path):
    svc = make(
        tmp_path, {50: single_stage_line(rarity=Rarity.COMMON)}, index=[BaseSpecies(50, 255)]
    )
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    svc.state.active.used_at_stage = 750_000_000 - 250_000_000
    svc.state.inventory[ItemKind.RARE_CANDY.value] = 5
    assert svc.max_candy_use == 3
    plan = svc.plan_candy_use(5)
    assert plan.count == 3 and plan.graduates and plan.discarded == 50_000_000
    assert await svc.use_rare_candy(5) == "graduated"
    assert svc.item_count(ItemKind.RARE_CANDY) == 2
    assert len(svc.state.dex) == 1


async def test_invalid_candy_counts_do_nothing(tmp_path):
    svc = await hatched(tmp_path)
    svc.state.inventory[ItemKind.RARE_CANDY.value] = 1
    for bad in (0, -1):
        assert svc.plan_candy_use(bad) is None
        with pytest.raises(ValueError):
            await svc.use_rare_candy(bad)
    assert svc.item_count(ItemKind.RARE_CANDY) == 1
    assert await svc.use_rare_candy(10**9) == "progressed"
    assert svc.state.active.used_at_stage == 100_000_000


async def test_candy_on_a_disguise_stops_at_the_reveal(tmp_path):
    svc = make(
        tmp_path,
        {1: three_stage_line(), B.DITTO_SPECIES_ID: ditto_line()},
        rng=AlwaysDisguiseRNG(7),
    )
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    svc.state.active.used_at_stage = 100_000_000
    svc.state.inventory[ItemKind.RARE_CANDY.value] = 5
    await svc.use_rare_candy(5)
    assert svc.item_count(ItemKind.RARE_CANDY) == 4
    a = svc.state.active
    assert a.ditto_revealed and a.used_at_stage == 75_000_000


# ---------------------------------------------------------------- candy epochs


def test_a_new_reset_marker_rearms_the_grant():
    tier: dict[str, int] = {}
    epochs: dict[str, str] = {}
    w1 = CandyWindow("session", "5-hour", "session", 100.0, epoch="2026-09-17T10:00:00Z")
    assert len(CompanionService.evaluate_candy_grants([w1], tier, epochs)) == 1
    assert CompanionService.evaluate_candy_grants([w1], tier, epochs) == []
    w2 = CandyWindow("session", "5-hour", "session", 100.0, epoch="2026-09-17T15:00:00Z")
    assert len(CompanionService.evaluate_candy_grants([w2], tier, epochs)) == 1
    assert epochs["session"] == "2026-09-17T15:00:00Z"


def test_a_first_epoch_on_an_old_save_does_not_regrant():
    tier = {"session": 1}
    epochs: dict[str, str] = {}
    w = CandyWindow("session", "5-hour", "session", 100.0, epoch="E1")
    assert CompanionService.evaluate_candy_grants([w], tier, epochs) == []
    assert epochs == {"session": "E1"} and tier == {"session": 1}


async def test_additional_accounts_pay_only_once_armed(tmp_path):
    svc = make(tmp_path, {1: three_stage_line()})
    svc.grant_candies([], limits_ready=True)  # seed
    full = CandyWindow("acct:session", "5-hour (work)", "session", 100.0, needs_arming=True)
    assert svc.grant_candies([full], limits_ready=True) == []
    low = CandyWindow("acct:session", "5-hour (work)", "session", 20.0, needs_arming=True)
    svc.grant_candies([low], limits_ready=True)
    assert len(svc.grant_candies([full], limits_ready=True)) == 1


# ---------------------------------------------------------------- hatch delay and odds


async def test_a_failed_hatch_is_flagged_then_cleared(tmp_path):
    svc = make(tmp_path, {}, index=[BaseSpecies(88, 190)])
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active is None and svc.hatch_retry_delayed
    svc.poke.lines[88] = three_stage_line(base=88)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active is not None and not svc.hatch_retry_delayed


async def test_the_hatch_event_carries_the_real_shiny_odds(tmp_path):
    svc = make(tmp_path, {1: three_stage_line()})
    svc.state.inventory[ItemKind.SHINY_CHARM.value] = 1
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.event["odds"] == 48


# ---------------------------------------------------------------- profiles


def slowpoke() -> PokemonDetails:
    return PokemonDetails(
        species_id=79,
        name="slowpoke",
        height=12,
        weight=360,
        base_experience=63,
        gender_rate=8,
        types=["water", "psychic"],
        base_stats={
            "hp": 90,
            "attack": 65,
            "defense": 65,
            "special-attack": 40,
            "special-defense": 40,
            "speed": 15,
        },
        abilities=[
            Ability("oblivious", 1, False),
            Ability("own-tempo", 2, False),
            Ability("regenerator", 3, True),
        ],
        moves=[
            MoveInfo("curse", [LearnMethod("level-up", 1)]),
            MoveInfo("tackle", [LearnMethod("level-up", 1)]),
            MoveInfo("growl", [LearnMethod("level-up", 5)]),
            MoveInfo("water-gun", [LearnMethod("level-up", 9)]),
            MoveInfo("confusion", [LearnMethod("level-up", 14)]),
            MoveInfo("surf", [LearnMethod("machine", 0)]),
        ],
    )


def test_profile_generation_is_deterministic():
    a = PokemonProfile.generate(42, instance_id="x")
    b = PokemonProfile.generate(42, instance_id="x")
    assert a == b
    assert all(0 <= a.ivs.get(k) <= 31 for k in ("hp", "attack", "speed"))


def test_enrich_is_idempotent_and_skips_machine_moves():
    p = PokemonProfile.generate(7)
    p.level = 12
    p.enrich(slowpoke())
    first = p.model_copy(deep=True)
    p.enrich(slowpoke())
    assert p == first
    assert p.gender == "female"  # gender_rate 8 is always female
    names = [m.name for m in p.moves]
    assert "water-gun" in names and "surf" not in names and len(names) == 4


def test_growth_and_level_never_go_down():
    p = PokemonProfile.generate(1)
    assert p.level == 5
    p.advance_growth(B.graduation_total(Rarity.COMMON), Rarity.COMMON)
    assert p.level == 100
    p.advance_growth(0, Rarity.COMMON)
    assert p.level == 100


def test_ditto_rebase_keeps_identity_and_ivs():
    p = PokemonProfile.generate(3, instance_id="keep")
    p.advance_growth(COMMON_STAGE_0, Rarity.COMMON)
    ivs = p.ivs.model_copy()
    p.rebase_for_species(Rarity.COMMON, Rarity.RARE)
    assert p.instance_id == "keep" and p.ivs == ivs
    assert p.growth_tokens == 500_000_000 and p.level == 20


def test_hostile_imported_profiles_are_clamped():
    p = PokemonProfile.model_validate(
        {
            "instance_id": "",
            "level": 9999,
            "growth_tokens": 10**30,
            "ivs": {
                "hp": 999,
                "attack": -5,
                "defense": 64,
                "special_attack": -1,
                "special_defense": 500,
                "speed": 31,
            },
            "moves": [{"name": "x" * 500, "learned_at_level": 900}] * 400,
        }
    )
    p.sanitize()
    assert p.level == 100 and p.instance_id
    assert [p.ivs.get(k) for k in ("hp", "attack", "defense")] == [31, 0, 31]
    assert len(p.moves) == 4 and len(p.moves[0].name) == 80 and p.moves[0].learned_at_level == 100


def test_nature_modifies_one_stat_up_and_one_down():
    assert actual_stat("attack", 100, 31, 50, "adamant") == math.floor(
        ((200 + 31) * 50 // 100 + 5) * 1.1
    )
    assert actual_stat("special-attack", 100, 31, 50, "adamant") == math.floor(
        ((200 + 31) * 50 // 100 + 5) * 0.9
    )
    assert actual_stat("hp", 90, 0, 50, "adamant") == (180 * 50) // 100 + 60
    assert stat_bar_scale([180, 299]) == 300 and stat_bar_scale([651, 300]) == 700


async def test_hatching_rolls_a_profile_that_graduates_at_level_100(tmp_path):
    svc = await hatched(tmp_path)
    profile = svc.state.active.profile
    assert profile is not None and profile.level == 5
    await feed(svc, 50 * B.graduation_total(Rarity.COMMON))
    entry = svc.state.dex[0]
    assert entry.profile.level == 100 and entry.id == profile.instance_id


async def test_details_enrich_the_active_profile(tmp_path):
    svc = await hatched(tmp_path)
    svc.poke.details[1] = slowpoke()
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active.profile.ability_name == "oblivious"


def test_legacy_saves_are_migrated_with_a_backup(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "active": {"base_id": 1, "current_id": 1, "total_forms": 3, "path_ids": [1]},'
        ' "dex": [{"base_id": 4, "final_id": 6, "chain_order": [4, 5, 6], "rarity": "common"}]}'
    )
    store = StateStore(path)
    assert store.state.active.profile is not None
    assert store.state.dex[0].profile.level == 100
    assert (tmp_path / "state.pre-profiles-v1.json").exists()
    again = StateStore(path)
    assert again.state.dex[0].id == store.state.dex[0].id
    assert again.state.active.profile == store.state.active.profile
    assert not migrate_profiles(again.state)


# ---------------------------------------------------------------- unown


def test_unown_letters_weigh_two_until_collected():
    rolls = [B.roll_unown_form(r, set()) for r in range(56)]
    assert rolls[:4] == ["a", "a", "b", "b"] and rolls[-2:] == ["question", "question"]
    assert B.roll_unown_form(56, set()) == "a"
    everything = set(B.UNOWN_FORMS)
    assert [B.roll_unown_form(r, everything) for r in range(28)] == B.UNOWN_FORMS


def test_unown_names_and_sprites():
    assert B.display_name("Unown", 201, "b") == "Unown [B]"
    assert B.display_name("Unown", 201, None) == "Unown [A]"
    assert B.display_name("Pikachu", 25, "b") == "Pikachu"
    assert B.sprite_name(201, "a") == "201" and B.sprite_name(201, "question") == "201-question"
    assert B.resolve_unown_form(201, "future-form") == "a"


async def test_an_unown_hatch_keeps_its_letter_through_graduation(tmp_path):
    unown = EvoLine(201, EvoNode(201), Rarity.UNCOMMON, {201: {"en": "Unown"}})
    svc = make(tmp_path, {201: unown}, index=[BaseSpecies(201, 225)])
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    form = svc.state.active.unown_form
    assert form in B.UNOWN_FORMS
    await feed(svc, 50 * B.graduation_total(Rarity.UNCOMMON))
    assert svc.state.dex[0].unown_form == form
    assert svc.collected_unown_forms() == {form}


def test_a_jittering_reset_marker_does_not_regrant():
    tier: dict[str, int] = {}
    epochs: dict[str, str] = {}
    first = CandyWindow(
        "session", "5-hour", "session", 100.0, epoch="2026-09-17T15:00:00.123456+00:00"
    )
    jitter = CandyWindow(
        "session", "5-hour", "session", 100.0, epoch="2026-09-17T15:00:01.654321+00:00"
    )
    assert len(CompanionService.evaluate_candy_grants([first], tier, epochs)) == 1
    assert CompanionService.evaluate_candy_grants([jitter], tier, epochs) == []
