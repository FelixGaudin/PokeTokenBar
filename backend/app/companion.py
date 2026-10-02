"""The Pokémon lifecycle: incubate, hatch, evolve, graduate — plus shop and bag.

Token usage drives everything. The service is fed a per-provider snapshot of today's
totals on each refresh, works out how much is genuinely new since the last
observation, and pours that delta into the egg or the active Pokémon.
"""

from __future__ import annotations

import logging
import math
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import balance as B
from .balance import ItemKind, Rarity
from .pokeapi import EvoLine, EvoNode, PokeAPIClient
from .profile import MAX_TOKEN_VALUE, PokemonProfile
from .state import DexEntry, MonState, PreferenceStore, SaveState, StateStore

log = logging.getLogger(__name__)

# Default source of randomness. Injectable so tests can pin every roll — shiny,
# nature, Ditto disguise and branch choice all draw from here.
_DEFAULT_RNG = random.SystemRandom()

# How long a hatch / evolve / graduate flourish stays on screen.
EVENT_WINDOW = timedelta(seconds=6)

# A failed PokéAPI details fetch is retried after this long, not on every tick.
DETAILS_RETRY_SECONDS = 300.0


@dataclass(frozen=True)
class CandyWindow:
    """A rate-limit window, as far as the candy grant is concerned."""

    key: str  # stable identifier — must not include volatile fields like resets_at
    name: str  # shown in the grant notice so the reward is explained
    kind: str  # "session" grants 1, "weekly" grants 5
    utilization: float  # 0-100+
    # The window's reset marker. A new one means a new window, which re-arms the grant.
    epoch: str | None = None
    # Additional accounts only pay once they have been seen below 100%.
    needs_arming: bool = False


@dataclass(frozen=True)
class CandyGrant:
    window_key: str
    window_name: str
    count: int


@dataclass(frozen=True)
class CandyPlan:
    count: int
    evolves: bool
    graduates: bool
    carryover: int
    discarded: int

    @property
    def xp(self) -> int:
        return self.count * B.RARE_CANDY_XP


