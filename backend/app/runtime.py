"""Wires the pieces together and owns the refresh loop.

The loop runs server-side rather than being driven by the browser, so the Pokémon
keeps incubating and evolving whether or not anyone has the page open.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from . import balance as B
from .accounts import AccountFolder, discover, parse_entries
from .balance import ItemKind, Rarity
from .companion import CandyWindow, CompanionService
from .config import Settings
from .limits import LimitsProvider, LimitStatus, LimitsUnavailable
from .pokeapi import PokeAPIClient, resolve_name
from .profile import STAT_KEYS, PokemonDetails, PokemonProfile, actual_stat, stat_bar_scale
from .readers.claude_code import PROVIDER_ID, CostCoverage
from .recap import make_recap, merge_ledger, prune_ledger
from .schemas import (
    AbilityView,
    AccountLimitsView,
    ActiveView,
    BagItemView,
    BlockView,
    CandyPreviewView,
    ChainSlot,
    CollectionView,
    CompanionView,
    CostCoverageView,
    DayPoint,
    DexChainSpecies,
    DexEntryView,
    DexSpeciesView,
    EggView,
    IndividualView,
    KnownMoveView,
    LimitsView,
    LimitWindowView,
    MetaView,
    MoveMethodView,
    MoveView,
    PeriodView,
    PokemonDetailView,
    RecapBucketView,
    RecapGraduateView,
    RecapView,
    ShopEggView,
    ShopItemView,
    ShopView,
    StateView,
    StatView,
    UnownFormView,
    UsageView,
    WalletView,
)
from .sprites import SpriteStore
from .state import DexEntry, MonState, PreferenceStore, StateStore
from .usage import PeriodUsage, UsageService, UsageSnapshot

log = logging.getLogger(__name__)

# A window at or above this percentage makes the Pokémon look worn out.
CRIT_THRESHOLD = 95.0

# Ceiling on the exponential limits backoff, so it always recovers eventually.
LIMITS_MAX_BACKOFF = 1800.0

# Bulk candy previews are computed for every count up to this many, and one use
# of the Bag never feeds more.
MAX_CANDY_PREVIEWS = 100

ITEM_LABELS = {
    ItemKind.RARE_CANDY: "Rare Candy",
    ItemKind.MINT: "Mint",
    ItemKind.SHINY_CHARM: "Shiny Charm",
}

EGG_LABELS: dict[Rarity | None, tuple[str, str]] = {
    None: ("Egg", "Sends off your current Pokémon and starts a fresh egg. No rarity guarantee."),
    Rarity.UNCOMMON: ("Fine Egg", "Guarantees Uncommon or better. Legendaries can still appear."),
    Rarity.RARE: ("Prime Egg", "Guarantees Rare or better. Roughly a 10% shot at a Legendary."),
}

EGG_LOCKED_REASON = "Available once your current egg hatches."


@dataclass
class AccountLimits:
    """One Claude login: its provider, last good reading and its own backoff."""

    id: str
    fallback_title: str
    provider: LimitsProvider
    folder: AccountFolder | None = None
    status: LimitStatus | None = None
    error: str | None = None
    auth_expired: bool = False
    next_attempt: float = 0.0
    failures: int = 0
    # Set when another tab already shows this login.
    alias_of: str | None = field(default=None)

    @property
    def is_default(self) -> bool:
        return self.folder is None

    @property
    def title(self) -> str:
        s = self.status
        if s is None or not s.account_email:
            identity = self.provider.fallback_identity()
            if identity is None:
                return self.fallback_title
            email, org = identity
        else:
            email, org = s.account_email, s.account_org
        # Team plans read best as the organisation; personal plans as the email.
        return org if org and email not in org else email


class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)

        self.store = StateStore(settings.state_file)
        self.prefs = PreferenceStore(settings.preferences_file)
        self.poke = PokeAPIClient(settings.cache_dir)
        self.sprites = SpriteStore(settings.sprite_dir)
        self.companion = CompanionService(self.store, self.poke, prefs=self.prefs)
        self.usage = UsageService(list(settings.claude_roots), settings.timezone)
        self.default_account = AccountLimits(
            id="default",
            fallback_title="~/.claude",
            provider=LimitsProvider(settings.credentials_file, settings.default_claude_json),
        )
        self.extra_accounts: list[AccountLimits] = []

        self.snapshot: UsageSnapshot | None = None
        self.last_refresh: datetime | None = None
        self._refresh_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None

    # -- compatibility accessors for the default account -------------------

    @property
    def limits(self) -> LimitStatus | None:
        return self.default_account.status

    @property
    def limits_error(self) -> str | None:
        return self.default_account.error

    @property
    def limits_auth_expired(self) -> bool:
        return self.default_account.auth_expired

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

    def _sync_account_folders(self) -> None:
        """Pick up account folders mounted or removed since the last refresh."""
        folders = discover(
            parse_entries(self.settings.account_dirs),
            self.settings.accounts_root,
            [r.parent for r in self.settings.claude_roots],
        )
        known = {a.id: a for a in self.extra_accounts}
        self.extra_accounts = [
            known.get(f.id)
            or AccountLimits(
                id=f.id,
                fallback_title=f.label,
                provider=LimitsProvider(f.credentials_file, f.claude_json),
                folder=f,
            )
            for f in folders
        ]
        roots = list(self.settings.claude_roots) + [f.projects for f in folders]
        if roots != self.usage.roots:
            self.usage.roots = roots

    @property
    def accounts(self) -> list[AccountLimits]:
        return [self.default_account, *self.extra_accounts]

    async def refresh(self) -> None:
        async with self._refresh_lock:
            self._sync_account_folders()
            # Scanning walks the log tree and parses files — keep it off the loop.
            snapshot = await asyncio.to_thread(self.usage.snapshot)
            self.snapshot = snapshot

            if self.settings.limits_enabled:
                for account in self.accounts:
                    await self._refresh_limits(account)
                self._dedupe_accounts()

            loaded = [a for a in self.accounts if a.status is not None and a.alias_of is None]
            limit_warning = any(a.status.max_utilization >= CRIT_THRESHOLD for a in loaded)
            # With no readable log directory there is nothing to observe, so report an
            # empty map rather than a confident zero. Claiming a zero observation would
            # set the install baseline from data that was never read, and later credit
            # a whole day at once when the mount finally appears.
            has_source = snapshot.has_source
            await self.companion.refresh(
                today_by_provider=self._today_by_provider(snapshot) if has_source else {},
                today_date=snapshot.today_date,
                burn_tier=snapshot.burn_tier,
                limit_warning=limit_warning,
                has_usage_data=has_source,
            )

            if loaded:
                self.companion.grant_candies(self._candy_windows(loaded), limits_ready=True)

            if has_source:
                self._record_ledger(snapshot)
            self.store.auto_snapshot()
            self.last_refresh = datetime.now(timezone.utc)

    def _today_by_provider(self, snapshot: UsageSnapshot) -> dict[str, int]:
        """One ledger line per log source, so each is credited on its own.

        A single combined total would read an account folder that is briefly
        unreadable as a regression, then credit its tokens a second time when it
        returns. With a line each, a missing source is left alone and a new one is
        seeded rather than credited with its history.
        """
        by_root = snapshot.today_by_root
        out: dict[str, int] = {}
        default_roots = [str(r) for r in self.settings.claude_roots if str(r) in by_root]
        if default_roots:
            out[PROVIDER_ID] = sum(by_root[r] for r in default_roots)
        for account in self.extra_accounts:
            root = str(account.folder.projects) if account.folder else None
            if root in by_root:
                out[f"{PROVIDER_ID}:{account.id}"] = by_root[root]
        return out

    def _candy_windows(self, loaded: list[AccountLimits]) -> list[CandyWindow]:
        several = len(loaded) > 1
        out: list[CandyWindow] = []
        for account in loaded:
            for w in account.status.windows:
                # Only the 5-hour and the all-model weekly windows pay out.
                if w.kind == "session":
                    kind = "session"
                elif w.kind in ("weekly", "weekly_all"):
                    kind = "weekly"
                else:
                    continue
                out.append(
                    CandyWindow(
                        # The default account keeps its historical keys.
                        key=w.key if account.is_default else f"{account.id}:{w.key}",
                        name=f"{w.name} ({account.title})" if several else w.name,
                        kind=kind,
                        utilization=w.utilization or 0.0,
                        epoch=w.resets_at,
                        needs_arming=not account.is_default,
                    )
                )
        return out

    def _record_ledger(self, snapshot: UsageSnapshot) -> None:
        state = self.store.state
        series = snapshot.recent_daily
        today = datetime.now(self.settings.timezone).date()
        changed = merge_ledger(state, series)
        changed = prune_ledger(state, today) or changed
        if changed:
            self.store.save()

    def _dedupe_accounts(self) -> None:
        """A folder holding a login another tab already shows gets no second tab."""
        owners: dict[str, str] = {}
        for account in self.accounts:
            account.alias_of = None
            s = account.status
            identity = (s.account_email, s.account_org) if s and s.account_email else (
                account.provider.fallback_identity()
            )
            if identity is None:
                continue
            key = f"{identity[0]}\n{identity[1] or ''}"
            if key in owners:
                account.alias_of = owners[key]
            else:
                owners[key] = account.id

    async def _refresh_limits(self, account: AccountLimits) -> None:
        if time.time() < account.next_attempt:
            return
        client = await self.http()
        try:
            account.status = await account.provider.fetch(client)
            account.error = None
            account.auth_expired = False
            account.next_attempt = 0.0
            account.failures = 0
        except LimitsUnavailable as exc:
            account.error = exc.reason
            account.auth_expired = exc.auth_expired
            account.failures += 1
            # A flat retry against a shared endpoint keeps re-tripping its rate limit,
            # especially when more than one instance uses the same token. Double the
            # wait on each consecutive failure, capped, and always honour Retry-After.
            backoff = exc.retry_after or min(
                self.settings.limits_interval * (2 ** (account.failures - 1)),
                LIMITS_MAX_BACKOFF,
            )
            account.next_attempt = time.time() + backoff
            log.info(
                "limits unavailable for %s: %s (attempt %d, retrying in %.0fs)",
                account.fallback_title,
                exc.reason,
                account.failures,
                backoff,
            )

    # -- view building -----------------------------------------------------

    def build_state(self) -> StateView:
        prefs = self.prefs.prefs
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
                log_roots=[str(p) for p in self.usage.roots],
                language=short_language(self.store.state.language),
                log_roots_present=(self.snapshot.present_roots if self.snapshot else []),
                log_files_found=(self.snapshot.total_files if self.snapshot else 0),
                source_warning=self._source_warning(),
                growth_difficulty=prefs.growth_difficulty,
                shop_difficulty=prefs.shop_difficulty,
                difficulty_min=B.DIFFICULTY_MIN,
                difficulty_max=B.DIFFICULTY_MAX,
                limit_display=prefs.limit_display,
                crit_threshold=CRIT_THRESHOLD,
            ),
        )

    def _source_warning(self) -> str | None:
        """Explain an all-zero reading, so it is never mistaken for "no usage today"."""
        snapshot = self.snapshot
        if snapshot is None:
            return "Waiting for the first scan."
        if not snapshot.has_source:
            roots = ", ".join(snapshot.missing_roots) or "(none configured)"
            return (
                f"No log directory found at {roots}. Mount your Claude config "
                f"read-only, or set PTB_CLAUDE_ROOTS. Token tracking is idle until then."
            )
        if snapshot.total_files == 0:
            roots = ", ".join(snapshot.present_roots)
            return (
                f"{roots} exists but holds no session files yet. Run Claude Code once "
                f"and the numbers will start moving."
            )
        return None

    def _species_name(self, species_id: int, form: str | None = None) -> str:
        line = self.companion.current_line
        lang = self.store.state.language
        name = line.display_name(species_id, lang) if line else f"#{species_id}"
        return B.display_name(name, species_id, form)

    def _companion_view(self) -> CompanionView:
        state = self.store.state
        hatch_at = self.companion.egg_hatch_threshold
        egg = EggView(
            usage=state.egg_usage,
            threshold=hatch_at,
            progress=min(1.0, max(0.0, state.egg_usage / hatch_at)),
            tokens_to_hatch=max(0, hatch_at - state.egg_usage),
            guaranteed_tier=state.egg_tier,
            hatch_delayed=self.companion.hatch_retry_delayed,
        )

        active_view: ActiveView | None = None
        a = state.active
        if a is not None:
            line = self.companion.current_line
            lang = state.language
            threshold = self.companion.stage_threshold_of(a)
            is_final = a.stage_index >= a.total_forms - 1

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
                        name=(
                            line.display_name(species_id, lang)
                            if (reached and line and species_id)
                            else None
                        ),
                        reached=reached,
                        is_current=idx == a.stage_index,
                        mystery=not reached,
                    )
                )

            active_view = ActiveView(
                base_id=a.base_id,
                species_id=a.current_id,
                name=self._species_name(a.current_id, a.unown_form),
                rarity=a.rarity,
                stage_index=a.stage_index,
                total_forms=a.total_forms,
                is_final=is_final,
                is_shiny=a.current_is_shiny,
                nature=a.nature,
                nature_label=B.NATURE_LABELS.get(a.nature or "", None),
                used_at_stage=a.used_at_stage,
                threshold=threshold,
                progress=min(1.0, max(0.0, a.used_at_stage / threshold)) if threshold else 0.0,
                tokens_to_next=max(0, threshold - a.used_at_stage),
                chain=chain,
                line_loaded=line is not None,
                hatched_at=a.hatched_at.isoformat() if a.hatched_at else None,
                growth_multiplier=B.REPEAT_GROWTH_MULTIPLIER if a.has_growth_boost else None,
                is_high_value=self.companion.is_high_value_companion,
                level=a.profile.level if a.profile else None,
                unown_form=B.resolve_unown_form(a.current_id, a.unown_form),
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
                cost_coverage=_coverage(p.coverage),
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
                cost_coverage=_coverage(snapshot.block.coverage),
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
            month_daily=[
                DayPoint(
                    date=d.date,
                    total_tokens=d.total_tokens,
                    cost=d.cost,
                    cost_coverage=_coverage(d.coverage),
                )
                for d in snapshot.month_daily
            ],
            scanned_files=snapshot.scanned_files,
        )

    def _account_view(self, account: AccountLimits) -> AccountLimitsView:
        s = account.status
        folder = str(account.folder.root) if account.folder else None
        if s is None:
            return AccountLimitsView(
                id=account.id,
                title=account.title,
                is_default=account.is_default,
                available=False,
                error=account.error,
                auth_expired=account.auth_expired,
                folder=folder,
            )
        return AccountLimitsView(
            id=account.id,
            title=account.title,
            is_default=account.is_default,
            available=True,
            # Retaining the last good reading is deliberate, but presenting it as
            # current is not — flag it so the UI can say how old it is.
            stale=account.error is not None,
            plan=s.plan,
            account=s.account,
            windows=[
                LimitWindowView(
                    key=w.key,
                    name=w.name,
                    kind=w.kind,
                    utilization=w.utilization or 0.0,
                    resets_at=w.resets_at,
                    span_seconds=w.span_seconds,
                )
                for w in s.windows
            ],
            error=account.error,
            auth_expired=account.auth_expired,
            fetched_at=datetime.fromtimestamp(s.fetched_at, timezone.utc).isoformat(),
            folder=folder,
        )

    def _limits_view(self) -> LimitsView:
        if not self.settings.limits_enabled:
            return LimitsView(available=False, enabled=False, error="limits disabled")
        accounts = [self._account_view(a) for a in self.accounts if a.alias_of is None]
        # Folders that never loaded stay hidden, unless their token was rejected.
        accounts = [
            v for v in accounts if v.is_default or v.available or v.auth_expired
        ]
        d = self._account_view(self.default_account)
        return LimitsView(
            available=d.available,
            enabled=True,
            stale=d.stale,
            plan=d.plan,
            account=d.account,
            windows=d.windows,
            error=d.error,
            auth_expired=d.auth_expired,
            fetched_at=d.fetched_at,
            accounts=accounts,
        )

    def _bag_view(self) -> list[BagItemView]:
        out: list[BagItemView] = []
        has_active = self.store.state.active is not None
        for kind in ItemKind:
            count = self.companion.item_count(kind)
            if count <= 0:
                continue
            max_use = 0
            previews: list[CandyPreviewView] = []
            if kind is ItemKind.RARE_CANDY:
                # One use feeds at most the preview cap, so every count shows its effect.
                max_use = min(self.companion.max_candy_use, MAX_CANDY_PREVIEWS)
                usable = max_use > 0
                for n in range(1, max_use + 1):
                    plan = self.companion.plan_candy_use(n)
                    if plan is not None:
                        previews.append(
                            CandyPreviewView(
                                count=plan.count,
                                xp=plan.xp,
                                evolves=plan.evolves,
                                graduates=plan.graduates,
                                carryover=plan.carryover,
                                discarded=plan.discarded,
                            )
                        )
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
                    max_use=max_use,
                    previews=previews,
                )
            )
        return out

    def _shop_view(self) -> ShopView:
        wallet = self.companion.available_tokens
        odds = B.shiny_denominator(False)
        charm_odds = B.shiny_denominator(True)
        descriptions = {
            ItemKind.RARE_CANDY: (
                f"Feeds {_compact(B.RARE_CANDY_XP)} growth tokens to your Pokémon."
            ),
            ItemKind.MINT: "Re-rolls your Pokémon's nature. Purely cosmetic.",
            ItemKind.SHINY_CHARM: (
                f"Held item. Improves shiny odds from 1/{odds} to 1/{charm_odds} "
                "on every future hatch."
            ),
        }
        items = [
            ShopItemView(
                kind=kind.value,
                label=ITEM_LABELS[kind],
                description=descriptions[kind],
                price=self.companion.price(kind),
                emoji=kind.emoji,
                sprite_name=kind.sprite_name,
                affordable=wallet >= self.companion.price(kind),
                owned=kind.is_passive and self.companion.item_count(kind) > 0,
                passive=kind.is_passive,
            )
            for kind in ItemKind
        ]
        has_active = self.store.state.active is not None
        eggs = []
        for tier in B.FRESH_EGG_SHOP_TIERS:
            label, description = EGG_LABELS[tier]
            price = self.companion.egg_price(tier)
            eggs.append(
                ShopEggView(
                    tier=tier,
                    label=label,
                    description=description,
                    price=price,
                    affordable=wallet >= price,
                    buyable=has_active,
                    locked_reason=None if has_active else EGG_LOCKED_REASON,
                )
            )
        return ShopView(items=items, eggs=eggs)

    # -- collection --------------------------------------------------------

    def _active_entry(self, a: MonState) -> DexEntryView:
        reached = a.path_ids[: a.stage_index + 1] or [a.base_id]
        line = self.companion.current_line
        names = {
            str(sid): (line.names.get(sid) or {}) for sid in reached
        } if line else {}
        return self._entry_view(
            entry_id=a.profile.instance_id if a.profile else f"active-{a.base_id}-{a.current_id}",
            is_active=True,
            is_released=False,
            base_id=a.base_id,
            final_id=a.current_id,
            chain=reached,
            names=names,
            rarity=a.rarity,
            is_shiny=a.current_is_shiny,
            nature=a.nature,
            caught_at=None,
            profile=a.profile,
            unown_form=a.unown_form,
        )

    def _entry_view(
        self,
        *,
        entry_id: str,
        is_active: bool,
        is_released: bool,
        base_id: int,
        final_id: int,
        chain: list[int],
        names: dict[str, dict[str, str]],
        rarity: Rarity,
        is_shiny: bool,
        nature: str | None,
        caught_at: datetime | None,
        profile: PokemonProfile | None,
        unown_form: str | None,
    ) -> DexEntryView:
        lang = self.store.state.language
        form = B.resolve_unown_form(base_id, unown_form)
        return DexEntryView(
            id=entry_id,
            is_active=is_active,
            is_released=is_released,
            base_id=base_id,
            final_id=final_id,
            final_name=B.display_name(_name_from(names, final_id, lang), final_id, form),
            rarity=rarity,
            is_shiny=is_shiny,
            nature=nature,
            nature_label=B.NATURE_LABELS.get(nature or "", None),
            caught_at=caught_at.isoformat() if caught_at else None,
            level=profile.level if profile else None,
            unown_form=form,
            chain=[
                DexChainSpecies(
                    species_id=sid, name=B.display_name(_name_from(names, sid, lang), sid, form)
                )
                for sid in chain
            ],
            search_names=sorted(
                {n for sid in chain for n in (names.get(str(sid)) or {}).values() if n}
            ),
        )

    def _collection_view(self) -> CollectionView:
        state = self.store.state
        lang = state.language
        counts: dict[str, int] = {r.value: 0 for r in Rarity}
        catch_log: list[DexEntryView] = []

        for entry in state.dex:
            counts[entry.rarity.value] = counts.get(entry.rarity.value, 0) + 1
            catch_log.append(
                self._entry_view(
                    entry_id=entry.id,
                    is_active=False,
                    is_released=entry.is_released,
                    base_id=entry.base_id,
                    final_id=entry.final_id,
                    chain=entry.chain_order,
                    names=entry.names,
                    rarity=entry.rarity,
                    is_shiny=entry.is_shiny,
                    nature=entry.nature,
                    caught_at=entry.caught_at,
                    profile=entry.profile,
                    unown_form=entry.unown_form,
                )
            )
        # Newest first is only the default; the client re-sorts.
        catch_log.sort(key=lambda e: e.caught_at or "", reverse=True)

        a = state.active
        species: dict[int, DexSpeciesView] = {}
        unown: dict[str, UnownFormView] = {}

        def fold(sid: int, name: str, names: dict[str, str], rarity: Rarity, shiny: bool,
                 caught: str | None, form: str | None, raising: bool) -> None:
            cell = species.get(sid)
            if cell is None:
                cell = species[sid] = DexSpeciesView(
                    species_id=sid,
                    name=name,
                    rarity=rarity,
                    count=0,
                    is_shiny=False,
                    has_normal=False,
                    is_raising=False,
                    first_caught_at=caught,
                )
            cell.count += 1
            if shiny:
                cell.is_shiny = True
            else:
                cell.has_normal = True
            cell.is_raising = cell.is_raising or raising
            if caught and (cell.first_caught_at is None or caught < cell.first_caught_at):
                cell.first_caught_at = caught
            cell.search_names = sorted({*cell.search_names, *(n for n in names.values() if n)})
            if sid == B.UNOWN_SPECIES_ID:
                f = B.resolve_unown_form(sid, form) or "a"
                u = unown.get(f) or UnownFormView(
                    form=f,
                    symbol=B.UNOWN_SYMBOLS[f],
                    is_shiny=False,
                    has_normal=False,
                    is_raising=False,
                )
                if shiny:
                    u.is_shiny = True
                else:
                    u.has_normal = True
                u.is_raising = u.is_raising or raising
                unown[f] = u

        for entry in state.dex:
            caught = entry.caught_at.isoformat() if entry.caught_at else None
            for sid in entry.chain_order:
                fold(sid, _name_from(entry.names, sid, lang), entry.names.get(str(sid)) or {},
                     entry.rarity, entry.is_shiny, caught, entry.unown_form, False)
        if a is not None:
            line = self.companion.current_line
            for sid in a.path_ids[: a.stage_index + 1]:
                by_lang = (line.names.get(sid) or {}) if line else {}
                fold(sid, resolve_name(by_lang, lang, sid), by_lang, a.rarity, a.current_is_shiny,
                     None, a.unown_form, sid == a.current_id)
            catch_log.insert(0, self._active_entry(a))

        return CollectionView(
            total=len(state.dex),
            counts_by_rarity=counts,
            catch_log=catch_log,
            pokedex=sorted(species.values(), key=lambda s: s.species_id),
            unown_forms=sorted(unown.values(), key=lambda u: B.UNOWN_FORMS.index(u.form)),
        )

    # -- detail page -------------------------------------------------------

    async def pokemon_detail(self, species_id: int, form: str | None = None) -> PokemonDetailView:
        details = await self.poke.pokemon_details(species_id)
        # Details just arrived: fill in every stored individual of this species.
        self.companion.enrich_dex(species_id)
        state = self.store.state
        lang = state.language

        individuals: list[tuple[str, IndividualView]] = []
        rarity: Rarity | None = None
        name: str | None = None
        wanted_form = B.resolve_unown_form(species_id, form) if form else None

        def add(entry_id: str, is_active: bool, profile: PokemonProfile, shiny: bool,
                nature: str | None, caught_at: datetime | None, unown_form: str | None) -> None:
            resolved = B.resolve_unown_form(species_id, unown_form)
            if wanted_form is not None and resolved != wanted_form:
                return
            stats = [
                StatView(
                    stat=k,
                    base=details.base_stats.get(k, 0),
                    iv=profile.ivs.get(k),
                    value=actual_stat(
                        k, details.base_stats.get(k, 0), profile.ivs.get(k), profile.level, nature
                    ),
                )
                for k in STAT_KEYS
            ]
            individuals.append(
                (
                    caught_at.isoformat() if caught_at else "",
                    IndividualView(
                        id=entry_id,
                        is_active=is_active,
                        is_shiny=shiny,
                        caught_at=caught_at.isoformat() if caught_at else None,
                        level=profile.level,
                        gender=profile.gender,
                        nature=nature,
                        nature_label=B.NATURE_LABELS.get(nature or "", None),
                        ability_name=profile.ability_name,
                        ability_is_hidden=profile.ability_is_hidden,
                        unown_form=resolved,
                        stats=stats,
                        stat_scale=stat_bar_scale([s.value or 0 for s in stats]),
                        moves=[
                            KnownMoveView(name=m.name, learned_at_level=m.learned_at_level)
                            for m in profile.moves
                        ],
                    ),
                )
            )

        for entry in state.dex:
            if species_id in entry.chain_order:
                rarity = rarity or entry.rarity
                name = name or _name_from(entry.names, species_id, lang)
            # Earlier evolution stages are species reference pages: no individuals.
            if entry.final_id == species_id and entry.profile is not None:
                add(
                    entry.id,
                    False,
                    entry.profile,
                    entry.is_shiny,
                    entry.nature,
                    entry.caught_at,
                    entry.unown_form,
                )
        # Newest first.
        individuals.sort(key=lambda kv: kv[0], reverse=True)

        a = state.active
        if a is not None and species_id in a.path_ids[: a.stage_index + 1]:
            rarity = rarity or a.rarity
            name = name or self._species_name(species_id)
            if a.current_id == species_id and a.profile is not None:
                before = len(individuals)
                add(
                    a.profile.instance_id,
                    True,
                    a.profile,
                    a.current_is_shiny,
                    a.nature,
                    None,
                    a.unown_form,
                )
                if len(individuals) > before:
                    individuals.insert(0, individuals.pop())

        return _detail_view(
            species_id, name or details.name.title(), rarity, details, [v for _, v in individuals]
        )

    # -- recap -------------------------------------------------------------

    def recap(self, scope: str, offset: int) -> RecapView:
        state = self.store.state
        today = datetime.now(self.settings.timezone).date()
        r = make_recap(state, scope, offset, today, self.settings.timezone)
        lang = state.language
        return RecapView(
            scope=r.scope,
            offset=r.offset,
            start=r.start,
            end=r.end,
            total=r.total,
            buckets=[
                RecapBucketView(
                    key=b.key, tokens=b.tokens, is_current=b.is_current, has_data=b.has_data
                )
                for b in r.buckets
            ],
            is_in_progress=r.is_in_progress,
            previous_total=r.previous_total,
            delta=r.delta,
            best_day=r.best_day,
            best_day_tokens=r.best_day_tokens,
            active_days=r.active_days,
            counted_days=r.counted_days,
            best_streak=r.best_streak,
            graduated_count=len(r.graduated),
            graduated=[_graduate_view(e, lang) for e in r.graduated[:4]],
            can_go_back=r.can_go_back,
        )


def _graduate_view(e: DexEntry, lang: str) -> RecapGraduateView:
    return RecapGraduateView(
        species_id=e.final_id,
        name=B.display_name(_name_from(e.names, e.final_id, lang), e.final_id, e.unown_form),
        is_shiny=e.is_shiny,
        unown_form=B.resolve_unown_form(e.final_id, e.unown_form),
    )


def _detail_view(
    species_id: int,
    name: str,
    rarity: Rarity | None,
    details: PokemonDetails,
    individuals: list[IndividualView],
) -> PokemonDetailView:
    return PokemonDetailView(
        species_id=species_id,
        name=name,
        rarity=rarity,
        types=details.types,
        height_m=details.height / 10,
        weight_kg=details.weight / 10,
        base_total=sum(details.base_stats.values()),
        base_stats=[StatView(stat=k, base=details.base_stats.get(k, 0)) for k in STAT_KEYS],
        abilities=[AbilityView(name=a.name, is_hidden=a.is_hidden) for a in details.abilities],
        moves=[
            MoveView(
                name=m.name,
                methods=[MoveMethodView(method=x.method, level=x.level) for x in m.learn_methods],
            )
            for m in details.moves
        ],
        individuals=individuals,
    )


def _compact(n: int) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            v = n / size
            return f"{v:.0f}{suffix}" if v == int(v) else f"{v:.1f}{suffix}"
    return str(n)


# PokéAPI reports Japanese under a script-qualified code; the UI uses the short form.
_STORED_TO_SHORT = {"ja-Hrkt": "ja"}


def short_language(stored: str) -> str:
    return _STORED_TO_SHORT.get(stored, stored)


def _coverage(c: CostCoverage) -> CostCoverageView:
    return CostCoverageView(reported=c.reported, estimated=c.estimated, unknown=c.unknown)


def _name_from(names: dict[str, dict[str, str]], species_id: int, lang: str) -> str:
    return resolve_name(names.get(str(species_id)) or {}, lang, species_id)

