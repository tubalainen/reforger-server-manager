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
from services import change_log, mod_registry, stages
from services.template_service import ModListEntry
from services.workshop_service import normalize_asset_id

router = APIRouter(prefix="/api/mod-templates", tags=["mod-templates"])


class ModTemplateSpec(BaseModel):
    """What the editor posts: a name, an optional note, and the mod list."""

    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    # Same enriched entries as a server template's mods (#55/#60), plus a
    # comment per mod (#216), in the order they were added. Every mod is an
    # explicit addon — see the model docstring.
    mods: list[ModListEntry] = []

    @field_validator("name", "description")
    @classmethod
    def _trim(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("mods")
    @classmethod
    def _clean_mods(cls, mods: list[ModListEntry]) -> list[ModListEntry]:
        """Normalise ids and drop duplicates, keeping the first (i.e. the order
        the user put them in). A mod listed twice is one mod, not a load-order
        instruction the engine could act on."""
        seen: set[str] = set()
        out: list[ModListEntry] = []
        for mod in mods:
            mod_id = normalize_asset_id(mod.modId or "")
            if not mod_id:
                raise ValueError(f"Not a valid Workshop mod id: {mod.modId!r}")
            if mod_id in seen:
                continue
            seen.add(mod_id)
            out.append(mod.model_copy(update={"modId": mod_id, "explicit": True}))
        return out


class ModTemplateSave(ModTemplateSpec):
    """What the editor saves: the list plus an optional change comment (#223)."""

    comment: str = Field("", max_length=stages.NOTE_MAX)


def _stage_error(exc: stages.StageError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _mods(mt: ModTemplate) -> list[dict]:
    try:
        mods = json.loads(mt.mods_json or "[]")
    except (ValueError, TypeError):
        return []
    return [m for m in mods if isinstance(m, dict) and m.get("modId")]


def _out(mt: ModTemplate, session: Session, content=None) -> dict:
    content = content or mt
    return {
        "id": mt.id,
        "name": mt.name,
        "description": content.description,
        "mods": _mods(content),
        **stages.stage_fields(mt, stages.names_of(session, ModTemplate)),
        "created_at": mt.created_at.isoformat(),
        "updated_at": mt.updated_at.isoformat(),
    }


def _summary(mt: ModTemplate, names: dict[int, str], versions: dict[int, list]) -> dict:
    mods = _mods(mt)
    return {
        "id": mt.id,
        "name": mt.name,
        "description": mt.description,
        "mod_count": len(mods),
        "locked_count": sum(1 for m in mods if m.get("version")),
        **stages.stage_fields(mt, names),
        "versions": versions.get(mt.id, []),
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
            status_code=409, detail=f"A mod list named '{name}' already exists"
        )


@router.get("")
async def list_mod_templates(_user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        rows = session.exec(select(ModTemplate).order_by(ModTemplate.name)).all()
        names = {mt.id: mt.name for mt in rows}
        versions = stages.version_list(session, stages.MOD_LISTS)
        return [_summary(mt, names, versions) for mt in rows]


@router.post("", status_code=201)
async def create_mod_template(
    spec: ModTemplateSave, _user: str = Depends(auth.require_session)
):
    with Session(get_engine()) as session:
        _require_free_name(session, spec.name)
        mt = ModTemplate(
            name=spec.name, description=spec.description, mods_json=_mods_json(spec),
            stage=stages.TEST,  # new work always starts in Test (#223)
        )
        session.add(mt)
        session.flush()  # assign the id before the log references it
        change_log.record_mod_template_creation(session, mt)
        # Mods on a mod template count as "in use" for the Mods Overview (#166):
        # they show up there and a rescan will not prune them.
        mod_registry.register_mods(session, [m.model_dump() for m in spec.mods])
        session.commit()
        session.refresh(mt)
        return _out(mt, session)


@router.post("/{mod_template_id}/copy", status_code=201)
async def copy_mod_template(
    mod_template_id: int,
    body: stages.CopyFrom | None = None,
    _user: str = Depends(auth.require_session),
):
    """Duplicate a mod list into Test under a free name (#223, #232).

    The copy remembers the Prod list it stems from, so its release can default
    to "next version of" that one. `version` copies an older released version.
    """
    version = body.version if body else None
    with Session(get_engine()) as session:
        src = session.get(ModTemplate, mod_template_id)
        if not src:
            raise HTTPException(status_code=404, detail="Mod list not found")
        try:
            content = stages.content_of(session, stages.MOD_LISTS, src, version)
        except stages.StageError as exc:
            raise _stage_error(exc) from exc
        based_on_id, based_on_version = stages.based_on_for(src, version)
        mt = ModTemplate(
            name=stages.copy_name(session, stages.MOD_LISTS, src),
            description=content.description,
            mods_json=content.mods_json,
            stage=stages.TEST,
            based_on_id=based_on_id,
            based_on_version=based_on_version,
        )
        session.add(mt)
        session.flush()
        change_log.record_mod_template_creation(session, mt, stages.copied_from(src, version))
        session.commit()
        session.refresh(mt)
        return _out(mt, session)


@router.post("/{mod_template_id}/promote")
async def promote_mod_template(
    mod_template_id: int,
    body: stages.Promote,
    _user: str = Depends(auth.require_session),
):
    """Move a mod list one stage up: Test → Dev, or release Dev to Prod (#223)."""
    with Session(get_engine()) as session:
        mt = session.get(ModTemplate, mod_template_id)
        if not mt:
            raise HTTPException(status_code=404, detail="Mod list not found")
        try:
            result = stages.promote(
                session, stages.MOD_LISTS, mt, note=body.note, into_id=body.into_id
            )
        except stages.StageError as exc:
            raise _stage_error(exc) from exc
        session.commit()
        session.refresh(result)
        return _out(result, session)


@router.get("/{mod_template_id}")
async def get_mod_template(
    mod_template_id: int,
    version: int | None = None,
    _user: str = Depends(auth.require_session),
):
    """A mod list, or (`version`) one of its released versions, which is what
    the template wizard loads when an older Prod version is picked (#223)."""
    with Session(get_engine()) as session:
        mt = session.get(ModTemplate, mod_template_id)
        if not mt:
            raise HTTPException(status_code=404, detail="Mod list not found")
        try:
            content = stages.content_of(session, stages.MOD_LISTS, mt, version)
        except stages.StageError as exc:
            raise _stage_error(exc) from exc
        return _out(mt, session, content)


@router.put("/{mod_template_id}")
async def update_mod_template(
    mod_template_id: int,
    spec: ModTemplateSave,
    _user: str = Depends(auth.require_session),
):
    with Session(get_engine()) as session:
        mt = session.get(ModTemplate, mod_template_id)
        if not mt:
            raise HTTPException(status_code=404, detail="Mod list not found")
        try:
            stages.ensure_editable(mt)  # Prod is locked (#223)
        except stages.StageError as exc:
            raise _stage_error(exc) from exc
        _require_free_name(session, spec.name, exclude_id=mod_template_id)
        before = change_log.mod_template_snapshot(mt)  # capture before mutating
        mt.name = spec.name
        mt.description = spec.description
        mt.mods_json = _mods_json(spec)
        mt.updated_at = datetime.now(UTC)
        if change_log.record_mod_template_update(
            session, mt.id, before, change_log.mod_template_snapshot(mt), mt.updated_at
        ):
            change_log.record_mod_template_lines(
                session, mt.id, stages.note_items(spec.comment), mt.updated_at
            )
        mod_registry.register_mods(session, [m.model_dump() for m in spec.mods])
        session.add(mt)
        session.commit()
        session.refresh(mt)
        return _out(mt, session)


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
            raise HTTPException(status_code=404, detail="Mod list not found")
        change_log.delete_for_mod_template(session, mod_template_id)
        stages.delete_versions(session, stages.MOD_LISTS, mod_template_id)
        session.flush()
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
            raise HTTPException(status_code=404, detail="Mod list not found")
        return change_log.mod_template_entries(session, mod_template_id, q)
