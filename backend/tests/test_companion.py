import random

import pytest

from app import balance as B
from app.balance import ItemKind, Rarity
from app.companion import CandyWindow, CompanionService, repaired_plan
from app.pokeapi import BaseSpecies, EvoLine, EvoNode
from app.state import StateStore


class StubPoke:
    """Serves fixed lines so progression can be tested without the network."""

    def __init__(self, lines: dict[int, EvoLine], index: list[BaseSpecies] | None = None):
        self.lines = lines
        self.index = index if index is not None else [BaseSpecies(1, 255)]
        self.line_calls = 0

    async def line(self, base_species_id: int) -> EvoLine:
        self.line_calls += 1
        try:
            return self.lines[base_species_id]
        except KeyError:
            raise ValueError(f"no stub line for {base_species_id}") from None

    async def base_species_index(self) -> list[BaseSpecies]:
        return list(self.index)

    async def base_species(self, species_id: int):
        return next((e for e in self.index if e.id == species_id), None)


def three_stage_line(base=1, rarity=Rarity.COMMON) -> EvoLine:
    tree = EvoNode(base, [EvoNode(base + 1, [EvoNode(base + 2)])])
    names = {sid: {"en": f"Mon{sid}"} for sid in (base, base + 1, base + 2)}
    return EvoLine(base, tree, rarity, names)


def single_stage_line(base=50, rarity=Rarity.RARE) -> EvoLine:
    return EvoLine(base, EvoNode(base), rarity, {base: {"en": f"Solo{base}"}})


def branching_line(base=133) -> EvoLine:
    tree = EvoNode(base, [EvoNode(134), EvoNode(135), EvoNode(136)])
    names = {sid: {"en": f"Eevee{sid}"} for sid in (base, 134, 135, 136)}
    return EvoLine(base, tree, Rarity.UNCOMMON, names)


def ditto_line() -> EvoLine:
    """Ditto is a single-form rare — what a disguise resolves into."""
    return EvoLine(
        B.DITTO_SPECIES_ID,
        EvoNode(B.DITTO_SPECIES_ID),
        Rarity.RARE,
        {B.DITTO_SPECIES_ID: {"en": "Ditto"}},
    )


class NoDisguiseRNG(random.Random):
    """Pins the 1-in-N rolls to "miss" so shiny and Ditto never fire.

    Without this, any test hatching a common multi-stage line has a 1/128 chance of
    drawing a disguised Ditto, which correctly refuses to evolve — a ~5% flake across
    the suite. The Ditto path gets its own tests below instead.
    """

    def randrange(self, start, stop=None, step=1):  # noqa: D102
        if stop is None and step == 1 and start > 1:
            return 1  # never zero, so `roll == 0` gates stay closed
        return super().randrange(start, stop, step) if stop is not None else 0


def make_service(tmp_path, lines, index=None, rng=None) -> CompanionService:
    store = StateStore(tmp_path / "state.json")
    return CompanionService(store, StubPoke(lines, index), rng=rng or NoDisguiseRNG(1234))


async def feed(svc: CompanionService, total: int, *, day="2026-08-19") -> None:
    """Report a cumulative daily total, the way a real refresh does."""
    await svc.refresh(
        today_by_provider={"claude_code": total},
        today_date=day,
        burn_tier="normal",
        limit_warning=False,
        has_usage_data=True,
    )


# ---------------------------------------------------------------- balance math


def test_phase_thresholds_sum_to_the_graduation_total():
    for rarity in Rarity:
        for forms in range(1, 5):
            total = sum(B.phase_threshold(rarity, forms, i) for i in range(forms))
            assert abs(total - B.graduation_total(rarity)) <= forms, (rarity, forms)


def test_same_rarity_costs_the_same_regardless_of_stage_count():
    one = sum(B.phase_threshold(Rarity.COMMON, 1, i) for i in range(1))
    three = sum(B.phase_threshold(Rarity.COMMON, 3, i) for i in range(3))
    assert abs(one - three) <= 3


