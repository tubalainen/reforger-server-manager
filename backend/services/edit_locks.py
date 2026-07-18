"""In-memory edit locks so two browsers can't edit the same template (#102).

One lock per template, held by a browser *tab* — identified by the random
X-Client-Id header every frontend request carries. Per-tab (not per-login) is
deliberate: there is only one admin account, so two tabs of the same user are
still two editors racing each other.

The registry is in-memory on purpose. Locks are ephemeral by design — the
holder heartbeats while the editor is open and the lock evaporates shortly
after the tab is gone — and a manager restart releasing every lock is the safe
direction. No table, no cleanup job: expired locks are pruned lazily on every
call, from the single asyncio event loop, so there is no locking around the
dict either.
"""
import time
from dataclasses import dataclass

# The editor heartbeats every 30s; three missed beats and the lock is stale.
# This is also the worst-case wait after a crashed/closed tab, which is why
# there is a "clear locks" button for the impatient.
LOCK_TTL_SECONDS = 90

_MAX_CLIENT_ID_LENGTH = 64


@dataclass
class _Lock:
    client_id: str
    last_seen: float


_locks: dict[int, _Lock] = {}


def _prune(now: float) -> None:
    stale = [tid for tid, lock in _locks.items() if now - lock.last_seen > LOCK_TTL_SECONDS]
    for tid in stale:
        del _locks[tid]


def valid_client_id(client_id: str) -> bool:
    """A usable lock holder id: non-empty, bounded (it's a client-chosen header)."""
    return bool(client_id) and len(client_id) <= _MAX_CLIENT_ID_LENGTH


def holder(template_id: int) -> str | None:
    """The client currently holding this template's lock, if any."""
    _prune(time.monotonic())
    lock = _locks.get(template_id)
    return lock.client_id if lock else None


def held_by_other(template_id: int, client_id: str) -> bool:
    """Is someone *else* editing this template right now?"""
    h = holder(template_id)
    return h is not None and h != client_id


def acquire(template_id: int, client_id: str) -> bool:
    """Take the lock, or renew it if this client already holds it (heartbeat).

    Returns False when another live client holds it.
    """
    now = time.monotonic()
    _prune(now)
    lock = _locks.get(template_id)
    if lock and lock.client_id != client_id:
        return False
    _locks[template_id] = _Lock(client_id, now)
    return True


def release(template_id: int, client_id: str) -> None:
    """Drop the lock if this client holds it; anyone else's release is a no-op."""
    lock = _locks.get(template_id)
    if lock and lock.client_id == client_id:
        del _locks[template_id]


def clear_all() -> int:
    """Force-release every lock (the GUI's reset button). Returns how many.

    A *live* editor re-acquires within one heartbeat — clearing only truly
    removes locks whose tabs are gone, which is exactly what the button is for.
    """
    _prune(time.monotonic())
    count = len(_locks)
    _locks.clear()
    return count
