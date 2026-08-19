"""Sprite proxy with an on-disk cache.

Sprites are fetched from the PokéAPI sprite repository at runtime and cached, so the
browser only ever talks to this server: the page keeps working offline once a sprite
has been seen, and no image assets are bundled with this project.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from .balance import has_animated_sprite

log = logging.getLogger(__name__)

SPRITE_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
ITEM_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items"


class SpriteStore:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def cache_key(species_id: int, *, animated: bool, shiny: bool) -> str:
        return f"{species_id}-{'sh' if shiny else ''}{'a' if animated else 's'}"

    def _sprite_url(self, species_id: int, *, animated: bool, shiny: bool) -> str:
        if animated:
            shiny_part = "shiny/" if shiny else ""
            return f"{SPRITE_BASE}/versions/generation-v/black-white/animated/{shiny_part}{species_id}.gif"
        shiny_part = "shiny/" if shiny else ""
        return f"{SPRITE_BASE}/{shiny_part}{species_id}.png"

    async def pokemon(
        self, client: httpx.AsyncClient, species_id: int, *, animated: bool, shiny: bool
    ) -> tuple[bytes, str] | None:
        if animated and not has_animated_sprite(species_id):
            animated = False
        key = self.cache_key(species_id, animated=animated, shiny=shiny)
        ext = "gif" if animated else "png"
        media = "image/gif" if animated else "image/png"
        return await self._fetch_cached(
            client, self.cache_dir / f"{key}.{ext}", self._sprite_url(species_id, animated=animated, shiny=shiny), media
        )

    async def item(self, client: httpx.AsyncClient, name: str) -> tuple[bytes, str] | None:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
        if not safe:
            return None
        return await self._fetch_cached(
            client, self.cache_dir / f"item-{safe}.png", f"{ITEM_BASE}/{safe}.png", "image/png"
        )

    async def _fetch_cached(
        self, client: httpx.AsyncClient, path: Path, url: str, media_type: str
    ) -> tuple[bytes, str] | None:
        try:
            return path.read_bytes(), media_type
        except OSError:
            pass
        try:
            resp = await client.get(url, timeout=20.0)
        except httpx.HTTPError as exc:
            log.debug("sprite fetch failed for %s: %s", url, exc)
            return None
        if resp.status_code != 200 or not resp.content:
            return None
        data = resp.content
        # Write via a temp file so a crash cannot leave a truncated sprite cached.
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
        return data, media_type