def test_later_stages_cost_more():
    thresholds = [B.phase_threshold(Rarity.RARE, 3, i) for i in range(3)]
    assert thresholds[0] < thresholds[1] < thresholds[2]


def test_rarity_classification_and_ordering():
    assert Rarity.classify(255, False, False) is Rarity.COMMON
    assert Rarity.classify(120, False, False) is Rarity.UNCOMMON
    assert Rarity.classify(45, False, False) is Rarity.RARE
    assert Rarity.classify(3, True, False) is Rarity.LEGENDARY
    assert Rarity.classify(255, False, True) is Rarity.LEGENDARY
    # A mythical with a high capture rate is still legendary.
    ranks = [r.sort_rank for r in (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.LEGENDARY)]
    assert ranks == sorted(ranks)


def test_one_rare_candy_can_never_chain_two_stages():
    cheapest = min(B.phase_threshold(r, f, 0) for r in Rarity for f in range(1, 4))
    assert B.RARE_CANDY_XP < cheapest


# ---------------------------------------------------------------- the ledger


async def test_first_observation_seeds_the_baseline_without_crediting_history(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 900_000_000)  # a machine with a long usage history
    assert svc.state.used_since_install == 0
    assert svc.state.egg_usage == 0
    assert svc.state.install_baseline_set


async def test_only_growth_since_the_last_observation_is_credited(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000_000)
    await feed(svc, 3_000_000)
    assert svc.state.used_since_install == 2_000_000
    assert svc.state.egg_usage == 2_000_000


async def test_a_new_day_credits_the_whole_of_that_day(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 4_000_000, day="2026-08-19")
    await feed(svc, 1_000_000, day="2026-08-20")
    # Yesterday's 4M seeded the baseline; today's 1M is genuinely new.
    assert svc.state.used_since_install == 1_000_000


async def test_a_provider_missing_on_the_first_refresh_of_a_day_is_not_reseeded(tmp_path):
    """Its ledger line opens at 0, so usage that appears later still counts."""
    svc = make_service(tmp_path, {1: three_stage_line()})
    await svc.refresh(
        today_by_provider={"claude_code": 1_000_000, "codex": 500_000},
        today_date="2026-08-19",
        burn_tier="normal",
        limit_warning=False,
        has_usage_data=True,
    )
    # New day, only one provider reports.
    await feed(svc, 200_000, day="2026-08-20")
    assert svc.state.claimed_today_by_provider == {"claude_code": 200_000, "codex": 0}
    # Now the other provider comes back with a real total for the same day.
    await svc.refresh(
        today_by_provider={"claude_code": 200_000, "codex": 300_000},
        today_date="2026-08-20",
        burn_tier="normal",
        limit_warning=False,
        has_usage_data=True,
    )
    assert svc.state.used_since_install == 200_000 + 300_000


