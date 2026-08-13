"""Mod templates: reusable named mod lists (#166), auth-gated CRUD + change log.

A mod template is a shelf you keep a set of mods on and load into the Mods step
of any server template. It holds nothing else — no scenario, no settings — so
there is no config.json to render and nothing here can start, stop or reconfigure
a server. Loading one into a template happens in the wizard, against the mod list
the user is editing, and is saved by the wizard's own Save.
"""
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

import auth
from models import ModTemplate, get_engine
from services import change_log, mod_registry
from services.template_service import ModEntry
from services.workshop_service import normalize_asset_id

router = APIRouter(prefix="/api/mod-templates", tags=["mod-templates"])


class ModTemplateSpec(BaseModel):
    """What the editor posts: a name, an optional note, and the mod list."""

    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    # Same enriched entries as a server template's mods (#55/#60), in load
    # order (#164). Every mod is an explicit addon — see the model docstring.
    mods: list[ModEntry] = []

    @field_validator("name", "description")
    @classmethod
    def _trim(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("mods")
    @classmethod
    def _clean_mods(cls, mods: list[ModEntry]) -> list[ModEntry]:
        """Normalise ids and drop duplicates, keeping the first (i.e. the order
        the user put them in). A mod listed twice is one mod, not a load-order
        instruction the engine could act on."""
        seen: set[str] = set()
        out: list[ModEntry] = []
        for mod in mods:
            mod_id = normalize_asset_id(mod.modId or "")
            if not mod_id:
                raise ValueError(f"Not a valid Workshop mod id: {mod.modId!r}")
            if mod_id in seen:
                continue
            seen.add(mod_id)
            out.append(mod.model_copy(update={"modId": mod_id, "explicit": True}))
        return out


def _mods(mt: ModTemplate) -> list[dict]:
    try:
        mods = json.loads(mt.mods_json or "[]")
    except (ValueError, TypeError):
        return []
    return [m for m in mods if isinstance(m, dict) and m.get("modId")]


def _out(mt: ModTemplate) -> dict:
    return {
        "id": mt.id,
        "name": mt.name,
        "description": mt.description,
        "mods": _mods(mt),
        "created_at": mt.created_at.isoformat(),
        "updated_at": mt.updated_at.isoformat(),
    }


def _summary(mt: ModTemplate) -> dict:
    mods = _mods(mt)
    return {
        "id": mt.id,
        "name": mt.name,
        "description": mt.description,
        "mod_count": len(mods),
        "locked_count": sum(1 for m in mods if m.get("version")),
        "updated_at": mt.updated_at.isoformat(),
    }


def _mods_json(spec: ModTemplateSpec) -> str:
    return json.dumps([m.model_dump() for m in spec.mods])


def _require_free_name(session: Session, name: str, exclude_id: int | None = None) -> None:
    query = select(ModTemplate).where(ModTemplate.name == name)
    if exclude_id is not None:
        query = query.where(ModTemplate.id != exclude_id)
    if session.exec(query).first():
        raise HTTPException(
            status_code=409, detail=f"A mod template named '{name}' already exists"
        )


@router.get("")
async def list_mod_templates(_user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        rows = session.exec(select(ModTemplate).order_by(ModTemplate.name)).all()
        return [_summary(mt) for mt in rows]


@router.post("", status_code=201)
async def create_mod_template(
    spec: ModTemplateSpec, _user: str = Depends(auth.require_session)
):
    with Session(get_engine()) as session:
        _require_free_name(session, spec.name)
        mt = ModTemplate(
            name=spec.name, description=spec.description, mods_json=_mods_json(spec)
        )
        session.add(mt)
        session.flush()  # assign the id before the log references it
        change_log.record_mod_template_creation(session, mt)
        # Mods on a mod template count as "in use" for the Mods Overview (#166):
        # they show up there and a rescan will not prune them.
        mod_registry.register_mods(session, [m.model_dump() for m in spec.mods])
        session.commit()
        session.refresh(mt)
        return _out(mt)


@router.get("/{mod_template_id}")
async def get_mod_template(
    mod_template_id: int, _user: str = Depends(auth.require_session)
):
    with Session(get_engine()) as session:
        mt = session.get(ModTemplate, mod_template_id)
        if not mt:
            raise HTTPException(status_code=404, detail="Mod template not found")
        return _out(mt)


@router.put("/{mod_template_id}")
async def update_mod_template(
    mod_template_id: int,
    spec: ModTemplateSpec,
    _user: str = Depends(auth.require_session),
):
    with Session(get_engine()) as session:
        mt = session.get(ModTemplate, mod_template_id)
        if not mt:
            raise HTTPException(status_code=404, detail="Mod template not found")
        _require_free_name(session, spec.name, exclude_id=mod_template_id)
        before = change_log.mod_template_snapshot(mt)  # capture before mutating
        mt.name = spec.name
        mt.description = spec.description
        mt.mods_json = _mods_json(spec)
        mt.updated_at = datetime.now(UTC)
        change_log.record_mod_template_update(
            session, mt.id, before, change_log.mod_template_snapshot(mt), mt.updated_at
        )
        mod_registry.register_mods(session, [m.model_dump() for m in spec.mods])
        session.add(mt)
        session.commit()
        session.refresh(mt)
        return _out(mt)


@router.delete("/{mod_template_id}", status_code=204)
async def delete_mod_template(
    mod_template_id: int, _user: str = Depends(auth.require_session)
):
    """Delete a mod template. Nothing depends on it: a server template that was
    built from one keeps its own copy of the mods, so this can never change what
    a running server loads."""
    with Session(get_engine()) as session:
        mt = session.get(ModTemplate, mod_template_id)
        if not mt:
            raise HTTPException(status_code=404, detail="Mod template not found")
        change_log.delete_for_mod_template(session, mod_template_id)
        session.delete(mt)
        session.commit()


@router.get("/{mod_template_id}/changelog")
async def mod_template_changelog(
    mod_template_id: int, q: str | None = None, _user: str = Depends(auth.require_session)
):
    """The mod template's change log, newest event first, optionally filtered.

    Read-only — the log is append-only and has no edit/delete endpoint (#166).
    """
    with Session(get_engine()) as session:
        if not session.get(ModTemplate, mod_template_id):
            raise HTTPException(status_code=404, detail="Mod template not found")
        return change_log.mod_template_entries(session, mod_template_id, q)
