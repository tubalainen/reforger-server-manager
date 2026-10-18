"""Backup and restore of server templates and mod templates (#173).

One file holds both kinds and nothing else. Server *instances* are deliberately
out of scope: a backup restores what you built, not what is running — an
instance is a container, a port lease and a disk full of saved games on one
particular host, none of which travel.

The file is a plain JSON envelope of the stored rows, not a re-render, so a
restore reproduces the template byte-for-byte as it was saved — including the
hand-edited custom keys (#29) and the enriched mod list with its dependency
metadata (#55), neither of which survives a trip through config.json alone.

Importing is a conversation in two steps, because the same name on both sides
is ambiguous by nature:

    preview()  says, per template, whether it is new, identical to what is
               already here, or different — and for a different one, exactly
               what differs, in the same words the change log uses;
    apply()    then does only what the user decided for each item, in one
               transaction: nothing is written unless every item can be.

Passwords ride along in clear text. That is deliberate — a "backup" that
silently blanked the admin password would restore an unprotected server — and
every place that hands the file to the user says so.

Release stages (#223) travel too: each entry says its stage, and a Prod entry
carries every released version, so a restore onto an empty manager brings the
golden templates back as golden, versions and all. What an import may do to a
Prod template that is already here is limited by the same lock as the GUI: it
can't be overwritten, only skipped or imported as a copy (which lands in Test).
"""
import json
from datetime import UTC, datetime

from sqlmodel import Session, select

import config
from models import ModTemplate, ModTemplateVersion, Template, TemplateVersion
from services import change_log, mod_registry, stages
from services.template_service import ModEntry, ModListEntry

# The envelope's format tag. Bump the number only for a change an older manager
# could not read correctly; anything additive keeps @1 (unknown keys are
# ignored on import, so an older file always loads).
FORMAT = "reforger-server-manager/backup@1"
_FORMAT_PREFIX = "reforger-server-manager/backup@"
_FORMAT_VERSION = 1

# What an item can be, and what may be done with it.
STATUS_NEW = "new"
STATUS_IDENTICAL = "identical"
STATUS_DIFFERS = "differs"

ACTION_SKIP = "skip"
ACTION_CREATE = "create"
ACTION_OVERWRITE = "overwrite"
ACTION_COPY = "copy"

_NAME_MAX = 100  # matches Template.name / ModTemplate.name in the wizard's models


class BackupError(ValueError):
    """A file we cannot use, with a message meant for the person who uploaded it."""


class LockedError(RuntimeError):
    """An overwrite target is open in another session (#102) — 423, not 400."""


