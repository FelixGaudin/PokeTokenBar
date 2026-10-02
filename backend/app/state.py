"""Persisted save state, its atomic on-disk store, snapshots and preferences."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import balance as B
from .balance import Rarity
from .profile import MAX_TOKEN_VALUE, PokemonProfile, fnv1a64, reconstructed_growth

log = logging.getLogger(__name__)

SAVE_VERSION = 2

SNAPSHOT_PREFIX = "companion-snapshot-"
MAX_SNAPSHOTS = 10
AUTO_SNAPSHOT_INTERVAL = 12 * 3600


def _lenient_profile(value: Any) -> PokemonProfile | None:
    """A corrupt profile is regenerated; it must never drop the Pokémon it belongs to."""
    if value is None or isinstance(value, PokemonProfile):
        return value
    try:
        return PokemonProfile.model_validate(value)
    except ValidationError:
        return None


class MonState(BaseModel):
    """The Pokémon currently being raised."""

    base_id: int
    current_id: int
    # Species actually passed through so far, root first.
    path_ids: list[int] = Field(default_factory=list)
    # The full route picked at hatch time, including stages not yet reached.
    planned_path_ids: list[int] = Field(default_factory=list)
    stage_index: int = 0
    used_at_stage: int = 0
    rarity: Rarity = Rarity.COMMON
    total_forms: int = 1
    is_shiny: bool = False
    nature: str | None = None
    # A common line that is secretly a Ditto until its first evolution.
    ditto_disguise: int | None = None
    ditto_revealed: bool = False
    hatched_at: datetime | None = None
    # This line graduated before, so every stage costs half.
    has_growth_boost: bool = False
    profile: PokemonProfile | None = None
    unown_form: str | None = None

    @field_validator("profile", mode="before")
    @classmethod
    def _lenient(cls, v: Any) -> PokemonProfile | None:
        return _lenient_profile(v)

    @field_validator("unown_form", mode="before")
    @classmethod
    def _form(cls, v: Any) -> str | None:
        return v if isinstance(v, str) else None

    @property
    def current_is_shiny(self) -> bool:
        """A disguised Ditto hides its shininess until the reveal."""
        return self.is_shiny and not (self.ditto_disguise is not None and not self.ditto_revealed)

    @property
    def phase_threshold(self) -> int:
        """This stage's cost at the default difficulty, boost included."""
        return B.phase_threshold(
            self.rarity,
            self.total_forms,
            self.stage_index,
            B.REPEAT_GROWTH_MULTIPLIER if self.has_growth_boost else 1,
        )