class CompanionService:
    def __init__(
        self,
        store: StateStore,
        poke: PokeAPIClient,
        rng: random.Random | None = None,
        prefs: PreferenceStore | None = None,
    ) -> None:
        self.store = store
        self.poke = poke
        self.prefs = prefs
        self._rng = rng or _DEFAULT_RNG
        self.current_line: EvoLine | None = None
        self.display_state = "egg"
        self.just_evolved_to: str | None = None
        self.just_graduated: str | None = None
        self.event: dict | None = None
        self.event_until: datetime | None = None
        # The egg is ready but the last hatch attempt failed for an external reason.
        self.hatch_retry_delayed = False
        self._hatching = False
        self._revealing_ditto = False
        self._loading_line = False
        self._details_failed_at: dict[int, float] = {}
        # Bumped whenever the active subject is replaced, so an in-flight network
        # fetch can tell that its result is stale and discard it.
        self._generation = 0

    # ------------------------------------------------------------------
    # convenience accessors
    # ------------------------------------------------------------------

    @property
    def state(self) -> SaveState:
        return self.store.state

    def save(self) -> None:
        self.store.save()

    @property
    def growth_difficulty(self) -> float:
        return self.prefs.prefs.growth_difficulty if self.prefs else B.DEFAULT_DIFFICULTY

    @property
    def shop_difficulty(self) -> float:
        return self.prefs.prefs.shop_difficulty if self.prefs else B.DEFAULT_DIFFICULTY

    @property
    def owns_shiny_charm(self) -> bool:
        return self.item_count(ItemKind.SHINY_CHARM) > 0

    def item_count(self, kind: ItemKind) -> int:
        return self.state.inventory.get(kind.value, 0)

    @property
    def available_tokens(self) -> int:
        """The shop wallet: lifetime usage minus lifetime shop spend."""
        return max(0, self.state.used_since_install - self.state.spent_tokens)

    # Every consumer goes through these helpers, never the raw balance constants, so
    # the difficulty and the repeat boost apply everywhere at once.

    @property
    def egg_hatch_threshold(self) -> int:
        return max(1, B.scaled(B.EGG_HATCH_THRESHOLD, self.growth_difficulty))

    def stage_threshold_of(self, mon: MonState, stage_index: int | None = None) -> int:
        stage = mon.stage_index if stage_index is None else stage_index
        base = B.phase_threshold(
            mon.rarity,
            mon.total_forms,
            stage,
            B.REPEAT_GROWTH_MULTIPLIER if mon.has_growth_boost else 1,
        )
        return max(1, B.scaled(base, self.growth_difficulty))

    @property
    def stage_threshold(self) -> int:
        a = self.state.active
        return 0 if a is None else self.stage_threshold_of(a)

    def price(self, kind: ItemKind) -> int:
        return max(1, B.scaled(kind.shop_price, self.shop_difficulty))

    def egg_price(self, tier: Rarity | None) -> int:
        return max(1, B.scaled(B.fresh_egg_price(tier), self.shop_difficulty))

    @property
    def is_high_value_companion(self) -> bool:
        """Worth a second confirmation before sending off. Rare is deliberately excluded."""
        a = self.state.active
        return a is not None and (a.current_is_shiny or a.rarity is Rarity.LEGENDARY)

    def has_collected_final(self, base_id: int) -> bool:
        prefix = f"{base_id}:"
        return any(c.startswith(prefix) for c in self.state.collected_finals)

    def collected_unown_forms(self) -> set[str]:
        forms: set[str] = set()
        for entry in self.state.dex:
            if B.UNOWN_SPECIES_ID in entry.chain_order:
                forms.add(B.resolve_unown_form(B.UNOWN_SPECIES_ID, entry.unown_form) or "a")
        a = self.state.active
        if a is not None and B.UNOWN_SPECIES_ID in a.path_ids[: a.stage_index + 1]:
            forms.add(B.resolve_unown_form(B.UNOWN_SPECIES_ID, a.unown_form) or "a")
        return forms

    # ------------------------------------------------------------------
    # refresh — ledger and progression
    # ------------------------------------------------------------------

    async def refresh(
        self,
        *,
        today_by_provider: dict[str, int],
        today_date: str,
        burn_tier: str,
        limit_warning: bool,
        has_usage_data: bool,
    ) -> None:
        state = self.state
        today_tokens = sum(today_by_provider.values())
        # `has_usage_data` only says a snapshot exists; this map holds providers that
        # actually reported a total for today. A stale or empty refresh must not be
        # allowed to move the ledger's reference point.
        has_current = has_usage_data and bool(today_by_provider)

        if not state.install_baseline_set:
            if not has_current:
                # Nothing to baseline against yet. Keep showing whatever we have and
                # keep retrying the line load — a fresh machine has no usage until
                # the first session runs, and stalling here would show an egg all day.
                self.display_state = "egg" if state.active is None else "idle"
                await self._ensure_line()
                return
            state.install_baseline_set = True
            state.claimed_today_by_provider = dict(today_by_provider)
            state.last_date = today_date
            self.save()
        elif has_current:
            await self._advance_ledger(today_by_provider, today_date)

        if self.event_until is not None and datetime.now(timezone.utc) > self.event_until:
            self.just_graduated = None
            self.just_evolved_to = None
            self.event = None
            self.event_until = None

        if state.active is None and state.install_baseline_set and not self._hatching:
            if state.egg_usage >= self.egg_hatch_threshold:
                await self._hatch()
        elif state.active is not None and self.current_line is None:
            await self._ensure_line()

        # Backup trigger: a disguised Ditto that reached its first threshold while the
        # line was still loading (or across a restart) never got the reveal kick.
        a = state.active
        if (
            a is not None
            and a.ditto_disguise is not None
            and not a.ditto_revealed
            and self.current_line is not None
            and not self._hatching
            and not self._revealing_ditto
            and a.used_at_stage >= self.stage_threshold_of(a, 0)
        ):
            await self._reveal_ditto()

        await self._warm_active_details()

        self.display_state = self._compute_display_state(
            burn_tier=burn_tier,
            limit_warning=limit_warning,
            has_usage_data=has_usage_data,
            today_tokens=today_tokens,
        )
        self.save()

    async def _advance_ledger(self, today_by_provider: dict[str, int], today_date: str) -> None:
        """Credit only the growth since the last observation of the same day."""
        state = self.state
        date_changed = today_date != state.last_date

        if state.claimed_today_by_provider is None:
            # An older save only carried an aggregate high-water mark, which cannot be
            # split per provider. Seed the new ledger and credit nothing retroactively.
            state.claimed_today_by_provider = dict(today_by_provider)
            state.last_date = today_date
            return

        if date_changed:
            # Totals from different days are not comparable, so a new day credits the
            # whole of today rather than differencing against yesterday. Providers
            # known yesterday but missing from this first refresh open at 0 instead of
            # being dropped, so they are not re-seeded at their current total later
            # in the day (which would swallow that usage).
            state.last_date = today_date
            ledger = {pid: 0 for pid in state.claimed_today_by_provider}
            ledger.update(today_by_provider)
            state.claimed_today_by_provider = ledger
            delta = sum(today_by_provider.values())
        else:
            ledger = dict(state.claimed_today_by_provider)
            delta = 0
            for provider_id, current in today_by_provider.items():
                previous = ledger.get(provider_id)
                if previous is None:
                    # A newly seen provider's history is not credited retroactively;
                    # seed it so later growth is tracked.
                    ledger[provider_id] = current
                    continue
                if current < previous:
                    # Rebase just this provider's line — other providers may simply
                    # not have reported in this refresh, so leave their marks alone.
                    ledger[provider_id] = current
                    log.info(
                        "usage regressed for %s (%d -> %d) — rebased that provider",
                        provider_id,
                        previous,
                        current,
                    )
                    continue
                delta += current - previous
                ledger[provider_id] = current
            state.claimed_today_by_provider = ledger

        if delta > 0:
            state.used_since_install += delta
            if state.active is None:
                state.egg_usage += delta
            else:
                await self._apply_usage(delta)

    async def _apply_usage(self, delta: int) -> None:
        """Add tokens to the active Pokémon, evolving or graduating as thresholds fall.

        Usage is always banked even when the line has not loaded (just after a restart,
        or offline) — dropping it here would lose the delta permanently, because the
        per-provider ledger has already moved on. Only the evolution check waits.
        """
        state = self.state
        if state.active is None:
            return
        self._reconcile_profile_growth()
        state.active.used_at_stage = min(MAX_TOKEN_VALUE, state.active.used_at_stage + delta)

        line = self.current_line
        if line is None:
            self._reconcile_profile_growth()
            self.save()
            return

        guard = 0
        while state.active is not None and guard < 50:
            guard += 1
            a = state.active
            threshold = self.stage_threshold_of(a)
            if a.used_at_stage < threshold:
                break
            node = line.tree.find(a.current_id)
            if node is None:
                break
            # A disguise must be revealed before any terminal graduation, or the
            # disguise species would graduate into the dex by mistake.
            if a.ditto_disguise is not None and not a.ditto_revealed:
                if not self._revealing_ditto:
                    await self._reveal_ditto()
                break
            if not node.children:
                self._graduate()
                break

            # Growth for the finished stage is credited before the stage index moves.
            self._reconcile_profile_growth(stage_complete=True)
            next_index = a.stage_index + 1
            nxt: EvoNode | None = None
            if next_index < len(a.planned_path_ids):
                planned_id = a.planned_path_ids[next_index]
                nxt = next((c for c in node.children if c.species_id == planned_id), None)
            if nxt is None:
                nxt = self._pick_planned_child(node, a.base_id)
                fallback = [node.species_id] + self._evolution_plan(nxt, a.base_id)
                repaired = repaired_plan(a.path_ids, a.stage_index, fallback)
                a.planned_path_ids = repaired
                a.total_forms = len(repaired)
                log.info("evolve: repaired invalid planned path for base %d", a.base_id)

            a.path_ids = a.path_ids[: a.stage_index + 1] + [nxt.species_id]
            a.current_id = nxt.species_id
            a.stage_index += 1
            a.used_at_stage -= threshold  # overflow carries into the next stage
            self._enrich_active()
            name = B.display_name(
                line.display_name(nxt.species_id, state.language), nxt.species_id, a.unown_form
            )
            self.just_evolved_to = name
            self._fire_event("evolve", name=name, shiny=a.is_shiny, species_id=nxt.species_id)

        self._reconcile_profile_growth()
        self.save()

    # ------------------------------------------------------------------
    # profiles
    # ------------------------------------------------------------------

    def _reconcile_profile_growth(self, *, stage_complete: bool = False) -> None:
        """Mirror stage progress into the profile's difficulty-free growth counter."""
        a = self.state.active
        if a is None or a.profile is None:
            return
        completed = sum(
            B.phase_threshold(a.rarity, a.total_forms, s) for s in range(a.stage_index)
        )
        standard = B.phase_threshold(a.rarity, a.total_forms, a.stage_index)
        fraction = 1.0 if stage_complete else min(
            1.0, max(0.0, a.used_at_stage / max(1, self.stage_threshold_of(a)))
        )
        candidate = min(
            B.graduation_total(a.rarity), completed + math.floor(standard * fraction)
        )
        a.profile.advance_growth(candidate, a.rarity)
        self._enrich_active()

    def _enrich_active(self) -> None:
        a = self.state.active
        if a is None or a.profile is None:
            return
        details = self.poke.cached_details(a.current_id)
        if details is not None:
            a.profile.enrich(details)

    async def _warm_active_details(self) -> None:
        """Fetch details for the species being raised, then enrich its profile."""
        a = self.state.active
        if a is None or a.profile is None:
            return
        if self.poke.cached_details(a.current_id) is not None:
            self._enrich_active()
            return
        failed_at = self._details_failed_at.get(a.current_id)
        if failed_at is not None and time.monotonic() - failed_at < DETAILS_RETRY_SECONDS:
            return
        generation = self._generation
        try:
            await self.poke.pokemon_details(a.current_id)
        except Exception as exc:  # noqa: BLE001 - details are enrichment, never required
            self._details_failed_at[a.current_id] = time.monotonic()
            log.info("details for %d unavailable (%s)", a.current_id, exc)
            return
        self._details_failed_at.pop(a.current_id, None)
        if self._generation == generation:
            self._enrich_active()

    def enrich_dex(self, species_id: int) -> bool:
        """Fill in dex profiles for a species whose details just arrived."""
        details = self.poke.cached_details(species_id)
        if details is None:
            return False
        changed = False
        for entry in self.state.dex:
            if entry.final_id == species_id and entry.profile is not None:
                before = entry.profile.model_dump()
                entry.profile.enrich(details)
                changed = changed or entry.profile.model_dump() != before
        a = self.state.active
        if a is not None and a.current_id == species_id and a.profile is not None:
            before = a.profile.model_dump()
            a.profile.enrich(details)
            changed = changed or a.profile.model_dump() != before
        if changed:
            self.save()
        return changed

    # ------------------------------------------------------------------
    # hatching
    # ------------------------------------------------------------------

    def _egg_still_ready(self, generation: int) -> bool:
        s = self.state
        return (
            self._generation == generation
            and s.active is None
            and s.egg_usage >= self.egg_hatch_threshold
        )

    async def _hatch(self) -> None:
        state = self.state
        if state.active is not None or state.egg_usage < self.egg_hatch_threshold:
            return
        self._hatching = True
        generation = self._generation
        try:
            base_id = await self._choose_base()
            if base_id is None:
                if self._egg_still_ready(generation):
                    self.hatch_retry_delayed = True
                return
            try:
                line = await self.poke.line(base_id)
            except Exception as exc:  # noqa: BLE001 - keep the egg, retry next tick
                log.warning("hatch: line fetch failed for base %d (%s)", base_id, exc)
                if self._egg_still_ready(generation):
                    self.hatch_retry_delayed = True
                return
            if self._generation != generation or state.active is not None:
                log.info("hatch: discarded — active subject replaced during fetch")
                return
            # Last gate on a purchased guarantee: only here is the true rarity known
            # (the candidate index carries capture_rate but not is_legendary). If the
            # filter drifted, keep the egg and the guarantee rather than handing over
            # a lower tier.
            if state.egg_tier is not None and line.rarity.sort_rank < state.egg_tier.sort_rank:
                log.info(
                    "hatch: rolled %s below guaranteed %s — re-rolling next tick",
                    line.rarity.value,
                    state.egg_tier.value,
                )
                state.egg_prefetch_base_id = None
                state.pending_unown_form = None
                self.hatch_retry_delayed = True
                self.save()
                return

            self.hatch_retry_delayed = False
            self.current_line = line
            overflow = max(0, state.egg_usage - self.egg_hatch_threshold)
            state.egg_usage = 0
            state.egg_tier = None  # the guarantee is consumed by this hatch
            state.egg_prefetch_base_id = None
            state.pending_unown_form = None

            # The odds shown are the ones this roll actually used.
            odds = B.shiny_denominator(self.owns_shiny_charm)
            is_shiny = self._rng.randrange(odds) == 0
            nature = self._rng.choice(B.NATURES)

            plan = self._evolution_plan(line.tree, line.base_id)
            ditto_disguise: int | None = None
            if (
                line.rarity is Rarity.COMMON
                and len(plan) >= 2
                and self._rng.randrange(B.DITTO_DISGUISE_DENOMINATOR) == 0
            ):
                ditto_disguise = line.base_id
            profile = PokemonProfile.generate(self._rng.getrandbits(64))
            unown_form = (
                B.roll_unown_form(self._rng.getrandbits(64), self.collected_unown_forms())
                if line.base_id == B.UNOWN_SPECIES_ID
                else None
            )

            self._generation += 1
            state.active = MonState(
                base_id=line.base_id,
                current_id=line.base_id,
                path_ids=[line.base_id],
                planned_path_ids=plan,
                stage_index=0,
                used_at_stage=0,
                rarity=line.rarity,
                total_forms=len(plan),
                is_shiny=is_shiny,
                nature=nature,
                ditto_disguise=ditto_disguise,
                hatched_at=datetime.now(timezone.utc),
                # Decided on the base line, never the planned final, which would leak
                # the hidden branch choice.
                has_growth_boost=self.has_collected_final(line.base_id),
                profile=profile,
                unown_form=unown_form,
            )
            self._enrich_active()
            # A disguise hides its shininess until the reveal.
            show_shiny = is_shiny and ditto_disguise is None
            self.just_evolved_to = None
            log.info(
                "hatch: base=%d rarity=%s shiny=%s forms=%d ditto=%s boost=%s",
                line.base_id,
                line.rarity.value,
                is_shiny,
                len(plan),
                ditto_disguise is not None,
                state.active.has_growth_boost,
            )
            self._fire_event(
                "hatch",
                name=B.display_name(
                    line.display_name(line.base_id, state.language), line.base_id, unown_form
                ),
                shiny=show_shiny,
                species_id=line.base_id,
                form=unown_form,
                odds=odds,
            )
            if overflow > 0:
                await self._apply_usage(overflow)
            self.save()
        finally:
            self._hatching = False

    async def _choose_base(self) -> int | None:
        """Weighted pick across every gen 1-5 line start.

        Weight is the official capture_rate, so Caterpie (255) is 85x likelier than
        Mewtwo (3) and the legendary group lands near 0.8%. A base whose finals are
        already collected is halved, nudging toward new species without closing off
        re-hatches or shiny hunting. Releasing does not count as collecting.
        """
        state = self.state
        tier = state.egg_tier
        try:
            full = await self.poke.base_species_index()
        except Exception:
            full = []

        if full:
            # A guaranteed tier narrows candidates first: the capture-rate ceiling is
            # the tier floor, so legendaries are naturally included in "rare or better".
            index = [e for e in full if tier.includes(e.capture_rate)] if tier else list(full)
            if not index:
                log.info("hatch: no candidate for guaranteed tier — egg kept")
                return None
            weights = [
                B.collection_weight(e.capture_rate, self.has_collected_final(e.id)) for e in index
            ]
            total = sum(weights)
            r = self._rng.randrange(total)
            for entry, weight in zip(index, weights):
                r -= weight
                if r < 0:
                    return entry.id
            return index[-1].id

        # Index endpoint down — reject-sample random ids over the animated range so a
        # hatch is never permanently blocked on one endpoint. The resulting rarity is
        # still exact, because `line()` derives it from the real capture_rate.
        log.info("hatch: base index unavailable — REST fallback")
        for _ in range(16):
            candidate = self._rng.randint(1, B.ANIMATED_SPECIES_MAX)
            if candidate == B.DITTO_SPECIES_ID:
                continue
            base = await self.poke.base_species(candidate)
            if base is None:
                continue
            if tier is not None and not tier.includes(base.capture_rate):
                continue
            log.info("hatch: REST fallback picked base %d", base.id)
            return base.id
        log.info("hatch: REST fallback exhausted")
        return None

    def _pick_planned_child(self, node: EvoNode, base_id: int) -> EvoNode:
        """Prefer a branch that still leads to an uncollected final form."""
        collected = set(self.state.collected_finals)
        fresh = [
            child
            for child in node.children
            if any(f"{base_id}:{fid}" not in collected for fid in child.final_ids)
        ]
        pool = fresh or node.children
        return pool[self._rng.randrange(len(pool))]

    def _evolution_plan(self, root: EvoNode, base_id: int) -> list[int]:
        plan = [root.species_id]
        node = root
        while node.children:
            node = self._pick_planned_child(node, base_id)
            plan.append(node.species_id)
        return plan

    # ------------------------------------------------------------------
    # ditto reveal
    # ------------------------------------------------------------------

    async def _reveal_ditto(self) -> None:
        """A Ditto cannot evolve — at its first threshold it drops the disguise instead."""
        state = self.state
        a = state.active
        if a is None or a.ditto_disguise is None or a.ditto_revealed or self._revealing_ditto:
            return
        if a.used_at_stage < self.stage_threshold_of(a, 0):
            return

        self._revealing_ditto = True
        generation = self._generation
        try:
            try:
                ditto_line = await self.poke.line(B.DITTO_SPECIES_ID)
            except Exception as exc:  # noqa: BLE001
                log.warning("ditto reveal: line fetch failed (%s) — retry next tick", exc)
                return
            if self._generation != generation:
                return
            m = state.active
            if m is None or m.ditto_disguise is None or m.ditto_revealed:
                return
            first_threshold = self.stage_threshold_of(m, 0)
            if m.used_at_stage < first_threshold:
                return

            disguise_name = (
                self.current_line.display_name(m.base_id, state.language)
                if self.current_line
                else f"#{m.base_id}"
            )
            carry_over = max(0, m.used_at_stage - first_threshold)
            plan = self._evolution_plan(ditto_line.tree, ditto_line.base_id)
            old_rarity = m.rarity

            m.base_id = ditto_line.base_id
            m.current_id = ditto_line.base_id
            m.path_ids = [ditto_line.base_id]
            m.planned_path_ids = plan
            m.stage_index = 0
            m.rarity = ditto_line.rarity
            m.total_forms = len(plan)
            m.used_at_stage = carry_over
            m.ditto_revealed = True
            # The boost stays: it was earned by the line that hatched.
            if m.profile is not None:
                m.profile.rebase_for_species(old_rarity, ditto_line.rarity)
            self.current_line = ditto_line
            self._enrich_active()

            log.info("ditto reveal: rarity=%s shiny=%s", ditto_line.rarity.value, m.is_shiny)
            self._fire_event(
                "ditto_reveal",
                name=ditto_line.display_name(ditto_line.base_id, state.language),
                shiny=m.is_shiny,
                species_id=ditto_line.base_id,
                disguise_name=disguise_name,
            )
            self.save()
        finally:
            self._revealing_ditto = False
        # Re-evaluate the carried-over tokens now that thresholds changed.
        await self._apply_usage(0)

    # ------------------------------------------------------------------
    # graduation and release
    # ------------------------------------------------------------------

    def _names_for(self, ids: list[int]) -> dict[str, dict[str, str]]:
        names: dict[str, dict[str, str]] = {}
        if self.current_line is not None:
            for sid in ids:
                by_lang = self.current_line.names.get(sid)
                if by_lang:
                    names[str(sid)] = by_lang
        return names

    def _graduate(self) -> None:
        state = self.state
        a = state.active
        if a is None:
            return
        final_id = a.current_id
        marker = f"{a.base_id}:{final_id}"
        if marker not in state.collected_finals:
            state.collected_finals.append(marker)

        if a.profile is not None:
            a.profile.advance_growth(B.graduation_total(a.rarity), a.rarity)
            self._enrich_active()

        state.dex.append(
            DexEntry(
                id=a.profile.instance_id if a.profile else str(uuid.uuid4()),
                base_id=a.base_id,
                final_id=final_id,
                chain_order=list(a.path_ids),
                rarity=a.rarity,
                caught_at=datetime.now(timezone.utc),
                is_shiny=a.is_shiny,
                nature=a.nature,
                names=self._names_for(a.path_ids),
                profile=a.profile,
                unown_form=a.unown_form,
            )
        )
        name = (
            self.current_line.display_name(final_id, state.language)
            if self.current_line
            else f"#{final_id}"
        )
        name = B.display_name(name, final_id, a.unown_form)
        self.just_graduated = name
        self._fire_event(
            "graduate", name=name, shiny=a.is_shiny, species_id=final_id, form=a.unown_form
        )

        state.active = None
        self._generation += 1
        self.current_line = None
        self.hatch_retry_delayed = False
        state.egg_usage = 0  # the next egg incubates from scratch
        # egg_tier is untouched: reaching here means a Pokémon was active, so any
        # guarantee was already consumed at hatch time.

    def _released_entry(self, a: MonState) -> DexEntry:
        """Only reached forms are recorded — never the planned route."""
        chain = a.path_ids[: max(1, a.stage_index + 1)] or [a.base_id]
        now = datetime.now(timezone.utc)
        return DexEntry(
            id=a.profile.instance_id if a.profile else str(uuid.uuid4()),
            base_id=a.base_id,
            final_id=chain[-1],
            chain_order=list(chain),
            rarity=a.rarity,
            caught_at=now,
            released_at=now,
            # Releasing must not reveal a disguised Ditto's shininess.
            is_shiny=a.current_is_shiny,
            nature=a.nature,
            names=self._names_for(chain),
            profile=a.profile,
            unown_form=a.unown_form,
        )

    # ------------------------------------------------------------------
    # line loading
    # ------------------------------------------------------------------

    async def _ensure_line(self) -> None:
        state = self.state
        a = state.active
        if a is None or self.current_line is not None or self._hatching or self._loading_line:
            return
        self._loading_line = True
        generation = self._generation
        try:
            line = await self.poke.line(a.base_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("line load failed for base %d (%s) — retry next tick", a.base_id, exc)
            return
        finally:
            self._loading_line = False
        if self._generation != generation or state.active is None:
            return
        self.current_line = line
        self._normalise_evolution_state(line)
        # Assets or a migration may have changed the tree; re-check thresholds.
        await self._apply_usage(0)

    def _normalise_evolution_state(self, line: EvoLine) -> None:
        """Reconcile the saved path against the current asset tree.

        A complete saved plan is reused verbatim so a restart consumes no RNG and the
        promised route stays stable.
        """
        a = self.state.active
        if a is None:
            return
        realized_path, realized_node = _longest_valid_path(a.path_ids, line.tree)
        candidate_path, candidate_node = _longest_valid_path(a.planned_path_ids, line.tree)
        can_reuse = (
            candidate_path == a.planned_path_ids
            and candidate_path[: len(realized_path)] == realized_path
            and not candidate_node.children
        )
        if can_reuse:
            plan = candidate_path
        else:
            suffix = self._evolution_plan(realized_node, a.base_id)
            plan = realized_path + suffix[1:]
        a.path_ids = realized_path
        a.current_id = realized_path[-1]
        a.planned_path_ids = plan
        a.stage_index = len(realized_path) - 1
        a.total_forms = len(plan)

    # ------------------------------------------------------------------
    # difficulty
    # ------------------------------------------------------------------

    def set_growth_difficulty(self, value: float) -> None:
        """Rescale banked progress so the earned fraction survives. Never evolves here."""
        if self.prefs is None:
            return
        new = B.clamp_difficulty(value)
        old = self.prefs.prefs.growth_difficulty
        if new == old:
            return
        self._rescale_banked_growth(old, new)
        self.prefs.prefs.growth_difficulty = new
        self.prefs.save()
        self.save()

    def set_shop_difficulty(self, value: float) -> None:
        if self.prefs is None:
            return
        self.prefs.prefs.shop_difficulty = B.clamp_difficulty(value)
        self.prefs.save()

    def _rescale_banked_growth(self, old: float, new: float) -> None:
        def rescaled(credits: int, base: int) -> int:
            old_thr = max(1, B.round_half_up(base * old))
            new_thr = max(1, B.round_half_up(base * new))
            r = int(min(MAX_TOKEN_VALUE, max(0, math.floor(credits / old_thr * new_thr))))
            # Rounding must never turn an incomplete stage into a completed one.
            return min(new_thr - 1, r) if credits < old_thr else r

        a = self.state.active
        if a is not None:
            a.used_at_stage = rescaled(a.used_at_stage, a.phase_threshold)
        else:
            self.state.egg_usage = rescaled(self.state.egg_usage, B.EGG_HATCH_THRESHOLD)

    # ------------------------------------------------------------------
    # rare candy grants
    # ------------------------------------------------------------------

    @staticmethod
    def evaluate_candy_grants(
        windows: list[CandyWindow],
        grant_tier: dict[str, int],
        window_epoch: dict[str, str] | None = None,
    ) -> list[CandyGrant]:
        """Edge-triggered: grant only on the transition up through 100%.

        Below 100% the window is removed from the map, which re-arms it. A window
        already granted (tier >= 1) never grants again until it re-arms — or until
        its reset marker changes, which means a whole new window has started.
        """
        epochs = window_epoch if window_epoch is not None else {}
        grants: list[CandyGrant] = []
        for w in windows:
            if w.epoch is not None:
                previous = epochs.get(w.key)
                if previous is not None and _is_new_window(previous, w.epoch):
                    grant_tier.pop(w.key, None)
                epochs[w.key] = w.epoch
            if w.utilization < 100:
                grant_tier.pop(w.key, None)
                continue
            if grant_tier.get(w.key, 0) >= 1:
                continue
            grant_tier[w.key] = 1
            count = B.RARE_CANDY_WEEKLY_GRANT if w.kind == "weekly" else 1
            grants.append(CandyGrant(w.key, w.name, count))
        return grants

    def grant_candies(self, windows: list[CandyWindow], *, limits_ready: bool) -> list[CandyGrant]:
        """Apply grants, seeding on first run so nothing is awarded retroactively."""
        if not limits_ready:
            return []
        state = self.state
        armed_before = list(state.armed_candy_windows)
        for w in windows:
            if w.needs_arming and w.utilization < 100 and w.key not in state.armed_candy_windows:
                state.armed_candy_windows.append(w.key)
        windows = [w for w in windows if not w.needs_arming or w.key in state.armed_candy_windows]

        if not state.candy_feature_seeded:
            for w in windows:
                if w.utilization >= 100:
                    state.candy_grant_tier[w.key] = 1
                if w.epoch is not None:
                    state.candy_window_epoch[w.key] = w.epoch
            state.candy_feature_seeded = True
            self.save()
            return []

        before = (dict(state.candy_grant_tier), dict(state.candy_window_epoch))
        grants = self.evaluate_candy_grants(
            windows, state.candy_grant_tier, state.candy_window_epoch
        )
        for g in grants:
            state.inventory[ItemKind.RARE_CANDY.value] = (
                state.inventory.get(ItemKind.RARE_CANDY.value, 0) + g.count
            )
        # Even with no grants, a re-arm must persist — otherwise a stale tier survives
        # a restart and the next 100% is mistaken for already-granted.
        after = (state.candy_grant_tier, state.candy_window_epoch)
        if grants or after != before or state.armed_candy_windows != armed_before:
            self.save()
        return grants

    # ------------------------------------------------------------------
    # shop and bag
    # ------------------------------------------------------------------

    def buy_item(self, kind: ItemKind) -> None:
        price = self.price(kind)
        if kind.is_passive and self.item_count(kind) > 0:
            raise ValueError(f"{kind.value} is already owned")
        if self.available_tokens < price:
            raise ValueError("not enough tokens")
        # Purchases only raise spent_tokens: growth progress, evolution and the
        # today/week/month stats are all untouched.
        self.state.spent_tokens += price
        self.state.inventory[kind.value] = self.item_count(kind) + 1
        self.save()

    def buy_fresh_egg(self, tier: Rarity | None) -> None:
        if tier not in B.FRESH_EGG_SHOP_TIERS:
            raise ValueError("that egg tier is not for sale")
        state = self.state
        if state.active is None:
            # A shop egg always means "send the current Pokémon off and re-roll".
            raise ValueError("available once your current egg hatches")
        price = self.egg_price(tier)
        if self.available_tokens < price:
            raise ValueError("not enough tokens")
        state.spent_tokens += price
        # A released Pokémon stays in the Pokédex, but it did not graduate: it adds
        # nothing to collected_finals, so it keeps full hatch odds and gets no boost.
        state.dex.append(self._released_entry(state.active))
        state.active = None
        self._generation += 1
        self.current_line = None
        self.hatch_retry_delayed = False
        state.egg_usage = 0
        state.egg_tier = tier
        state.egg_prefetch_base_id = None
        state.pending_unown_form = None
        self.save()

    def candy_stage_costs(self) -> list[int]:
        """Remaining stage costs along the apparent path — a disguise looks ordinary."""
        a = self.state.active
        if a is None or self.current_line is None:
            return []
        if (
            a.ditto_disguise is not None
            and not a.ditto_revealed
            and a.used_at_stage >= self.stage_threshold_of(a)
        ):
            return []  # a reveal is pending
        return [self.stage_threshold_of(a, s) for s in range(a.stage_index, a.total_forms)]

    @property
    def max_candy_use(self) -> int:
        a = self.state.active
        costs = self.candy_stage_costs()
        if a is None or not costs:
            return 0
        needed = max(0, sum(costs) - a.used_at_stage)
        return min(self.item_count(ItemKind.RARE_CANDY), -(-needed // B.RARE_CANDY_XP))

    def plan_candy_use(self, requested: int) -> CandyPlan | None:
        a = self.state.active
        count = min(max(0, requested), self.max_candy_use)
        if count == 0 or a is None:
            return None
        costs = self.candy_stage_costs()
        remaining = a.used_at_stage + count * B.RARE_CANDY_XP
        evolves = False
        for idx, stage_cost in enumerate(costs):
            if remaining < stage_cost:
                break
            remaining -= stage_cost
            if idx == len(costs) - 1:
                return CandyPlan(count, evolves, True, 0, remaining)
            evolves = True
        return CandyPlan(count, evolves, False, remaining if evolves else 0, 0)

    async def use_rare_candy(self, count: int = 1) -> str:
        """Feed candies in one go. Returns "graduated", "evolved" or "progressed"."""
        a = self.state.active
        if a is None:
            raise ValueError("no active Pokémon")
        if self.current_line is None:
            raise ValueError("evolution line still loading")
        if self.item_count(ItemKind.RARE_CANDY) <= 0:
            raise ValueError("no rare candy")
        plan = self.plan_candy_use(count)
        if plan is None:
            raise ValueError("rare candy can't be used right now")
        consumed = plan.count
        if a.ditto_disguise is not None and not a.ditto_revealed:
            # Stop at the reveal, so whole candies beyond it are kept.
            needed = max(0, self.stage_threshold_of(a) - a.used_at_stage)
            consumed = min(consumed, -(-needed // B.RARE_CANDY_XP))
        stage_before = a.stage_index
        dex_before = len(self.state.dex)
        left = self.item_count(ItemKind.RARE_CANDY) - consumed
        self.state.inventory[ItemKind.RARE_CANDY.value] = left
        # Candy XP feeds stage progress only — it is not counted as real usage, so it
        # never appears in the today/week/month totals or the wallet.
        await self._apply_usage(consumed * B.RARE_CANDY_XP)
        self.save()
        if len(self.state.dex) > dex_before:
            return "graduated"
        if self.state.active is not None and self.state.active.stage_index > stage_before:
            return "evolved"
        return "progressed"

    def use_mint(self) -> str:
        if self.state.active is None:
            raise ValueError("no active Pokémon")
        if self.item_count(ItemKind.MINT) <= 0:
            raise ValueError("no mint")
        current = self.state.active.nature
        choices = [n for n in B.NATURES if n != current] or B.NATURES
        nature = self._rng.choice(choices)
        self.state.inventory[ItemKind.MINT.value] = self.item_count(ItemKind.MINT) - 1
        self.state.active.nature = nature
        self.save()
        return nature

    def reset_after_import(self) -> None:
        self.current_line = None
        self.hatch_retry_delayed = False
        self._generation += 1

    # ------------------------------------------------------------------
    # presentation helpers
    # ------------------------------------------------------------------

    def _compute_display_state(
        self, *, burn_tier: str, limit_warning: bool, has_usage_data: bool, today_tokens: int
    ) -> str:
        if self.state.active is None:
            return "egg"
        if self.event is not None:
            return "levelUp"
        if limit_warning:
            return "tired"
        if not has_usage_data or today_tokens == 0:
            return "sleep"
        if burn_tier == "idle":
            return "idle"
        if burn_tier == "normal":
            return "working"
        return "focus"

    def _fire_event(self, kind: str, **payload: object) -> None:
        self.event = {"kind": kind, **payload}
        self.event_until = datetime.now(timezone.utc) + EVENT_WINDOW

    def take_event(self) -> dict | None:
        """Hand the pending flourish to the client exactly once."""
        event = self.event
        if event is None:
            return None
        self.event = None
        self.event_until = None
        return event


# A reset that moves by less than this is the same window reported with jitter;
# a genuinely new 5-hour or weekly window always moves by more.
NEW_WINDOW_MIN_SHIFT = timedelta(hours=1)


def _is_new_window(previous: str, current: str) -> bool:
    try:
        before = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        after = datetime.fromisoformat(current.replace("Z", "+00:00"))
        return after - before > NEW_WINDOW_MIN_SHIFT
    except (TypeError, ValueError):
        return previous != current


def _longest_valid_path(ids: list[int], root: EvoNode) -> tuple[list[int], EvoNode]:
    """Longest prefix of `ids` that actually walks the tree from the root."""
    path = [root.species_id]
    node = root
    if not ids or ids[0] != root.species_id:
        return path, node
    for species_id in ids[1:]:
        child = next((c for c in node.children if c.species_id == species_id), None)
        if child is None:
            break
        path.append(species_id)
        node = child
    return path, node


def repaired_plan(realized_path: list[int], stage_index: int, fallback_route: list[int]) -> list[int]:
    """Splice a fresh route onto the part of the path already walked."""
    if not realized_path:
        return fallback_route
    current_index = min(stage_index, len(realized_path) - 1)
    prefix = realized_path[: current_index + 1]
    if not fallback_route or fallback_route[0] != prefix[-1]:
        return prefix
    return prefix + fallback_route[1:]
