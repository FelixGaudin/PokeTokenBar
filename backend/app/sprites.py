"""Sprite proxy with an on-disk cache.

Sprites are fetched from the PokéAPI sprite repository at runtime and cached, so the
browser only ever talks to this server: the page keeps working offline once a sprite
has been seen, and no image assets are bundled with this project.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from . import forms
from .balance import has_animated_sprite

log = logging.getLogger(__name__)

SPRITE_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
ITEM_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items"


class SpriteUnavailable(Exception):
    """No sprite. `transient` (network error, 5xx) is worth retrying; a 404 is not."""

    def __init__(self, transient: bool) -> None:
        super().__init__("sprite temporarily unavailable" if transient else "sprite missing")
        self.transient = transient


class SpriteStore:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # URLs the sprite repository answered 404 for, so a missing back view is asked once.
        self._missing: set[str] = set()

    @staticmethod
    def cache_key(
        species_id: int,
        *,
        animated: bool,
        shiny: bool,
        form: str | None = None,
        back: bool = False,
    ) -> str:
        # A default form keeps the plain key, so caches from before forms stay valid.
        name = forms.sprite_name(species_id, form)
        side = "back-" if back else ""
        return f"{name}-{side}{'sh' if shiny else ''}{'a' if animated else 's'}"

    def _sprite_url(
        self,
        species_id: int,
        *,
        animated: bool,
        shiny: bool,
        form: str | None = None,
        back: bool = False,
    ) -> str:
        # back/ goes before shiny/ in the repository layout.
        variant = ("back/" if back else "") + ("shiny/" if shiny else "")
        if animated:
            folder = f"{SPRITE_BASE}/versions/generation-v/black-white/animated/{variant}"
            return f"{folder}{forms.sprite_name(species_id, form)}.gif"
        return f"{SPRITE_BASE}/{variant}{forms.static_sprite_name(species_id, form)}.png"

    async def pokemon(
        self,
        client: httpx.AsyncClient,
        species_id: int,
        *,
        animated: bool,
        shiny: bool,
        form: str | None = None,
        back: bool = False,
    ) -> tuple[bytes, str]:
        """The sprite, or SpriteUnavailable.

        A back view falls back to the plain back, then to the front, but only once every
        back variant is confirmed missing: a network hiccup must not settle for the front.
        """
        if animated and not has_animated_sprite(species_id):
            animated = False
        sides = (True, False) if back else (False,)
        for side in sides:
            for sh in (True, False) if shiny else (False,):
                try:
                    return await self._variant(client, species_id, animated, sh, form, side)
                except SpriteUnavailable as exc:
                    # Only a back view degrades to the plain or front picture.
                    if exc.transient or not back:
                        raise
        raise SpriteUnavailable(transient=False)

    async def _variant(
        self,
        client: httpx.AsyncClient,
        species_id: int,
        animated: bool,
        shiny: bool,
        form: str | None,
        back: bool,
    ) -> tuple[bytes, str]:
        key = self.cache_key(species_id, animated=animated, shiny=shiny, form=form, back=back)
        ext = "gif" if animated else "png"
        media = "image/gif" if animated else "image/png"
        url = self._sprite_url(species_id, animated=animated, shiny=shiny, form=form, back=back)
        return await self._fetch_cached(client, self.cache_dir / f"{key}.{ext}", url, media)

    async def item(self, client: httpx.AsyncClient, name: str) -> tuple[bytes, str]:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
        if not safe:
            raise SpriteUnavailable(transient=False)
        return await self._fetch_cached(
            client, self.cache_dir / f"item-{safe}.png", f"{ITEM_BASE}/{safe}.png", "image/png"
        )

    async def _fetch_cached(
        self, client: httpx.AsyncClient, path: Path, url: str, media_type: str
    ) -> tuple[bytes, str]:
        try:
            return path.read_bytes(), media_type
        except OSError:
            pass
        if url in self._missing:
            raise SpriteUnavailable(transient=False)
        try:
            resp = await client.get(url, timeout=20.0)
        except httpx.HTTPError as exc:
            log.debug("sprite fetch failed for %s: %s", url, exc)
            raise SpriteUnavailable(transient=True) from None
        if resp.status_code == 404:
            self._missing.add(url)
            raise SpriteUnavailable(transient=False)
        if resp.status_code != 200 or not resp.content:
            raise SpriteUnavailable(transient=True)
        data = resp.content
        # Write via a temp file so a crash cannot leave a truncated sprite cached.
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
        return data, media_type
