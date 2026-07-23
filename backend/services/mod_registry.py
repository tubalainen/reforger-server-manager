"""The persistent "Mods Overview" registry (#131).

A single, template-independent list of every mod that has ever been baked into a
server template, plus everything the overview needs to reason about it: which
templates and instances currently carry each mod (and at what version), a
persist flag that shields a mod from pruning, and — on demand — the live
dependency tree scraped from the Workshop.

Two ideas keep this simple:

  * The registry table (ModRegistryEntry) remembers only what nothing else can:
    the mod id, a best-known name, the persist flag and first/last-seen stamps.
    "Once added, always on the overview" is just: upsert a row the first time a
    template lists the mod, and never auto-remove a persisted one.

  * Everything volatile — the templates/instances using a mod, their configured
    versions, the dependency edges — is derived live on every read from the
    current templates (and, for the tree, the Workshop). Nothing to keep in sync.
"""
import json
import logging
from datetime import UTC, datetime

from sqlmodel import Session, select

from models import Instance, ModRegistryEntry, Template, _utcnow, get_engine
from services import change_log, template_service
from services.template_service import TemplateSpec
from services.workshop_service import WorkshopError, normalize_asset_id, workshop

logger = logging.getLogger("manager.mods")


# --------------------------------------------------------------------------- #
# Reading a template's mods
# --------------------------------------------------------------------------- #

def _enriched_mods(t: Template) -> list[dict]:
    """A template's mod list, richest form available.

    Prefers the enriched Template.mods_json (dependency metadata + version locks,
    #55) and falls back to the flat game.mods in config.json for templates saved
    before that column existed — the same precedence templates_api._out uses.
    """
    if t.mods_json:
        try:
            mods = json.loads(t.mods_json)
            if isinstance(mods, list):
                return [m for m in mods if isinstance(m, dict) and m.get("modId")]
        except (ValueError, TypeError):
            pass
    try:
        game = (json.loads(t.config_json or "{}").get("game") or {})
        mods = game.get("mods") or []
        return [m for m in mods if isinstance(m, dict) and m.get("modId")]
    except (ValueError, TypeError, AttributeError):
        return []


def _norm(mod_id: str | None) -> str | None:
    return normalize_asset_id(mod_id or "") if mod_id else None


# --------------------------------------------------------------------------- #
# Registration ("once added, always on the overview")
# --------------------------------------------------------------------------- #

def register_mods(session: Session, mods: list[dict]) -> int:
    """Upsert each mod into the registry; returns how many rows were inserted.

    Refreshes the display name (when a non-empty one is supplied) and last_seen
    for mods already present, and stamps first_added on brand-new ones. Does not
    commit — the caller owns the transaction so a template save and its mod
    registration land together.
    """
    inserted = 0
    for mod in mods:
        mod_id = _norm(mod.get("modId"))
        if not mod_id:
            continue
        name = (mod.get("name") or "").strip()
        row = session.exec(
            select(ModRegistryEntry).where(ModRegistryEntry.mod_id == mod_id)
        ).first()
        if row is None:
            session.add(ModRegistryEntry(mod_id=mod_id, name=name))
            inserted += 1
        else:
            if name:
                row.name = name
            row.last_seen_at = _utcnow()
            session.add(row)
    return inserted


def backfill_from_templates(session: Session | None = None) -> int:
    """Seed the registry from every existing template's mods (idempotent).

    Run once at startup so templates that predate the registry still show their
    mods without the user having to re-save each one. Safe to run repeatedly:
    register_mods only inserts mods it hasn't seen.
    """
    own = session is None
    session = session or Session(get_engine())
    try:
        inserted = 0
        for t in session.exec(select(Template)).all():
            inserted += register_mods(session, _enriched_mods(t))
        if own:
            session.commit()
        if inserted:
            logger.info("Mod registry: backfilled %d mod(s) from templates", inserted)
        return inserted
    finally:
        if own:
            session.close()


# --------------------------------------------------------------------------- #
# The overview (pure DB — fast, no Docker, no network)
# --------------------------------------------------------------------------- #

