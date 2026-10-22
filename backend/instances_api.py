"""Server instance CRUD + lifecycle + live log streaming (auth-gated)."""
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import auth
from services import ai_client, docker_service, instance_backup, instance_service, log_explain
from services.instance_service import InstanceError

logger = logging.getLogger("manager.instances_api")

router = APIRouter(prefix="/api/instances", tags=["instances"])


class CreateInstance(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    template_id: int
    # A Prod template's released version to pin (#223); omit for "latest"
    template_version: int | None = None
    branch: str = "stable"
    # Optional explicit host ports; omit to auto-lease from the .env ranges
    game_port: int | None = Field(default=None, ge=1, le=65535)
    a2s_port: int | None = Field(default=None, ge=1, le=65535)
    rcon_port: int | None = Field(default=None, ge=1, le=65535)


class RestartSettings(BaseModel):
    auto_restart: bool | None = None
    auto_start: bool | None = None


class RestartSchedule(BaseModel):
    # Daily restart times as 24-hour "HH:MM"; empty list clears the schedule.
    times: list[str] = Field(default_factory=list)


class ModUpdates(BaseModel):
    # Update the template's mods on the scheduled restart (#246)
    enabled: bool


class StartOptions(BaseModel):
    # Stored data to delete before starting: any of mods | saves | logs
    clear: list[Literal["mods", "saves", "logs"]] = Field(default_factory=list)
    # Archive the saved game before "saves" deletes it
    backup_first: bool = False


class ExplainLine(BaseModel):
    # The error line, as the Console shows it; it must be in this server's log
    line: str = Field(min_length=1, max_length=4000)


class ExplainFile(BaseModel):
    # A path as the log-file list gives it, relative to the server's profile
    path: str = Field(min_length=1, max_length=1000)


class Redownload(BaseModel):
    # "template": the template's mods and their dependencies; "all": every
    # downloaded mod, leftovers of earlier templates too (#246)
    scope: Literal["template", "all"] = "template"


class UpdatePorts(BaseModel):
    game_port: int | None = Field(default=None, ge=1, le=65535)
    a2s_port: int | None = Field(default=None, ge=1, le=65535)
    rcon_port: int | None = Field(default=None, ge=1, le=65535)


class SetTemplate(BaseModel):
    template_id: int
    # A Prod template's released version to pin (#223); None = latest
    template_version: int | None = None
    # Archive the saved game data before repointing the instance (#179). The GUI
    # ticks this by default when there is anything to save, because the save the
    # old scenario built is exactly what a template swap orphans.
    backup_first: bool = False


class CreateBackup(BaseModel):
    label: str = Field(default="", max_length=120)


class RestoreBackup(BaseModel):
    """Restore, plus the two things that make it usable across templates (#181).

    ``template_id`` repoints the instance in the same step, so a world from an
    older setup lands on a server configured to read it. ``backup_first`` keeps a
    copy of what the restore is about to delete.
    """

    template_id: int | None = None
    backup_first: bool = False
    # Step over the rule that a world only loads under the scenario and hive id
    # that wrote it (#184). Refused restores come back as 409 with the reason;
    # this is the one documented way past it, and it forces the safety copy on.
    force: bool = False


class EditInstance(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    branch: str | None = None


class ClearData(BaseModel):
    # Any of instance_service.DATA_TARGETS: mods | saves | logs (#79)
    targets: list[str] = Field(min_length=1)


@router.get("")
async def list_instances(_user: str = Depends(auth.require_session)):
    return await asyncio.to_thread(instance_service.list_views)


@router.get("/summary")
async def summary(_user: str = Depends(auth.require_session)):
    return await asyncio.to_thread(instance_service.instances_summary)


@router.post("", status_code=201)
async def create_instance(body: CreateInstance, _user: str = Depends(auth.require_session)):
    try:
        inst, parked = await asyncio.to_thread(
            instance_service.create_instance,
            body.name, body.template_id, body.branch,
            body.game_port, body.a2s_port, body.rcon_port, body.template_version,
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    view = await asyncio.to_thread(_view, inst.id)
    # Say it out loud when this id came with someone else's data attached (#187):
    # the files are safe, but they are no longer where their owner would look.
    return {**view, "orphaned_data": parked}


def _view(instance_id: int) -> dict:
    for v in instance_service.list_views():
        if v["id"] == instance_id:
            return v
    raise HTTPException(status_code=404, detail="Server not found")


@router.get("/{instance_id}")
async def get_instance(instance_id: int, _user: str = Depends(auth.require_session)):
    return await asyncio.to_thread(_view, instance_id)


@router.get("/{instance_id}/stats")
async def stats(instance_id: int, _user: str = Depends(auth.require_session)):
    try:
        return await asyncio.to_thread(instance_service.instance_stats, instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _action(fn, instance_id: int):
    try:
        fn(instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{instance_id}/start")
async def start(
    instance_id: int, body: StartOptions | None = None,
    _user: str = Depends(auth.require_session),
):
    """Start a server; optionally clear stored data first.

    The GUI offers the clearing after the server was switched to another
    template, whose mods, saves and logs are not the ones on disk.
    """
    backup = None
    if body and body.clear:
        try:
            # Before the clear: a backup of a world that is already gone is no backup.
            if body.backup_first and "saves" in body.clear:
                backup = await asyncio.to_thread(
                    instance_backup.create_backup, instance_id, "Before a clean start",
                    instance_backup.SOURCE_TEMPLATE_SWITCH,
                )
            await asyncio.to_thread(instance_service.clear_instance_data, instance_id, body.clear)
        except InstanceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    await asyncio.to_thread(_action, instance_service.start_instance, instance_id)
    view = await asyncio.to_thread(_view, instance_id)
    return {**view, "backup": backup} if backup else view


@router.post("/{instance_id}/stop")
async def stop(instance_id: int, _user: str = Depends(auth.require_session)):
    await asyncio.to_thread(_action, instance_service.stop_instance, instance_id)
    return await asyncio.to_thread(_view, instance_id)


@router.post("/{instance_id}/restart")
async def restart(instance_id: int, _user: str = Depends(auth.require_session)):
    await asyncio.to_thread(_action, instance_service.restart_instance, instance_id)
    return await asyncio.to_thread(_view, instance_id)


@router.put("/{instance_id}")
async def edit_instance(
    instance_id: int, body: EditInstance, _user: str = Depends(auth.require_session)
):
    try:
        await asyncio.to_thread(
            instance_service.edit_instance, instance_id, body.name, body.branch
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await asyncio.to_thread(_view, instance_id)


@router.put("/{instance_id}/ports")
async def update_ports(
    instance_id: int, body: UpdatePorts, _user: str = Depends(auth.require_session)
):
    try:
        await asyncio.to_thread(
            instance_service.update_ports,
            instance_id, body.game_port, body.a2s_port, body.rcon_port,
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await asyncio.to_thread(_view, instance_id)


@router.put("/{instance_id}/template")
async def set_template(
    instance_id: int, body: SetTemplate, _user: str = Depends(auth.require_session)
):
    backup = None
    try:
        # Before the repoint, not after: once the instance points at the new
        # template, the backup would be labelled with a template that never
        # wrote a byte of it.
        if body.backup_first:
            backup = await asyncio.to_thread(
                instance_backup.create_backup,
                instance_id, "Before template change",
                instance_backup.SOURCE_TEMPLATE_SWITCH,
            )
        await asyncio.to_thread(
            instance_service.set_instance_template,
            instance_id, body.template_id, body.template_version,
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    view = await asyncio.to_thread(_view, instance_id)
    return {**view, "backup": backup}


@router.put("/{instance_id}/restart-settings")
async def restart_settings(
    instance_id: int, body: RestartSettings, _user: str = Depends(auth.require_session)
):
    try:
        await asyncio.to_thread(
            instance_service.set_restart_settings,
            instance_id, body.auto_restart, body.auto_start,
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await asyncio.to_thread(_view, instance_id)


@router.put("/{instance_id}/schedule")
async def restart_schedule(
    instance_id: int, body: RestartSchedule, _user: str = Depends(auth.require_session)
):
    try:
        await asyncio.to_thread(
            instance_service.set_restart_schedule, instance_id, body.times
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await asyncio.to_thread(_view, instance_id)


@router.post("/{instance_id}/logs/explain")
async def explain_log_line(
    instance_id: int, body: ExplainLine, _user: str = Depends(auth.require_session)
):
    """Ask the configured AI what an error line means (#254).

    The prompt is built from this server's own log around that line; the
    browser's text only picks the line. Returns {reply, model, sent}, where
    `sent` is the excerpt exactly as it left the manager.
    """
    status = await asyncio.to_thread(ai_client.status)
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"] or "No AI service is working.")
    try:
        ctx = await asyncio.to_thread(instance_service.log_context, instance_id)
        lines, at = log_explain.excerpt(ctx["lines"], body.line)
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except log_explain.LineNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="That line is no longer in the server's log. Pick it again from a fresh view.",
        ) from exc
    prompt = log_explain.build_prompt(lines, at, ctx["mods"], ctx["scenario"])
    try:
        result = await asyncio.to_thread(log_explain.ask, prompt)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {**result, "sent": prompt}


@router.put("/{instance_id}/mod-updates")
async def mod_updates(
    instance_id: int, body: ModUpdates, _user: str = Depends(auth.require_session)
):
    try:
        await asyncio.to_thread(instance_service.set_mod_auto_update, instance_id, body.enabled)
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await asyncio.to_thread(_view, instance_id)


@router.get("/{instance_id}/load-order")
async def load_order(instance_id: int, _user: str = Depends(auth.require_session)):
    """The order the engine mounted the mods in at the last start, from the
    server's console.log, against the config.json it was given (#257)."""
    try:
        return await asyncio.to_thread(instance_service.engine_load_order, instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{instance_id}/mods")
async def mods(instance_id: int, _user: str = Depends(auth.require_session)):
    """The template's mods: the version on disk against the Workshop's (#246)."""
    try:
        return await asyncio.to_thread(instance_service.mod_status, instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{instance_id}/mods/update")
async def force_update_mods(instance_id: int, _user: str = Depends(auth.require_session)):
    """Restart (or start) with the template's mods at their newest release."""
    await asyncio.to_thread(_action, instance_service.force_update_mods, instance_id)
    return await asyncio.to_thread(_view, instance_id)


@router.post("/{instance_id}/mods/redownload")
async def redownload_mods(
    instance_id: int, body: Redownload | None = None,
    _user: str = Depends(auth.require_session),
):
    """Delete mods and start, so they download and bake afresh."""
    scope = body.scope if body else "template"
    try:
        result = await asyncio.to_thread(instance_service.redownload_mods, instance_id, scope)
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    view = await asyncio.to_thread(_view, instance_id)
    return {**view, **result}


@router.delete("/{instance_id}", status_code=204)
async def delete(
    instance_id: int,
    purge_data: bool = False,
    _user: str = Depends(auth.require_session),
):
    try:
        await asyncio.to_thread(
            instance_service.delete_instance, instance_id, purge_data
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{instance_id}/data")
async def instance_data(instance_id: int, _user: str = Depends(auth.require_session)):
    """What this instance has stored on disk: baked mods, saves, logs (#79)."""
    try:
        return await asyncio.to_thread(instance_service.instance_data, instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{instance_id}/data/clear")
async def clear_instance_data(
    instance_id: int, body: ClearData, _user: str = Depends(auth.require_session)
):
    """Wipe the selected stored data so the next start rebuilds it (#79)."""
    try:
        return await asyncio.to_thread(
            instance_service.clear_instance_data, instance_id, body.targets
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Saved game backups (#179)
# --------------------------------------------------------------------------- #

@router.get("/{instance_id}/backups")
async def list_backups(instance_id: int, _user: str = Depends(auth.require_session)):
    """This instance's backup shelf, plus what a new backup would hold."""
    try:
        return await asyncio.to_thread(instance_backup.overview, instance_id)
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{instance_id}/backups", status_code=201)
async def create_backup(
    instance_id: int, body: CreateBackup, _user: str = Depends(auth.require_session)
):
    """Archive the saved game data as it stands right now."""
    try:
        return await asyncio.to_thread(
            instance_backup.create_backup, instance_id, body.label
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{instance_id}/backups/upload", status_code=201)
async def upload_backup(
    instance_id: int,
    file: UploadFile = File(...),
    label: str = Form(default=""),
    _user: str = Depends(auth.require_session),
):
    """Put a previously downloaded backup file back on this instance's shelf.

    Streamed to a temp file and validated as a whole before it is adopted: an
    archive is only ever unpacked as root, so one that holds a symlink, an
    absolute path or a `..` is refused here rather than sanitised later.
    """
    tmp = Path(tempfile.mkdtemp(prefix="rsm-upload-")) / "upload.tar.gz"
    try:
        written = 0
        with tmp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > instance_backup.MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="That file is too large.")
                out.write(chunk)
        try:
            return await asyncio.to_thread(
                instance_backup.store_upload, instance_id, label or file.filename, tmp
            )
        except InstanceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()


@router.get("/{instance_id}/backups/{backup_id}/download")
async def download_backup(
    instance_id: int, backup_id: str, _user: str = Depends(auth.require_session)
):
    try:
        archive = await asyncio.to_thread(
            instance_backup.archive_path, instance_id, backup_id
        )
        name = await asyncio.to_thread(
            instance_backup.download_name, instance_id, backup_id
        )
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(archive, filename=name, media_type="application/gzip")


@router.post("/{instance_id}/backups/{backup_id}/restore")
async def restore_backup(
    instance_id: int,
    backup_id: str,
    body: RestoreBackup = RestoreBackup(),
    _user: str = Depends(auth.require_session),
):
    """Replace the current saved game data with this backup's (stopped only)."""
    try:
        return await asyncio.to_thread(
            instance_backup.restore_backup,
            instance_id, backup_id, body.template_id, body.backup_first, body.force,
        )
    except InstanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{instance_id}/backups/{backup_id}", status_code=204)
async def delete_backup(
    instance_id: int, backup_id: str, _user: str = Depends(auth.require_session)
):
    try:
        await asyncio.to_thread(instance_backup.delete_backup, instance_id, backup_id)
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(target, filename=target.name, media_type="text/plain")


@router.post("/{instance_id}/logfiles/explain")
async def explain_logfile(
    instance_id: int, body: ExplainFile, _user: str = Depends(auth.require_session)
):
    """Ask the configured AI what went wrong in one of the server's log files (#254).

    The file is read here, through the same guard as its download, and the last
    error in it (or its last lines) is what is asked about.
    """
    status = await asyncio.to_thread(ai_client.status)
    if not status["available"]:
        raise HTTPException(status_code=503, detail=status["reason"] or "No AI service is working.")
    try:
        ctx = await asyncio.to_thread(
            instance_service.log_file_context, instance_id, body.path, log_explain.FILE_READ_BYTES,
        )
    except InstanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not ctx["lines"]:
        raise HTTPException(status_code=409, detail="That log file is empty.")
    # Pruned first: each distinct error once, with its count (a real error.log
    # went from ~100k tokens to ~2k). A file without errors: its last lines.
    d = log_explain.digest(ctx["lines"])
    if d["kinds"]:
        prompt = log_explain.build_digest_prompt(d, ctx["mods"], ctx["scenario"], body.path)
        answer_tokens = log_explain.DIGEST_ANSWER_TOKENS
    else:
        lines, at = log_explain.file_excerpt(ctx["lines"])
        prompt = log_explain.build_prompt(lines, at, ctx["mods"], ctx["scenario"], source=body.path)
        answer_tokens = log_explain.ANSWER_TOKENS
    try:
        result = await asyncio.to_thread(log_explain.ask, prompt, answer_tokens)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {**result, "sent": prompt,
            "summary": {"errors": d["errors"], "kinds": len(d["kinds"]) + d["omitted"],
                        "warnings": d["warnings"], "lines": len(ctx["lines"])}}


@router.websocket("/{instance_id}/logs")
async def logs(websocket: WebSocket, instance_id: int):
    if not auth.request_origin_ok(websocket):
        await websocket.close(code=4403)  # cross-site WebSocket hijacking (R12)
        return
    if not auth.session_username(websocket.cookies.get(auth.cookie_name())):
        await websocket.close(code=4401)
        return
    await websocket.accept()

    container = await asyncio.to_thread(
        docker_service.find_instance_container, instance_id
    )
    if container is None:
        await websocket.send_json({"type": "info", "line": "No container yet — start the server."})
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
