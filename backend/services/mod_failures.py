"""Which mods made a server's last start fail, read from its console.log (#270).

After a Reforger update a mod often stops loading, and the server then fails to
start. The engine says why in its console.log, but in a long log among noise.
This module picks out the mods to blame, so the server page can name them.

Seen on a real host (1.9.0.37): a mod whose dependency is not on disk.

    ENGINE (E): Addon 'WCS_Scopes' dependency '615806DC6C57AF02' can't be added
    ENGINE (E): Addon 'EscapistsWCSFORTEXCustomize' dependency '62A668F513428630' can't be added
    ENGINE (E): Cannot initialize game project settings!
    ENGINE (E): Cannot create game!
    ENGINE (E): Addon loading failed {…every mod id…}
    …                       (the engine falls back to the base game, then)
    ENGINE (E): Unable to initialize the game

The second line follows from the first: 62A668F513428630 is WCS_Scopes itself.
A session boots more than once (a mods-free pass for the download, the mounts,
and that fallback), so the WHOLE session is read, not the part after the last
`Addon dirs:` the way load_order reads the mounts.

Not seen in a real log yet: a mod whose scripts don't compile against the new
game version. The forum and wiki speak of `Can't compile "Game" script module!`
with `SCRIPT (E)` lines. Those lines are collected as they are, from the script
compile only, with no guess at which mod they belong to.

Not counted, because they are noise on a healthy start too: `RESOURCES (E)`
lines (the base game's own Rally missions print `Wrong GUID/name` errors) and the
`Resource leaks` report at shutdown.
"""
import re

_DEPENDENCY = re.compile(r"Addon '([^']+)' dependency '([0-9A-Fa-f]{16})' can't be added")
# `gproj: '/reforger/workshop/addons/WCS_Scopes_62A668F513428630/addon.gproj' guid: '62A6…'`
_GPROJ = re.compile(r"gproj: '([^']*)' guid: '([0-9A-Fa-f]{16})'")
_FOLDER_ID = re.compile(r"_([0-9A-Fa-f]{16})$")
_FATAL = re.compile(
    r"Addon loading failed|Cannot create game!|Unable to initialize the game|Can't compile"
)
_ONLINE = re.compile(r"Entered online game state", re.IGNORECASE)
_COMPILE_START = "Compiling Game scripts"
# Where a script compile is over: the module loaded, the game is being made, or
# it was torn down.
_COMPILE_END = re.compile(r"Module: Game; loaded|Creating game instance|Game destroyed")
_SCRIPT_ERROR = re.compile(r"SCRIPT\s*\(E\)")
_TIMESTAMP = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\s+")

FATAL_LINES = 6
SCRIPT_LINES = 40
LINE_LIMIT = 400


def _clean(line: str) -> str:
    line = _TIMESTAMP.sub("", line.rstrip("\r\n")).strip()
    return line if len(line) <= LINE_LIMIT else line[: LINE_LIMIT - 1] + "…"


def name_key(name: str) -> str:
    """A name compared the way the engine and a template may differ in writing
    it: case, spaces and punctuation left out."""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def addon_names(text: str) -> dict[str, str]:
    """Name key -> mod id, for every mod folder the log lists.

    The engine names a mod after its project, which on disk is the folder's
    name before the id (`WCS_Scopes_62A6…`) or a .gproj file not called addon
    (`TaskForceMattockWeapons_5B3E…/TFMattockWeapons.gproj`): both are keys.
    """
    out: dict[str, str] = {}
    for m in _GPROJ.finditer(text):
        path, guid = m.group(1), m.group(2).upper()
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if len(parts) < 2:
            continue
        folder, gproj = parts[-2], parts[-1]
        stem = gproj.rsplit(".", 1)[0]
        prefix = _FOLDER_ID.sub("", folder)
        for name in (prefix, stem):
            if name and name.lower() != "addon":
                out.setdefault(name_key(name), guid)
    return out


def available_ids(text: str) -> set[str]:
    """Every mod id the log lists as available, the base game's own projects
    (ArmaReforger 58D0FB3206B6F859, core, the Rally DLC) among them: those are
    never in the Workshop folder, yet a mod depending on them misses nothing."""
    return {m.group(2).upper() for m in _GPROJ.finditer(text)}


def folder_names(text: str) -> dict[str, str]:
    """Mod id -> its folder's name before the id, as the log lists it."""
    out: dict[str, str] = {}
    for m in _GPROJ.finditer(text):
        parts = [p for p in m.group(1).replace("\\", "/").split("/") if p]
        if len(parts) >= 2 and _FOLDER_ID.search(parts[-2]):
            out.setdefault(m.group(2).upper(), _FOLDER_ID.sub("", parts[-2]))
    return out