class DexEntry(BaseModel):
    """A graduated or released Pokémon, kept permanently."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    base_id: int
    final_id: int
    chain_order: list[int] = Field(default_factory=list)
    rarity: Rarity = Rarity.COMMON
    caught_at: datetime | None = None
    # Set when it was sent off for an egg instead of graduating.
    released_at: datetime | None = None
    is_shiny: bool = False
    nature: str | None = None
    names: dict[str, dict[str, str]] = Field(default_factory=dict)
    profile: PokemonProfile | None = None
    unown_form: str | None = None

    @field_validator("profile", mode="before")
    @classmethod
    def _lenient(cls, v: Any) -> PokemonProfile | None:
        return _lenient_profile(v)

    @field_validator("unown_form", mode="before")
    @classmethod
    def _form(cls, v: Any) -> str | None:
        return v if isinstance(v, str) else None

    @property
    def is_released(self) -> bool:
        return self.released_at is not None


class SaveState(BaseModel):
    version: int = SAVE_VERSION

    # The install baseline is only set once real provider data arrives, so usage
    # logged before this app existed is never retroactively credited.
    install_baseline_set: bool = False
    claimed_today_by_provider: dict[str, int] | None = None
    last_date: str | None = None

    # Lifetime tokens observed since install: the growth meter and the shop's income.
    used_since_install: int = 0
    # Lifetime shop spend. Wallet balance is used_since_install - spent_tokens.
    spent_tokens: int = 0

    egg_usage: int = 0
    egg_tier: Rarity | None = None
    egg_prefetch_base_id: int | None = None
    pending_unown_form: str | None = None

    active: MonState | None = None
    dex: list[DexEntry] = Field(default_factory=list)
    # "baseID:finalID" pairs already graduated — steers branch choice toward new forms.
    collected_finals: list[str] = Field(default_factory=list)

    inventory: dict[str, int] = Field(default_factory=dict)
    candy_grant_tier: dict[str, int] = Field(default_factory=dict)
    # Reset marker last seen per window; a new one re-arms that window's grant.
    candy_window_epoch: dict[str, str] = Field(default_factory=dict)
    candy_feature_seeded: bool = False
    # Additional accounts' windows only pay once they have been seen below 100%.
    armed_candy_windows: list[str] = Field(default_factory=list)

    # Daily token totals kept beyond the current month, for the usage recap.
    token_ledger: dict[str, int] = Field(default_factory=dict)
    token_ledger_since: str | None = None

    language: str = "en"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def migrate_profiles(state: SaveState) -> bool:
    """Give every Pokémon without one a deterministic profile. Returns True if changed.

    No network: seeds come from stable fields, and growth is reconstructed from what
    the save already records.
    """
    changed = False
    a = state.active
    if a is not None and a.profile is None:
        seed = fnv1a64(f"active:{a.base_id}:{','.join(map(str, a.path_ids))}:{state.last_date}")
        a.profile = PokemonProfile.generate(seed)
        changed = True
    for entry in state.dex:
        if entry.profile is not None:
            continue
        if entry.is_released:
            forms = max(1, len(entry.chain_order))
            completed = max(0, len(entry.chain_order) - 1)
            growth = reconstructed_growth(entry.rarity, forms, completed, 0)
        else:
            growth = B.graduation_total(entry.rarity)
        profile = PokemonProfile.generate(
            fnv1a64(f"dex:{entry.id}:{entry.final_id}"), growth, instance_id=entry.id
        )
        profile.advance_growth(growth, entry.rarity)
        entry.profile = profile
        changed = True
    return changed


def sanitize(state: SaveState) -> None:
    """Normalise everything an imported or recovered save could have hand-edited."""
    state.used_since_install = min(MAX_TOKEN_VALUE, max(0, state.used_since_install))
    state.spent_tokens = min(MAX_TOKEN_VALUE, max(0, state.spent_tokens))
    state.egg_usage = min(MAX_TOKEN_VALUE, max(0, state.egg_usage))
    state.pending_unown_form = B.resolve_unown_form(
        state.egg_prefetch_base_id or 0, state.pending_unown_form
    )
    if state.active is not None:
        a = state.active
        a.unown_form = B.resolve_unown_form(a.base_id, a.unown_form)
        if state.active.profile is not None:
            state.active.profile.sanitize()
    for entry in state.dex:
        # A naive timestamp would break every sort against the aware ones.
        if entry.caught_at is not None and entry.caught_at.tzinfo is None:
            entry.caught_at = entry.caught_at.replace(tzinfo=timezone.utc)
        if entry.released_at is not None and entry.released_at.tzinfo is None:
            entry.released_at = entry.released_at.replace(tzinfo=timezone.utc)
        entry.unown_form = B.resolve_unown_form(entry.base_id, entry.unown_form)
        if entry.profile is not None:
            entry.profile.sanitize()


def parse_save(raw: Any) -> SaveState:
    """Accept both a bare state and an export envelope `{format, state, ...}`."""
    if isinstance(raw, dict) and raw.get("format") == "poketokenbar.save" and "state" in raw:
        raw = raw["state"]
    return SaveState.model_validate(raw)


class SnapshotInfo(BaseModel):
    id: str
    created_at: datetime
    dex_count: int
    lifetime_tokens: int
    current_species_id: int | None
    current_is_shiny: bool


_STAMP = re.compile(r"^companion-snapshot-(\d{4}-\d{2}-\d{2}-\d{6})(?:-\d+)?\.json$")


class StateStore:
    """Loads the save on start and writes it back atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = self._load()

    @property
    def snapshot_dir(self) -> Path:
        return self.path.parent / ".snapshots" / self.path.name

    @property
    def corrupt_path(self) -> Path:
        return self.path.with_name(self.path.name + ".corrupt")

    def _load(self) -> SaveState:
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            log.info("no save at %s — starting fresh", self.path)
            return SaveState()
        except OSError as exc:
            log.error("save at %s is unreadable (%s)", self.path, exc)
            return self._recover()

        try:
            raw = json.loads(text)
            state = parse_save(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            log.error("save at %s is corrupt (%s)", self.path, exc)
            return self._recover()

        if migrate_profiles(state):
            # One copy of the pre-migration save, never overwritten.
            backup = self.path.with_name(self.path.stem + ".pre-profiles-v1.json")
            if not backup.exists():
                try:
                    shutil.copyfile(self.path, backup)
                except OSError as exc:
                    log.warning("could not back up save before migrating: %s", exc)
            state.version = SAVE_VERSION
            self.state = state
            self.save()
        return state

    def _recover(self) -> SaveState:
        """Move the bad file aside, so the next save cannot destroy it, then restore."""
        try:
            self.corrupt_path.unlink(missing_ok=True)
            self.path.replace(self.corrupt_path)
            log.error("moved the unreadable save to %s", self.corrupt_path)
        except OSError as exc:
            log.error("could not move the unreadable save aside: %s", exc)
        for info in self.list_snapshots():
            try:
                state = self.read_snapshot(info.id)
            except (OSError, ValueError):
                continue
            sanitize(state)
            migrate_profiles(state)
            log.warning("recovered the save from snapshot %s", info.id)
            self.state = state
            self.save()
            return state
        return SaveState()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(self.state.model_dump_json())
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("could not write save to %s: %s", self.path, exc)
            tmp.unlink(missing_ok=True)

    def replace(self, state: SaveState) -> None:
        self.state = state
        self.save()

    # -- snapshots ---------------------------------------------------------

    def list_snapshots(self) -> list[SnapshotInfo]:
        out: list[SnapshotInfo] = []
        try:
            names = os.listdir(self.snapshot_dir)
        except OSError:
            return []
        for name in names:
            if not (name.startswith(SNAPSHOT_PREFIX) and name.endswith(".json")):
                continue
            path = self.snapshot_dir / name
            try:
                state = parse_save(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
            out.append(
                SnapshotInfo(
                    id=name,
                    created_at=_snapshot_date(path),
                    dex_count=len(state.dex),
                    lifetime_tokens=state.used_since_install,
                    current_species_id=state.active.current_id if state.active else None,
                    current_is_shiny=state.active.current_is_shiny if state.active else False,
                )
            )
        out.sort(key=lambda s: (s.created_at, s.id), reverse=True)
        return out

    def read_snapshot(self, snapshot_id: str) -> SaveState:
        if snapshot_id not in {s.id for s in self.list_snapshots()}:
            raise ValueError("no such snapshot")
        return parse_save(json.loads((self.snapshot_dir / snapshot_id).read_text()))

    def create_snapshot(self, now: datetime | None = None) -> SnapshotInfo:
        now = now or datetime.now().astimezone()
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        stem = SNAPSHOT_PREFIX + now.strftime("%Y-%m-%d-%H%M%S")
        path = self.snapshot_dir / f"{stem}.json"
        n = 1
        while path.exists():
            path = self.snapshot_dir / f"{stem}-{n}.json"
            n += 1
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json())
        os.replace(tmp, path)
        os.utime(path, (now.timestamp(), now.timestamp()))
        infos = self.list_snapshots()
        for stale in infos[MAX_SNAPSHOTS:]:
            (self.snapshot_dir / stale.id).unlink(missing_ok=True)
        return next((s for s in infos if s.id == path.name), infos[0])

    def auto_snapshot(self, now: datetime | None = None) -> bool:
        """Take a snapshot every 12 hours, once there is something worth keeping."""
        s = self.state
        if s.used_since_install <= 0 and not s.dex and s.active is None:
            return False
        now = now or datetime.now().astimezone()
        newest = self.list_snapshots()[:1]
        if newest and (now - newest[0].created_at).total_seconds() < AUTO_SNAPSHOT_INTERVAL:
            return False
        try:
            self.create_snapshot(now)
        except OSError as exc:
            log.warning("automatic snapshot failed: %s", exc)
            return False
        return True


def _snapshot_date(path: Path) -> datetime:
    m = _STAMP.match(path.name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d-%H%M%S").astimezone()
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return datetime.now().astimezone()


class Preferences(BaseModel):
    """Per-install settings. Kept out of the save so an import never changes them."""

    growth_difficulty: float = B.DEFAULT_DIFFICULTY
    shop_difficulty: float = B.DEFAULT_DIFFICULTY
    limit_display: str = "used"  # "used" | "remaining"


class PreferenceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.prefs = Preferences()
        try:
            raw = json.loads(path.read_text())
            self.prefs = Preferences.model_validate(raw)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable preferences %s: %s", path, exc)
        # Clamped on read: hand edits can put 0, negatives or NaN here.
        self.prefs.growth_difficulty = B.clamp_difficulty(self.prefs.growth_difficulty)
        self.prefs.shop_difficulty = B.clamp_difficulty(self.prefs.shop_difficulty)
        if self.prefs.limit_display not in ("used", "remaining"):
            self.prefs.limit_display = "used"

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self.prefs.model_dump_json(indent=2))
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("could not save preferences: %s", exc)