def overview(session: Session) -> list[dict]:
    """Every registry mod, enriched with its current template/instance usage.

    For each mod: the templates that list it now, and the instances that carry it
    (an instance carries a mod when its template lists it — instances re-bake from
    their template's config), each with the version that template configures
    (a locked version, or null for "latest"). Mods no template references any
    more still appear — that is the whole point of the registry.
    """
    templates = list(session.exec(select(Template)).all())
    instances = list(session.exec(select(Instance)).all())

    # modId -> [{id, name}]  and  (template_id, modId) -> version
    mod_templates: dict[str, list[dict]] = {}
    tmpl_mod_version: dict[tuple[int, str], str | None] = {}
    tmpl_by_id: dict[int, Template] = {}
    # modId -> does any template's entry mark it as publishing its own scenario(s)?
    # A DB-only hint (#131) so the overview can flag scenario-carrying mods even
    # when the Workshop (and thus the live type badge) is unreachable.
    mod_provides: dict[str, bool] = {}
    for t in templates:
        tmpl_by_id[t.id] = t
        for m in _enriched_mods(t):
            mid = _norm(m.get("modId"))
            if not mid:
                continue
            mod_templates.setdefault(mid, []).append({"id": t.id, "name": t.name})
            tmpl_mod_version[(t.id, mid)] = m.get("version")
            if m.get("provides_scenarios"):
                mod_provides[mid] = True

    # modId -> [{id, name, template, version}] via each instance's template
    mod_instances: dict[str, list[dict]] = {}
    for inst in instances:
        t = tmpl_by_id.get(inst.template_id)
        if not t:
            continue
        for m in _enriched_mods(t):
            mid = _norm(m.get("modId"))
            if not mid:
                continue
            mod_instances.setdefault(mid, []).append({
                "id": inst.id,
                "name": inst.name,
                "template": t.name,
                "version": tmpl_mod_version.get((t.id, mid)),
            })

    rows = session.exec(select(ModRegistryEntry).order_by(ModRegistryEntry.name)).all()
    out = []
    for r in rows:
        used_by = mod_templates.get(r.mod_id, [])
        # A template name is a friendlier fallback than the bare id when the
        # registry row's own name is still empty (e.g. a manual/legacy add).
        name = r.name or (used_by[0]["name"] if used_by else "") or r.mod_id
        out.append({
            "mod_id": r.mod_id,
            "name": name,
            "persist": r.persist,
            "first_added_at": r.first_added_at.isoformat(),
            "templates": used_by,
            "instances": mod_instances.get(r.mod_id, []),
            "orphaned": not used_by,  # in no template any more, kept by the rule
            "provides_scenarios": mod_provides.get(r.mod_id, False),
        })
    # Name-sort is case-insensitive; SQL's ORDER BY name isn't, so redo it here.
    out.sort(key=lambda m: (m["name"] or m["mod_id"]).lower())
    return out


def current_mod_ids(session: Session) -> set[str]:
    """Every modId listed by some template right now."""
    ids: set[str] = set()
    for t in session.exec(select(Template)).all():
        for m in _enriched_mods(t):
            mid = _norm(m.get("modId"))
            if mid:
                ids.add(mid)
    return ids


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #

def rescan(session: Session) -> dict:
    """Clear stale mods and re-sync the registry to the templates (#131).

    Removes every non-persisted mod that no template lists any more, then
    re-registers all current template mods (picking up anything new and
    refreshing names). Persisted mods are never removed. Returns a summary.
    """
    current = current_mod_ids(session)
    pruned = 0
    for r in list(session.exec(select(ModRegistryEntry)).all()):
        if not r.persist and r.mod_id not in current:
            session.delete(r)
            pruned += 1
    backfill_from_templates(session)  # re-add / refresh everything in use now
    session.commit()
    total = len(session.exec(select(ModRegistryEntry)).all())
    logger.info("Mod registry rescan: pruned %d, %d mod(s) remain", pruned, total)
    return {"pruned": pruned, "total": total}


def set_persist(session: Session, mod_id: str, persist: bool) -> dict:
    """Flip a mod's persist flag. Raises KeyError if it isn't registered."""
    mid = _norm(mod_id) or mod_id
    row = session.exec(
        select(ModRegistryEntry).where(ModRegistryEntry.mod_id == mid)
    ).first()
    if row is None:
        raise KeyError(mid)
    row.persist = persist
    session.add(row)
    session.commit()
    return {"mod_id": mid, "persist": persist}


def delete(session: Session, mod_id: str) -> None:
    """Remove one mod from the overview.

    Refuses a persisted mod — the persist flag means "never delete" (#131);
    the user must clear it first. Raises KeyError if unknown, PermissionError
    if persisted.
    """
    mid = _norm(mod_id) or mod_id
    row = session.exec(
        select(ModRegistryEntry).where(ModRegistryEntry.mod_id == mid)
    ).first()
    if row is None:
        raise KeyError(mid)
    if row.persist:
        raise PermissionError(mid)
    session.delete(row)
    session.commit()


# --------------------------------------------------------------------------- #
# Live dependency tree (best-effort Workshop scrape)
# --------------------------------------------------------------------------- #

