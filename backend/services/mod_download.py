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
"""
import json
import logging
import threading
import time

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


def progress(key: str, downloaded: int, config_json: str | None) -> dict:
    """The `mod_download` block of a starting server's stats."""
    expected = expected_total(mod_ids(config_json))
    return {
        "downloaded_bytes": downloaded,
        "expected_bytes": expected["bytes"],
        "expected_state": expected["state"],
        "mods": expected["mods"],
        "missing": expected["missing"],
        **record(key, downloaded),
    }


def _reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _expected.clear()
        _resolving.clear()
        _samples.clear()
