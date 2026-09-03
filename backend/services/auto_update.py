"""Daily check for new Arma Reforger server releases on Steam (#177).

Three separate things, each one opt-in beyond the first:

  1. Every 24 hours the manager asks Steam for the current build id of each
     INSTALLED branch and remembers it. Nothing is downloaded; the Server
     Instances page just says an update is waiting.
  2. With *auto_download* on, a branch whose build moved starts its normal
     SteamCMD download by itself — the same job the Download button starts,
     visible in the same place with the same log.
  3. With *auto_restart* on, every instance of that branch that should be
     running is restarted once the download finished, which is the only way a
     server actually starts running the new build.

The check result is persisted (models.AppSetting) so the prompt survives a
manager restart and a restart does not re-run the check. "Update available" is
never stored: it is recomputed from the build id Steam last reported against
the build id currently on disk, so it disappears the moment the files are
updated — by the auto-updater or by hand.
"""
import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from sqlmodel import Session

import config
import models
from services import docker_service, instance_service
from services.steam_service import steam

logger = logging.getLogger("manager.autoupdate")

SETTINGS_KEY = "auto_update"
CHECK_INTERVAL_SECONDS = 24 * 3600
# How often a running auto-download is looked at while waiting for it to end.
DOWNLOAD_POLL_SECONDS = 5

_check_task: asyncio.Task | None = None
_download_tasks: dict[str, asyncio.Task] = {}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def _blank() -> dict:
    return {
        "auto_download": False,
        "auto_restart": False,
        "last_check_at": None,
        # branch -> {"latest_build": str|None, "checked_at": float, "error": str}
        "branches": {},
    }


def _read() -> dict:
    """The stored blob, normalised. Anything unparseable reads as defaults."""
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, SETTINGS_KEY)
        raw = row.value if row else ""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    state = _blank()
    if not isinstance(data, dict):
        return state
    state["auto_download"] = bool(data.get("auto_download"))
    state["auto_restart"] = bool(data.get("auto_restart"))
    last = data.get("last_check_at")
    if isinstance(last, int | float):
        state["last_check_at"] = float(last)
    branches = data.get("branches")
    if isinstance(branches, dict):
        state["branches"] = {
            name: entry
            for name, entry in branches.items()
            if name in config.BRANCHES and isinstance(entry, dict)
        }
    return state


def _write(state: dict) -> None:
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, SETTINGS_KEY)
        if row is None:
            row = models.AppSetting(key=SETTINGS_KEY)
        row.value = json.dumps(state)
        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _branch_status(name: str, state: dict) -> dict:
    meta = config.BRANCHES[name]
    recorded = state["branches"].get(name, {})
    installed = steam.installed_info(name)
    installed_build = installed.get("build_id") if installed else None
    latest_build = recorded.get("latest_build")
    job = steam.job(name)
    return {
        "branch": name,
        "label": meta["label"],
        "installed": installed is not None,
        "installed_build": installed_build,
        "latest_build": latest_build,
        "update_available": bool(
            installed_build and latest_build and installed_build != latest_build
        ),
        "checked_at": recorded.get("checked_at"),
        "error": recorded.get("error", ""),
        "downloading": bool(job and job.running),
    }


def status() -> dict:
    """Settings plus what the last check found, for the GUI (blocking)."""
    state = _read()
    last = state["last_check_at"]
    return {
        "auto_download": state["auto_download"],
        "auto_restart": state["auto_restart"],
        "last_check_at": last,
        "next_check_at": (last + CHECK_INTERVAL_SECONDS) if last else None,
        "checking": busy(),
        "branches": [_branch_status(name, state) for name in config.BRANCHES],
    }


def save_settings(auto_download: bool | None, auto_restart: bool | None) -> dict:
    """Update the toggles (None leaves one alone) and report the new status."""
    state = _read()
    if auto_download is not None:
        state["auto_download"] = bool(auto_download)
    if auto_restart is not None:
        state["auto_restart"] = bool(auto_restart)
    _write(state)
    logger.info(
        "Automatic server-file updates: download=%s restart=%s",
        state["auto_download"], state["auto_restart"],
    )
    return status()


