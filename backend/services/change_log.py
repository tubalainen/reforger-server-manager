"""Per-template change log (#112).

An append-only, human-readable audit of what changed in a server template and
when. The template create/update handlers call record_* to add lines; the
change-log view reads them back newest-first. Nothing here (or in the API)
edits or removes an individual line — a log is only ever wiped wholesale when
its template is deleted (delete_for_template), so the user can't tamper with it.

A "snapshot" is the minimal picture of a template we diff:
    {"name", "description", "scenario_name", "config": <rendered config dict>}
The rendered config already carries every setting plus game.mods and
game.scenarioId, so one comparison of two snapshots covers mods, the scenario
and all settings; name/description live outside config and are compared directly.
"""
import os
from datetime import UTC
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from models import Template, TemplateChange

# config paths handled on their own, so they're skipped by the generic settings
# diff (mods as add/remove lines, the scenario as its own line).
_SKIP_PATHS = {("game", "mods"), ("game", "scenarioId")}


def snapshot(template: Template) -> dict:
    """The diffable picture of a template row."""
    import json

    try:
        config = json.loads(template.config_json or "{}")
    except (ValueError, TypeError):
        config = {}
    return {
        "name": template.name,
        "description": template.description or "",
        "scenario_name": template.scenario_name or "",
        "config": config if isinstance(config, dict) else {},
    }


def _game(snap: dict) -> dict:
    game = snap.get("config", {}).get("game", {})
    return game if isinstance(game, dict) else {}


def _mod_label(mod: dict) -> str:
    mid = mod.get("modId") or ""
    name = mod.get("name") or mid or "?"
    return f"'{name}' ({mid})" if mid and name != mid else f"'{name}'"


def _scenario_label(snap: dict) -> str:
    return snap.get("scenario_name") or _game(snap).get("scenarioId", "") or "(none)"


def _is_secret(path: str) -> bool:
    return "password" in path.lower()


def _fmt(value) -> str:
    """A short, printable rendering of a setting value for the log line."""
    import json

    text = value if isinstance(value, str) else json.dumps(value)
    text = str(text)
    return text if len(text) <= 60 else text[:57] + "…"


def _flatten(obj: dict, prefix: tuple = ()) -> dict:
    """config dict -> {"dotted.path": scalar_or_list}, minus the special paths."""
    out: dict = {}
    for key, value in obj.items():
        path = prefix + (key,)
        if path in _SKIP_PATHS:
            continue
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[".".join(path)] = value
    return out


def _mod_changes(old_mods, new_mods) -> list[tuple[str, str]]:
    old = {m.get("modId"): m for m in old_mods if isinstance(m, dict)}
    new = {m.get("modId"): m for m in new_mods if isinstance(m, dict)}
    items: list[tuple[str, str]] = []
    for mid in sorted(new.keys() - old.keys(), key=lambda x: x or ""):
        items.append(("mod", f"Added mod {_mod_label(new[mid])}"))
    for mid in sorted(old.keys() - new.keys(), key=lambda x: x or ""):
        items.append(("mod", f"Removed mod {_mod_label(old[mid])}"))
    for mid in sorted(old.keys() & new.keys(), key=lambda x: x or ""):
        ov, nv = old[mid].get("version"), new[mid].get("version")
        if ov != nv:
            items.append((
                "mod",
                f"Mod {_mod_label(new[mid])} version {ov or 'latest'} → {nv or 'latest'}",
            ))
    return items


def _setting_changes(old_cfg: dict, new_cfg: dict) -> list[tuple[str, str]]:
    old = _flatten(old_cfg)
    new = _flatten(new_cfg)
    items: list[tuple[str, str]] = []
    for path in sorted(new.keys() - old.keys()):
        val = "changed" if _is_secret(path) else f"set to {_fmt(new[path])}"
        items.append(("setting", f"{path} {val}"))
    for path in sorted(old.keys() - new.keys()):
        items.append(("setting", f"{path} removed"))
    for path in sorted(old.keys() & new.keys()):
        if old[path] != new[path]:
            if _is_secret(path):
                items.append(("setting", f"{path} changed"))
            else:
                items.append(("setting", f"{path}: {_fmt(old[path])} → {_fmt(new[path])}"))
    return items


