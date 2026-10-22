"""Which version of each mod a server runs, and when that changes (#246).

A mod that follows the latest Workshop release used to reach config.json with no
"version" at all, so whatever the server did on start decided what players got.
A crash restart in the middle of an evening could change mods under them.

Now every start names an exact version for each of the template's mods:

- **freeze** (every ordinary start: manual, crash recovery, the server-files
  auto-restart, a scheduled restart with mod updates off): the version already
  on disk, so nothing changes;
- **update** (a scheduled restart with mod updates on, Force update, Redownload
  and bake): the Workshop's current release, so the server fetches it.

A version locked in the template always wins, and a mod not on disk yet has
nothing to freeze, so it downloads the latest as before. Because config.json
always carries exact versions, a restart Docker does by itself (its restart
policy reuses the last config.json) cannot pull a new version either.

The version on disk is read from the server's own bookkeeping: each mod folder
(`addons/<Name>_<ID>`) holds a ServerData.json whose `revision.version` is the
release it downloaded (confirmed on a real host, #246).
"""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.workshop_service import WorkshopError, normalize_asset_id, workshop

logger = logging.getLogger("manager.mod_versions")

MODE_FREEZE = "freeze"
MODE_UPDATE = "update"

# A folder named after a Workshop id, bare or behind the mod's name (as in #235).
_MOD_DIR_RE = re.compile(r"(?:^|_)([0-9A-F]{16})$", re.IGNORECASE)
# addon.gproj: `Dependencies { "58D0FB3206B6F859" ... }`
_GPROJ_DEPS_RE = re.compile(r"Dependencies\s*\{([^}]*)\}", re.IGNORECASE)
_HEX_ID_RE = re.compile(r"[0-9A-F]{16}", re.IGNORECASE)

# Workshop pages fetched at once when looking up the latest releases.
_LOOKUP_WORKERS = 8


def addon_roots(instance_dir: Path) -> list[Path]:
    """Where a server keeps its downloaded mods: the Workshop folder's addons dir,
    plus the copy some builds keep inside the profile."""
    return [instance_dir / "workshop" / "addons", instance_dir / "profile" / "addons"]


def mod_dirs(roots: list[Path]) -> dict[str, list[Path]]:
    """Workshop id -> the folder(s) holding that mod under the given roots."""
    found: dict[str, list[Path]] = {}
    for root in roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            m = _MOD_DIR_RE.search(entry.name)
            if m and entry.is_dir():
                found.setdefault(m.group(1).upper(), []).append(entry)
    return found


