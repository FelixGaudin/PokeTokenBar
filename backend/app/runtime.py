"""Wires the pieces together and owns the refresh loop.

The loop runs server-side rather than being driven by the browser, so the Pokémon
keeps incubating and evolving whether or not anyone has the page open.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from . import balance as B
from .balance import ItemKind, Rarity
from .companion import CandyWindow, CompanionService
from .config import Settings
from .limits import LimitsProvider, LimitsUnavailable, LimitStatus
from .pokeapi import PokeAPIClient, resolve_name
from .readers.claude_code import PROVIDER_ID
from .schemas import (
    ActiveView,
    BagItemView,
    BlockView,
    ChainSlot,
    CollectionView,
    CompanionView,
    DayPoint,
    DexChainSpecies,
    DexEntryView,
    DexSpeciesView,
    EggView,
    LimitsView,
    LimitWindowView,
    MetaView,
    PeriodView,
    ShopEggView,
    ShopItemView,
    ShopView,
    StateView,
    UsageView,
    WalletView,
)
from .sprites import SpriteStore
from .state import StateStore
from .usage import PeriodUsage, UsageService, UsageSnapshot

log = logging.getLogger(__name__)

# A window at or above this percentage makes the Pokémon look worn out.
CRIT_THRESHOLD = 95.0

ITEM_LABELS = {
    ItemKind.RARE_CANDY: "Rare Candy",
    ItemKind.MINT: "Mint",
    ItemKind.SHINY_CHARM: "Shiny Charm",
}

ITEM_DESCRIPTIONS = {
    ItemKind.RARE_CANDY: "Feeds 100M growth tokens to your Pokémon. Never skips more than one stage.",
    ItemKind.MINT: "Re-rolls your Pokémon's nature. Purely cosmetic.",
    ItemKind.SHINY_CHARM: "Held item. Improves shiny odds from 1/64 to 1/48 on every future hatch.",
}

EGG_LABELS: dict[Rarity | None, tuple[str, str]] = {
    None: ("Egg", "Discards your current Pokémon and starts a fresh egg. No rarity guarantee."),
    Rarity.UNCOMMON: ("Fine Egg", "Guarantees Uncommon or better. Legendaries can still appear."),
    Rarity.RARE: ("Prime Egg", "Guarantees Rare or better. Roughly a 10% shot at a Legendary."),
}


class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)

        self.store = StateStore(settings.state_file)
        self.poke = PokeAPIClient(settings.cache_dir)
        self.sprites = SpriteStore(settings.sprite_dir)
        self.companion = CompanionService(self.store, self.poke)
        self.usage = UsageService(settings.claude_roots, settings.timezone)
        self.limits_provider = LimitsProvider(settings.credentials_file, settings.oauth_token)

        self.snapshot: UsageSnapshot | None = None
        self.limits: LimitStatus | None = None
        self.limits_error: str | None = None
        self.limits_auth_expired = False
        self._limits_next_attempt = 0.0
        self.last_refresh: datetime | None = None
        self._refresh_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)
        self._task = asyncio.create_task(self._loop(), name="ptb-refresh")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        await self.poke.aclose()

    async def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)
        return self._http

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive any single failure
                log.exception("refresh failed")
            await asyncio.sleep(max(5.0, self.settings.poll_interval))

    # -- refresh -----------------------------------------------------------

    async def refresh(self) -> None:
        async with self._refresh_lock:
            # Scanning walks the log tree and parses files — keep it off the loop.
            snapshot = await asyncio.to_thread(self.usage.snapshot)
            self.snapshot = snapshot

            if self.settings.limits_enabled:
                await self._refresh_limits()

            limit_warning = (
                self.limits is not None and self.limits.max_utilization >= CRIT_THRESHOLD
            )
            await self.companion.refresh(
                today_by_provider={PROVIDER_ID: snapshot.today.total_tokens},
                today_date=snapshot.today_date,
                burn_tier=snapshot.burn_tier,
                limit_warning=limit_warning,
                has_usage_data=True,
            )

            if self.limits is not None:
                windows = [
                    CandyWindow(
                        key=w.key,
                        name=w.name,
                        kind="weekly" if w.kind.startswith("weekly") else "session",
                        utilization=w.utilization or 0.0,
                    )
                    for w in self.limits.windows
                ]
                self.companion.grant_candies(windows, limits_ready=True)

            self.last_refresh = datetime.now(timezone.utc)

    async def _refresh_limits(self) -> None:
        if time.time() < self._limits_next_attempt:
            return
        client = await self.http()
        try:
            self.limits = await self.limits_provider.fetch(client)
            self.limits_error = None
            self.limits_auth_expired = False
            self._limits_next_attempt = 0.0
        except LimitsUnavailable as exc:
            self.limits_error = exc.reason
            self.limits_auth_expired = exc.auth_expired
            # Back off so a 429 or an expired login is not hammered every tick.
            backoff = exc.retry_after or self.settings.limits_interval
            self._limits_next_attempt = time.time() + backoff
            log.info("limits unavailable: %s (retrying in %.0fs)", exc.reason, backoff)

    # -- view building -----------------------------------------------------

    def build_state(self) -> StateView:
        return StateView(
            companion=self._companion_view(),
            usage=self._usage_view(),
            limits=self._limits_view(),
            wallet=WalletView(
                available_tokens=self.companion.available_tokens,
                used_since_install=self.store.state.used_since_install,
                spent_tokens=self.store.state.spent_tokens,
            ),
            bag=self._bag_view(),
            shop=self._shop_view(),
            collection=self._collection_view(),
            meta=MetaView(
                last_refresh=self.last_refresh.isoformat() if self.last_refresh else None,
                poll_interval=self.settings.poll_interval,
                timezone=str(self.settings.timezone),
                providers=[PROVIDER_ID],
                log_roots=[str(p) for p in self.settings.claude_roots],
                language=short_language(self.store.state.language),
            ),
        )

    def _companion_view(self) -> CompanionView:
        state = self.store.state
        egg = EggView(
            usage=state.egg_usage,
            threshold=B.EGG_HATCH_THRESHOLD,
            progress=min(1.0, max(0.0, state.egg_usage / B.EGG_HATCH_THRESHOLD)),
            tokens_to_hatch=max(0, B.EGG_HATCH_THRESHOLD - state.egg_usage),
            guaranteed_tier=state.egg_tier,
        )

        active_view: ActiveView | None = None
        a = state.active
        if a is not None:
            line = self.companion.current_line
            lang = state.language
            threshold = B.phase_threshold(a.rarity, a.total_forms, a.stage_index)
            is_final = a.stage_index >= a.total_forms - 1
            name = line.display_name(a.current_id, lang) if line else f"#{a.current_id}"

            chain: list[ChainSlot] = []
            for idx in range(a.total_forms):
                reached = idx <= a.stage_index
                species_id = (
                    a.path_ids[idx]
                    if reached and idx < len(a.path_ids)
                    else (a.planned_path_ids[idx] if idx < len(a.planned_path_ids) else None)
                )
                # Future forms are deliberately hidden so the evolution stays a surprise.
                chain.append(
                    ChainSlot(
                        species_id=species_id if reached else None,
                        name=(line.display_name(species_id, lang) if (reached and line and species_id) else None),
                        reached=reached,
                        is_current=idx == a.stage_index,
                        mystery=not reached,
                    )
                )

            active_view = ActiveView(
                base_id=a.base_id,
                species_id=a.current_id,
                name=name,
                rarity=a.rarity,
                stage_index=a.stage_index,
                total_forms=a.total_forms,
                is_final=is_final,
                is_shiny=a.is_shiny and not (a.ditto_disguise is not None and not a.ditto_revealed),
                nature=a.nature,
                nature_label=B.NATURE_LABELS.get(a.nature or "", None),
                used_at_stage=a.used_at_stage,
                threshold=threshold,
                progress=min(1.0, max(0.0, a.used_at_stage / threshold)) if threshold else 0.0,
                tokens_to_next=max(0, threshold - a.used_at_stage),
                chain=chain,
                line_loaded=line is not None,
                hatched_at=a.hatched_at.isoformat() if a.hatched_at else None,
            )

        return CompanionView(
            display_state=self.companion.display_state,
            egg=egg,
            active=active_view,
            event=self.companion.take_event(),
            just_evolved_to=self.companion.just_evolved_to,
            just_graduated=self.companion.just_graduated,
        )

    def _usage_view(self) -> UsageView:
        snapshot = self.snapshot
        if snapshot is None:
            empty = PeriodView(total_tokens=0, cost=0.0)
            return UsageView(
                today_date=datetime.now(self.settings.timezone).strftime("%Y-%m-%d"),
                today=empty,
                week=empty,
                month=empty,
                block=None,
                burn_per_minute=0.0,
                burn_tier="idle",
            )

        def period(p: PeriodUsage) -> PeriodView:
            return PeriodView(
                total_tokens=p.total_tokens,
                cost=p.cost,
                input=p.input,
                output=p.output,
                cache_write=p.cache_write,
                cache_read=p.cache_read,
                by_model=p.by_model,
            )

        block = (
            BlockView(
                start=snapshot.block.start,
                end=snapshot.block.end,
                total_tokens=snapshot.block.total_tokens,
                cost=snapshot.block.cost,
                tokens_per_minute=snapshot.block.tokens_per_minute,
            )
            if snapshot.block
            else None
        )

        return UsageView(
            today_date=snapshot.today_date,
            today=period(snapshot.today),
            week=period(snapshot.week),
            month=period(snapshot.month),
            block=block,
            burn_per_minute=snapshot.burn_per_minute,
            burn_tier=snapshot.burn_tier,
            daily_history=[
                DayPoint(date=d, total_tokens=t, cost=c) for d, t, c in snapshot.daily_history
            ],
            scanned_files=snapshot.scanned_files,
        )

    def _limits_view(self) -> LimitsView:
        if not self.settings.limits_enabled:
            return LimitsView(available=False, enabled=False, error="limits disabled")
        if self.limits is None:
            return LimitsView(
                available=False,
                enabled=True,
                source=self.limits_provider.source,
                error=self.limits_error,
                auth_expired=self.limits_auth_expired,
            )
        return LimitsView(
            available=True,
            enabled=True,
            source=self.limits_provider.source,
            plan=self.limits.plan,
            windows=[
                LimitWindowView(
                    key=w.key,
                    name=w.name,
                    kind=w.kind,
                    utilization=w.utilization or 0.0,
                    resets_at=w.resets_at,
                )
                for w in self.limits.windows
            ],
            error=self.limits_error,
            auth_expired=self.limits_auth_expired,
            fetched_at=datetime.fromtimestamp(self.limits.fetched_at, timezone.utc).isoformat(),
        )

    def _bag_view(self) -> list[BagItemView]:
        out: list[BagItemView] = []
        has_active = self.store.state.active is not None
        line_loaded = self.companion.current_line is not None
        for kind in ItemKind:
            count = self.companion.item_count(kind)
            if count <= 0:
                continue
            if kind is ItemKind.RARE_CANDY:
                usable = has_active and line_loaded
            elif kind is ItemKind.MINT:
                usable = has_active
            else:
                usable = False
            out.append(
                BagItemView(
                    kind=kind.value,
                    label=ITEM_LABELS[kind],
                    count=count,
                    emoji=kind.emoji,
                    sprite_name=kind.sprite_name,
                    passive=kind.is_passive,
                    usable=usable,
                )
            )
        return out

    def _shop_view(self) -> ShopView:
        wallet = self.companion.available_tokens
        items = [
            ShopItemView(
                kind=kind.value,
                label=ITEM_LABELS[kind],
                description=ITEM_DESCRIPTIONS[kind],
                price=kind.shop_price,
                emoji=kind.emoji,
                sprite_name=kind.sprite_name,
                affordable=wallet >= kind.shop_price,
                owned=kind.is_passive and self.companion.item_count(kind) > 0,
                passive=kind.is_passive,
            )
            for kind in ItemKind
        ]
        eggs = []
        for tier in B.FRESH_EGG_SHOP_TIERS:
            label, description = EGG_LABELS[tier]
            price = B.fresh_egg_price(tier)
            eggs.append(
                ShopEggView(
                    tier=tier,
                    label=label,
                    description=description,
                    price=price,
                    affordable=wallet >= price,
                )
            )
        return ShopView(items=items, eggs=eggs)

    def _collection_view(self) -> CollectionView:
        state = self.store.state
        lang = state.language
        counts: dict[str, int] = {r.value: 0 for r in Rarity}
        catch_log: list[DexEntryView] = []

        for entry in state.dex:
            counts[entry.rarity.value] = counts.get(entry.rarity.value, 0) + 1
            chain = [
                DexChainSpecies(
                    species_id=sid,
                    name=_name_from(entry.names, sid, lang),
                )
                for sid in entry.chain_order
            ]
            catch_log.append(
                DexEntryView(
                    base_id=entry.base_id,
                    final_id=entry.final_id,
                    final_name=_name_from(entry.names, entry.final_id, lang),
                    rarity=entry.rarity,
                    is_shiny=entry.is_shiny,
                    nature=entry.nature,
                    nature_label=B.NATURE_LABELS.get(entry.nature or "", None),
                    caught_at=entry.caught_at.isoformat() if entry.caught_at else None,
                    chain=chain,
                )
            )

        # Catch log reads newest first; the Pokédex is ordered by dex number.
        catch_log.sort(key=lambda e: e.caught_at or "", reverse=True)

        by_species: dict[int, DexSpeciesView] = {}
        for entry in state.dex:
            existing = by_species.get(entry.final_id)
            caught = entry.caught_at.isoformat() if entry.caught_at else None
            if existing is None:
                by_species[entry.final_id] = DexSpeciesView(
                    final_id=entry.final_id,
                    name=_name_from(entry.names, entry.final_id, lang),
                    rarity=entry.rarity,
                    count=1,
                    shiny_count=1 if entry.is_shiny else 0,
                    first_caught_at=caught,
                )
            else:
                existing.count += 1
                if entry.is_shiny:
                    existing.shiny_count += 1
                if caught and (existing.first_caught_at is None or caught < existing.first_caught_at):
                    existing.first_caught_at = caught

        return CollectionView(
            total=len(state.dex),
            counts_by_rarity=counts,
            catch_log=catch_log,
            pokedex=sorted(by_species.values(), key=lambda s: s.final_id),
        )


# PokéAPI reports Japanese under a script-qualified code; the UI uses the short form.
_STORED_TO_SHORT = {"ja-Hrkt": "ja"}


def short_language(stored: str) -> str:
    return _STORED_TO_SHORT.get(stored, stored)


def _name_from(names: dict[str, dict[str, str]], species_id: int, lang: str) -> str:
    return resolve_name(names.get(str(species_id)) or {}, lang, species_id)
