"""Template CRUD + config.json export (auth-gated)."""
import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, func, select

import auth
import categories_api
from mod_templates_api import ModTemplateSpec
from models import Instance, ModTemplate, Template, TemplateCategory, get_engine
from services import (
    change_log,
    config_validator,
    edit_locks,
    instance_service,
    mod_registry,
    stages,
    template_service,
)
from services.template_service import TemplateSpec

router = APIRouter(prefix="/api/templates", tags=["templates"])

_LOCKED_DETAIL = (
    "This template is being edited in another session. Wait for them to finish, "
    "or use 'Clear edit locks' on the Templates page if a lock is stuck."
)


class TemplateSave(TemplateSpec):
    """What the editor saves: the spec plus an optional change comment and its
    category (#223). A save that leaves category_id out keeps the category."""

    comment: str = Field("", max_length=stages.NOTE_MAX)
    category_id: int | None = None


class CategoryChoice(BaseModel):
    category_id: int | None = None


def stage_error(exc: stages.StageError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))

def _client_id(request: Request) -> str:
    """The per-tab id the frontend sends on every request; '' for bare curl."""
    return request.headers.get("X-Client-Id", "").strip()


def _drop_mission_header(extras: dict) -> dict:
    """Take the mission header out of a stored extras patch (#162).

    Anyone who set a mission header before the wizard modelled it has it sitting
    in extras_json, and to_config applies extras *last* — so that stale copy
    would win over whatever the new UI writes, for ever: you'd edit a value,
    save, and nothing would change. The rendered config_json already carries the
    same values (extras were merged in when it was written), so spec_from_config
    has already read them into mission_header; all that's left is to stop the
    patch re-applying them. Emptied parents are pruned so the overlay doesn't
    keep hollow objects around.
    """
    game = extras.get("game")
    if not isinstance(game, dict):
        return extras
    props = game.get("gameProperties")
    if not isinstance(props, dict) or "missionHeader" not in props:
        return extras
    props = {k: v for k, v in props.items() if k != "missionHeader"}
    game = {k: v for k, v in game.items() if k != "gameProperties"}
    if props:
        game["gameProperties"] = props
    out = {k: v for k, v in extras.items() if k != "game"}
    if game:
        out["game"] = game
    return out


def _out(t: Template, session: Session) -> dict:
    spec = template_service.spec_from_config(t.config_json)
    spec["scenario_name"] = t.scenario_name
    spec["scenario_player_count"] = t.scenario_player_count
    spec["launch"] = json.loads(t.launch_params_json or "{}")
    # The enriched mod list (dependency metadata) is the editing source of truth
    # when present; older templates fall back to the flat mods[] from config (#55).
    if t.mods_json:
        try:
            spec["mods"] = json.loads(t.mods_json)
        except (ValueError, TypeError):
            pass
    # The hand-edited overlay (#29). Stored rather than re-derived from
    # config_json so the user's intent survives even if to_config's defaults move.
    try:
        spec["extras"] = _drop_mission_header(json.loads(t.extras_json or "{}"))
    except (ValueError, TypeError):
        spec["extras"] = {}
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "spec": spec,
        # Which custom keys this template carries, so the wizard can badge them on
        # load without re-deriving "what counts as custom" in JS and drifting from
        # the editor's warnings (#29).
        "extras_paths": config_validator.unknown_paths(json.loads(t.config_json)),
        **stages.stage_fields(t, stages.names_of(session, Template)),
        "category_id": t.category_id,
        "category": categories_api.name_of(session, t.category_id),
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _mods_json(spec: TemplateSpec) -> str:
    """Serialize the enriched mod list (with dependency metadata) for storage."""
    return json.dumps([m.model_dump() for m in spec.mods])


def _same_mod_ids(wizard_mods, config_mods) -> bool:
    """Do these two mod lists name the same mods at the same versions?

    config.json holds a flat mods[] (modId/name/version); the wizard's list also
    carries the dependency graph (#55). When a raw edit didn't touch the mods,
    taking the flat list back would silently discard that graph — so compare, and
    keep the enriched list when nothing about the mods actually changed (#29).
    """
    def key(mods):
        if not isinstance(mods, list):
            return None
        return [(m.get("modId"), m.get("version")) for m in mods if isinstance(m, dict)]

    wizard, config = key(wizard_mods), key(config_mods)
    return wizard is not None and wizard == config


