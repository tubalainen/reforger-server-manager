"""API for downloading/updating the Arma Reforger server files per branch."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

import auth
import config
from services import docker_service
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
        "server_image": config.settings.reforger_server_image,
        "branches": [_branch_state(b) for b in config.BRANCHES],
    }


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
        raise HTTPException(status_code=409, detail=str(exc))
    return job.snapshot()


@router.websocket("/{branch}/ws")
async def download_events(websocket: WebSocket, branch: str):
    if not auth.session_username(websocket.cookies.get(auth.COOKIE_NAME)):
        await websocket.close(code=4401)
        return
    if branch not in config.BRANCHES:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    job = steam.job(branch)
    await websocket.send_json({
        "type": "snapshot",
        "state": _branch_state(branch),
        "log": list(job.log) if job else [],
    })
    queue = steam.subscribe(branch)
    if queue is None:
        await websocket.close()
        return
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        steam.unsubscribe(branch, queue)
