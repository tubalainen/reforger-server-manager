"""Template CRUD + config.json export (auth-gated)."""
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError
from sqlmodel import Session, select

import auth
from models import Template, get_engine
from services import (
    change_log,
    config_validator,
    edit_locks,
    instance_service,
    template_service,
)
from services.template_service import TemplateSpec

router = APIRouter(prefix="/api/templates", tags=["templates"])

_LOCKED_DETAIL = (
    "This template is being edited in another session. Wait for them to finish, "
    "or use 'Clear edit locks' on the Templates page if a lock is stuck."
)


def _client_id(request: Request) -> str:
    """The per-tab id the frontend sends on every request; '' for bare curl."""
    return request.headers.get("X-Client-Id", "").strip()


def _out(t: Template) -> dict:
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
        spec["extras"] = json.loads(t.extras_json or "{}")
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
        if value is None and path in known:
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
        return [
            {"id": t.id, "name": t.name, "description": t.description,
             "updated_at": t.updated_at.isoformat(),
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
        if not session.get(Template, template_id):
            raise HTTPException(status_code=404, detail="Template not found")
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
async def create_template(spec: TemplateSpec, _user: str = Depends(auth.require_session)):
    config_json = template_service.render_config_json(spec)
    with Session(get_engine()) as session:
        if session.exec(select(Template).where(Template.name == spec.name)).first():
            raise HTTPException(status_code=409, detail=f"A template named '{spec.name}' already exists")
        t = Template(
            name=spec.name, description=spec.description, config_json=config_json,
            scenario_name=spec.scenario_name,
            scenario_player_count=spec.scenario_player_count,
            launch_params_json=spec.launch.model_dump_json(),
            mods_json=_mods_json(spec),
            extras_json=json.dumps(spec.extras),
        )
        session.add(t)
        session.flush()  # assign the id before the log references it
        change_log.record_creation(session, t)  # start the change log (#112)
        session.commit()
        session.refresh(t)
        return _out(t)


@router.get("/{template_id}")
async def get_template(template_id: int, _user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        return _out(t)


@router.put("/{template_id}")
async def update_template(
    template_id: int,
    spec: TemplateSpec,
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
        clash = session.exec(
            select(Template).where(Template.name == spec.name, Template.id != template_id)
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail=f"A template named '{spec.name}' already exists")
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
        change_log.record_update(session, t.id, before, change_log.snapshot(t), t.updated_at)
        session.add(t)
        session.commit()
        session.refresh(t)
        return _out(t)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: int, request: Request, _user: str = Depends(auth.require_session)
):
    if edit_locks.held_by_other(template_id, _client_id(request)):
        raise HTTPException(status_code=423, detail=_LOCKED_DETAIL)
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        # Don't orphan instances: block the delete while any still use this
        # template, and tell the user which ones to repoint or remove (issue #31).
        used = instance_service.instances_using_template(template_id)
        if used:
            listed = ", ".join(f"{u['name']} ({u['status']})" for u in used)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Can't delete '{t.name}': used by {len(used)} instance(s): "
                    f"{listed}. Repoint or delete them first."
                ),
            )
        change_log.delete_for_template(session, template_id)  # the log dies with it (#112)
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


@router.get("/{template_id}/config.json")
async def download_config(template_id: int, _user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        # Re-dump to guarantee pretty output regardless of how it was stored
        pretty = json.dumps(json.loads(t.config_json), indent=2)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in t.name) or "config"
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