def _loads(raw: str, fallback):
    try:
        value = json.loads(raw or "")
    except (ValueError, TypeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def _mods_of(t: Template) -> list[dict]:
    """The template's mod list as the wizard edits it.

    Prefers the enriched list; a template saved before that existed (or imported
    from a bare config.json) has only the flat mods[] in its config, so those are
    lifted through ModEntry to give every row the fields the mod list expects.
    """
    stored = _loads(t.mods_json, [])
    if stored:
        return [m for m in stored if isinstance(m, dict) and m.get("modId")]
    flat = _loads(t.config_json, {}).get("game", {}).get("mods", [])
    out = []
    for mod in flat if isinstance(flat, list) else []:
        if isinstance(mod, dict) and mod.get("modId"):
            out.append(ModEntry(**mod).model_dump())
    return out


def _template_content(t: Template | TemplateVersion) -> dict:
    return {
        "description": t.description or "",
        "scenario_name": t.scenario_name or "",
        "scenario_player_count": t.scenario_player_count,
        "config": _loads(t.config_json, {}),
        "launch": _loads(t.launch_params_json, {}),
        "mods": _mods_of(t),
        "extras": _loads(t.extras_json, {}),
    }


def _mod_template_content(mt: ModTemplate | ModTemplateVersion) -> dict:
    mods = _loads(mt.mods_json, [])
    return {
        "description": mt.description or "",
        "mods": [m for m in mods if isinstance(m, dict) and m.get("modId")],
    }


def _stage_entry(session: Session, kind, row, names: dict[int, str], content) -> dict:
    """The stage part of an entry (#223): stage, origin and, for Prod, versions."""
    based_on = None
    if row.based_on_id is not None and row.based_on_id in names:
        based_on = {"name": names[row.based_on_id], "version": row.based_on_version}
    out = {"stage": row.stage, "based_on": based_on}
    if row.stage == stages.PROD:
        out["versions"] = [
            {
                "version": v.version,
                "released_at": v.released_at.isoformat(),
                "note": v.note or "",
                **content(v),
            }
            for v in reversed(stages.versions(session, kind, row.id))
        ]
    return out


def _template_entry(session: Session, t: Template, names: dict[int, str]) -> dict:
    return {
        "name": t.name,
        **_template_content(t),
        **_stage_entry(session, stages.TEMPLATES, t, names, _template_content),
        # Informational only — an imported template is a new row with its own
        # timestamps; these say when the original was made and last touched.
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _mod_template_entry(session: Session, mt: ModTemplate, names: dict[int, str]) -> dict:
    return {
        "name": mt.name,
        **_mod_template_content(mt),
        **_stage_entry(session, stages.MOD_LISTS, mt, names, _mod_template_content),
        "created_at": mt.created_at.isoformat(),
        "updated_at": mt.updated_at.isoformat(),
    }


def build(session: Session) -> dict:
    """The whole shelf: every server template and every mod template."""
    templates = session.exec(select(Template).order_by(Template.name)).all()
    mod_templates = session.exec(select(ModTemplate).order_by(ModTemplate.name)).all()
    names = {t.id: t.name for t in templates}
    mod_names = {mt.id: mt.name for mt in mod_templates}
    return {
        "format": FORMAT,
        "app_version": config.APP_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "server_templates": [_template_entry(session, t, names) for t in templates],
        "mod_templates": [_mod_template_entry(session, mt, mod_names) for mt in mod_templates],
    }


def counts(session: Session) -> dict:
    """How much a backup would hold, without rendering one."""
    return {
        "server_templates": len(session.exec(select(Template.id)).all()),
        "mod_templates": len(session.exec(select(ModTemplate.id)).all()),
    }


def filename(when: datetime | None = None) -> str:
    stamp = (when or datetime.now(UTC)).strftime("%Y-%m-%d")
    return f"reforger-templates-backup-{stamp}.json"


# --------------------------------------------------------------------------- #
# Reading a file back
# --------------------------------------------------------------------------- #

def _str(value, label: str, *, required: bool = False, limit: int = 0) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise BackupError(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        raise BackupError(f"{label} is missing.")
    if limit and len(value) > limit:
        raise BackupError(f"{label} is longer than {limit} characters.")
    return value


def _dict(value, label: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BackupError(f"{label} must be a JSON object.")
    return value


def _list(value, label: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BackupError(f"{label} must be a JSON list.")
    return value


def _clean_mods(mods: list, label: str, entry=ModEntry) -> list[dict]:
    out = []
    for mod in mods:
        if not isinstance(mod, dict) or not str(mod.get("modId") or "").strip():
            raise BackupError(f"{label} has a mod entry with no modId.")
        try:
            out.append(entry(**mod).model_dump())
        except (TypeError, ValueError) as exc:
            raise BackupError(f"{label} has an unusable mod entry: {exc}") from exc
    return out


def _read_stage(raw: dict, label: str, read_content) -> dict:
    """An entry's stage, origin and versions (#223).

    A file written before stages existed has none of it; its templates arrive
    in Dev, exactly where the upgrade put the ones already on this manager.
    """
    stage = raw.get("stage") or stages.DEV
    if stage not in stages.STAGES:
        raise BackupError(f"{label}: unknown stage {stage!r}.")
    based_on = raw.get("based_on")
    if based_on is not None:
        based_on = _dict(based_on, f"{label}: based_on")
        version = based_on.get("version")
        based_on = {
            "name": _str(based_on.get("name"), f"{label}: based_on.name", limit=_NAME_MAX),
            "version": version if isinstance(version, int) else None,
        }
    versions = []
    if stage == stages.PROD:
        seen = set()
        for i, v in enumerate(_list(raw.get("versions"), f"{label}: versions")):
            v = _dict(v, f"{label}: version #{i + 1}")
            number = v.get("version")
            if not isinstance(number, int) or number < 1 or number in seen:
                raise BackupError(f"{label}: version #{i + 1} has no usable number.")
            seen.add(number)
            versions.append({
                "version": number,
                "note": _str(v.get("note"), f"{label} v{number}: note", limit=stages.NOTE_MAX),
                **read_content(v, f"{label} v{number}"),
            })
        versions.sort(key=lambda v: v["version"])
    return {"stage": stage, "based_on": based_on, "versions": versions}


def _read_template_content(raw: dict, label: str) -> dict:
    cfg = _dict(raw.get("config"), f"{label}: config")
    scenario_id = _dict(cfg.get("game"), f"{label}: config.game").get("scenarioId")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        # Without a scenario there is no server to run, and the wizard could not
        # have saved it — so this is a damaged or hand-made file, not a restore.
        raise BackupError(f"{label}: config.game.scenarioId is missing.")
    count = raw.get("scenario_player_count")
    if count is not None and not isinstance(count, int):
        raise BackupError(f"{label}: scenario_player_count must be a whole number.")
    return {
        "description": _str(raw.get("description"), f"{label}: description"),
        "scenario_name": _str(raw.get("scenario_name"), f"{label}: scenario_name"),
        "scenario_player_count": count,
        "config": cfg,
        "launch": _dict(raw.get("launch"), f"{label}: launch"),
        "mods": _clean_mods(_list(raw.get("mods"), f"{label}: mods"), label),
        "extras": _dict(raw.get("extras"), f"{label}: extras"),
    }


def _read_template(raw, index: int) -> dict:
    if not isinstance(raw, dict):
        raise BackupError(f"Server template #{index + 1} is not a JSON object.")
    name = _str(raw.get("name"), f"Server template #{index + 1}: name",
                required=True, limit=_NAME_MAX)
    label = f"Server template '{name}'"
    return {
        "name": name,
        **_read_template_content(raw, label),
        **_read_stage(raw, label, _read_template_content),
    }


def _read_mod_template_content(raw: dict, label: str) -> dict:
    return {
        "description": _str(raw.get("description"), f"{label}: description"),
        # A mod list's entries keep their per-mod comment (#216).
        "mods": _clean_mods(_list(raw.get("mods"), f"{label}: mods"), label, ModListEntry),
    }


def _read_mod_template(raw, index: int) -> dict:
    if not isinstance(raw, dict):
        raise BackupError(f"Mod list #{index + 1} is not a JSON object.")
    name = _str(raw.get("name"), f"Mod list #{index + 1}: name",
                required=True, limit=_NAME_MAX)
    label = f"Mod list '{name}'"
    return {
        "name": name,
        **_read_mod_template_content(raw, label),
        **_read_stage(raw, label, _read_mod_template_content),
    }


def _no_duplicate_names(entries: list[dict], kind: str) -> None:
    seen = set()
    for entry in entries:
        if entry["name"] in seen:
            raise BackupError(
                f"The file lists more than one {kind} named '{entry['name']}'. "
                "Names must be unique — fix the file and try again."
            )
        seen.add(entry["name"])


def parse(payload) -> dict:
    """Validate an uploaded backup and return its entries, or raise BackupError."""
    if not isinstance(payload, dict):
        raise BackupError("That file is not a backup — expected a JSON object.")
    fmt = payload.get("format")
    if not isinstance(fmt, str) or not fmt.startswith(_FORMAT_PREFIX):
        raise BackupError(
            "That file is not a Reforger Server Manager backup "
            "(no 'reforger-server-manager/backup' format tag)."
        )
    try:
        version = int(fmt[len(_FORMAT_PREFIX):])
    except ValueError as exc:
        raise BackupError(f"Unreadable backup format tag: {fmt!r}") from exc
    if version > _FORMAT_VERSION:
        raise BackupError(
            f"This backup is format @{version}, and this manager reads up to "
            f"@{_FORMAT_VERSION}. Update the manager, then import it again."
        )

    templates = [
        _read_template(raw, i)
        for i, raw in enumerate(_list(payload.get("server_templates"), "server_templates"))
    ]
    mod_templates = [
        _read_mod_template(raw, i)
        for i, raw in enumerate(_list(payload.get("mod_templates"), "mod_templates"))
    ]
    _no_duplicate_names(templates, "server template")
    _no_duplicate_names(mod_templates, "mod list")
    if not templates and not mod_templates:
        raise BackupError("This backup holds no templates.")
    return {
        # Informational, shown on the preview: which manager wrote the file and
        # when. Read leniently — they never decide anything.
        "app_version": str(payload.get("app_version") or ""),
        "exported_at": str(payload.get("exported_at") or ""),
        "server_templates": templates,
        "mod_templates": mod_templates,
    }


# --------------------------------------------------------------------------- #
# Preview: what would this import change?
# --------------------------------------------------------------------------- #

def _short(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= 40 else text[:37] + "…"


def _launch_lines(old: dict, new: dict) -> list[str]:
    """Engine launch parameters, which live outside config.json (#20).

    The change log never sees these — it diffs the rendered config — so without
    this an import could quietly swap a server's launch arguments while the
    preview said "identical".
    """
    lines = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        lines.append(f"Launch parameter {key}: {_short(before)} → {_short(after)}")
    return lines


def _template_changes(existing: Template, incoming: dict) -> list[str]:
    """Human-readable lines turning the stored template into the file's version."""
    old = change_log.snapshot(existing)
    new = {
        "name": incoming["name"],
        "description": incoming["description"],
        "scenario_name": incoming["scenario_name"],
        "config": incoming["config"],
    }
    lines = [summary for _category, summary in change_log.diff(old, new)]
    # The scenario's display name is not in config.json, so the diff above only
    # notices a scenario change by id — two rows showing different text in the
    # wizard would otherwise be called identical.
    if (existing.scenario_name or "") != incoming["scenario_name"]:
        lines.append(
            f"Scenario name: {existing.scenario_name or '(none)'} → "
            f"{incoming['scenario_name'] or '(none)'}"
        )
    lines += _launch_lines(_loads(existing.launch_params_json, {}), incoming["launch"])
    if _loads(existing.extras_json, {}) != incoming["extras"]:
        lines.append("Custom config keys (from Edit JSON) differ")
    # The enriched list carries the load order and the version locks; the config
    # diff above only sees mods[] as the server gets it, so an order change or a
    # dependency-metadata change would otherwise go unmentioned.
    stored_mods = _mods_of(existing)
    if stored_mods != incoming["mods"]:
        order_lines = change_log.order_change(stored_mods, incoming["mods"])
        lines += [summary for _category, summary in order_lines]
    return lines


def _mod_template_changes(existing: ModTemplate, incoming: dict) -> list[str]:
    old = change_log.mod_template_snapshot(existing)
    new = {
        "name": incoming["name"],
        "description": incoming["description"],
        "mods": incoming["mods"],
    }
    return [summary for _category, summary in change_log.mod_template_diff(old, new)]


def _item(name: str, existing, changes: list[str], extra: dict) -> dict:
    if existing is None:
        status, action = STATUS_NEW, ACTION_CREATE
    elif changes:
        status, action = STATUS_DIFFERS, ACTION_SKIP  # never overwrite unasked
    else:
        status, action = STATUS_IDENTICAL, ACTION_SKIP
    return {
        "name": name,
        "status": status,
        "suggested_action": action,
        "existing_id": existing.id if existing is not None else None,
        # A Prod template here can't be overwritten (#223); the GUI hides that.
        "existing_stage": existing.stage if existing is not None else None,
        "changes": changes,
        **extra,
    }


def preview(session: Session, payload) -> dict:
    """What each template in the file is, next to what is already here."""
    parsed = parse(payload)
    by_name = {t.name: t for t in session.exec(select(Template)).all()}
    mods_by_name = {mt.name: mt for mt in session.exec(select(ModTemplate)).all()}

    templates = []
    for entry in parsed["server_templates"]:
        existing = by_name.get(entry["name"])
        changes = _template_changes(existing, entry) if existing else []
        templates.append(_item(entry["name"], existing, changes, {
            "scenario_name": entry["scenario_name"],
            "mod_count": len(entry["mods"]),
            "stage": entry["stage"],
        }))

    mod_templates = []
    for entry in parsed["mod_templates"]:
        existing = mods_by_name.get(entry["name"])
        changes = _mod_template_changes(existing, entry) if existing else []
        mod_templates.append(_item(entry["name"], existing, changes, {
            "mod_count": len(entry["mods"]),
            "stage": entry["stage"],
        }))

    return {
        "app_version": parsed["app_version"],
        "exported_at": parsed["exported_at"],
        "server_templates": templates,
        "mod_templates": mod_templates,
    }


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #

def free_name(taken: set[str], name: str, suffix: str = "imported") -> str:
    """A free "<name> (imported)" — then "(imported 2)", … — within 100 chars."""
    def fit(tail: str) -> str:
        return f"{name}"[: _NAME_MAX - len(tail)] + tail

    candidate = fit(f" ({suffix})")
    n = 2
    while candidate in taken:
        candidate = fit(f" ({suffix} {n})")
        n += 1
    return candidate


def _resolve(items: list[dict], decisions: dict, kind: str) -> list[tuple[dict, str]]:
    """Pair each entry with the action the user chose, defaulting to skip.

    Anything the user did not decide is skipped: an import must never write
    something nobody asked for, and a preview the user walked away from is
    exactly that.
    """
    allowed = {ACTION_SKIP, ACTION_CREATE, ACTION_OVERWRITE, ACTION_COPY}
    out = []
    for entry in items:
        action = decisions.get(entry["name"], ACTION_SKIP)
        if action not in allowed:
            raise BackupError(
                f"Unknown action {action!r} for the {kind} '{entry['name']}'."
            )
        out.append((entry, action))
    return out


def _template_columns(content: dict) -> dict:
    """A template entry's content as the stored columns (row or version)."""
    return {
        "description": content["description"],
        "config_json": json.dumps(content["config"]),
        "scenario_name": content["scenario_name"],
        "scenario_player_count": content["scenario_player_count"],
        "launch_params_json": json.dumps(content["launch"]),
        # An empty list is stored as "" on purpose: that is what makes the
        # template reader fall back to the flat mods[] in config.json (#55).
        "mods_json": json.dumps(content["mods"]) if content["mods"] else "",
        "extras_json": json.dumps(content["extras"]),
    }


def _mod_template_columns(content: dict) -> dict:
    return {"description": content["description"], "mods_json": json.dumps(content["mods"])}


def _restore_stage(session: Session, kind, row, entry: dict, columns, when) -> None:
    """Give a newly created row the stage it was saved in, with its versions.

    A Prod entry with no versions (hand-made file) gets a v1 of its head, so a
    Prod row always has the version its prod_version names.
    """
    row.stage = entry["stage"]
    row.prod_version = None
    if row.stage == stages.PROD:
        versions = entry["versions"] or [{"version": 1, "note": "", **entry}]
        for v in versions:
            session.add(kind.version_model(**{
                kind.fk: row.id, "version": v["version"], "released_at": when,
                "note": v.get("note") or "", **columns(v),
            }))
        row.prod_version = versions[-1]["version"]
    session.add(row)


def _link_based_on(session: Session, made: list[tuple], names: dict) -> None:
    """Point each restored row at the row its entry was based on, by name, now
    that every row of the import exists. Only a Prod row can be an origin."""
    for row, entry in made:
        origin = entry.get("based_on")
        source = names.get(origin["name"]) if origin else None
        if source is not None and source.stage == stages.PROD:
            row.based_on_id, row.based_on_version = source.id, origin["version"]
            session.add(row)


def _as_copy_of(row, existing) -> None:
    """An imported copy lands in Test and stems from what it was copied next to."""
    row.stage = stages.TEST
    row.prod_version = None
    row.based_on_id, row.based_on_version = stages.based_on_for(existing)


def _apply_template(session: Session, entry: dict, existing: Template | None, when):
    """Write one server template row from a backup entry."""
    target = existing or Template(name=entry["name"], config_json="{}")
    before = change_log.snapshot(target) if existing else None
    target.name = entry["name"]
    for col, value in _template_columns(entry).items():
        setattr(target, col, value)
    target.updated_at = when
    if existing is None:
        target.created_at = when
    session.add(target)
    session.flush()  # assign the id before the log references it
    change_log.record_template_import(session, target, before, when)
    # Imported mods join the Mods Overview registry like any other save (#131).
    mod_registry.register_mods(session, entry["mods"])
    return target


def _apply_mod_template(session: Session, entry: dict, existing: ModTemplate | None, when):
    target = existing or ModTemplate(name=entry["name"])
    before = change_log.mod_template_snapshot(target) if existing else None
    target.name = entry["name"]
    for col, value in _mod_template_columns(entry).items():
        setattr(target, col, value)
    target.updated_at = when
    if existing is None:
        target.created_at = when
    session.add(target)
    session.flush()
    change_log.record_mod_template_import(session, target, before, when)
    mod_registry.register_mods(session, entry["mods"])
    return target


def apply(session: Session, payload, decisions: dict, *, is_locked=None) -> dict:
    """Carry out the decisions the user made on a preview.

    All of them or none: the whole import is one transaction, so a name that
    turns out to be taken (or a template someone else started editing since the
    preview) stops everything before a half-restored shelf can exist.
    """
    parsed = parse(payload)
    decisions = decisions if isinstance(decisions, dict) else {}
    plan = _resolve(
        parsed["server_templates"],
        _dict(decisions.get("server_templates"), "decisions.server_templates"),
        "server template",
    )
    mod_plan = _resolve(
        parsed["mod_templates"],
        _dict(decisions.get("mod_templates"), "decisions.mod_templates"),
        "mod template",
    )

    templates = {t.name: t for t in session.exec(select(Template)).all()}
    mod_templates = {mt.name: mt for mt in session.exec(select(ModTemplate)).all()}
    taken = set(templates)
    mod_taken = set(mod_templates)

    # Refuse before writing anything: an overwrite is a save, and a template
    # someone else has open must not be saved under them (#102) — nor may a
    # Prod one be saved over at all (#223).
    for entries, rows in ((plan, templates), (mod_plan, mod_templates)):
        for entry, action in entries:
            existing = rows.get(entry["name"])
            if action != ACTION_OVERWRITE or existing is None:
                continue
            stages.ensure_editable(existing)
            if rows is templates and is_locked is not None and is_locked(existing.id):
                raise LockedError(
                    f"'{entry['name']}' is being edited in another session, so it "
                    "cannot be overwritten. Wait for them to finish, or use "
                    "'Clear edit locks' on the Server Templates page."
                )

    when = datetime.now(UTC)
    results = {"server_templates": [], "mod_templates": []}
    made: list[tuple] = []      # (row, entry) created from the file as it was
    mod_made: list[tuple] = []

    for entry, action in plan:
        existing = templates.get(entry["name"])
        if action == ACTION_SKIP:
            results["server_templates"].append({"name": entry["name"], "action": "skipped"})
            continue
        if action == ACTION_CREATE and existing:
            raise BackupError(
                f"A server template named '{entry['name']}' already exists. "
                "Preview the file again and choose what to do with it."
            )
        if action in (ACTION_OVERWRITE, ACTION_COPY) and not existing:
            # The template it would have replaced is gone since the preview.
            action = ACTION_CREATE
        copy_of = existing if action == ACTION_COPY else None
        if action == ACTION_COPY:
            entry = {**entry, "name": free_name(taken, entry["name"])}
            existing = None
        row = _apply_template(session, entry, existing if action == ACTION_OVERWRITE else None, when)
        if copy_of is not None:
            _as_copy_of(row, copy_of)
        elif action == ACTION_CREATE:
            _restore_stage(session, stages.TEMPLATES, row, entry, _template_columns, when)
            made.append((row, entry))
        taken.add(row.name)
        results["server_templates"].append({
            "name": row.name,
            "action": "overwritten" if action == ACTION_OVERWRITE else "created",
            "id": row.id,
        })

    for entry, action in mod_plan:
        existing = mod_templates.get(entry["name"])
        if action == ACTION_SKIP:
            results["mod_templates"].append({"name": entry["name"], "action": "skipped"})
            continue
        if action == ACTION_CREATE and existing:
            raise BackupError(
                f"A mod template named '{entry['name']}' already exists. "
                "Preview the file again and choose what to do with it."
            )
        if action in (ACTION_OVERWRITE, ACTION_COPY) and not existing:
            action = ACTION_CREATE
        copy_of = existing if action == ACTION_COPY else None
        if action == ACTION_COPY:
            entry = {**entry, "name": free_name(mod_taken, entry["name"])}
            existing = None
        row = _apply_mod_template(
            session, entry, existing if action == ACTION_OVERWRITE else None, when
        )
        if copy_of is not None:
            _as_copy_of(row, copy_of)
        elif action == ACTION_CREATE:
            _restore_stage(session, stages.MOD_LISTS, row, entry, _mod_template_columns, when)
            mod_made.append((row, entry))
        mod_taken.add(row.name)
        results["mod_templates"].append({
            "name": row.name,
            "action": "overwritten" if action == ACTION_OVERWRITE else "created",
            "id": row.id,
        })

    session.flush()
    _link_based_on(session, made, {t.name: t for t in session.exec(select(Template)).all()})
    _link_based_on(
        session, mod_made, {mt.name: mt for mt in session.exec(select(ModTemplate)).all()}
    )
    session.commit()
    counts = {"created": 0, "overwritten": 0, "skipped": 0}
    for item in results["server_templates"] + results["mod_templates"]:
        counts[item["action"]] += 1
    return {**results, "counts": counts}
