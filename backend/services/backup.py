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
"""
import json
from datetime import UTC, datetime

from sqlmodel import Session, select

import config
from models import ModTemplate, Template
from services import change_log, mod_registry
from services.template_service import ModEntry

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


def _template_entry(t: Template) -> dict:
    return {
        "name": t.name,
        "description": t.description or "",
        "scenario_name": t.scenario_name or "",
        "scenario_player_count": t.scenario_player_count,
        "config": _loads(t.config_json, {}),
        "launch": _loads(t.launch_params_json, {}),
        "mods": _mods_of(t),
        "extras": _loads(t.extras_json, {}),
        # Informational only — an imported template is a new row with its own
        # timestamps; these say when the original was made and last touched.
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _mod_template_entry(mt: ModTemplate) -> dict:
    mods = _loads(mt.mods_json, [])
    return {
        "name": mt.name,
        "description": mt.description or "",
        "mods": [m for m in mods if isinstance(m, dict) and m.get("modId")],
        "created_at": mt.created_at.isoformat(),
        "updated_at": mt.updated_at.isoformat(),
    }


def build(session: Session) -> dict:
    """The whole shelf: every server template and every mod template."""
    templates = session.exec(select(Template).order_by(Template.name)).all()
    mod_templates = session.exec(select(ModTemplate).order_by(ModTemplate.name)).all()
    return {
        "format": FORMAT,
        "app_version": config.APP_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "server_templates": [_template_entry(t) for t in templates],
        "mod_templates": [_mod_template_entry(mt) for mt in mod_templates],
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


def _clean_mods(mods: list, label: str) -> list[dict]:
    out = []
    for mod in mods:
        if not isinstance(mod, dict) or not str(mod.get("modId") or "").strip():
            raise BackupError(f"{label} has a mod entry with no modId.")
        try:
            out.append(ModEntry(**mod).model_dump())
        except (TypeError, ValueError) as exc:
            raise BackupError(f"{label} has an unusable mod entry: {exc}") from exc
    return out


def _read_template(raw, index: int) -> dict:
    if not isinstance(raw, dict):
        raise BackupError(f"Server template #{index + 1} is not a JSON object.")
    name = _str(raw.get("name"), f"Server template #{index + 1}: name",
                required=True, limit=_NAME_MAX)
    label = f"Server template '{name}'"
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
        "name": name,
        "description": _str(raw.get("description"), f"{label}: description"),
        "scenario_name": _str(raw.get("scenario_name"), f"{label}: scenario_name"),
        "scenario_player_count": count,
        "config": cfg,
        "launch": _dict(raw.get("launch"), f"{label}: launch"),
        "mods": _clean_mods(_list(raw.get("mods"), f"{label}: mods"), label),
        "extras": _dict(raw.get("extras"), f"{label}: extras"),
    }


def _read_mod_template(raw, index: int) -> dict:
    if not isinstance(raw, dict):
        raise BackupError(f"Mod template #{index + 1} is not a JSON object.")
    name = _str(raw.get("name"), f"Mod template #{index + 1}: name",
                required=True, limit=_NAME_MAX)
    label = f"Mod template '{name}'"
    return {
        "name": name,
        "description": _str(raw.get("description"), f"{label}: description"),
        "mods": _clean_mods(_list(raw.get("mods"), f"{label}: mods"), label),
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
    _no_duplicate_names(mod_templates, "mod template")
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
        }))

    mod_templates = []
    for entry in parsed["mod_templates"]:
        existing = mods_by_name.get(entry["name"])
        changes = _mod_template_changes(existing, entry) if existing else []
        mod_templates.append(_item(entry["name"], existing, changes, {
            "mod_count": len(entry["mods"]),
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


def _apply_template(session: Session, entry: dict, existing: Template | None, when):
    """Write one server template row from a backup entry."""
    target = existing or Template(name=entry["name"], config_json="{}")
    before = change_log.snapshot(target) if existing else None
    target.name = entry["name"]
    target.description = entry["description"]
    target.config_json = json.dumps(entry["config"])
    target.scenario_name = entry["scenario_name"]
    target.scenario_player_count = entry["scenario_player_count"]
    target.launch_params_json = json.dumps(entry["launch"])
    # An empty list is stored as "" on purpose: that is what makes the template
    # reader fall back to the flat mods[] in config.json for older rows (#55).
    target.mods_json = json.dumps(entry["mods"]) if entry["mods"] else ""
    target.extras_json = json.dumps(entry["extras"])
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
    target.description = entry["description"]
    target.mods_json = json.dumps(entry["mods"])
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
    # someone else has open must not be saved under them (#102).
    if is_locked is not None:
        for entry, action in plan:
            existing = templates.get(entry["name"])
            if action == ACTION_OVERWRITE and existing and is_locked(existing.id):
                raise LockedError(
                    f"'{entry['name']}' is being edited in another session, so it "
                    "cannot be overwritten. Wait for them to finish, or use "
                    "'Clear edit locks' on the Server Templates page."
                )

    when = datetime.now(UTC)
    results = {"server_templates": [], "mod_templates": []}

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
        if action == ACTION_COPY:
            entry = {**entry, "name": free_name(taken, entry["name"])}
            existing = None
        row = _apply_template(session, entry, existing if action == ACTION_OVERWRITE else None, when)
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
        if action == ACTION_COPY:
            entry = {**entry, "name": free_name(mod_taken, entry["name"])}
            existing = None
        row = _apply_mod_template(
            session, entry, existing if action == ACTION_OVERWRITE else None, when
        )
        mod_taken.add(row.name)
        results["mod_templates"].append({
            "name": row.name,
            "action": "overwritten" if action == ACTION_OVERWRITE else "created",
            "id": row.id,
        })

    session.commit()
    counts = {"created": 0, "overwritten": 0, "skipped": 0}
    for item in results["server_templates"] + results["mod_templates"]:
        counts[item["action"]] += 1
    return {**results, "counts": counts}
