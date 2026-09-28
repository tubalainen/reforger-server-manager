"""Reforger Server Manager — FastAPI entrypoint.

Serves the JSON API under /api/* and the built Vue SPA for everything else.
"""
import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

import auth
import backup_api
import config
import instances_api
import mod_templates_api
import models
import mods_api
import serverfiles_api
import stacks
import system_api
import templates_api
import web
import workshop_api
from services import auto_update, docker_service, fleet_history, instance_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("manager")

# The SPA ships a web app manifest next to its icons. Older Pythons' type table does
# not know the extension, and a guessed octet-stream under nosniff is not a manifest.
mimetypes.add_type("application/manifest+json", ".webmanifest")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("=" * 60)
    logger.info("%s v%s", config.APP_NAME, config.APP_VERSION)
    logger.info("=" * 60)
    # The stack name becomes part of container, network and volume names; a bad
    # one would only fail later, at the first container create (#204).
    stack_error = stacks.stack_name_error(config.settings.rsm_stack)
    if stack_error:
        logger.error(stack_error)
        raise RuntimeError(f"Refusing to start: {stack_error}")
    if config.settings.rsm_stack != stacks.DEFAULT_STACK:
        logger.info("Stack: %s", config.settings.rsm_stack)
    # Fail closed on an insecure, network-exposed configuration (security review
    # R2/R3). Fatal issues only fire when WEB_BIND publishes the GUI beyond
    # localhost, so a local run stays frictionless; an exposed one must either
    # have a real login or an explicit AUTH_DELEGATED_ACK that a proxy owns auth.
    # The database first: a password changed in the GUI (#204) lives there, and
    # it is the password in effect that the check has to judge.
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
            "Refusing to start: insecure configuration for a network-exposed, "
            "Docker-controlling GUI (see the SECURITY errors above). Fix the "
            "configuration, or bind the GUI to 127.0.0.1."
        )
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
    exposure_checked = False
    gate_checked = False
    while True:
        try:
            if await asyncio.to_thread(docker_service.ping):
                if not steamcmd_cleaned:
                    # One-time startup cleanup, deferred until Docker is actually there.
                    await asyncio.to_thread(
                        docker_service.remove_exited, docker_service.ROLE_STEAMCMD
                    )
                    steamcmd_cleaned = True
                if not exposure_checked:
                    # Say it in the log too, not only in the GUI (v0.64.1). Asked
                    # again each pass until the answer is certain.
                    warning = await asyncio.to_thread(docker_service.docker_api_exposure)
                    exposure_checked = docker_service.exposure_known()
                    if warning:
                        logger.error("SECURITY: %s %s", warning["detail"], warning["action"])
                        gate_checked = True  # the same fix; one message is enough
                if exposure_checked and not gate_checked:
                    # A manager on a compose file older than v0.65.0 (#204).
                    gate = await asyncio.to_thread(docker_service.gate_warning)
                    gate_checked = docker_service.gate_known()
                    if gate:
                        logger.warning("%s. %s %s", gate["title"], gate["detail"], gate["action"])
                    engine = await asyncio.to_thread(docker_service.engine_warning)
                    if engine:
                        logger.error("%s. %s %s", engine["title"], engine["detail"],
                                     engine["action"])
                # Recover crashed servers, and bring auto_start ones back after a
                # reboot / the #113 shutdown that removed their containers.
                await asyncio.to_thread(instance_service.reconcile_and_recover)
                await asyncio.to_thread(instance_service.apply_scheduled_restarts)
                # Daily check for new Arma server releases; a no-op until it
                # comes due, and it runs its own task when it does (#177).
                await auto_update.tick()
                if ticks % 240 == 0:  # ~hourly at a 15s cadence
                    await asyncio.to_thread(instance_service.prune_old_logs)
                # One point a minute for the Servers overview's sparklines (#189).
                if ticks % fleet_history.SAMPLE_EVERY_TICKS == 0:
                    await asyncio.to_thread(instance_service.record_summary_sample)
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

web.harden(app, logger)


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
    if not auth.session_username(request.cookies.get(auth.cookie_name())):
        return public
    return {**public, "version": config.APP_VERSION, "repo_url": REPO_URL}


# The SPA, when a built frontend is present (i.e. in the image).
web.serve_spa(app, config.settings.static_dir)