def _custom_keys_only(patch: dict, prefix: str = "") -> dict:
    """Drop "delete a key the wizard manages" entries from an extras patch (#29).

    diff_patch marks every baseline key missing from the user's config as a null
    (RFC 7386 for "delete"). But to_config always renders the keys it models, so
    storing such a null would permanently subtract a managed key: the matching GUI
    control would silently stop doing anything, and the custom-key badge wouldn't
    even show it (unknown_paths can't see a key that isn't there).

    So extras keeps only what the wizard doesn't manage. Deleting a managed key in
    the editor reverts to the wizard's value on Apply — visible, and consistent
    with the wizard owning those keys. Deleting a *custom* key still works: it
    simply diffs away to nothing and drops out of extras.
    """
    known = config_validator.known_paths()
    out: dict = {}
    for key, value in patch.items():
        path = f"{prefix}{key}"
        if value is None and (path in known or config_validator.in_free_form_subtree(path)):
            continue
        if isinstance(value, dict):
            sub = _custom_keys_only(value, f"{path}.")
            if sub:
                out[key] = sub
            continue
        out[key] = value
    return out


@router.get("")
async def list_templates(request: Request, _user: str = Depends(auth.require_session)):
    cid = _client_id(request)
    with Session(get_engine()) as session:
        rows = session.exec(select(Template).order_by(Template.name)).all()
        names = {t.id: t.name for t in rows}
        servers = dict(session.exec(
            select(Instance.template_id, func.count()).group_by(Instance.template_id)
        ).all())
        # every Prod template's released versions, for the version pickers (#223)
        versions = stages.version_list(session, stages.TEMPLATES)
        categories = stages.names_of(session, TemplateCategory)
        return [
            {"id": t.id, "name": t.name, "description": t.description,
             "updated_at": t.updated_at.isoformat(),
             **stages.stage_fields(t, names),
             # how many servers run it: the release dialog says they move along
             "server_count": servers.get(t.id, 0),
             "versions": versions.get(t.id, []),
             "category_id": t.category_id,
             "category": categories.get(t.category_id),
             # someone else is editing this right now (#102) — the list polls,
             # so the badge and the disabled Edit button stay current
             "locked": edit_locks.held_by_other(t.id, cid),
             # persistence/hiveId so the instance template-swap UI can warn when
             # the save target changes (issue #31)
             **template_service.persistence_summary(t.config_json)}
            for t in rows
        ]


@router.post("/locks/clear")
async def clear_edit_locks(_user: str = Depends(auth.require_session)):
    """Force-release every edit lock (#102) — the GUI's reset button.

    Meant for locks whose tab is gone (crash, closed laptop); a still-open
    editor simply re-acquires on its next heartbeat.
    """
    return {"cleared": edit_locks.clear_all()}


@router.post("/{template_id}/lock")
async def lock_template(
    template_id: int, request: Request, _user: str = Depends(auth.require_session)
):
    """Acquire — or, for the current holder, renew — a template's edit lock (#102).

    The wizard calls this on open and then every 30s as a heartbeat; locks
    expire on their own when the heartbeats stop (see services/edit_locks).
    """
    cid = _client_id(request)
    if not edit_locks.valid_client_id(cid):
        raise HTTPException(status_code=400, detail="Missing or invalid X-Client-Id header")
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        # Prod is never edited, so it is never "being edited" either (#223).
        try:
            stages.ensure_editable(t)
        except stages.StageError as exc:
            raise stage_error(exc) from exc
    if not edit_locks.acquire(template_id, cid):
        raise HTTPException(status_code=423, detail=_LOCKED_DETAIL)
    return {"ok": True}


@router.delete("/{template_id}/lock", status_code=204)
async def unlock_template(
    template_id: int, request: Request, _user: str = Depends(auth.require_session)
):
    """Release the caller's lock. Deliberately lenient — this is fired best-effort
    from a closing tab, so a lock that's already gone is not an error."""
    edit_locks.release(template_id, _client_id(request))