# --------------------------------------------------------------------------- #
# The daily check
# --------------------------------------------------------------------------- #

def busy() -> bool:
    return _check_task is not None and not _check_task.done()


def due(now: float | None = None) -> bool:
    """True when 24h have passed since the last completed check (or none has)."""
    last = _read()["last_check_at"]
    if not last:
        return True
    return (now or time.time()) - last >= CHECK_INTERVAL_SECONDS


async def tick() -> None:
    """One background-monitor pass: start the daily check if it is due.

    Returns immediately — the check itself runs as its own task, because
    querying Steam spawns a short container per branch and must never hold up
    crash recovery or the scheduled restarts sharing that loop.
    """
    if busy() or not await asyncio.to_thread(due):
        return
    start_check()


def start_check() -> bool:
    """Kick off a check now. False if one is already running."""
    global _check_task
    if busy():
        return False
    _check_task = asyncio.create_task(_run_check())
    return True


def _record_check(results: dict[str, dict | None]) -> dict:
    """Store what the check found (blocking), and return the fresh state.

    The settings are re-read here rather than carried through the check: a check
    can spend a minute or two talking to Steam, and a toggle the user flipped in
    the meantime must not be written back to its old value.
    """
    state = _read()
    for branch, entry in results.items():
        if entry is None:
            state["branches"].pop(branch, None)
        else:
            state["branches"][branch] = entry
    state["last_check_at"] = time.time()
    _write(state)
    return state


async def _run_check() -> None:
    try:
        if not await asyncio.to_thread(docker_service.ping):
            # No daemon, no steamcmd container: leave last_check_at alone so the
            # check simply happens on a later tick.
            return
        results: dict[str, dict | None] = {}
        updated: list[str] = []
        for branch in config.BRANCHES:
            installed = await asyncio.to_thread(steam.installed_info, branch)
            installed_build = installed.get("build_id") if installed else None
            if not installed_build:
                # Not installed: there is no "update", only a first download.
                results[branch] = None
                continue
            latest = await asyncio.to_thread(steam.latest_build_id, branch)
            results[branch] = {
                "latest_build": latest,
                "checked_at": time.time(),
                "error": "" if latest else "Steam did not report a build id",
            }
            if latest and latest != installed_build:
                logger.info(
                    "New %s server release: build %s (installed %s)",
                    branch, latest, installed_build,
                )
                updated.append(branch)
        state = await asyncio.to_thread(_record_check, results)
        if state["auto_download"]:
            for branch in updated:
                _start_download(branch)
    except Exception as exc:  # a failed check must never take the monitor down
        logger.warning("Automatic update check failed: %s", exc)


# --------------------------------------------------------------------------- #
# Auto-download → auto-restart
# --------------------------------------------------------------------------- #

def _start_download(branch: str) -> None:
    task = _download_tasks.get(branch)
    if task and not task.done():
        return
    _download_tasks[branch] = asyncio.create_task(_download_and_restart(branch))


async def _download_and_restart(branch: str) -> None:
    """Run the normal download job, then restart that branch's instances."""
    try:
        job = await steam.start(branch)
    except RuntimeError as exc:
        # A download is already running (started by hand, or a leftover
        # container): nothing to do, it updates the same files.
        logger.info("Automatic %s download not started: %s", branch, exc)
        return
    logger.info("Automatic %s server-files download started", branch)
    try:
        while job.running:
            await asyncio.sleep(DOWNLOAD_POLL_SECONDS)
        if job.status != "success":
            logger.warning(
                "Automatic %s download did not finish: %s", branch, job.error
            )
            return
        # Read the toggle now rather than when the check started: a download can
        # take an hour, and the user may well have changed their mind in it.
        if not await asyncio.to_thread(lambda: _read()["auto_restart"]):
            logger.info(
                "Automatic %s download finished; instances keep the old build "
                "until they are restarted", branch,
            )
            return
        names = await asyncio.to_thread(
            instance_service.restart_instances_for_branch, branch
        )
        logger.info(
            "Restarted %s instance(s) onto the new %s build: %s",
            len(names), branch, ", ".join(names) or "none",
        )
    except Exception as exc:
        logger.warning("Automatic %s update failed after download: %s", branch, exc)
