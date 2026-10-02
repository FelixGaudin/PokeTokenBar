"""PokéAPI client for species, evolution chains and the hatch candidate index.

No Pokémon data is bundled with this project — species, evolution chains and sprites
are all fetched at runtime and cached on disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .balance import ANIMATED_SPECIES_MAX, DITTO_SPECIES_ID, Rarity, has_animated_sprite
from .profile import Ability, LearnMethod, MoveInfo, PokemonDetails

log = logging.getLogger(__name__)

REST_BASE = "https://pokeapi.co/api/v2"
GRAPHQL_URL = "https://graphql.pokeapi.co/v1beta2"
LANG_CODES = ("en", "ko", "ja-Hrkt", "ja", "es", "fr", "de")
# PokéAPI reports Japanese under two codes and only one is present per species, so a
# single-key lookup silently falls through to English. Try the variants in order.
LANG_FALLBACKS: dict[str, tuple[str, ...]] = {
    "ja": ("ja-Hrkt", "ja"),
    "ja-Hrkt": ("ja-Hrkt", "ja"),
}


def resolve_name(by_lang: dict[str, str], lang: str, species_id: int) -> str:
    for code in LANG_FALLBACKS.get(lang, (lang,)):
        name = by_lang.get(code)
        if name:
            return name
    return by_lang.get("en") or f"#{species_id}"


BASE_INDEX_TTL = 30 * 86_400
DETAILS_TTL = 30 * 86_400
# Moves are read from one version group so level-up tables are consistent.
MOVE_VERSION_GROUP = "black-2-white-2"


@dataclass(frozen=True)
class BaseSpecies:
    """A hatch candidate: the start of an evolution line, plus its official rarity."""

    id: int
    capture_rate: int


@dataclass
class EvoNode:
    species_id: int
    children: list["EvoNode"] = field(default_factory=list)

    @property
    def depth(self) -> int:
        return 1 + max((c.depth for c in self.children), default=0)

    def find(self, species_id: int) -> "EvoNode | None":
        if self.species_id == species_id:
            return self
        for child in self.children:
            found = child.find(species_id)
            if found is not None:
                return found
        return None

    @property
    def final_ids(self) -> list[int]:
        if not self.children:
            return [self.species_id]
        out: list[int] = []
        for child in self.children:
            out.extend(child.final_ids)
        return out

    @property
    def all_ids(self) -> list[int]:
        out = [self.species_id]
        for child in self.children:
            out.extend(child.all_ids)
        return out

    def keeping_animated(self) -> "EvoNode | None":
        """Drop species with no animated sprite, and everything below them."""
        if not has_animated_sprite(self.species_id):
            return None
        kept = [c for c in (child.keeping_animated() for child in self.children) if c]
        return EvoNode(self.species_id, kept)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.species_id, "children": [c.to_dict() for c in self.children]}

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "EvoNode":
        return EvoNode(
            int(raw["id"]),
            [EvoNode.from_dict(c) for c in raw.get("children", [])],
        )


@dataclass
class EvoLine:
    base_id: int
    tree: EvoNode
    rarity: Rarity
    names: dict[int, dict[str, str]]

    def display_name(self, species_id: int, lang: str = "en") -> str:
        return resolve_name(self.names.get(species_id) or {}, lang, species_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "tree": self.tree.to_dict(),
            "rarity": self.rarity.value,
            "names": {str(k): v for k, v in self.names.items()},
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "EvoLine":
        return EvoLine(
            base_id=int(raw["base_id"]),
            tree=EvoNode.from_dict(raw["tree"]),
            rarity=Rarity(raw["rarity"]),
            names={int(k): v for k, v in (raw.get("names") or {}).items()},
        )


class PokeAPIClient:
    """Species / chain / index lookups with a memory + disk cache in front."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._species: dict[int, dict[str, Any]] = {}
        self._lines: dict[int, EvoLine] = {}
        self._details: dict[int, PokemonDetails] = {}
        self._base_index: list[BaseSpecies] | None = None
        self._index_lock = asyncio.Lock()
        self._rest_build_started = False
        self._client: httpx.AsyncClient | None = None
        self._load_line_cache()

    # -- lifecycle ---------------------------------------------------------

    async def http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0),
                headers={"User-Agent": "poketokenbar-web/1.0 (+https://github.com/chattymin/PokeTokenBar)"},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # -- disk cache --------------------------------------------------------

    @property
    def _base_index_file(self) -> Path:
        return self.cache_dir / "base-index.json"

    @property
    def _line_cache_file(self) -> Path:
        return self.cache_dir / "evo-lines.json"

    def _load_line_cache(self) -> None:
        try:
            raw = json.loads(self._line_cache_file.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            return
        for key, value in (raw or {}).items():
            try:
                self._lines[int(key)] = EvoLine.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue

    def _save_line_cache(self) -> None:
        payload = {str(k): v.to_dict() for k, v in self._lines.items()}
        _atomic_write_json(self._line_cache_file, payload)

    # -- species / chains --------------------------------------------------

    async def _get_json(self, url: str) -> dict[str, Any]:
        client = await self.http()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def species(self, species_id: int) -> dict[str, Any]:
        cached = self._species.get(species_id)
        if cached is not None:
            return cached
        data = await self._get_json(f"{REST_BASE}/pokemon-species/{species_id}")
        self._species[species_id] = data
        return data

    def _node(self, raw: dict[str, Any]) -> EvoNode:
        species_url = (raw.get("species") or {}).get("url") or ""
        species_id = _id_from_url(species_url)
        children = [self._node(c) for c in raw.get("evolves_to") or []]
        return EvoNode(species_id, children)

    async def line(self, base_species_id: int) -> EvoLine:
        cached = self._lines.get(base_species_id)
        if cached is not None:
            return cached

        base = await self.species(base_species_id)
        chain_url = ((base.get("evolution_chain") or {}).get("url")) or ""
        if not chain_url.startswith("http"):
            raise ValueError(f"species {base_species_id} has no evolution chain URL")

        chain = await self._get_json(chain_url)
        tree = self._node(chain.get("chain") or {})
        animated = tree.keeping_animated()
        if animated is None:
            raise ValueError(f"species {base_species_id} has no animated sprite")
        tree = animated

        rarity = Rarity.classify(
            int(base.get("capture_rate") or 255),
            bool(base.get("is_legendary")),
            bool(base.get("is_mythical")),
        )

        names: dict[int, dict[str, str]] = {}
        for sid in tree.all_ids:
            sp = await self.species(sid)
            by_lang: dict[str, str] = {}
            for entry in sp.get("names") or []:
                code = ((entry.get("language") or {}).get("name")) or ""
                if code in LANG_CODES:
                    by_lang[code] = entry.get("name") or ""
            names[sid] = by_lang

        line = EvoLine(base_species_id, tree, rarity, names)
        self._lines[base_species_id] = line
        self._save_line_cache()
        return line

    # -- per-species details (stats, abilities, moves) ---------------------

    def _details_file(self, species_id: int) -> Path:
        return self.cache_dir / "pokemon-details-v1" / f"{species_id}.json"

    def cached_details(self, species_id: int) -> PokemonDetails | None:
        """Memory or disk only, never the network — safe to call from the refresh path."""
        cached = self._details.get(species_id)
        if cached is not None:
            return cached
        disk = _read_details(self._details_file(species_id))
        if disk is None:
            return None
        self._details[species_id] = disk[1]
        return disk[1]

    async def pokemon_details(self, species_id: int) -> PokemonDetails:
        cached = self._details.get(species_id)
        if cached is not None:
            return cached
        path = self._details_file(species_id)
        disk = _read_details(path)
        if disk is not None and time.time() - disk[0] < DETAILS_TTL:
            self._details[species_id] = disk[1]
            return disk[1]
        try:
            raw = await self._get_json(f"{REST_BASE}/pokemon/{species_id}")
            species = await self.species(species_id)
            details = parse_details(species_id, raw, int(species.get("gender_rate", -1)))
        except Exception:
            # A stale copy beats nothing; details are enrichment, never required.
            if disk is not None:
                self._details[species_id] = disk[1]
                return disk[1]
            raise
        _atomic_write_json(path, {"fetched_at": time.time(), "details": _details_to_dict(details)})
        self._details[species_id] = details
        return details

    # -- hatch candidate index ---------------------------------------------

    async def base_species_index(self) -> list[BaseSpecies]:
        """Every gen 1-5 evolution-line start, with its capture rate.

        Memory cache, then a 30-day disk snapshot, then GraphQL. If GraphQL is down
        and there is no snapshot, a stale snapshot still wins, and a REST rebuild is
        kicked off in the background so hatching is never permanently tied to one
        endpoint staying up.
        """
        if self._base_index is not None:
            return self._base_index

        async with self._index_lock:
            if self._base_index is not None:
                return self._base_index

            disk = _read_index_snapshot(self._base_index_file)
            if disk is not None:
                fetched_at, entries = disk
                if entries and time.time() - fetched_at < BASE_INDEX_TTL:
                    self._base_index = entries
                    return entries

            try:
                entries = await self._fetch_index_graphql()
                self._base_index = entries
                _atomic_write_json(
                    self._base_index_file,
                    {
                        "fetched_at": time.time(),
                        "entries": [{"id": e.id, "capture_rate": e.capture_rate} for e in entries],
                    },
                )
                return entries
            except Exception as exc:  # noqa: BLE001 - any failure falls back
                if disk is not None and disk[1]:
                    log.warning("base index: GraphQL failed (%s) — using stale snapshot", exc)
                    self._base_index = disk[1]
                    return disk[1]
                log.warning("base index: GraphQL failed (%s) — starting REST rebuild", exc)
                if not self._rest_build_started:
                    self._rest_build_started = True
                    asyncio.create_task(self._build_index_via_rest())
                raise

    async def _fetch_index_graphql(self) -> list[BaseSpecies]:
        # Ditto (#132) is reserved for the disguise reveal, so it is excluded from
        # the normal hatch pool.
        query = (
            "{ pokemonspecies(where: {evolves_from_species_id: {_is_null: true}, "
            f"id: {{_lte: {ANIMATED_SPECIES_MAX}, _neq: {DITTO_SPECIES_ID}}}}}, "
            "order_by: {id: asc}) { id capture_rate } }"
        )
        client = await self.http()
        resp = await client.post(GRAPHQL_URL, json={"query": query})
        resp.raise_for_status()
        rows = ((resp.json() or {}).get("data") or {}).get("pokemonspecies") or []
        entries = [BaseSpecies(int(r["id"]), int(r["capture_rate"])) for r in rows]
        if not entries:
            raise ValueError("GraphQL returned an empty base index")
        return entries

    async def _build_index_via_rest(self) -> None:
        """Rebuild the index one species at a time when GraphQL is unavailable.

        Once this succeeds it persists for 30 days, so later hatches are weighted and
        work offline. Concurrency is kept small out of courtesy to PokéAPI.
        """
        log.info("base index: building via REST")
        found: list[BaseSpecies] = []
        semaphore = asyncio.Semaphore(6)

        async def one(species_id: int) -> None:
            async with semaphore:
                try:
                    sp = await self.species(species_id)
                except Exception:
                    return
                if sp.get("evolves_from_species") is not None:
                    return
                if species_id == DITTO_SPECIES_ID:
                    return
                found.append(BaseSpecies(species_id, int(sp.get("capture_rate") or 255)))

        await asyncio.gather(*(one(i) for i in range(1, ANIMATED_SPECIES_MAX + 1)))
        if not found:
            log.warning("base index: REST rebuild produced nothing")
            return

        found.sort(key=lambda e: e.id)
        self._base_index = found
        _atomic_write_json(
            self._base_index_file,
            {
                "fetched_at": time.time(),
                "entries": [{"id": e.id, "capture_rate": e.capture_rate} for e in found],
            },
        )
        log.info("base index: REST rebuild complete (%d entries)", len(found))

    async def base_species(self, species_id: int) -> BaseSpecies | None:
        """One species as a hatch candidate, or None if it is not a line start."""
        try:
            sp = await self.species(species_id)
        except Exception:
            return None
        if sp.get("evolves_from_species") is not None:
            return None
        return BaseSpecies(species_id, int(sp.get("capture_rate") or 255))


def _id_from_url(url: str) -> int:
    parts = [p for p in url.rstrip("/").split("/") if p]
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


def _read_index_snapshot(path: Path) -> tuple[float, list[BaseSpecies]] | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    entries = [
        BaseSpecies(int(e["id"]), int(e["capture_rate"]))
        for e in (raw.get("entries") or [])
        if "id" in e and "capture_rate" in e
    ]
    return float(raw.get("fetched_at") or 0.0), entries


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write via a temp file so a crash never leaves a truncated cache behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)
        tmp.unlink(missing_ok=True)