def diff(old: dict, new: dict) -> list[tuple[str, str]]:
    """Human-readable (category, summary) lines turning `old` into `new`."""
    items: list[tuple[str, str]] = []
    if old.get("name") and new.get("name") and old["name"] != new["name"]:
        items.append(("meta", f"Renamed from '{old['name']}' to '{new['name']}'"))
    if (old.get("description") or "") != (new.get("description") or ""):
        items.append(("meta", "Description updated"))

    old_sid = _game(old).get("scenarioId", "")
    new_sid = _game(new).get("scenarioId", "")
    if old_sid != new_sid:
        if new_sid:
            items.append(("scenario", f"Scenario changed to {_scenario_label(new)}"))
        else:
            items.append(("scenario", "Scenario cleared"))

    items += _mod_changes(_game(old).get("mods", []), _game(new).get("mods", []))
    items += _setting_changes(old.get("config", {}), new.get("config", {}))
    return items


def _manager_tz():
    """The manager's configured timezone from the TZ env (e.g. Europe/Stockholm),
    or None to fall back to the process's local timezone when TZ is unset."""
    name = (os.environ.get("TZ") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
            pass
    return None


def format_local(dt) -> str:
    """A UTC datetime rendered in the manager's timezone, 24-hour and
    yyyy-mm-dd — the European/ISO standard the change log uses (#112). The zone
    abbreviation is appended so a log read from another region is unambiguous.
    Falls back to the process's local zone (UTC in a bare container) when TZ
    isn't set — the format stays European either way.
    """
    tz = _manager_tz()
    local = dt.astimezone(tz) if tz else dt.astimezone()
    return f"{local.strftime('%Y-%m-%d %H:%M:%S')} {local.strftime('%Z')}".strip()


def _append(session: Session, template_id: int, items, when) -> None:
    for category, summary in items:
        session.add(TemplateChange(
            template_id=template_id, changed_at=when, category=category, summary=summary
        ))


def record_creation(session: Session, template: Template) -> None:
    """Seed the log when a template is created: the create event, its scenario
    and its initial mods (default settings are omitted — they'd be pure noise)."""
    snap = snapshot(template)
    items: list[tuple[str, str]] = [("meta", "Template created")]
    if _game(snap).get("scenarioId"):
        items.append(("scenario", f"Scenario set to {_scenario_label(snap)}"))
    for mod in _game(snap).get("mods", []):
        if isinstance(mod, dict):
            items.append(("mod", f"Added mod {_mod_label(mod)}"))
    _append(session, template.id, items, template.created_at)


def record_update(session: Session, template_id: int, old: dict, new: dict, when) -> list:
    """Record what an edit changed. Nothing is written when nothing changed."""
    items = diff(old, new)
    _append(session, template_id, items, when)
    return items


def entries(session: Session, template_id: int, query: str | None = None) -> list[dict]:
    """All log lines for a template, newest event first, oldest-within-event
    order preserved. `query` filters by case-insensitive substring."""
    rows = list(session.exec(
        select(TemplateChange).where(TemplateChange.template_id == template_id)
    ).all())
    # Stable: order within a save event (ascending id), then newest event first.
    rows.sort(key=lambda r: r.id or 0)
    rows.sort(key=lambda r: r.changed_at, reverse=True)
    if query:
        needle = query.strip().lower()
        rows = [r for r in rows if needle in r.summary.lower()]
    out = []
    for r in rows:
        # SQLite hands datetimes back naive; they are UTC.
        aware = r.changed_at if r.changed_at.tzinfo else r.changed_at.replace(tzinfo=UTC)
        out.append({
            "id": r.id,
            "changed_at": aware.isoformat(),          # machine-readable, grouping key
            "display": format_local(aware),           # manager-timezone, for the UI (#112)
            "category": r.category,
            "summary": r.summary,
        })
    return out


def delete_for_template(session: Session, template_id: int) -> None:
    """Remove a template's whole log — only ever called when the template goes."""
    for row in session.exec(
        select(TemplateChange).where(TemplateChange.template_id == template_id)
    ).all():
        session.delete(row)
