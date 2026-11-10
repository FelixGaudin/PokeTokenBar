import httpx
import pytest

from app.sprites import SpriteStore, SpriteUnavailable


def test_back_sprites_use_their_own_cache_key_and_url(tmp_path):
    store = SpriteStore(tmp_path)
    assert store.cache_key(25, animated=True, shiny=True, back=True) == "25-back-sha"
    assert store.cache_key(25, animated=False, shiny=False) == "25-s"
    url = store._sprite_url(25, animated=True, shiny=True, back=True)
    assert url.endswith("/animated/back/shiny/25.gif")
    assert store._sprite_url(25, animated=False, shiny=False, back=True).endswith("/back/25.png")


async def test_a_cached_back_sprite_is_served(tmp_path):
    store = SpriteStore(tmp_path)
    (tmp_path / "25-back-a.gif").write_bytes(b"back")
    async with httpx.AsyncClient() as client:
        assert await store.pokemon(client, 25, animated=True, shiny=False, back=True) == (
            b"back",
            "image/gif",
        )


async def test_a_missing_back_view_falls_back_to_the_front(tmp_path):
    store = SpriteStore(tmp_path)
    (tmp_path / "25-a.gif").write_bytes(b"front")
    # Both back variants were already answered 404 in this process.
    for shiny in (True, False):
        store._missing.add(store._sprite_url(25, animated=True, shiny=shiny, back=True))
    store._missing.add(store._sprite_url(25, animated=True, shiny=True))
    async with httpx.AsyncClient() as client:
        data, _ = await store.pokemon(client, 25, animated=True, shiny=True, back=True)
    assert data == b"front"


async def test_a_missing_front_sprite_is_not_retried(tmp_path):
    store = SpriteStore(tmp_path)
    store._missing.add(store._sprite_url(25, animated=True, shiny=False))
    async with httpx.AsyncClient() as client:
        with pytest.raises(SpriteUnavailable) as exc:
            await store.pokemon(client, 25, animated=True, shiny=False)
    assert not exc.value.transient
