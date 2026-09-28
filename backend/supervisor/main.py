"""The Server Supervisor — FastAPI entrypoint (#204, v0.67.0).

One page for whoever manages the machine: every stack on it, its manager and
Docker gate, its ports and its game servers, with the totals and anything that
needs attention. View only — every control stays in each team's own manager.

It runs from the manager's image with its own compose file, next to its
observer (gate/observer.py), which is the only one of the two that can reach
Docker:

    uvicorn supervisor.main:app --host 0.0.0.0 --port 8080

The login is the manager's (auth.py), from the Supervisor's own .env, with a
session cookie of its own. Its password is only ever the .env's.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request

import auth
import config
import models
import web
from supervisor.collector import HISTORY_EVERY, Collector, Observer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("supervisor")

APP_NAME = "Reforger Server Supervisor"
REPO_URL = "https://github.com/tubalainen/reforger-server-manager"

collector = Collector(Observer())


async def _history():
    """A point a minute for the sparklines, whether or not anyone is watching."""
    while True:
        try:
            await asyncio.to_thread(collector.record)
        except Exception as exc:  # never let the loop die silently
            logger.warning("Could not record the totals: %s", exc)
        await asyncio.sleep(HISTORY_EVERY)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("%s v%s", APP_NAME, config.APP_VERSION)
    # Same rules as a manager's login: a password changed in the GUI lives in
    # the database, so it is read before the password in effect is judged.
    models.init_db()
    fatal, warnings = config.startup_security_issues(
        config.settings, gui_password_set=auth.gui_password_set()
    )
    for message in warnings:
        logger.warning(message)
    if fatal:
        for message in fatal:
            logger.error("SECURITY: %s", message)
        raise RuntimeError(
            "Refusing to start: insecure configuration for a network-exposed Supervisor "
            "(see the SECURITY errors above). Fix its .env, or bind it to 127.0.0.1."
        )
    task = asyncio.create_task(_history())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title=APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if config.settings.api_docs else None,
    redoc_url="/redoc" if config.settings.api_docs else None,
    openapi_url="/openapi.json" if config.settings.api_docs else None,
)
web.harden(app, logger)


@app.post("/api/auth/password")
async def no_password_change():
    """The Supervisor's password is the one in its .env, and nowhere else.

    A team changes its password in its GUI because it has no shell; whoever
    runs the Supervisor has the .env. Registered before the login routes, so it
    takes this path from them.
    """
    raise HTTPException(
        status_code=400,
        detail="The Supervisor's password is ADMIN_PASSWORD in its .env: change it "
               "there, then restart it (sudo rsm supervisor restart).",
    )


app.include_router(auth.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def version(request: Request):
    """What the login page needs; the exact version only with a session (R6)."""
    public = {"name": APP_NAME, "auth_enabled": config.settings.auth_enabled}
    if not auth.session_username(request.cookies.get(auth.cookie_name())):
        return public
    return {**public, "version": config.APP_VERSION, "repo_url": REPO_URL}


@app.get("/api/overview")
def overview(_user: str = Depends(auth.require_session)):
    """Every stack on the machine. Plain `def`: it waits on the observer."""
    return collector.overview()


web.serve_spa(app, config.settings.static_dir, index="supervisor.html")
