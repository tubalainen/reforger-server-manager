"""Reforger Server Manager — FastAPI entrypoint.

Serves the JSON API under /api/* and the built Vue SPA for everything else.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import auth
import config
import models
import serverfiles_api
import templates_api
import workshop_api
from services import docker_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("manager")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("=" * 60)
    logger.info("%s v%s", config.APP_NAME, config.APP_VERSION)
    logger.info("=" * 60)
    if config.settings.session_secret_generated:
        logger.warning(
            "SESSION_SECRET not set — generated a random one; all sessions reset on restart"
        )
    if not config.settings.admin_password or config.settings.admin_password == "change-me-now":
        logger.warning("ADMIN_PASSWORD is unset or still the example value — change it in .env")
    models.init_db()
    if await asyncio.to_thread(docker_service.ping):
        await asyncio.to_thread(
            docker_service.remove_exited, docker_service.ROLE_STEAMCMD
        )
    else:
        logger.warning(
            "Docker daemon not reachable — downloads and server instances are disabled"
        )
    yield


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, lifespan=lifespan)
app.include_router(auth.router)
app.include_router(serverfiles_api.router)
app.include_router(workshop_api.router)
app.include_router(templates_api.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": config.APP_VERSION}


# --- SPA serving (only when a built frontend is present, i.e. in the image) ---
_static_dir = Path(config.settings.static_dir) if config.settings.static_dir else None
if _static_dir and _static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (_static_dir / full_path).resolve()
        if full_path and candidate.is_file() and _static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        # Deep links (/instances, /templates, ...) fall through to the SPA router
        return FileResponse(_static_dir / "index.html")