def parse_details(species_id: int, raw: dict[str, Any], gender_rate: int) -> PokemonDetails:
    types = [
        (t.get("type") or {}).get("name") or ""
        for t in sorted(raw.get("types") or [], key=lambda t: t.get("slot") or 0)
    ]
    stats = {
        (s.get("stat") or {}).get("name") or "": int(s.get("base_stat") or 0)
        for s in raw.get("stats") or []
    }
    abilities = sorted(
        (
            Ability(
                name=(a.get("ability") or {}).get("name") or "",
                slot=int(a.get("slot") or 0),
                is_hidden=bool(a.get("is_hidden")),
            )
            for a in raw.get("abilities") or []
        ),
        key=lambda a: a.slot,
    )
    moves: list[MoveInfo] = []
    for m in raw.get("moves") or []:
        methods = [
            LearnMethod(
                method=(d.get("move_learn_method") or {}).get("name") or "",
                level=int(d.get("level_learned_at") or 0),
            )
            for d in m.get("version_group_details") or []
            if (d.get("version_group") or {}).get("name") == MOVE_VERSION_GROUP
        ]
        if methods:
            name = (m.get("move") or {}).get("name") or ""
            moves.append(MoveInfo(name=name, learn_methods=methods))
    moves.sort(key=lambda m: m.name)
    return PokemonDetails(
        species_id=species_id,
        name=raw.get("name") or "",
        height=int(raw.get("height") or 0),
        weight=int(raw.get("weight") or 0),
        base_experience=raw.get("base_experience"),
        gender_rate=gender_rate,
        types=[t for t in types if t],
        base_stats=stats,
        abilities=abilities,
        moves=moves,
    )


