"""Reforger Server Manager — FastAPI entrypoint.

Serves the JSON API under /api/* and the built Vue SPA for everything else.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import auth
import backup_api
import config
import instances_api
import mod_templates_api
import models
import mods_api
import serverfiles_api
import system_api
import templates_api
import workshop_api
from services import docker_service, instance_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("manager")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("=" * 60)
    logger.info("%s v%s", config.APP_NAME, config.APP_VERSION)
    logger.info("=" * 60)
    # Fail closed on an insecure, network-exposed configuration (security review
    # R2/R3). Fatal issues only fire when WEB_BIND publishes the GUI beyond
    # localhost, so a local run stays frictionless; an exposed one must either
    # have a real login or an explicit AUTH_DELEGATED_ACK that a proxy owns auth.
    fatal, warnings = config.startup_security_issues(config.settings)
    for message in warnings:
        logger.warning(message)
    if fatal:
        for message in fatal:
            logger.error("SECURITY: %s", message)
        raise RuntimeError(
            "Refusing to start: insecure configuration for a network-exposed, "
            "Docker-controlling GUI (see the SECURITY errors above). Fix the "
            "configuration, or bind the GUI to 127.0.0.1."
        )
    models.init_db()
    # Seed the Mods Overview registry from existing templates so mods that were
    # baked in before the registry existed still show up (#131). Idempotent.
    try:
        from services import mod_registry
        await asyncio.to_thread(mod_registry.backfill_from_templates)
    except Exception as exc:  # never block startup on a best-effort backfill
        logger.warning("Mod registry backfill failed: %s", exc)
    if not await asyncio.to_thread(docker_service.ping):
        logger.warning(
            "Docker daemon not reachable — downloads and server instances are "
            "disabled until it comes back"
        )
    # Started unconditionally. It used to be created only if this first ping
    # succeeded, so a daemon that was merely slow to come up (a host reboot, a
    # cold Docker Desktop) silently cost you crash recovery, scheduled restarts
    # AND log pruning for the lifetime of the process — precisely when auto-restart
    # matters most (#85). Each pass now checks the daemon itself and no-ops.
    monitor_task = asyncio.create_task(_crash_monitor())
    try:
        yield
    finally:
        monitor_task.cancel()
        # Graceful shutdown (#113): stop every running game server and remove
        # the sibling containers, so nothing keeps the compose network 'in use'
        # and `docker compose down` can remove it. desired_state stays 'running',
        # so reconcile_and_recover brings the auto_start ones back on the next
        # boot (see _crash_monitor). Needs the stop_grace_period set in
        # docker-compose.yaml — with the default 10s compose would SIGKILL us
        # mid-stop.
        await asyncio.to_thread(instance_service.shutdown_all_instances)


async def _crash_monitor():
    """Restart crashed instances, apply scheduled restarts (every 15s), and
    prune old logs (hourly). Waits the daemon out rather than giving up on it."""
    ticks = 0
    steamcmd_cleaned = False
    while True:
        try:
            if await asyncio.to_thread(docker_service.ping):
                if not steamcmd_cleaned:
                    # One-time startup cleanup, deferred until Docker is actually there.
                    await asyncio.to_thread(
                        docker_service.remove_exited, docker_service.ROLE_STEAMCMD
                    )
                    steamcmd_cleaned = True
                # Recover crashed servers, and bring auto_start ones back after a
                # reboot / the #113 shutdown that removed their containers.
                await asyncio.to_thread(instance_service.reconcile_and_recover)
                await asyncio.to_thread(instance_service.apply_scheduled_restarts)
                if ticks % 240 == 0:  # ~hourly at a 15s cadence
                    await asyncio.to_thread(instance_service.prune_old_logs)
        except Exception as exc:  # never let the monitor die silently
            logger.warning("Crash monitor pass failed: %s", exc)
        ticks += 1
        await asyncio.sleep(15)


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
    # The interactive docs enumerate every endpoint of an API that controls
    # Docker, and they are served before any auth dependency runs. Off unless
    # API_DOCS=true is set deliberately for development (security review R6).
    docs_url="/docs" if config.settings.api_docs else None,
    redoc_url="/redoc" if config.settings.api_docs else None,
    openapi_url="/openapi.json" if config.settings.api_docs else None,
)

# Kept tight on purpose: the SPA loads no third-party scripts, fonts or images,
# so nothing here needs a CDN allowance. 'unsafe-inline' is granted for STYLES
# only — Vue writes inline style attributes for :style bindings — and never for
# scripts, which is the direction that matters for XSS. Override with
# CONTENT_SECURITY_POLICY if a deployment genuinely needs a looser policy.
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def security_middleware(request, call_next):
    """Reject cross-site state changes, then harden every response (R7)."""
    # CSRF: the session is a cookie, so a state-changing request that a browser
    # says came from another site must not be honoured. A request with no Origin
    # is a non-browser client (curl, scripts) and is left alone — browsers always
    # send Origin on cross-origin writes, which is what this is defending against.
    if request.method not in _SAFE_METHODS and not auth.request_origin_ok(request):
        logger.warning(
            "Blocked cross-origin %s %s from origin %r",
            request.method, request.url.path, request.headers.get("origin", ""),
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "Cross-origin request blocked"},
        )

    response = await call_next(request)
    headers = response.headers
    headers.setdefault("Content-Security-Policy",
                       config.settings.content_security_policy or DEFAULT_CSP)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")  # legacy peer of frame-ancestors
    headers.setdefault("Referrer-Policy", "no-referrer")
    if auth.is_https(request):
        # Only meaningful over TLS, and actively harmful on the plain-HTTP
        # localhost default: it would pin a browser to HTTPS for a host that
        # does not serve it.
        headers.setdefault("Strict-Transport-Security",
                           "max-age=31536000; includeSubDomains")
    return response


app.include_router(auth.router)
app.include_router(serverfiles_api.router)
app.include_router(workshop_api.router)
app.include_router(templates_api.router)
app.include_router(mods_api.router)
app.include_router(mod_templates_api.router)
app.include_router(backup_api.router)
app.include_router(instances_api.router)
app.include_router(system_api.router)


REPO_URL = "https://github.com/tubalainen/reforger-server-manager"


@app.get("/api/health")
async def health():
    # Deliberately just liveness. It used to report APP_VERSION, which handed an
    # unauthenticated caller the exact build to match against known CVEs (R6).
    return {"status": "ok"}


@app.get("/api/version")
async def version(request: Request):
    """Build info. Unauthenticated callers get only what the login page needs.

    ``auth_enabled`` stays public because the SPA reads it before anyone can log
    in, and it discloses nothing an attacker could not learn by simply calling a
    protected endpoint. The precise version and repo link are the fingerprinting
    risk, so those require a session (R6).
    """
    public = {"name": config.APP_NAME, "auth_enabled": config.settings.auth_enabled}
    if not auth.session_username(request.cookies.get(auth.COOKIE_NAME)):
        return public
    return {**public, "version": config.APP_VERSION, "repo_url": REPO_URL}


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
