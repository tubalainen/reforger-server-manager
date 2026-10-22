"""How far a starting server has got with downloading its mods (#235).

A fresh server spends minutes downloading the template's Workshop mods before it
logs anything worth reading, so its console looks stuck. The server does not
report that progress itself, but the download lands in the instance's addons
folder, so it can be watched from outside: the bytes on disk so far, against
what the Workshop says the template's mods and their dependencies weigh.

Two halves, both in memory:

- the expected total, resolved from the Workshop in a background thread and
  cached per mod list, so the 5-second stats poll never waits on the network;
- a short history of the bytes seen per container, for the speed and for
  whether the download is still going.

The Workshop's sizes are the published package sizes. What lands on disk need
not match them byte for byte, so callers cap the percentage and treat "stopped
growing" as the end of the download rather than trusting 100 %.

The addons folder outlives a template change: the mods of the template a server
ran before stay on disk. Each mod sits in its own folder named after its Workshop
id ("591AF5BDA9F7CE8B", or "Name_591AF5BDA9F7CE8B"), so once the total is known
only the folders of the mods it covers are counted. Without that, a server
switched to a smaller template showed "28.84 GB of 10.47 GB" (#235).

Comparing everything on disk with the Workshop total only works from an empty
folder. A start that updates mods (#246) begins with nearly all of it on disk,
and the mods take more room on disk than the Workshop says, so the bar read
"20.45 GB of 19.28 GB · 100 %" while it was still downloading. So each run gets
a plan, taken once the sizes are known: the mods missing from disk, or on disk
at another version than config.json pins. Only those count. One is done once
its folder holds the pinned version; a new one's folder counts as it fills,
capped at its size. A mod being updated is rewritten in place (seen on a real
host: no folder keeps two versions), so it counts nothing until it is done.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from services import mod_versions
from services.workshop_service import WorkshopError, normalize_asset_id, workshop

logger = logging.getLogger("manager.mod_download")

EXPECTED_READY = "ready"          # total known (possibly short a few mods)
EXPECTED_RESOLVING = "resolving"  # lookup still running
EXPECTED_FAILED = "failed"        # Workshop unreachable for every mod
EXPECTED_NONE = "none"            # the template has no mods

# A resolved total is kept for an hour; a failed lookup is retried sooner.
_READY_TTL = 3600.0
_FAILED_TTL = 300.0

# The download counts as still going if its size went up within this window.
GROWING_WINDOW = 30.0
# Samples older than this are dropped; a container not sampled for _FORGET_AFTER
# is forgotten altogether, so the history cannot grow without bound.
_SAMPLE_WINDOW = 60.0
_FORGET_AFTER = 600.0

_lock = threading.Lock()
_expected: dict[tuple, tuple[float, dict]] = {}
_resolving: set[tuple] = set()
_samples: dict[str, list[tuple[float, int]]] = {}
# run key -> {"seen": monotonic, "mods": {modId: {"size", "kind", "want"}}}
_plans: dict[str, dict] = {}

# A folder named after a Workshop id, bare or behind the mod's name.
_MOD_DIR_RE = re.compile(r"(?:^|_)([0-9A-F]{16})$", re.IGNORECASE)


def mod_ids(config_json: str | None) -> list[str]:
    """The Workshop ids in a template's config.json game.mods, in order."""
    try:
        mods = (json.loads(config_json or "{}").get("game") or {}).get("mods") or []
    except (ValueError, TypeError, AttributeError):
        return []
    out: list[str] = []
    for m in mods:
        mid = normalize_asset_id(m.get("modId") or "") if isinstance(m, dict) else None
        if mid and mid not in out:
            out.append(mid)
    return out


