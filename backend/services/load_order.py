"""The order the engine actually mounted a server's mods in, read from its log (#257).

Whether the server follows the order of `mods` in config.json had never been
checked. The engine says what it did in the server's console.log: after its last
`Addon dirs:` block it adds each mod to its file system,

    ENGINE : FileSystem: Adding package '/reforger/workshop/addons/COE2_60926835F4A7B0CA/'
             (pak count: 1) to filesystem under name COE2

one line per mod in mount order, the base game ('./addons/data/', './addons/core/')
last. (The `Available addons:` block before it lists every folder on disk, in
directory order: not a load order.) In one public log compared with its config,
the mounts were config.json reversed, except that each mod came ahead of the
mods it depends on — the engine appeared to settle dependency order itself. This
module shows that order for any server, so its admin need not take it on trust.
"""
import re
from pathlib import Path

# The mount block sits near the top of a session's console.log, after any mod
# download; a session runs on for days, so only the start of the file is read.
READ_BYTES = 4 * 1024 * 1024

_ADDON_DIRS = "Addon dirs:"
_PACKAGE = re.compile(r"FileSystem: Adding package '([^']*)'[^\n]*?under name (\S+)")
_MOD_ID = re.compile(r"_([0-9A-Fa-f]{16})/*$")


def latest_console_log(profile: Path) -> Path | None:
    """The newest session's console.log under a server's profile, or None."""
    try:
        logs = [p for p in (profile / "logs").glob("logs_*/console.log") if p.is_file()]
    except OSError:
        return None
    return max(logs, key=lambda p: p.stat().st_mtime, default=None)


def read_head(path: Path, limit: int = READ_BYTES) -> str:
    with path.open("rb") as f:
        return f.read(limit).decode("utf-8-sig", errors="replace")


def parse(text: str) -> list[dict]:
    """[{name, modId}] in mount order; modId None for the base game's packages.

    Taken after the LAST `Addon dirs:`: a server that downloads mods first
    lists its dirs twice, and only the second time does it mount anything.
    """
    start = text.rfind(_ADDON_DIRS)
    if start < 0:
        return []
    out = []
    for m in _PACKAGE.finditer(text, start):
        found = _MOD_ID.search(m.group(1))
        out.append({"name": m.group(2), "modId": found.group(1).upper() if found else None})
    return out


def compare(mounted: list[dict], listed: list[str]) -> dict:
    """How the mount order relates to the order of the mods in config.json.

    `relation`: same | reversed | reordered | unknown. Only mods both mounted and
    listed are compared; `inverted` counts the pairs of them mounted the other
    way round from the list (all pairs inverted = exactly reversed).
    """
    position = {mid: i for i, mid in enumerate(listed)}
    both = [m["modId"] for m in mounted if m["modId"] in position]
    pairs = len(both) * (len(both) - 1) // 2
    inverted = sum(1 for i, a in enumerate(both) for b in both[i + 1:]
                   if position[a] > position[b])
    if len(both) < 2:
        relation = "unknown"
    elif inverted == 0:
        relation = "same"
    elif inverted == pairs:
        relation = "reversed"
    else:
        relation = "reordered"
    rows = []
    for i, m in enumerate(mounted):
        rows.append({
            "position": i + 1,
            "name": m["name"],
            "modId": m["modId"],
            "base_game": m["modId"] is None,
            # where the mod sits in config.json (1-based), or None: not listed
            "listed_at": position[m["modId"]] + 1 if m["modId"] in position else None,
        })
    return {
        "mounted": rows,
        "relation": relation,
        "pairs": pairs,
        "inverted": inverted,
        "unlisted": sum(1 for r in rows if not r["base_game"] and r["listed_at"] is None),
        "not_mounted": [mid for mid in listed if mid not in {m["modId"] for m in mounted}],
    }
