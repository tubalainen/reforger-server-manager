"""Backup & restore of server templates and mod templates (#173), auth-gated.

Three endpoints, one conversation: download the shelf, ask what a file would
change, then apply the answers. Server instances are not part of any of it — see
services/backup for why.
"""
import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session

import auth
from models import get_engine
from services import backup, edit_locks

router = APIRouter(prefix="/api/backup", tags=["backup"])


def _client_id(request: Request) -> str:
    return request.headers.get("X-Client-Id", "").strip()


@router.get("/export")
async def export_backup(_user: str = Depends(auth.require_session)):
    """Download every server template and mod template as one JSON file.

    Served as an attachment so the browser saves it: the GUI links straight to
    this URL, the session cookie rides along, and nothing has to be assembled in
    the page. It carries the server, admin and RCON passwords in clear text —
    that is what makes it a restore rather than a sketch — so the button that
    points here says so.
    """
    with Session(get_engine()) as session:
        payload = backup.build(session)
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{backup.filename()}"'},
    )


@router.get("/summary")
async def backup_summary(_user: str = Depends(auth.require_session)):
    """How much a backup would hold right now, for the export card's line."""
    with Session(get_engine()) as session:
        return backup.counts(session)


@router.post("/preview")
async def preview_backup(
    payload: dict = Body(...), _user: str = Depends(auth.require_session)
):
    """What an uploaded backup would do, per template — changes nothing.

    Every item comes back as new / identical / differs, and a differing one
    carries the list of what differs in the change log's own words, so the
    keep-or-overwrite choice is made with the diff in view (#173).
    """
    with Session(get_engine()) as session:
        try:
            return backup.preview(session, payload)
        except backup.BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import")
async def import_backup(
    request: Request,
    payload: dict = Body(...),
    _user: str = Depends(auth.require_session),
):
    """Apply the decisions made on a preview: {backup: {...}, decisions: {...}}.

    All or nothing. Anything the caller did not decide on is skipped, so a
    partially-answered preview can never quietly restore the rest.
    """
    with Session(get_engine()) as session:
        try:
            return backup.apply(
                session,
                payload.get("backup"),
                payload.get("decisions") or {},
                is_locked=lambda tid: edit_locks.held_by_other(tid, _client_id(request)),
            )
        except backup.LockedError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except backup.BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