def _resolve(ids: tuple) -> dict:
    """Sum the sizes of the given mods and everything they depend on, once each."""
    sizes: dict[str, int] = {}
    missing: set[str] = set()
    resolved_any = False
    for mid in ids:
        try:
            res = workshop.resolve_dependencies(mid)
        except WorkshopError:
            missing.add(mid)
            continue
        resolved_any = True
        for m in res.get("mods") or []:
            if m.get("modId"):
                sizes.setdefault(m["modId"], int(m.get("size") or 0))
        missing.update(res.get("missing") or [])
    missing -= sizes.keys()
    if not resolved_any:
        return {"state": EXPECTED_FAILED, "bytes": None, "mods": 0, "missing": len(missing)}
    return {
        "state": EXPECTED_READY,
        "bytes": sum(sizes.values()),
        "mods": len(sizes),
        "missing": len(missing),
        # Which mod folders the download is made of. The ones the Workshop could
        # not size are still downloaded by the server, so they count too.
        "ids": frozenset(i.upper() for i in (*ids, *sizes, *missing)),
        # Each mod's own size, for the per-run plan (#246).
        "sizes": {i.upper(): s for i, s in sizes.items()},
    }


def _resolve_in_background(ids: tuple) -> None:
    try:
        result = _resolve(ids)
    except Exception:  # noqa: BLE001 — never let a scrape bug kill the thread silently
        logger.exception("Resolving the mod download size failed")
        result = {"state": EXPECTED_FAILED, "bytes": None, "mods": 0, "missing": 0}
    with _lock:
        _expected[ids] = (time.monotonic(), result)
        _resolving.discard(ids)


def expected_total(ids: list[str]) -> dict:
    """What the given mods should weigh: {state, bytes, mods, missing}.

    Never blocks: the first call for a mod list starts the Workshop lookup and
    answers "resolving"; later calls answer from the cache.
    """
    if not ids:
        return {"state": EXPECTED_NONE, "bytes": None, "mods": 0, "missing": 0}
    key = tuple(sorted(ids))
    now = time.monotonic()
    with _lock:
        cached = _expected.get(key)
        if cached:
            at, result = cached
            ttl = _READY_TTL if result["state"] == EXPECTED_READY else _FAILED_TTL
            if now - at < ttl:
                return result
        start = key not in _resolving
        if start:
            _resolving.add(key)
    if start:
        # Outside the lock: the thread takes it to store its result.
        threading.Thread(
            target=_resolve_in_background, args=(key,),
            name="mod-download-size", daemon=True,
        ).start()
    # A stale total is still better than none while it is refreshed.
    if cached and cached[1]["state"] == EXPECTED_READY:
        return cached[1]
    return {"state": EXPECTED_RESOLVING, "bytes": None, "mods": 0, "missing": 0}


def record(key: str, downloaded: int, now: float | None = None) -> dict:
    """Note the bytes on disk for one run; returns {rate_bps, growing}.

    `growing` is None until there are two samples to compare.

    `key` is the container id, so a recreated container starts a fresh history.
    """
    now = time.monotonic() if now is None else now
    with _lock:
        for k in [k for k, s in _samples.items() if s and now - s[-1][0] > _FORGET_AFTER]:
            del _samples[k]
        history = [s for s in _samples.get(key, []) if now - s[0] <= _SAMPLE_WINDOW]
        history.append((now, downloaded))
        _samples[key] = history
    recent = [s for s in history if now - s[0] <= GROWING_WINDOW]
    first = recent[0]
    # One sample cannot tell: None, not False, so the first poll of a server that
    # is mid-download does not claim it has finished.
    growing = (
        any(b > a for (_, a), (_, b) in zip(recent, recent[1:], strict=False))
        if len(recent) > 1 else None
    )
    elapsed = now - first[0]
    rate = (downloaded - first[1]) / elapsed if elapsed > 0 else 0.0
    return {"rate_bps": max(0, int(rate)), "growing": growing}


def downloaded_bytes(paths: list[Path], wanted: frozenset | None) -> int:
    """The bytes under the addons folder(s) that belong to this download.

    A folder named after a mod counts only when its id is in `wanted`; anything
    else (files outside a mod folder, a download's scratch space) always
    counts. With `wanted` None, everything counts: that is while the total is
    still unknown, when the dependencies are too, and leaving one out would make
    the download look finished while it is still fetching it.
    """
    total = 0
    for root in paths:
        for dirpath, dirnames, filenames in os.walk(root):
            keep = []
            for name in dirnames:
                m = _MOD_DIR_RE.search(name)
                if m is None:
                    keep.append(name)
                elif wanted is None or m.group(1).upper() in wanted:
                    total += _tree_bytes(os.path.join(dirpath, name))
            dirnames[:] = keep
            total += sum(_file_bytes(os.path.join(dirpath, f)) for f in filenames)
    return total


