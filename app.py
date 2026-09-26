"""FastAPI entry point. Run with::

    uv run uvicorn app:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from config import get_settings
from memu_setup import build_memory_service

from routes.character_routes import router as char_router
from routes.chat_import_routes import router as chat_import_router
from routes.chat_routes import router as chat_router
from routes.group_routes import router as group_router
from routes.memory_routes import router as memory_router
from routes.settings_routes import router as settings_router
from routes.tts_routes import router as tts_router
from routes.upload_routes import router as upload_router


settings = get_settings()
log = logging.getLogger("ai-girlfriend")

# Ensure application logs reach stderr in a stable format. uvicorn
# configures its own access logger but leaves the root logger alone,
# which is why chat_routes / preset_seed log calls were silently
# swallowed in earlier runs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("[lifespan] startup: building memU singleton")
    app.state.memu = build_memory_service()
    log.info("[lifespan] ready")

    # Auto-import preset photos from <repo-root>/img/<char-name>/ into the
    # pool for any character that doesn't have one yet. Non-blocking —
    # we only seed the DB rows; the vision caption + embedding get
    # generated lazily by a background task so startup stays fast.
    try:
        from preset_seed import seed_preset_pool

        await seed_preset_pool()
    except Exception as exc:  # noqa: BLE001
        log.warning("preset image pool seeding failed: %s", exc)

    # Pre-warm the local embedding model in the background so the first
    # chat message after a restart doesn't blow the picker's 12s timeout
    # on a cold model load (bge-m3 takes ~6s to load from disk).
    try:
        import threading
        from local_embed_server import _load_model

        def _warmup() -> None:
            try:
                _load_model(settings.embed_model_name)
            except Exception as exc:  # noqa: BLE001
                log.warning("embedding model pre-warm failed: %s", exc)

        threading.Thread(target=_warmup, daemon=True, name="embed-warmup").start()
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding model pre-warm setup failed: %s", exc)

    try:
        yield
    finally:
        log.info("[lifespan] shutting down")
        app.state.memu.database.close()


app = FastAPI(title="AI Girlfriend", lifespan=lifespan)

# Session middleware for cookie-based auth
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="gf_session",
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=False,  # set True behind TLS
)

# Static + uploads
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount(
    "/uploads",
    StaticFiles(directory=str(settings.data_dir / "uploads")),
    name="uploads",
)


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Prevent browser caching of JS/CSS so edits take effect on refresh."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") and request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

# Local embedding server — register routes directly on the same FastAPI app
# so memU's httpx backend can call http://127.0.0.1:{APP_PORT}/v1/embeddings.
if settings.embed_local_enabled:
    try:
        from local_embed_server import register_routes

        register_routes(app, settings.embed_model_name)
        log.info(
            "registered local embed routes at /v1/* (model=%s)", settings.embed_model_name
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to register local embed routes: %s", exc)


templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse(url="/chat")


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"user": get_settings().admin_username},
    )


# Routers
app.include_router(char_router)

app.include_router(chat_router)
app.include_router(chat_import_router)
app.include_router(group_router)
app.include_router(memory_router)
app.include_router(settings_router)
app.include_router(tts_router)
app.include_router(upload_router)


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )