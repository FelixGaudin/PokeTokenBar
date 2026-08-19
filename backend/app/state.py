"""Persisted save state and its atomic on-disk store."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .balance import Rarity

log = logging.getLogger(__name__)

SAVE_VERSION = 1


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


class DexEntry(BaseModel):
    """A graduated Pokémon, kept permanently."""

    base_id: int
    final_id: int
    chain_order: list[int] = Field(default_factory=list)
    rarity: Rarity = Rarity.COMMON
    caught_at: datetime | None = None
    is_shiny: bool = False
    nature: str | None = None
    names: dict[str, dict[str, str]] = Field(default_factory=dict)


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

    active: MonState | None = None
    dex: list[DexEntry] = Field(default_factory=list)
    # "baseID:finalID" pairs already graduated — steers branch choice toward new forms.
    collected_finals: list[str] = Field(default_factory=list)

    inventory: dict[str, int] = Field(default_factory=dict)
    candy_grant_tier: dict[str, int] = Field(default_factory=dict)
    candy_feature_seeded: bool = False

    language: str = "en"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StateStore:
    """Loads the save on start and writes it back atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = self._load()

    def _load(self) -> SaveState:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            log.info("no save at %s — starting fresh", self.path)
            return SaveState()
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            backup = self.path.with_suffix(".corrupt.json")
            log.error("save at %s is unreadable (%s) — moved to %s", self.path, exc, backup)
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return SaveState()

        try:
            return SaveState.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - a bad save must not block startup
            log.error("save at %s failed validation (%s) — starting fresh", self.path, exc)
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