def _tree_bytes(path: str) -> int:
    return sum(
        _file_bytes(os.path.join(dirpath, f))
        for dirpath, _dirs, files in os.walk(path)
        for f in files
    )


def _file_bytes(path: str) -> int:
    try:
        return os.stat(path, follow_symlinks=False).st_size
    except OSError:
        return 0


def _make_plan(expected: dict, roots: list[Path], pinned: dict[str, str]) -> dict:
    """What this run has to fetch: missing mods, and mods at another version."""
    dirs = mod_versions.mod_dirs(roots)
    installed = mod_versions.installed_versions(roots)
    mods = {}
    for mid in expected["ids"]:
        want = pinned.get(mid)
        # A folder with no readable version is a download cut short: still to come.
        if mid not in dirs or mid not in installed:
            kind = "new"
        elif want and installed.get(mid) != want:
            kind = "update"
        else:
            continue
        mods[mid] = {"size": int(expected.get("sizes", {}).get(mid) or 0),
                     "kind": kind, "want": want}
    return mods


def _plan_progress(mods: dict, roots: list[Path], names: dict[str, str]) -> dict:
    dirs = mod_versions.mod_dirs(roots)
    installed = mod_versions.installed_versions(roots)
    done = got = 0
    for mid, m in mods.items():
        have = installed.get(mid)
        if have and (m["want"] is None or have == m["want"]):
            done += 1
            got += m["size"]
        elif m["kind"] == "new" and mid in dirs:
            got += min(m["size"], sum(_tree_bytes(str(d)) for d in dirs[mid]))
    updating = [mid for mid, m in mods.items() if m["kind"] == "update"]
    return {
        "plan_mods": len(mods),
        "plan_done": done,
        "plan_bytes": sum(m["size"] for m in mods.values()),
        "plan_done_bytes": got,
        # "update" when every mod to fetch is already on disk at another version
        "plan_kind": "update" if mods and len(updating) == len(mods) else "download",
        "plan_names": [names.get(mid) or mid for mid in sorted(mods)][:3],
    }


def progress(key: str, paths: list[Path], config_json: str | None,
             roots: list[Path] | None = None, pinned: dict[str, str] | None = None,
             names: dict[str, str] | None = None) -> dict:
    """The `mod_download` block of a starting server's stats.

    `key` names one run of a container (id and start time). With `roots` and
    `pinned` (the versions this run's config.json names), the block also carries
    the run's plan: what it actually has to fetch (#246).
    """
    expected = expected_total(mod_ids(config_json))
    wanted = expected.get("ids")
    downloaded = downloaded_bytes(paths, wanted)
    # Counting fewer folders once the total arrives is a drop, not a download
    # that went backwards: a fresh history, so the speed is not skewed by it.
    history = f"{key}/{'mods' if wanted is not None else 'all'}"
    out = {
        "downloaded_bytes": downloaded,
        "expected_bytes": expected["bytes"],
        "expected_state": expected["state"],
        "mods": expected["mods"],
        "missing": expected["missing"],
        **record(history, downloaded),
    }
    if roots is None or pinned is None or expected["state"] != EXPECTED_READY:
        return out
    now = time.monotonic()
    with _lock:
        for k in [k for k, p in _plans.items() if now - p["seen"] > _FORGET_AFTER]:
            del _plans[k]
        plan = _plans.get(key)
    if plan is None:
        # Taken once per run: a mod that finishes must not drop out of the total.
        plan = {"mods": _make_plan(expected, roots, pinned)}
    plan["seen"] = now
    with _lock:
        _plans[key] = plan
    return {**out, **_plan_progress(plan["mods"], roots, names or {})}


def _reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _expected.clear()
        _resolving.clear()
        _samples.clear()
        _plans.clear()