async def test_a_regression_rebases_only_that_provider(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await svc.refresh(
        today_by_provider={"a": 1_000_000, "b": 1_000_000},
        today_date="2026-08-19",
        burn_tier="normal",
        limit_warning=False,
        has_usage_data=True,
    )
    await svc.refresh(
        today_by_provider={"a": 400_000, "b": 1_500_000},
        today_date="2026-08-19",
        burn_tier="normal",
        limit_warning=False,
        has_usage_data=True,
    )
    # b grew by 500k; a regressed and was rebased rather than counted negative.
    assert svc.state.used_since_install == 500_000
    assert svc.state.claimed_today_by_provider == {"a": 400_000, "b": 1_500_000}


async def test_an_empty_refresh_does_not_move_the_ledger(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000_000)
    await feed(svc, 6_000_000)
    before = dict(svc.state.claimed_today_by_provider or {})
    await svc.refresh(
        today_by_provider={},
        today_date="2026-08-19",
        burn_tier="idle",
        limit_warning=False,
        has_usage_data=True,
    )
    assert svc.state.claimed_today_by_provider == before


# ---------------------------------------------------------------- lifecycle


async def test_egg_hatches_at_the_threshold_and_carries_overflow(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    assert svc.state.active is None

    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD + 250_000)
    assert svc.state.active is not None
    assert svc.state.egg_usage == 0
    # The overflow past the hatch threshold went into the hatchling, not the bin.
    assert svc.state.active.used_at_stage == 250_000
    assert svc.state.active.total_forms == 3


async def test_growth_evolves_then_graduates_into_the_dex(tmp_path):
    line = three_stage_line()
    svc = make_service(tmp_path, {1: line, B.DITTO_SPECIES_ID: ditto_line()})
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active is not None and svc.state.active.stage_index == 0

    total = B.graduation_total(Rarity.COMMON)
    thresholds = [B.phase_threshold(Rarity.COMMON, 3, i) for i in range(3)]

    cumulative = 1_000 + B.EGG_HATCH_THRESHOLD
    cumulative += thresholds[0]
    await feed(svc, cumulative)
    assert svc.state.active.stage_index == 1
    assert svc.state.active.current_id == 2

    cumulative += thresholds[1]
    await feed(svc, cumulative)
    assert svc.state.active.stage_index == 2
    assert svc.state.active.current_id == 3

    cumulative += thresholds[2]
    await feed(svc, cumulative)
    assert svc.state.active is None, "final form should have graduated"
    assert len(svc.state.dex) == 1
    entry = svc.state.dex[0]
    assert entry.final_id == 3
    assert entry.chain_order == [1, 2, 3]
    assert "1:3" in svc.state.collected_finals
    # A fresh egg starts from zero.
    assert svc.state.egg_usage == 0
    assert abs(sum(thresholds) - total) <= 3


async def test_a_single_form_line_graduates_without_evolving(tmp_path):
    svc = make_service(tmp_path, {50: single_stage_line()}, index=[BaseSpecies(50, 40)])
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active is not None
    assert svc.state.active.total_forms == 1
    assert svc.state.active.rarity is Rarity.RARE

    cumulative = 1_000 + B.EGG_HATCH_THRESHOLD + B.phase_threshold(Rarity.RARE, 1, 0)
    await feed(svc, cumulative)
    assert svc.state.active is None
    assert svc.state.dex[0].final_id == 50


async def test_a_huge_single_delta_does_not_chain_past_graduation(tmp_path):
    """One enormous jump should graduate once, not loop into the next Pokémon."""
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    await feed(svc, 10 * B.graduation_total(Rarity.COMMON))
    assert len(svc.state.dex) == 1
    assert svc.state.active is None


async def test_evolution_is_deferred_while_the_line_is_unloaded_but_usage_is_kept(tmp_path):
    line = three_stage_line()
    svc = make_service(tmp_path, {1: line})
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)

    # Simulate a restart: state survives, the loaded line does not.
    svc.current_line = None
    svc.poke.lines = {}  # the line fetch now fails

    cumulative = 1_000 + B.EGG_HATCH_THRESHOLD + B.phase_threshold(Rarity.COMMON, 3, 0)
    await feed(svc, cumulative)
    # The delta must not be lost, even though the evolution could not be evaluated.
    assert svc.state.active.used_at_stage >= B.phase_threshold(Rarity.COMMON, 3, 0)
    assert svc.state.active.stage_index == 0

    # Once the line is reachable again, the pending evolution resolves.
    svc.poke.lines = {1: line}
    await feed(svc, cumulative)
    assert svc.state.active.stage_index == 1


async def test_branching_line_picks_one_route_and_keeps_it(tmp_path):
    svc = make_service(tmp_path, {133: branching_line()}, index=[BaseSpecies(133, 45)])
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    a = svc.state.active
    assert a is not None
    assert a.total_forms == 2
    assert a.planned_path_ids[1] in (134, 135, 136)
    planned = list(a.planned_path_ids)

    cumulative = 1_000 + B.EGG_HATCH_THRESHOLD + B.phase_threshold(a.rarity, 2, 0)
    await feed(svc, cumulative)
    assert svc.state.active.current_id == planned[1], "should follow the route chosen at hatch"