def resolve_tree(mod_ids: list[str]) -> dict:
    """Resolve the dependency edges for the given mods, from the Workshop.

    The wizard deliberately stores plain addons with no dependency edges (the
    game auto-downloads sub-deps, #97), so a real tree can't come from stored
    data — it's scraped live here and merged into one graph. Best-effort: a mod
    whose page can't be fetched lands in `missing` and simply renders flat.
    Relies on WorkshopService's in-memory asset cache so shared dependencies
    across roots aren't refetched.

    Returns {edges: {modId: [depId]}, names: {modId: name}, types: {modId:
    {kind, tags}}, missing: [modId], resolved: bool} covering the requested mods
    and every dependency discovered beneath them. `kind` is the Workshop
    classification (scenario|terrain|addon) and `tags` its category labels, so
    the overview can show what each mod actually is (#131).
    """
    edges: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    types: dict[str, dict] = {}
    missing: set[str] = set()
    resolved_any = False

    for raw in mod_ids:
        mid = _norm(raw)
        if not mid:
            continue
        try:
            res = workshop.resolve_dependencies(mid)
        except WorkshopError:
            missing.add(mid)
            continue
        resolved_any = True
        for m in res.get("mods") or []:
            emid = m.get("modId")
            if not emid:
                continue
            if m.get("name"):
                names[emid] = m["name"]
            deps = [d for d in (m.get("dependencies") or []) if d]
            # Prefer the first non-empty edge set we learn for a mod.
            if emid not in edges or (deps and not edges[emid]):
                edges[emid] = deps
            if emid not in types and (m.get("kind") or m.get("tags")):
                types[emid] = {"kind": m.get("kind"), "tags": m.get("tags") or []}
        for miss in res.get("missing") or []:
            missing.add(miss)

    return {
        "edges": edges,
        "names": names,
        "types": types,
        "missing": sorted(missing),
        "resolved": resolved_any,
    }


# --------------------------------------------------------------------------- #
# One-click "add ticked mods to a template" (#131)
# --------------------------------------------------------------------------- #

def _spec_dict(t: Template) -> dict:
    """Reconstruct the wizard spec for a template, ready to re-render.

    Mirrors how the wizard save rebuilds a spec: config-derived fields, the
    DB-only scenario name/count, launch params, the custom-key overlay, and the
    enriched mod list (so dependency metadata and version locks survive).
    """
    spec = template_service.spec_from_config(t.config_json)
    spec["name"] = t.name
    spec["description"] = t.description
    spec["scenario_name"] = t.scenario_name
    spec["scenario_player_count"] = t.scenario_player_count
    spec["launch"] = json.loads(t.launch_params_json or "{}")
    spec["extras"] = json.loads(t.extras_json or "{}")
    spec["mods"] = _enriched_mods(t)
    return spec


def add_mods_to_template(session: Session, t: Template, mod_ids: list[str]) -> dict:
    """Merge the given mods into a template as top-level (explicit) addons.

    Matches the wizard's single-addon add (#97): only the mod itself is added —
    its sub-dependencies download at runtime — so no dependency tree is resolved
    or stored. Each mod's name/versions are enriched from the Workshop when
    reachable, and skipped silently when it isn't (the mod is still added by id).
    Re-renders config.json, refreshes the enriched mod list, writes a change-log
    entry and registers the mods. Commits. Returns what was added/skipped.
    """
    mods = _enriched_mods(t)
    have = {_norm(m.get("modId")) for m in mods}
    added: list[dict] = []
    skipped: list[str] = []

    for raw in mod_ids:
        mid = _norm(raw)
        if not mid:
            continue
        if mid in have:
            skipped.append(mid)
            continue
        name, versions, provides = None, [], False
        try:
            asset = workshop.get_asset(mid)
            name = asset.get("name")
            versions = asset.get("versions") or []
            provides = bool(asset.get("scenarios"))
        except WorkshopError:
            logger.info("add-to-template: Workshop unreachable for %s, adding by id", mid)
        entry = {
            "modId": mid,
            "name": name,
            "version": None,          # follow latest; the user can lock it later
            "explicit": True,
            "from_scenario": False,
            "dependencies": [],       # top-level only (#97)
            "versions": versions,
            "provides_scenarios": provides,
        }
        mods.append(entry)
        have.add(mid)
        added.append({"mod_id": mid, "name": name or mid})

    if not added:
        return {"added": [], "skipped": skipped}

    spec_dict = _spec_dict(t)
    spec_dict["mods"] = mods
    spec = TemplateSpec(**spec_dict)

    before = change_log.snapshot(t)
    t.config_json = template_service.render_config_json(spec)
    t.mods_json = json.dumps([m.model_dump() for m in spec.mods])
    t.updated_at = datetime.now(UTC)
    change_log.record_update(session, t.id, before, change_log.snapshot(t), t.updated_at)
    register_mods(session, [m.model_dump() for m in spec.mods])
    session.add(t)
    session.commit()
    session.refresh(t)
    return {"added": added, "skipped": skipped}