def _script_errors(lines: list[str]) -> list[str]:
    out: list[str] = []
    compiling = False
    for line in lines:
        if _COMPILE_START in line:
            compiling = True
        elif compiling and _COMPILE_END.search(line):
            compiling = False
        if "Can't compile" in line or (compiling and _SCRIPT_ERROR.search(line)):
            out.append(_clean(line))
    return out


def parse(text: str) -> dict:
    """What a session's console.log says went wrong with its mods.

    {"failed": the start failed (a fatal line, and the server never came online),
     "online": it came online,
     "dependencies": [{"name", "modId" (None: not in the log's folders),
                       "missing": [ids, in log order]}],
     "script_errors": [compile error lines], "fatal": [the first fatal lines]}
    """
    lines = text.splitlines()
    online = bool(_ONLINE.search(text))
    fatal = [_clean(line) for line in lines if _FATAL.search(line)]
    names = addon_names(text)
    by_name: dict[str, dict] = {}
    for m in _DEPENDENCY.finditer(text):
        name, dep = m.group(1), m.group(2).upper()
        entry = by_name.setdefault(name, {"name": name, "modId": names.get(name_key(name)), "missing": []})
        if dep not in entry["missing"]:
            entry["missing"].append(dep)
    return {
        "failed": bool(fatal) and not online,
        "online": online,
        "dependencies": list(by_name.values()),
        "script_errors": _script_errors(lines)[:SCRIPT_LINES],
        "fatal": fatal[:FATAL_LINES],
    }


def failing_mods(parsed: dict, on_disk: set[str], listed: dict[str, str],
                 disk_dependencies: dict[str, list[str]], names: dict[str, str],
                 projects: dict[str, str] | None = None) -> list[dict]:
    """The mods to show as failing, each with what it needs and why that fails.

    `on_disk`: mod ids with a folder. `projects`: name key -> id from each mod
    folder's .gproj `ID`, which is the name the engine logs a mod under (it can
    differ from the folder's, #270), so it is asked first. `listed`: name key ->
    id for the mods in the config.json the server started with, the last
    resort for a mod with no folder. `disk_dependencies`: id -> every dependency its .gproj
    lists, so a mod shows ALL its missing dependencies, not only the first the
    engine stopped at. `names`: id -> display name.

    Each need's state: "failed" (it is one of the failing mods), "not_installed"
    (no folder on disk) or "installed" (on disk, yet the engine couldn't add it).
    A mod whose every need failed only because another mod did is a "knock_on":
    fix the other one first.
    """
    mods = []
    for d in parsed["dependencies"]:
        key = name_key(d["name"])
        mid = (projects or {}).get(key) or d["modId"] or listed.get(key)
        mods.append({"name": d["name"], "modId": mid, "logged": list(d["missing"])})
    failed_ids = {m["modId"] for m in mods if m["modId"]}
    out = []
    for m in mods:
        needs = list(m["logged"])
        for dep in disk_dependencies.get(m["modId"] or "", []):
            if dep not in needs and dep not in on_disk:
                needs.append(dep)
        rows = []
        for dep in needs:
            state = "failed" if dep in failed_ids else (
                "installed" if dep in on_disk else "not_installed")
            rows.append({"modId": dep, "name": names.get(dep), "state": state})
        out.append({
            "name": names.get(m["modId"] or "") or m["name"],
            "log_name": m["name"],
            "modId": m["modId"],
            "needs": rows,
            "knock_on": bool(rows) and all(r["state"] == "failed" for r in rows),
        })
    # The causes first, the mods that only fail because of them after.
    out.sort(key=lambda m: m["knock_on"])
    return out


def pulled_in_by(failing: list[str], template: list[str],
                 disk_dependencies: dict[str, list[str]]) -> dict[str, list[str]]:
    """Failing mod id -> the template's mods that bring it in.

    A server fetches a mod's dependencies by itself, so a failing mod is often
    in no template at all (#270: WCS_Scopes, which a template mod needs). The
    template's mods are the ones its admin can remove, so for each failing mod
    this lists the template mods whose dependencies, followed on disk through
    each .gproj, reach it. A failing mod that is itself in the template is
    left out: it can be removed as it is.
    """
    wanted = {f for f in failing if f not in template}
    out: dict[str, list[str]] = {f: [] for f in wanted}
    if not wanted:
        return out
    for root in template:
        seen: set[str] = set()
        stack = list(disk_dependencies.get(root, []))
        while stack:
            mid = stack.pop()
            if mid in seen:
                continue
            seen.add(mid)
            stack.extend(disk_dependencies.get(mid, []))
        for f in wanted & seen:
            out[f].append(root)
    return out