async def test_a_guaranteed_egg_only_hatches_at_or_above_its_tier(tmp_path):
    # The index offers a common and a rare; the guarantee must exclude the common.
    svc = make_service(
        tmp_path,
        {1: three_stage_line(), 50: single_stage_line()},
        index=[BaseSpecies(1, 255), BaseSpecies(50, 40)],
    )
    await feed(svc, 1_000)
    svc.state.used_since_install = 10_000_000_000
    svc.buy_fresh_egg(Rarity.RARE)
    assert svc.state.egg_tier is Rarity.RARE

    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD + 10_000_000)
    a = svc.state.active
    assert a is not None
    assert a.rarity.sort_rank >= Rarity.RARE.sort_rank
    assert svc.state.egg_tier is None, "the guarantee is consumed by the hatch"


async def test_a_mismatched_guarantee_keeps_the_egg_rather_than_downgrading(tmp_path):
    """A stale index that only offers a common must not satisfy a rare guarantee."""
    svc = make_service(tmp_path, {1: three_stage_line()}, index=[BaseSpecies(1, 40)])
    await feed(svc, 1_000)
    svc.state.used_since_install = 10_000_000_000
    svc.buy_fresh_egg(Rarity.RARE)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD + 1_000)
    # capture_rate said rare, the real line is common — keep the egg and the guarantee.
    assert svc.state.active is None
    assert svc.state.egg_tier is Rarity.RARE


# ---------------------------------------------------------------- shop and bag


