"""Mods Overview: a persistent registry of every mod baked into a template (#131).

Read the overview, prune/persist entries, resolve the live dependency tree, and
add ticked mods straight into an existing template. All auth-gated.
"""
import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlmodel import Session, select

import auth
from models import ModRegistryEntry, Template, get_engine
from services import edit_locks, mod_registry

router = APIRouter(prefix="/api/mods", tags=["mods"])

_LOCKED_DETAIL = (
    "That template is being edited in another session. Wait for them to finish, "
    "or use 'Clear edit locks' on the Templates page if a lock is stuck."
)


def _client_id(request: Request) -> str:
    return request.headers.get("X-Client-Id", "").strip()


@router.get("")
async def list_mods(_user: str = Depends(auth.require_session)):
    """The overview: every registered mod with its live template/instance usage."""
    with Session(get_engine()) as session:
        return {"mods": mod_registry.overview(session)}


@router.get("/tree")
async def mods_tree(_user: str = Depends(auth.require_session)):
    """The dependency edges for the registry, scraped live from the Workshop.

    Best-effort and separate from the overview so the list renders instantly even
    when the Workshop is slow or unreachable; unresolved mods come back in
    `missing` and the UI shows them flat.
    """
    with Session(get_engine()) as session:
        mod_ids = [r.mod_id for r in session.exec(select(ModRegistryEntry)).all()]
    return await asyncio.to_thread(mod_registry.resolve_tree, mod_ids)


@router.post("/rescan")
async def rescan_mods(_user: str = Depends(auth.require_session)):
    """Prune mods no template uses any more (persisted ones excepted) and re-sync."""
    with Session(get_engine()) as session:
        return mod_registry.rescan(session)


@router.patch("/{mod_id}")
async def set_persist(
    mod_id: str, payload: dict = Body(...), _user: str = Depends(auth.require_session)
):
    """Set a mod's persist flag — a persisted mod is never pruned or deleted."""
    if "persist" not in payload or not isinstance(payload["persist"], bool):
        raise HTTPException(status_code=400, detail="Body must be {\"persist\": true|false}")
    with Session(get_engine()) as session:
        try:
            return mod_registry.set_persist(session, mod_id, payload["persist"])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mod not in the overview") from exc


@router.delete("/{mod_id}", status_code=204)
async def delete_mod(mod_id: str, _user: str = Depends(auth.require_session)):
    """Remove a mod from the overview. Refuses a persisted mod (clear it first)."""
    with Session(get_engine()) as session:
        try:
            mod_registry.delete(session, mod_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mod not in the overview") from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail="This mod is marked persist and can't be deleted — clear persist first.",
            ) from exc


def _add_to_template(template_id: int, mod_ids: list[str], client_id: str) -> dict:
    """Blocking worker for add-to-template: lock check + Workshop enrich + save."""
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        if edit_locks.held_by_other(template_id, client_id):
            raise HTTPException(status_code=423, detail=_LOCKED_DETAIL)
        result = mod_registry.add_mods_to_template(session, t, mod_ids)
        result["template"] = {"id": t.id, "name": t.name}
        return result


@router.post("/add-to-template")
async def add_to_template(
    request: Request,
    payload: dict = Body(...),
    _user: str = Depends(auth.require_session),
):
    """Add ticked mods to an existing template in one click (#131).

    Merges each mod as a top-level addon (its deps download at runtime, #97),
    re-renders the template and logs the change — the same effect as adding the
    mod in the wizard, without opening it. Respects the template edit lock.
    """
    template_id = payload.get("template_id")
    mod_ids = payload.get("mod_ids")
    if not isinstance(template_id, int) or not isinstance(mod_ids, list) or not mod_ids:
        raise HTTPException(
            status_code=400,
            detail="Body must be {template_id: int, mod_ids: [id, ...]}",
        )
    return await asyncio.to_thread(
        _add_to_template, template_id, [str(m) for m in mod_ids], _client_id(request)
    )
