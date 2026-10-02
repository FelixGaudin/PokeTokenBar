"""FastAPI application: JSON API plus the built single-page frontend."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .balance import ItemKind
from .config import SETTINGS
from .runtime import AppRuntime
from .schemas import (
    ActionResult,
    BuyEggRequest,
    BuyItemRequest,
    DifficultyRequest,
    LanguageRequest,
    LimitDisplayRequest,
    PokemonDetailView,
    RecapView,
    SnapshotView,
    StateView,
    UseCandyRequest,
)
from .state import SaveState, migrate_profiles, parse_save, sanitize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("poketokenbar")

runtime = AppRuntime(SETTINGS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("scanning log roots: %s", ", ".join(str(p) for p in SETTINGS.claude_roots))
    log.info("data dir: %s | timezone: %s", SETTINGS.data_dir, SETTINGS.timezone)
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="PokeTokenBar Web", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    """Hashed assets are immutable; the HTML shell must always be revalidated.

    Without an explicit directive on the shell, browsers apply heuristic caching and
    can keep serving an old index.html — which points at an old bundle it also has
    cached, so a rebuilt frontend silently keeps running the previous code.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers.setdefault(
            "Cache-Control", "public, max-age=31536000, immutable"
        )
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
async def health() -> dict:
    roots = [
        {"path": str(p), "exists": p.is_dir()} for p in SETTINGS.claude_roots
    ]
    return {
        "ok": True,
        "log_roots": roots,
        "credentials_mounted": SETTINGS.credentials_file.is_file(),
        "limits_enabled": SETTINGS.limits_enabled,
        "last_refresh": runtime.last_refresh.isoformat() if runtime.last_refresh else None,
    }


@app.get("/api/state", response_model=StateView)
async def get_state() -> StateView:
    return runtime.build_state()


@app.post("/api/refresh", response_model=StateView)
async def force_refresh() -> StateView:
    await runtime.refresh()
    return runtime.build_state()


@app.post("/api/shop/item", response_model=ActionResult)
async def buy_item(body: BuyItemRequest) -> ActionResult:
    try:
        kind = ItemKind(body.kind)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown item: {body.kind}") from None
    try:
        runtime.companion.buy_item(kind)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    labels = {"rareCandy": "Rare Candy", "mint": "Mint", "shinyCharm": "Shiny Charm"}
    return ActionResult(ok=True, message=f"Bought a {labels[kind.value]}")


@app.post("/api/shop/egg", response_model=ActionResult)
async def buy_egg(body: BuyEggRequest) -> ActionResult:
    try:
        runtime.companion.buy_fresh_egg(body.tier)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ActionResult(ok=True, message="A new egg is incubating")


@app.post("/api/bag/candy", response_model=ActionResult)
async def use_candy(body: UseCandyRequest | None = None) -> ActionResult:
    count = body.count if body else 1
    try:
        outcome = await runtime.companion.use_rare_candy(count)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    messages = {
        "graduated": "Rare Candy used — it graduated!",
        "evolved": "Rare Candy used — it evolved!",
        "progressed": "Rare Candy used",
    }
    return ActionResult(ok=True, message=messages[outcome])


@app.post("/api/bag/mint", response_model=ActionResult)
async def use_mint() -> ActionResult:
    try:
        nature = runtime.companion.use_mint()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ActionResult(ok=True, message=f"Nature is now {nature}")


@app.post("/api/settings/language", response_model=ActionResult)
async def set_language(body: LanguageRequest) -> ActionResult:
    allowed = {"en", "ko", "ja", "es", "fr", "de"}
    if body.language not in allowed:
        raise HTTPException(status_code=400, detail=f"unsupported language: {body.language}")
    # Stored as the short code; name lookup resolves PokéAPI's variant codes.
    runtime.store.state.language = body.language
    runtime.store.save()
    return ActionResult(ok=True, message=f"Language set to {body.language}")


@app.post("/api/settings/difficulty", response_model=ActionResult)
async def set_difficulty(body: DifficultyRequest) -> ActionResult:
    # Settings changes never evolve, graduate or hatch on the spot.
    runtime.companion.set_growth_difficulty(body.growth)
    runtime.companion.set_shop_difficulty(body.shop)
    return ActionResult(ok=True, message="Difficulty saved")


@app.post("/api/settings/limit-display", response_model=ActionResult)
async def set_limit_display(body: LimitDisplayRequest) -> ActionResult:
    if body.mode not in ("used", "remaining"):
        raise HTTPException(status_code=400, detail=f"unknown mode: {body.mode}")
    runtime.prefs.prefs.limit_display = body.mode
    runtime.prefs.save()
    return ActionResult(ok=True, message=None)