async def test_wallet_is_lifetime_usage_minus_spend(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    svc.state.used_since_install = 1_000_000_000
    assert svc.available_tokens == 1_000_000_000
    svc.buy_item(ItemKind.RARE_CANDY)
    assert svc.state.spent_tokens == B.RARE_CANDY_PRICE
    assert svc.available_tokens == 1_000_000_000 - B.RARE_CANDY_PRICE
    assert svc.item_count(ItemKind.RARE_CANDY) == 1


async def test_buying_cannot_go_into_debt(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    svc.state.used_since_install = 10
    with pytest.raises(ValueError, match="not enough tokens"):
        svc.buy_item(ItemKind.RARE_CANDY)
    assert svc.state.spent_tokens == 0


async def test_a_purchase_does_not_advance_growth_or_stats(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    svc.state.used_since_install = 5_000_000_000
    stage_before = svc.state.active.used_at_stage
    lifetime_before = svc.state.used_since_install
    svc.buy_item(ItemKind.MINT)
    assert svc.state.active.used_at_stage == stage_before
    assert svc.state.used_since_install == lifetime_before


async def test_the_shiny_charm_is_a_one_off_purchase(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    svc.state.used_since_install = 20_000_000_000
    svc.buy_item(ItemKind.SHINY_CHARM)
    assert svc.owns_shiny_charm
    with pytest.raises(ValueError, match="already owned"):
        svc.buy_item(ItemKind.SHINY_CHARM)


async def test_rare_candy_feeds_growth_without_counting_as_usage(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    svc.state.inventory[ItemKind.RARE_CANDY.value] = 1
    lifetime_before = svc.state.used_since_install
    stage_before = svc.state.active.used_at_stage

    await svc.use_rare_candy()
    assert svc.state.active.used_at_stage == stage_before + B.RARE_CANDY_XP
    assert svc.state.used_since_install == lifetime_before, "candy XP is not real usage"
    assert svc.item_count(ItemKind.RARE_CANDY) == 0


async def test_rare_candy_needs_an_active_pokemon_and_a_loaded_line(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    svc.state.inventory[ItemKind.RARE_CANDY.value] = 1
    with pytest.raises(ValueError, match="no active"):
        await svc.use_rare_candy()


async def test_a_mint_always_changes_the_nature(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    svc.state.inventory[ItemKind.MINT.value] = 1
    before = svc.state.active.nature
    after = svc.use_mint()
    assert after != before
    assert svc.state.active.nature == after


async def test_a_fresh_egg_discards_without_recording_a_catch(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    assert svc.state.active is not None
    svc.state.used_since_install = 20_000_000_000

    svc.buy_fresh_egg(None)
    assert svc.state.active is None
    assert svc.state.dex == [], "a discarded Pokémon must not enter the Pokédex"
    assert svc.state.collected_finals == []
    assert svc.state.egg_usage == 0


async def test_an_unlisted_egg_tier_is_refused(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    svc.state.used_since_install = 50_000_000_000
    with pytest.raises(ValueError, match="not for sale"):
        svc.buy_fresh_egg(Rarity.LEGENDARY)


# ---------------------------------------------------------------- candy grants


def test_candy_grants_are_edge_triggered_and_rearm():
    tier: dict[str, int] = {}
    session = CandyWindow("session", "5-hour", "session", 100.0)
    weekly = CandyWindow("weekly_all", "Weekly", "weekly", 100.0)

    grants = CompanionService.evaluate_candy_grants([session, weekly], tier)
    assert {(g.window_key, g.count) for g in grants} == {
        ("session", 1),
        ("weekly_all", B.RARE_CANDY_WEEKLY_GRANT),
    }

    # Still at 100% — no second grant.
    assert CompanionService.evaluate_candy_grants([session, weekly], tier) == []

    # Dropping below 100% re-arms, and the next crossing grants again.
    reset = CandyWindow("session", "5-hour", "session", 12.0)
    assert CompanionService.evaluate_candy_grants([reset], tier) == []
    assert "session" not in tier
    assert len(CompanionService.evaluate_candy_grants([session], tier)) == 1


async def test_the_first_limits_load_seeds_without_granting(tmp_path):
    """A window already at 100% on first run must not pay out retroactively."""
    svc = make_service(tmp_path, {1: three_stage_line()})
    window = CandyWindow("session", "5-hour", "session", 100.0)
    assert svc.grant_candies([window], limits_ready=True) == []
    assert svc.item_count(ItemKind.RARE_CANDY) == 0
    assert svc.state.candy_feature_seeded

    # After seeding, a re-arm then a crossing does grant.
    svc.grant_candies([CandyWindow("session", "5-hour", "session", 5.0)], limits_ready=True)
    grants = svc.grant_candies([window], limits_ready=True)
    assert len(grants) == 1
    assert svc.item_count(ItemKind.RARE_CANDY) == 1


async def test_grants_wait_until_limits_are_actually_loaded(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    assert svc.grant_candies([], limits_ready=False) == []
    assert not svc.state.candy_feature_seeded


# ---------------------------------------------------------------- plan repair


def test_repaired_plan_keeps_the_walked_prefix():
    assert repaired_plan([1, 2], 1, [2, 9]) == [1, 2, 9]
    # A fallback that does not join the realised path is discarded.
    assert repaired_plan([1, 2], 1, [7, 8]) == [1, 2]
    assert repaired_plan([], 0, [1, 2]) == [1, 2]


async def test_persistence_round_trips_through_disk(tmp_path):
    svc = make_service(tmp_path, {1: three_stage_line()})
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD + 7_777)
    svc.save()

    reloaded = StateStore(tmp_path / "state.json")
    assert reloaded.state.active is not None
    assert reloaded.state.active.used_at_stage == svc.state.active.used_at_stage
    assert reloaded.state.active.nature == svc.state.active.nature
    assert reloaded.state.used_since_install == svc.state.used_since_install


# ---------------------------------------------------------------- ditto disguise


class AlwaysDisguiseRNG(random.Random):
    """Forces every 1-in-N gate to hit, so shiny and the Ditto disguise both fire."""

    def randrange(self, start, stop=None, step=1):
        if stop is None and step == 1:
            return 0
        return 0


async def test_a_disguised_ditto_hides_its_shininess_until_the_reveal(tmp_path):
    svc = make_service(
        tmp_path,
        {1: three_stage_line(), B.DITTO_SPECIES_ID: ditto_line()},
        rng=AlwaysDisguiseRNG(7),
    )
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    a = svc.state.active
    assert a is not None
    assert a.ditto_disguise == 1, "a common multi-form line should be disguise-eligible"
    assert a.is_shiny, "the roll was forced, so it really is shiny underneath"
    assert not a.ditto_revealed
    # The hatch flourish must not leak the shininess while disguised.
    assert svc.event is not None and svc.event["shiny"] is False


async def test_a_disguise_reveals_as_ditto_instead_of_evolving(tmp_path):
    svc = make_service(
        tmp_path,
        {1: three_stage_line(), B.DITTO_SPECIES_ID: ditto_line()},
        rng=AlwaysDisguiseRNG(7),
    )
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    first_threshold = B.phase_threshold(Rarity.COMMON, 3, 0)

    cumulative = 1_000 + B.EGG_HATCH_THRESHOLD + first_threshold + 25_000
    await feed(svc, cumulative)

    a = svc.state.active
    assert a is not None
    assert a.ditto_revealed
    assert a.base_id == B.DITTO_SPECIES_ID
    assert a.current_id == B.DITTO_SPECIES_ID
    assert a.rarity is Rarity.RARE, "rarity comes from the real Ditto line"
    assert a.total_forms == 1
    assert a.stage_index == 0
    # Growth past the disguise's first threshold carries into Ditto.
    assert a.used_at_stage == 25_000
    # The reveal is where the shininess finally shows.
    assert a.is_shiny


async def test_a_disguised_ditto_never_graduates_as_the_disguise_species(tmp_path):
    """The reveal must happen before any terminal graduation."""
    svc = make_service(
        tmp_path,
        {1: three_stage_line(), B.DITTO_SPECIES_ID: ditto_line()},
        rng=AlwaysDisguiseRNG(7),
    )
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
    # Enough to blow through the whole common line in one go.
    await feed(svc, 20 * B.graduation_total(Rarity.COMMON))
    assert all(e.base_id != 1 for e in svc.state.dex), "disguise species must not enter the dex"


async def test_a_failed_ditto_reveal_keeps_the_mon_and_retries(tmp_path):
    """If species 132 is unreachable, the disguise stalls rather than mis-evolving."""
    line = three_stage_line()
    svc = make_service(tmp_path, {1: line}, rng=AlwaysDisguiseRNG(7))
    await feed(svc, 1_000)
    await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)

    cumulative = 1_000 + B.EGG_HATCH_THRESHOLD + B.phase_threshold(Rarity.COMMON, 3, 0)
    await feed(svc, cumulative)
    a = svc.state.active
    assert a is not None
    assert not a.ditto_revealed
    assert a.stage_index == 0, "must not evolve past a pending reveal"
    assert svc.state.dex == []

    # Once reachable, the pending reveal completes.
    svc.poke.lines[B.DITTO_SPECIES_ID] = ditto_line()
    await feed(svc, cumulative)
    assert svc.state.active.ditto_revealed


async def test_the_rng_seam_makes_hatches_reproducible(tmp_path):
    """Same seed, same index, same outcome — the guard that keeps tests honest."""
    index = [BaseSpecies(i, 100 + i) for i in range(1, 6)]
    lines = {i: three_stage_line(base=i) for i in range(1, 6)}

    async def hatch_with(seed):
        svc = make_service(tmp_path / str(seed), lines, index=index, rng=random.Random(seed))
        (tmp_path / str(seed)).mkdir(parents=True, exist_ok=True)
        await feed(svc, 1_000)
        await feed(svc, 1_000 + B.EGG_HATCH_THRESHOLD)
        a = svc.state.active
        return (a.base_id, a.nature, a.is_shiny)

    assert await hatch_with(42) == await hatch_with(42)