def _server_data_version(folder: Path) -> str | None:
    try:
        data = json.loads((folder / "ServerData.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    revision = data.get("revision") if isinstance(data, dict) else None
    version = revision.get("version") if isinstance(revision, dict) else None
    return version if isinstance(version, str) and version else None


def installed_versions(roots: list[Path]) -> dict[str, str]:
    """Workshop id -> the version on disk, for every mod folder that records one."""
    out: dict[str, str] = {}
    for mid, folders in mod_dirs(roots).items():
        for folder in folders:
            version = _server_data_version(folder)
            if version:
                out[mid] = version
                break
    return out


def _gproj_dependencies(folder: Path) -> list[str]:
    try:
        text = (folder / "addon.gproj").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    block = _GPROJ_DEPS_RE.search(text)
    return [i.upper() for i in _HEX_ID_RE.findall(block.group(1))] if block else []


def with_installed_dependencies(ids: list[str], roots: list[Path]) -> set[str]:
    """The given mods plus everything they depend on, as far as the disk tells.

    Read from each downloaded mod's addon.gproj, so it needs no network and
    covers exactly what the server downloaded for them.
    """
    dirs = mod_dirs(roots)
    seen: set[str] = set()
    queue = [i.upper() for i in ids]
    while queue:
        mid = queue.pop()
        if mid in seen:
            continue
        seen.add(mid)
        for folder in dirs.get(mid, []):
            queue.extend(d for d in _gproj_dependencies(folder) if d not in seen)
    return seen


def latest_versions(ids: list[str]) -> dict[str, str | None]:
    """Workshop id -> its current release, or None where the Workshop did not say.

    Pages are cached by the Workshop service for ten minutes, so a status view
    followed by an update does not fetch everything twice.
    """
    def one(mid: str) -> tuple[str, str | None]:
        try:
            asset = workshop.get_asset(mid)
        except WorkshopError:
            return mid, None
        version = asset.get("version") or next(iter(asset.get("versions") or []), None)
        return mid, version or None

    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=min(_LOOKUP_WORKERS, len(ids))) as pool:
        return dict(pool.map(one, ids))


def config_mods(cfg: dict) -> list[dict]:
    """The mod entries of a rendered config.json (game.mods), dicts only."""
    mods = (cfg.get("game") or {}).get("mods") if isinstance(cfg, dict) else None
    return [m for m in mods or [] if isinstance(m, dict)]


def config_mod_ids(cfg: dict, unlocked: bool = False) -> list[str]:
    """The Workshop ids in a rendered config, in order; with `unlocked`, only
    the ones the template does not lock to a version."""
    out: list[str] = []
    for m in config_mods(cfg):
        if unlocked and m.get("version"):
            continue
        mid = normalize_asset_id(str(m.get("modId") or ""))
        if mid and mid not in out:
            out.append(mid)
    return out


def pin_versions(cfg: dict, mode: str, installed: dict[str, str],
                 latest: dict[str, str | None] | None = None) -> dict:
    """Write an exact version into every unlocked mod of a rendered config.

    Edits `cfg` in place and returns what happened, per mod id:
    {"locked": [...], "frozen": [...], "updated": [(id, old, new)], "latest": [...],
     "unpinned": [...]} where "latest" are mods not on disk yet that the update
    pinned to the Workshop release, and "unpinned" are mods left without a
    version (not on disk and no Workshop answer): those download the latest.
    """
    latest = latest or {}
    report = {"locked": [], "frozen": [], "updated": [], "latest": [], "unpinned": []}
    for m in config_mods(cfg):
        mid = normalize_asset_id(str(m.get("modId") or ""))
        if not mid:
            continue
        if m.get("version"):
            report["locked"].append(mid)
            continue
        have = installed.get(mid)
        want = latest.get(mid) if mode == MODE_UPDATE else None
        if want:
            m["version"] = want
            if have and have != want:
                report["updated"].append((mid, have, want))
            elif have:
                report["frozen"].append(mid)
            else:
                report["latest"].append(mid)
        elif have:
            # Freezing, or an update the Workshop gave no answer for: keep what
            # is on disk rather than let the server pick something on its own.
            m["version"] = have
            report["frozen"].append(mid)
        else:
            report["unpinned"].append(mid)
    return report


def summarise(report: dict) -> str:
    """One log line's worth of a pin_versions report."""
    parts = []
    if report["updated"]:
        changes = ", ".join(f"{mid} {old} -> {new}" for mid, old, new in report["updated"][:5])
        more = len(report["updated"]) - 5
        parts.append(f"{len(report['updated'])} updated ({changes}{f', +{more} more' if more > 0 else ''})")
    for key, label in (("frozen", "kept"), ("locked", "locked"),
                       ("latest", "new"), ("unpinned", "left to the server")):
        if report[key]:
            parts.append(f"{len(report[key])} {label}")
    return ", ".join(parts) or "no mods"


def status(cfg: dict, roots: list[Path], names: dict[str, str] | None = None) -> dict:
    """The template's mods with the version on disk and the Workshop's latest.

    `state` per mod: locked (the template fixes a version), current, behind,
    missing (not downloaded yet), unknown (the Workshop gave no answer).
    """
    installed = installed_versions(roots)
    entries = []
    for m in config_mods(cfg):
        mid = normalize_asset_id(str(m.get("modId") or ""))
        if mid and all(e["modId"] != mid for e in entries):
            entries.append({"modId": mid, "name": m.get("name") or (names or {}).get(mid),
                            "locked": m.get("version") or None})
    latest = latest_versions([e["modId"] for e in entries])
    behind = 0
    for e in entries:
        e["installed"] = installed.get(e["modId"])
        e["latest"] = latest.get(e["modId"])
        if e["locked"]:
            e["state"] = "locked"
        elif not e["installed"]:
            e["state"] = "missing"
        elif not e["latest"]:
            e["state"] = "unknown"
        elif e["installed"] != e["latest"]:
            e["state"] = "behind"
            behind += 1
        else:
            e["state"] = "current"
    return {"mods": entries, "updates_available": behind}