@app.get("/api/recap", response_model=RecapView)
async def recap(scope: str = "week", offset: int = 0) -> RecapView:
    if scope not in ("week", "month", "year"):
        raise HTTPException(status_code=400, detail=f"unknown scope: {scope}")
    return runtime.recap(scope, min(0, offset))


@app.get("/api/pokemon/{species_id}", response_model=PokemonDetailView)
async def pokemon_detail(species_id: int, form: str | None = None) -> PokemonDetailView:
    if not 1 <= species_id <= 1025:
        raise HTTPException(status_code=404, detail="no such species")
    try:
        return await runtime.pokemon_detail(species_id, form)
    except Exception as exc:  # noqa: BLE001 - network or parse failure
        log.info("details for %d failed: %s", species_id, exc)
        raise HTTPException(
            status_code=502, detail="Pokémon details could not be loaded."
        ) from None


@app.get("/api/snapshots", response_model=list[SnapshotView])
async def list_snapshots() -> list[SnapshotView]:
    return [_snapshot_view(s) for s in runtime.store.list_snapshots()]


@app.post("/api/snapshots", response_model=SnapshotView)
async def create_snapshot() -> SnapshotView:
    try:
        return _snapshot_view(runtime.store.create_snapshot())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write snapshot: {exc}") from None


@app.post("/api/snapshots/{snapshot_id}/restore", response_model=ActionResult)
async def restore_snapshot(snapshot_id: str) -> ActionResult:
    # Read first: the safety snapshot below may prune the very file being restored.
    try:
        state = runtime.store.read_snapshot(snapshot_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"snapshot unreadable: {exc}") from None
    try:
        runtime.store.create_snapshot()
    except OSError as exc:
        log.warning("safety snapshot before restore failed: %s", exc)
    await _apply_save(state)
    return ActionResult(
        ok=True,
        message=f"Restored — {len(state.dex)} in Pokédex · {state.used_since_install:,} lifetime",
    )


def _snapshot_view(s) -> SnapshotView:
    return SnapshotView(
        id=s.id,
        created_at=s.created_at.isoformat(),
        dex_count=s.dex_count,
        lifetime_tokens=s.lifetime_tokens,
        current_species_id=s.current_species_id,
        current_is_shiny=s.current_is_shiny,
    )


async def _apply_save(state: SaveState) -> None:
    """Shared by import and restore."""
    sanitize(state)
    migrate_profiles(state)
    # The imported ledger belongs to another machine's log history, so drop the
    # baseline and let the next refresh re-seed it here. Without this, the first
    # refresh would read this machine's whole daily total as brand-new usage.
    state.claimed_today_by_provider = None
    state.install_baseline_set = False
    state.last_date = None
    runtime.store.replace(state)
    runtime.companion.reset_after_import()
    await runtime.refresh()


@app.get("/api/save")
async def export_save() -> Response:
    payload = runtime.store.state.model_dump_json(indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="poketokenbar-save.json"'},
    )


@app.post("/api/save", response_model=ActionResult)
async def import_save(request: Request) -> ActionResult:
    raw = await request.body()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"not valid JSON: {exc}") from None
    try:
        state = parse_save(parsed)
    except Exception as exc:  # noqa: BLE001 - report the validation failure verbatim
        raise HTTPException(status_code=400, detail=f"not a valid save: {exc}") from None
    await _apply_save(state)
    return ActionResult(ok=True, message="Save imported")


@app.get("/api/sprite/{species_id}")
async def sprite(
    species_id: int, animated: bool = True, shiny: bool = False, form: str | None = None
) -> Response:
    if not 1 <= species_id <= 1025:
        raise HTTPException(status_code=404, detail="no such species")
    client = await runtime.http()
    got = await runtime.sprites.pokemon(
        client, species_id, animated=animated, shiny=shiny, form=form
    )
    if got is None:
        raise HTTPException(status_code=404, detail="sprite unavailable")
    data, media_type = got
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/api/sprite/item/{name}")
async def item_sprite(name: str) -> Response:
    client = await runtime.http()
    got = await runtime.sprites.item(client, name)
    if got is None:
        raise HTTPException(status_code=404, detail="sprite unavailable")
    data, media_type = got
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


if SETTINGS.static_dir is not None:
    static_dir = SETTINGS.static_dir
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(path: str) -> Response:
        """Serve the built SPA, falling back to index.html for client routes."""
        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = (static_dir / path).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
        except ValueError:
            return JSONResponse({"detail": "not found"}, status_code=404)
        if path and candidate.is_file():
            return FileResponse(candidate)
        index = static_dir / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"detail": "frontend not built"}, status_code=404)
else:

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def no_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "detail": "frontend not built — the API is at /api/state",
                "docs": "/docs",
            }
        )
