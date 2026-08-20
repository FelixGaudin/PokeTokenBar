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
    LanguageRequest,
    StateView,
)
from .state import SaveState

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
    return ActionResult(ok=True, message=f"Bought {kind.value}")


@app.post("/api/shop/egg", response_model=ActionResult)
async def buy_egg(body: BuyEggRequest) -> ActionResult:
    try:
        runtime.companion.buy_fresh_egg(body.tier)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ActionResult(ok=True, message="A new egg is incubating")


@app.post("/api/bag/candy", response_model=ActionResult)
async def use_candy() -> ActionResult:
    try:
        await runtime.companion.use_rare_candy()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ActionResult(ok=True, message="Rare Candy used")


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
        state = SaveState.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001 - report the validation failure verbatim
        raise HTTPException(status_code=400, detail=f"not a valid save: {exc}") from None

    # The imported ledger belongs to another machine's log history, so drop the
    # baseline and let the next refresh re-seed it here. Without this, the first
    # refresh would read this machine's whole daily total as brand-new usage.
    state.claimed_today_by_provider = None
    state.install_baseline_set = False
    state.last_date = None

    runtime.store.replace(state)
    runtime.companion.current_line = None
    await runtime.refresh()
    return ActionResult(ok=True, message="Save imported")


@app.get("/api/sprite/{species_id}")
async def sprite(species_id: int, animated: bool = True, shiny: bool = False) -> Response:
    if not 1 <= species_id <= 1025:
        raise HTTPException(status_code=404, detail="no such species")
    client = await runtime.http()
    got = await runtime.sprites.pokemon(client, species_id, animated=animated, shiny=shiny)
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