def _details_to_dict(d: PokemonDetails) -> dict[str, Any]:
    return {
        "species_id": d.species_id,
        "name": d.name,
        "height": d.height,
        "weight": d.weight,
        "base_experience": d.base_experience,
        "gender_rate": d.gender_rate,
        "types": d.types,
        "base_stats": d.base_stats,
        "abilities": [
            {"name": a.name, "slot": a.slot, "is_hidden": a.is_hidden} for a in d.abilities
        ],
        "moves": [
            {
                "name": m.name,
                "methods": [{"method": x.method, "level": x.level} for x in m.learn_methods],
            }
            for m in d.moves
        ],
    }


def _read_details(path: Path) -> tuple[float, PokemonDetails] | None:
    try:
        raw = json.loads(path.read_text())
        d = raw["details"]
        details = PokemonDetails(
            species_id=int(d["species_id"]),
            name=d.get("name") or "",
            height=int(d.get("height") or 0),
            weight=int(d.get("weight") or 0),
            base_experience=d.get("base_experience"),
            gender_rate=int(d.get("gender_rate", -1)),
            types=list(d.get("types") or []),
            base_stats={k: int(v) for k, v in (d.get("base_stats") or {}).items()},
            abilities=[
                Ability(a["name"], int(a["slot"]), bool(a["is_hidden"]))
                for a in d.get("abilities") or []
            ],
            moves=[
                MoveInfo(
                    m["name"],
                    [LearnMethod(x["method"], int(x["level"])) for x in m.get("methods") or []],
                )
                for m in d.get("moves") or []
            ],
        )
        return float(raw.get("fetched_at") or 0.0), details
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError):
        return None