@router.post("", status_code=201)
async def create_template(spec: TemplateSave, _user: str = Depends(auth.require_session)):
    config_json = template_service.render_config_json(spec)
    with Session(get_engine()) as session:
        if session.exec(select(Template).where(Template.name == spec.name)).first():
            raise HTTPException(status_code=409, detail=f"A template named '{spec.name}' already exists")
        categories_api.require(session, spec.category_id)
        t = Template(
            name=spec.name, description=spec.description, config_json=config_json,
            scenario_name=spec.scenario_name,
            scenario_player_count=spec.scenario_player_count,
            launch_params_json=spec.launch.model_dump_json(),
            mods_json=_mods_json(spec),
            extras_json=json.dumps(spec.extras),
            stage=stages.TEST,  # new work always starts in Test (#223)
            category_id=spec.category_id,
        )
        session.add(t)
        session.flush()  # assign the id before the log references it
        change_log.record_creation(session, t)  # start the change log (#112)
        # Every mod this template bakes in joins the Mods Overview registry (#131).
        mod_registry.register_mods(session, [m.model_dump() for m in spec.mods])
        session.commit()
        session.refresh(t)
        return _out(t, session)


def _unique_copy_name(session: Session, source_name: str) -> str:
    """A free "Copy of …" name for a duplicated template (#129).

    Names are unique, so a duplicate can't reuse the original's. Try "Copy of X"
    first, then "Copy of X (2)", "(3)", … until one is free. Truncated to the
    column's 100-char limit so a long source name still yields a valid name.
    """
    taken = set(session.exec(select(Template.name)).all())
    base = f"Copy of {source_name}"[:100]
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = f"Copy of {source_name}"[: 100 - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1


@router.post("/{template_id}/copy", status_code=201)
async def copy_template(
    template_id: int,
    body: stages.CopyFrom | None = None,
    _user: str = Depends(auth.require_session),
):
    """Duplicate a template into a new one under a fresh "Copy of …" name (#129).

    Copies the stored config, scenario, launch params, enriched mod list and the
    custom-key overlay verbatim, so the copy is a faithful, independently editable
    twin. It gets its own change log, seeded like any freshly created template.

    A copy always lands in Test, whatever stage the original is in, and remembers
    the Prod template it stems from so its release can default to "next version
    of" that one (#223). `version` copies an older released version instead.
    """
    version = body.version if body else None
    with Session(get_engine()) as session:
        src = session.get(Template, template_id)
        if not src:
            raise HTTPException(status_code=404, detail="Template not found")
        try:
            content = stages.content_of(session, stages.TEMPLATES, src, version)
        except stages.StageError as exc:
            raise stage_error(exc) from exc
        based_on_id, based_on_version = stages.based_on_for(src, version)
        t = Template(
            name=_unique_copy_name(session, src.name),
            description=content.description,
            config_json=content.config_json,
            scenario_name=content.scenario_name,
            scenario_player_count=content.scenario_player_count,
            launch_params_json=content.launch_params_json,
            mods_json=content.mods_json,
            extras_json=content.extras_json,
            stage=stages.TEST,
            based_on_id=based_on_id,
            based_on_version=based_on_version,
            category_id=src.category_id,
        )
        session.add(t)
        session.flush()  # assign the id before the log references it
        # the copy starts its own log (#112), saying where it came from
        change_log.record_creation(session, t, stages.copied_from(src, version))
        session.commit()
        session.refresh(t)
        return _out(t, session)


def _mods_without_scenarios(mods: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split a template's mods into (keep, left_out) for a mod list (#201).

    A mod list holds no scenario, so the mod backing the template's scenario —
    and any other mod that publishes scenarios of its own — is left out, along
    with the dependencies only those mods needed. A dependency that a kept mod
    still requires stays: dropping it would leave that mod broken. The kept mods
    keep the template's load order (#164).
    """
    by_id = {m["modId"]: m for m in mods}

    def is_scenario(m: dict) -> bool:
        return bool(m.get("from_scenario") or m.get("provides_scenarios"))

    needed: set[str] = set()
    stack = [m["modId"] for m in mods if m.get("explicit", True) and not is_scenario(m)]
    while stack:
        mod_id = stack.pop()
        if mod_id in needed:
            continue
        needed.add(mod_id)
        for dep in (by_id.get(mod_id) or {}).get("dependencies") or []:
            if dep in by_id and dep not in needed:
                stack.append(dep)
    keep = [m for m in mods if m["modId"] in needed]
    left_out = [m for m in mods if m["modId"] not in needed]
    return keep, left_out


def _unique_mod_list_name(session: Session, source_name: str) -> str:
    """A free "<template> mods" name, then "… (2)", "(3)", … (names are unique)."""
    taken = set(session.exec(select(ModTemplate.name)).all())
    n = 1
    while True:
        suffix = " mods" if n == 1 else f" mods ({n})"
        candidate = source_name[: 100 - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1


@router.post("/{template_id}/mod-list", status_code=201)
async def make_mod_list(template_id: int, _user: str = Depends(auth.require_session)):
    """Save a template's mods as a new mod list (#201), minus its scenario mods.

    The mod list gets a free "<template> mods" name and a description naming the
    source and what was left out; the frontend opens it in the editor straight
    away so it can be renamed or tweaked. The template itself is not touched.
    """
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        try:
            mods = json.loads(t.mods_json or "[]")
        except (ValueError, TypeError):
            mods = []
        mods = [m for m in mods if isinstance(m, dict) and m.get("modId")]
        keep, left_out = _mods_without_scenarios(mods)
        if not keep:
            raise HTTPException(
                status_code=400,
                detail="This template has no mods besides its scenario's, "
                "so there is nothing to put in a mod list",
            )
        description = f"Made from the server template “{t.name}”."
        if left_out:
            names = ", ".join(m.get("name") or m["modId"] for m in left_out)
            description += f" Scenario mods left out: {names}."
        spec = ModTemplateSpec(
            name=_unique_mod_list_name(session, t.name),
            description=description,
            mods=keep,
        )
        mt = ModTemplate(
            name=spec.name,
            description=spec.description,
            mods_json=json.dumps([m.model_dump() for m in spec.mods]),
        )
        session.add(mt)
        session.flush()  # assign the id before the log references it
        change_log.record_mod_template_creation(session, mt)
        mod_registry.register_mods(session, [m.model_dump() for m in spec.mods])
        session.commit()
        session.refresh(mt)
        return {
            "id": mt.id,
            "name": mt.name,
            "mod_count": len(spec.mods),
            "left_out": [
                {"modId": m["modId"], "name": m.get("name")} for m in left_out
            ],
        }


@router.get("/{template_id}")
async def get_template(template_id: int, _user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        return _out(t, session)


@router.put("/{template_id}")
async def update_template(
    template_id: int,
    spec: TemplateSave,
    request: Request,
    _user: str = Depends(auth.require_session),
):
    # Enforced server-side, not just by disabling the Save button: without this
    # two editors' last write silently wins (#102).
    if edit_locks.held_by_other(template_id, _client_id(request)):
        raise HTTPException(status_code=423, detail=_LOCKED_DETAIL)
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        try:
            stages.ensure_editable(t)  # Prod is locked (#223)
        except stages.StageError as exc:
            raise stage_error(exc) from exc
        clash = session.exec(
            select(Template).where(Template.name == spec.name, Template.id != template_id)
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail=f"A template named '{spec.name}' already exists")
        old_category = t.category_id
        if "category_id" in spec.model_fields_set:
            categories_api.require(session, spec.category_id)
            t.category_id = spec.category_id
        before = change_log.snapshot(t)  # capture before mutating (#112)
        t.name = spec.name
        t.description = spec.description
        t.config_json = template_service.render_config_json(spec)
        t.scenario_name = spec.scenario_name
        t.scenario_player_count = spec.scenario_player_count
        t.launch_params_json = spec.launch.model_dump_json()
        t.mods_json = _mods_json(spec)
        t.extras_json = json.dumps(spec.extras)
        t.updated_at = datetime.now(UTC)
        # Log what this edit changed; writes nothing when nothing changed (#112).
        # A comment rides along only on a save that changed something (#223).
        changed = change_log.record_update(session, t.id, before, change_log.snapshot(t), t.updated_at)
        if t.category_id != old_category:
            changed = True
            change_log.record_lines(session, t.id, [categories_api.change_line(
                categories_api.name_of(session, old_category),
                categories_api.name_of(session, t.category_id),
            )], t.updated_at)
        if changed:
            change_log.record_lines(session, t.id, stages.note_items(spec.comment), t.updated_at)
        # Keep the Mods Overview registry current with this template's mods (#131).
        mod_registry.register_mods(session, [m.model_dump() for m in spec.mods])
        session.add(t)
        session.commit()
        session.refresh(t)
        return _out(t, session)


@router.put("/{template_id}/category")
async def set_category(
    template_id: int, body: CategoryChoice, _user: str = Depends(auth.require_session)
):
    """File a template under a category, in any stage (#223): a category is only
    a label, so even a Prod template's can change. It is logged, and it does not
    count as a change to what servers run."""
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        categories_api.require(session, body.category_id)
        if body.category_id != t.category_id:
            line = categories_api.change_line(
                categories_api.name_of(session, t.category_id),
                categories_api.name_of(session, body.category_id),
            )
            t.category_id = body.category_id
            session.add(t)
            change_log.record_lines(session, t.id, [line], datetime.now(UTC))
            session.commit()
            session.refresh(t)
        return _out(t, session)


@router.post("/{template_id}/promote")
async def promote_template(
    template_id: int,
    body: stages.Promote,
    request: Request,
    _user: str = Depends(auth.require_session),
):
    """Move a template one stage up: Test → Dev, or release Dev to Prod (#223).

    Releasing into an existing Prod template uses the Dev one up, so the answer
    is the template it became — its id can differ from the one in the path.
    """
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        try:
            result = stages.promote(
                session, stages.TEMPLATES, t,
                note=body.note, into_id=body.into_id, client_id=_client_id(request),
            )
        except stages.StageError as exc:
            raise stage_error(exc) from exc
        session.commit()
        session.refresh(result)
        return _out(result, session)


@router.get("/{template_id}/servers")
async def template_servers(template_id: int, _user: str = Depends(auth.require_session)):
    """The servers using a template, [{id, name, status}]: what deleting it
    would leave without one, for the confirmation that names them (#223)."""
    with Session(get_engine()) as session:
        if not session.get(Template, template_id):
            raise HTTPException(status_code=404, detail="Template not found")
    return await asyncio.to_thread(instance_service.instances_using_template, template_id)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: int,
    request: Request,
    force: bool = False,
    _user: str = Depends(auth.require_session),
):
    """Delete a template. Servers that use it are left without one (#223): they
    keep their data, running ones are stopped, and each needs a new template
    before it can start. That takes `force`, which the GUI sends once the user
    has confirmed it; without it the delete is refused and names them."""
    if edit_locks.held_by_other(template_id, _client_id(request)):
        raise HTTPException(status_code=423, detail=_LOCKED_DETAIL)
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        name = t.name
    used = await asyncio.to_thread(instance_service.instances_using_template, template_id)
    if used and not force:
        listed = ", ".join(f"{u['name']} ({u['status']})" for u in used)
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{name}' is used by {len(used)} server(s): {listed}. Deleting it "
                "leaves them without a template, and stops any that is running."
            ),
        )
    if used:
        await asyncio.to_thread(instance_service.detach_template, template_id)
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            return
        # A server pointed at it in the meantime must not keep a dangling id:
        # SQLite hands a deleted template's id to the next one created.
        for inst in session.exec(select(Instance).where(Instance.template_id == template_id)).all():
            inst.template_id = None
            inst.template_version = None
            inst.desired_state = "stopped"
            session.add(inst)
        change_log.delete_for_template(session, template_id)  # the log dies with it (#112)
        stages.delete_versions(session, stages.TEMPLATES, template_id)
        session.flush()
        session.delete(t)
        session.commit()


@router.get("/{template_id}/changelog")
async def template_changelog(
    template_id: int, q: str | None = None, _user: str = Depends(auth.require_session)
):
    """The template's change log, newest event first, optionally filtered by `q`.

    Read-only — the log is append-only and has no edit/delete endpoint (#112).
    """
    with Session(get_engine()) as session:
        if not session.get(Template, template_id):
            raise HTTPException(status_code=404, detail="Template not found")
        return change_log.entries(session, template_id, q)


@router.get("/{template_id}/versions")
async def template_versions(template_id: int, _user: str = Depends(auth.require_session)):
    """A Prod template's released versions, newest first, with their notes and
    the servers pinned to each (#223)."""
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        pinned: dict[int, list[str]] = {}
        for inst in session.exec(select(Instance).where(Instance.template_id == template_id)).all():
            if inst.template_version:
                pinned.setdefault(inst.template_version, []).append(inst.name)
        return [
            {**v, "pinned_by": pinned.get(v["version"], [])}
            for v in stages.version_list(session, stages.TEMPLATES, [template_id]).get(template_id, [])
        ]


@router.get("/{template_id}/config.json")
async def download_config(
    template_id: int,
    version: int | None = None,
    _user: str = Depends(auth.require_session),
):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        try:
            content = stages.content_of(session, stages.TEMPLATES, t, version)
        except stages.StageError as exc:
            raise stage_error(exc) from exc
        # Re-dump to guarantee pretty output regardless of how it was stored
        pretty = json.dumps(json.loads(content.config_json), indent=2)
        name = f"{t.name} v{version}" if version else t.name
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "config"
        return Response(
            content=pretty,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe}.json"'},
        )


@router.post("/preview")
async def preview_config(spec: TemplateSpec, _user: str = Depends(auth.require_session)):
    """Render config.json for a spec without saving (live wizard preview)."""
    return spec.to_config()


@router.post("/validate")
async def validate_config(
    config: dict = Body(...), _user: str = Depends(auth.require_session)
):
    """Check a hand-edited config.json without applying it (#29).

    Backs the JSON editor's live feedback, so it never mutates anything. Unknown
    keys come back as warnings, never errors — see services/config_validator.
    """
    return config_validator.validate_config(config)


@router.post("/reconcile")
async def reconcile_config(
    payload: dict = Body(...), _user: str = Depends(auth.require_session)
):
    """Fold a hand-edited config.json back into the wizard's spec (#29).

    Splits the edit in two: keys the model knows are read back into spec fields
    (so editing maxPlayers by hand moves the wizard's slider), and whatever is
    left over becomes the `extras` merge patch that to_config re-applies on every
    future render. That split is why the mod manager survives a raw edit —
    game.mods is modelled, so it lands in the spec and diffs away to nothing.
    """
    spec_in = payload.get("spec")
    config = payload.get("config")
    if not isinstance(spec_in, dict) or not isinstance(config, dict):
        raise HTTPException(
            status_code=400, detail="Expected a JSON object with 'spec' and 'config'."
        )

    result = config_validator.validate_config(config)
    if result["errors"]:
        listed = "; ".join(
            f"{e['path']}: {e['message']}" if e["path"] else e["message"]
            for e in result["errors"]
        )
        raise HTTPException(status_code=400, detail=f"Can't apply this config — {listed}")

    known = template_service.spec_from_config(json.dumps(config))
    # spec_from_config returns these two as placeholders ("" / None) because
    # config.json has nowhere to put them — they come from the DB row. Letting a
    # placeholder win the merge below would wipe the scenario the wizard shows.
    for db_only in ("scenario_name", "scenario_player_count"):
        known.pop(db_only, None)
    # Fields that live only in the DB (name, description, launch, mod dependency
    # metadata) aren't in config.json either, so they come from the spec the
    # wizard is holding. spec_from_config's flat mods[] would strip the
    # dependency graph, so keep the wizard's list when the modIds still match.
    merged = {**spec_in, **known}
    if _same_mod_ids(spec_in.get("mods"), known.get("mods")):
        merged["mods"] = spec_in["mods"]
    merged["extras"] = {}

    try:
        # The template's `name` is a DB label that to_config never renders (the
        # in-game name is `game_name`), so a not-yet-named new template must still
        # be able to render a baseline. Substituting here keeps name's min_length
        # enforced where it actually matters — create/update.
        baseline = TemplateSpec(**{**merged, "name": merged.get("name") or "_"}).to_config()
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Can't apply this config: {exc}") from exc

    merged["extras"] = _custom_keys_only(template_service.diff_patch(baseline, config))
    return {"spec": merged, "warnings": result["warnings"]}


@router.post("/import")
async def import_config(
    config: dict = Body(...), _user: str = Depends(auth.require_session)
):
    """Map an uploaded Reforger config.json into editable wizard fields (#35).

    Returns the same {spec} shape the wizard loads when editing a template, so
    the frontend can pre-fill the form from a config.json (launch args aren't
    part of config.json, so those stay at their defaults).
    """
    try:
        spec = template_service.spec_from_config(json.dumps(config))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Not a valid Reforger config.json: {exc}"
        ) from exc
    return {"spec": spec}
