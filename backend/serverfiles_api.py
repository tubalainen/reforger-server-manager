"""API for downloading/updating the Arma Reforger server files per branch."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel

import auth
import config
from services import auto_update, docker_service, instance_service
from services.image_service import ImageService, image, steamcmd_image
from services.jobs import stream_job
from services.steam_service import steam

router = APIRouter(prefix="/api/serverfiles", tags=["serverfiles"])


def _branch_state(branch: str) -> dict:
    job = steam.job(branch)
    return {
        "branch": branch,
        "label": config.BRANCHES[branch]["label"],
        "app_id": config.BRANCHES[branch]["app_id"],
        "installed": steam.installed_info(branch),
        "job": job.snapshot() if job else None,
    }


def _require_branch(branch: str) -> None:
    if branch not in config.BRANCHES:
        raise HTTPException(status_code=404, detail=f"Unknown branch '{branch}'")


@router.get("")
async def serverfiles_status(_user: str = Depends(auth.require_session)):
    docker_ok = await asyncio.to_thread(docker_service.ping)
    return {
        "docker": docker_ok,
        "steamcmd_image": config.settings.steamcmd_image,
        "steamcmd_image_present": (
            await asyncio.to_thread(steamcmd_image.present) if docker_ok else False
        ),
        "steamcmd_image_job": steamcmd_image.job.snapshot() if steamcmd_image.job else None,
        "server_image": config.settings.reforger_server_image,
        "server_image_present": (
            await asyncio.to_thread(image.present) if docker_ok else False
        ),
        "server_image_job": image.job.snapshot() if image.job else None,
        "branches": [_branch_state(b) for b in config.BRANCHES],
    }


async def _pull(service: ImageService) -> dict:
    if not await asyncio.to_thread(docker_service.ping):
        raise HTTPException(status_code=409, detail="Docker daemon is not reachable")
    try:
        job = await service.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.snapshot()


async def _pull_events(websocket: WebSocket, service: ImageService) -> None:
    if not auth.request_origin_ok(websocket):
        await websocket.close(code=4403)  # cross-site WebSocket hijacking (R12)
        return
    if not auth.session_username(websocket.cookies.get(auth.cookie_name())):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    job = service.job
    await stream_job(websocket, job, {
        "type": "snapshot",
        "job": job.snapshot() if job else None,
        "present": await asyncio.to_thread(service.present),
        "log": list(job.log) if job else [],
    })


@router.post("/image/pull", status_code=202)
async def pull_server_image(_user: str = Depends(auth.require_session)):
    """Pull the reforger runtime image the instances are created from (#50)."""
    return await _pull(image)


@router.websocket("/image/ws")
async def image_events(websocket: WebSocket):
    await _pull_events(websocket, image)


@router.post("/steamcmd-image/pull", status_code=202)
async def pull_steamcmd_image(_user: str = Depends(auth.require_session)):
    """Pull the SteamCMD image the server-file downloads run in (#241)."""
    return await _pull(steamcmd_image)


@router.websocket("/steamcmd-image/ws")
async def steamcmd_image_events(websocket: WebSocket):
    await _pull_events(websocket, steamcmd_image)


class AutoUpdateSettings(BaseModel):
    """The two opt-in toggles of the daily update check (#177)."""

    auto_download: bool | None = None
    auto_restart: bool | None = None


@router.get("/auto-update")
async def auto_update_status(_user: str = Depends(auth.require_session)):
    """What the daily check last found, plus the toggles (#177)."""
    return await asyncio.to_thread(auto_update.status)


@router.put("/auto-update")
async def set_auto_update(
    body: AutoUpdateSettings, _user: str = Depends(auth.require_session)
):
    return await asyncio.to_thread(
        auto_update.save_settings, body.auto_download, body.auto_restart
    )


@router.post("/auto-update/check", status_code=202)
async def run_auto_update_check(_user: str = Depends(auth.require_session)):
    """Run the daily check now instead of waiting for it to come due."""
    if not await asyncio.to_thread(docker_service.ping):
        raise HTTPException(status_code=409, detail="Docker daemon is not reachable")
    auto_update.start_check()
    return await asyncio.to_thread(auto_update.status)


@router.delete("/{branch}", status_code=204)
async def remove_serverfiles(branch: str, _user: str = Depends(auth.require_session)):
    """Delete a branch's server files so they can be re-downloaded (issue #18)."""
    _require_branch(branch)
    running = await asyncio.to_thread(
        instance_service.running_instance_names_for_branch, branch
    )
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"Stop these {branch} servers first: {', '.join(running)}",
        )
    try:
        await asyncio.to_thread(steam.remove_files, branch)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{branch}/check-update")
async def check_update(branch: str, _user: str = Depends(auth.require_session)):
    """Compare the installed build against Steam's current public build."""
    _require_branch(branch)
    if not await asyncio.to_thread(docker_service.ping):
        raise HTTPException(status_code=409, detail="Docker daemon is not reachable")
    return await asyncio.to_thread(steam.update_status, branch)


@router.post("/{branch}/download", status_code=202)
async def start_download(branch: str, _user: str = Depends(auth.require_session)):
    _require_branch(branch)
    try:
        job = await steam.start(branch)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.snapshot()


@router.websocket("/{branch}/ws")
async def download_events(websocket: WebSocket, branch: str):
    if not auth.request_origin_ok(websocket):
        await websocket.close(code=4403)  # cross-site WebSocket hijacking (R12)
        return
    if not auth.session_username(websocket.cookies.get(auth.cookie_name())):
        await websocket.close(code=4401)
        return
    if branch not in config.BRANCHES:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    job = steam.job(branch)
    await stream_job(websocket, job, {
        "type": "snapshot",
        "state": _branch_state(branch),
        "log": list(job.log) if job else [],
    })
