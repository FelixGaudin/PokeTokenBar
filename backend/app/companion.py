"""The Pokémon lifecycle: incubate, hatch, evolve, graduate — plus shop and bag.

Token usage drives everything. The service is fed a per-provider snapshot of today's
totals on each refresh, works out how much is genuinely new since the last
observation, and pours that delta into the egg or the active Pokémon.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import balance as B
from .balance import ItemKind, Rarity
from .pokeapi import EvoLine, EvoNode, PokeAPIClient
from .state import DexEntry, MonState, SaveState, StateStore

log = logging.getLogger(__name__)

# Default source of randomness. Injectable so tests can pin every roll — shiny,
# nature, Ditto disguise and branch choice all draw from here.
_DEFAULT_RNG = random.SystemRandom()

# How long a hatch / evolve / graduate flourish stays on screen.
EVENT_WINDOW = timedelta(seconds=6)


@dataclass(frozen=True)
class CandyWindow:
    """A rate-limit window, as far as the candy grant is concerned."""

    key: str  # stable identifier — must not include volatile fields like resets_at
    name: str  # shown in the grant notice so the reward is explained
    kind: str  # "session" grants 1, "weekly" grants 5
    utilization: float  # 0-100+


@dataclass(frozen=True)
class CandyGrant:
    window_key: str
    window_name: str
    count: int


class CompanionService:
    def __init__(
        self,
        store: StateStore,
        poke: PokeAPIClient,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.poke = poke
        self._rng = rng or _DEFAULT_RNG
        self.current_line: EvoLine | None = None
        self.display_state = "egg"
        self.just_evolved_to: str | None = None
        self.just_graduated: str | None = None
        self.event: dict | None = None
        self.event_until: datetime | None = None
        self._hatching = False
        self._revealing_ditto = False
        self._loading_line = False
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
    def owns_shiny_charm(self) -> bool:
        return self.item_count(ItemKind.SHINY_CHARM) > 0

    def item_count(self, kind: ItemKind) -> int:
        return self.state.inventory.get(kind.value, 0)

    @property
    def available_tokens(self) -> int:
        """The shop wallet: lifetime usage minus lifetime shop spend."""
        return max(0, self.state.used_since_install - self.state.spent_tokens)

    @property
    def stage_threshold(self) -> int:
        a = self.state.active
        if a is None:
            return 0
        return B.phase_threshold(a.rarity, a.total_forms, a.stage_index)

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
            if state.egg_usage >= B.EGG_HATCH_THRESHOLD:
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
            and a.used_at_stage >= B.phase_threshold(a.rarity, a.total_forms, 0)
        ):
            await self._reveal_ditto()

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
        state.active.used_at_stage += delta

        line = self.current_line
        if line is None:
            self.save()
            return

        guard = 0
        while state.active is not None and guard < 50:
            guard += 1
            a = state.active
            threshold = B.phase_threshold(a.rarity, a.total_forms, a.stage_index)
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
            name = line.display_name(nxt.species_id, state.language)
            self.just_evolved_to = name
            self._fire_event("evolve", name=name, shiny=a.is_shiny, species_id=nxt.species_id)

        self.save()

    # ------------------------------------------------------------------
    # hatching
    # ------------------------------------------------------------------

    async def _hatch(self) -> None:
        state = self.state
        if state.active is not None or state.egg_usage < B.EGG_HATCH_THRESHOLD:
            return
        self._hatching = True
        generation = self._generation
        try:
            base_id = await self._choose_base()
            if base_id is None:
                return
            try:
                line = await self.poke.line(base_id)
            except Exception as exc:  # noqa: BLE001 - keep the egg, retry next tick
                log.warning("hatch: line fetch failed for base %d (%s)", base_id, exc)
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
                self.save()
                return

            self.current_line = line
            overflow = max(0, state.egg_usage - B.EGG_HATCH_THRESHOLD)
            state.egg_usage = 0
            state.egg_tier = None  # the guarantee is consumed by this hatch
            state.egg_prefetch_base_id = None

            is_shiny = self._rng.randrange(
                B.SHINY_DENOMINATOR_WITH_CHARM if self.owns_shiny_charm else B.SHINY_DENOMINATOR
            ) == 0
            nature = self._rng.choice(B.NATURES)

            plan = self._evolution_plan(line.tree, line.base_id)
            ditto_disguise: int | None = None
            if (
                line.rarity is Rarity.COMMON
                and len(plan) >= 2
                and self._rng.randrange(B.DITTO_DISGUISE_DENOMINATOR) == 0
            ):
                ditto_disguise = line.base_id

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
            )
            # A disguise hides its shininess until the reveal.
            show_shiny = is_shiny and ditto_disguise is None
            self.just_evolved_to = None
            log.info(
                "hatch: base=%d rarity=%s shiny=%s forms=%d ditto=%s",
                line.base_id,
                line.rarity.value,
                is_shiny,
                len(plan),
                ditto_disguise is not None,
            )
            self._fire_event(
                "hatch",
                name=line.display_name(line.base_id, state.language),
                shiny=show_shiny,
                species_id=line.base_id,
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
        re-hatches or shiny hunting.
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
            collected_prefixes = {c.split(":", 1)[0] for c in state.collected_finals}
            weights = [
                max(1, e.capture_rate // 2)
                if str(e.id) in collected_prefixes
                else max(1, e.capture_rate)
                for e in index
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
        fresh = [
            child
            for child in node.children
            if any(f"{base_id}:{fid}" not in set(self.state.collected_finals) for fid in child.final_ids)
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
        if a.used_at_stage < B.phase_threshold(a.rarity, a.total_forms, 0):
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
            first_threshold = B.phase_threshold(m.rarity, m.total_forms, 0)
            if m.used_at_stage < first_threshold:
                return

            disguise_name = (
                self.current_line.display_name(m.base_id, state.language)
                if self.current_line
                else f"#{m.base_id}"
            )
            carry_over = max(0, m.used_at_stage - first_threshold)
            plan = self._evolution_plan(ditto_line.tree, ditto_line.base_id)

            m.base_id = ditto_line.base_id
            m.current_id = ditto_line.base_id
            m.path_ids = [ditto_line.base_id]
            m.planned_path_ids = plan
            m.stage_index = 0
            m.rarity = ditto_line.rarity
            m.total_forms = len(plan)
            m.used_at_stage = carry_over
            m.ditto_revealed = True
            self.current_line = ditto_line

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
    # graduation
    # ------------------------------------------------------------------

    def _graduate(self) -> None:
        state = self.state
        a = state.active
        if a is None:
            return
        final_id = a.current_id
        marker = f"{a.base_id}:{final_id}"
        if marker not in state.collected_finals:
            state.collected_finals.append(marker)

        names: dict[str, dict[str, str]] = {}
        if self.current_line is not None:
            for sid in a.path_ids:
                by_lang = self.current_line.names.get(sid)
                if by_lang:
                    names[str(sid)] = by_lang

        state.dex.append(
            DexEntry(
                base_id=a.base_id,
                final_id=final_id,
                chain_order=list(a.path_ids),
                rarity=a.rarity,
                caught_at=datetime.now(timezone.utc),
                is_shiny=a.is_shiny,
                nature=a.nature,
                names=names,
            )
        )
        name = (
            self.current_line.display_name(final_id, state.language)
            if self.current_line
            else f"#{final_id}"
        )
        self.just_graduated = name
        self._fire_event("graduate", name=name, shiny=a.is_shiny, species_id=final_id)

        state.active = None
        self._generation += 1
        self.current_line = None
        state.egg_usage = 0  # the next egg incubates from scratch
        # egg_tier is untouched: reaching here means a Pokémon was active, so any
        # guarantee was already consumed at hatch time.

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
    # rare candy grants
    # ------------------------------------------------------------------

    @staticmethod
    def evaluate_candy_grants(
        windows: list[CandyWindow], grant_tier: dict[str, int]
    ) -> list[CandyGrant]:
        """Edge-triggered: grant only on the transition up through 100%.

        Below 100% the window is removed from the map, which re-arms it. A window
        already granted (tier >= 1) never grants again until it re-arms.
        """
        grants: list[CandyGrant] = []
        for w in windows:
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
        if not state.candy_feature_seeded:
            for w in windows:
                if w.utilization >= 100:
                    state.candy_grant_tier[w.key] = 1
            state.candy_feature_seeded = True
            self.save()
            return []

        before = dict(state.candy_grant_tier)
        grants = self.evaluate_candy_grants(windows, state.candy_grant_tier)
        for g in grants:
            state.inventory[ItemKind.RARE_CANDY.value] = (
                state.inventory.get(ItemKind.RARE_CANDY.value, 0) + g.count
            )
        # Even with no grants, a re-arm must persist — otherwise a stale tier survives
        # a restart and the next 100% is mistaken for already-granted.
        if grants or state.candy_grant_tier != before:
            self.save()
        return grants

    # ------------------------------------------------------------------
    # shop and bag
    # ------------------------------------------------------------------

    def buy_item(self, kind: ItemKind) -> None:
        price = kind.shop_price
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
        price = B.fresh_egg_price(tier)
        if self.available_tokens < price:
            raise ValueError("not enough tokens")
        state = self.state
        state.spent_tokens += price
        # A discarded Pokémon simply disappears — it does not graduate, so the dex and
        # the branch-choice weights are untouched, as if it had never been drawn.
        state.active = None
        self._generation += 1
        self.current_line = None
        state.egg_usage = 0
        state.egg_tier = tier
        state.egg_prefetch_base_id = None
        self.save()

    async def use_rare_candy(self) -> None:
        if self.state.active is None:
            raise ValueError("no active Pokémon")
        if self.current_line is None:
            raise ValueError("evolution line still loading")
        if self.item_count(ItemKind.RARE_CANDY) <= 0:
            raise ValueError("no rare candy")
        self.state.inventory[ItemKind.RARE_CANDY.value] = self.item_count(ItemKind.RARE_CANDY) - 1
        # Candy XP feeds stage progress only — it is not counted as real usage, so it
        # never appears in the today/week/month totals or the wallet.
        await self._apply_usage(B.RARE_CANDY_XP)
        self.save()

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
