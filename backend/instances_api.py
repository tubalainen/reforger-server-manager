"""Server instance CRUD + lifecycle + live log streaming (auth-gated)."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import auth
from services import docker_service, instance_service
from services.instance_service import InstanceError

logger = logging.getLogger("manager.instances_api")

router = APIRouter(prefix="/api/instances", tags=["instances"])


class CreateInstance(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    template_id: int
    branch: str = "stable"
    # Optional explicit host ports; omit to auto-lease from the .env ranges
    game_port: int | None = Field(default=None, ge=1, le=65535)
    a2s_port: int | None = Field(default=None, ge=1, le=65535)
    rcon_port: int | None = Field(default=None, ge=1, le=65535)


class AutoRestart(BaseModel):
    auto_restart: bool


@router.get("")
async def list_instances(_user: str = Depends(auth.require_session)):
    return await asyncio.to_thread(instance_service.list_views)


@router.get("/summary")
async def summary(_user: str = Depends(auth.require_session)):
    return await asyncio.to_thread(instance_service.instances_summary)


@router.post("", status_code=201)
async def create_instance(body: CreateInstance, _user: str = Depends(auth.require_session)):
    try:
        inst = await asyncio.to_thread(
            instance_service.create_instance,
            body.name, body.template_id, body.branch,
            body.game_port, body.a2s_port, body.rcon_port,
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await asyncio.to_thread(_view, inst.id)


def _view(instance_id: int) -> dict:
    for v in instance_service.list_views():
        if v["id"] == instance_id:
            return v
    raise HTTPException(status_code=404, detail="Instance not found")


@router.get("/{instance_id}")
async def get_instance(instance_id: int, _user: str = Depends(auth.require_session)):
    return await asyncio.to_thread(_view, instance_id)


@router.get("/{instance_id}/stats")
async def stats(instance_id: int, _user: str = Depends(auth.require_session)):
    try:
        return await asyncio.to_thread(instance_service.instance_stats, instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _action(fn, instance_id: int):
    try:
        fn(instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{instance_id}/start")
async def start(instance_id: int, _user: str = Depends(auth.require_session)):
    await asyncio.to_thread(_action, instance_service.start_instance, instance_id)
    return await asyncio.to_thread(_view, instance_id)


@router.post("/{instance_id}/stop")
async def stop(instance_id: int, _user: str = Depends(auth.require_session)):
    await asyncio.to_thread(_action, instance_service.stop_instance, instance_id)
    return await asyncio.to_thread(_view, instance_id)


@router.post("/{instance_id}/restart")
async def restart(instance_id: int, _user: str = Depends(auth.require_session)):
    await asyncio.to_thread(_action, instance_service.stop_instance, instance_id)
    await asyncio.to_thread(_action, instance_service.start_instance, instance_id)
    return await asyncio.to_thread(_view, instance_id)


@router.put("/{instance_id}/auto-restart")
async def auto_restart(
    instance_id: int, body: AutoRestart, _user: str = Depends(auth.require_session)
):
    await asyncio.to_thread(
        _action_value, instance_service.set_auto_restart, instance_id, body.auto_restart
    )
    return await asyncio.to_thread(_view, instance_id)


def _action_value(fn, instance_id: int, value):
    try:
        fn(instance_id, value)
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{instance_id}", status_code=204)
async def delete(instance_id: int, _user: str = Depends(auth.require_session)):
    await asyncio.to_thread(instance_service.delete_instance, instance_id)


@router.get("/{instance_id}/logfiles")
async def list_logfiles(instance_id: int, _user: str = Depends(auth.require_session)):
    return await asyncio.to_thread(instance_service.list_log_files, instance_id)


@router.get("/{instance_id}/logfiles/download")
async def download_logfile(
    instance_id: int, path: str, _user: str = Depends(auth.require_session)
):
    try:
        target = await asyncio.to_thread(
            instance_service.resolve_log_file, instance_id, path
        )
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(target, filename=target.name, media_type="text/plain")


@router.websocket("/{instance_id}/logs")
async def logs(websocket: WebSocket, instance_id: int):
    if not auth.session_username(websocket.cookies.get(auth.COOKIE_NAME)):
        await websocket.close(code=4401)
        return
    await websocket.accept()

    container = await asyncio.to_thread(
        docker_service.find_instance_container, instance_id
    )
    if container is None:
        await websocket.send_json({"type": "info", "line": "No container yet — start the instance."})
        await websocket.close()
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()

    def pump():
        # Blocking log stream on a worker thread; forwards lines to the loop.
        try:
            for chunk in container.logs(stream=True, follow=True, tail=200):
                if stop.is_set():
                    break
                line = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception as exc:  # container removed / daemon hiccup
            loop.call_soon_threadsafe(queue.put_nowait, f"[log stream ended: {exc}]")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = asyncio.create_task(asyncio.to_thread(pump))
    try:
        while True:
            line = await queue.get()
            if line is None:
                break
            await websocket.send_json({"type": "log", "line": line})
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        task.cancel()
