"""Stop bringing back a server that never gets as far as coming online.

A server with auto-start or restart-on-crash is started again whenever it goes
down: by Docker's restart policy, and by the manager's monitor behind it. That
is right for a crash, and wrong for a server that cannot start at all (a mod
that does not compile, a missing dependency, a config the engine refuses): it
came back up every few seconds for ever, with nothing to tell the two apart.

This keeps count. A *run* is one start of the container (its Docker StartedAt).
A run that ends without the server ever reporting itself online is a failed
start; one that did come online resets the count. After MAX_FAILED_STARTS in a
row the monitor stops the server and leaves it stopped, with a note the GUI
shows, until someone starts it again.

Only the bookkeeping lives here, so it can be tested without Docker. It is kept
in memory: after a manager restart the count begins again, and a server that
was given up on is simply stopped.
"""
from collections.abc import Callable
from datetime import UTC, datetime

# Failed starts in a row before the monitor stops trying.
MAX_FAILED_STARTS = 3

# instance id -> the run last seen: {started, restarts, online, counted, failed}
_runs: dict[int, dict] = {}
# instance id -> {"attempts": n, "at": iso time}, while it is left stopped
_given_up: dict[int, dict] = {}


def observe(instance_id: int, started_at: str, restarts: int, running: bool,
            is_online: Callable[[], bool]) -> int:
    """Record what the monitor sees of a server; return its failed starts in a row.

    `started_at` names the run, `restarts` is Docker's RestartCount (several
    restarts can pass between two looks), `running` whether the container is up
    right now, and `is_online` is asked only while a run has not yet been seen
    online.
    """
    rec = _runs.get(instance_id)
    if rec is None or rec["started"] != started_at:
        failed = 0
        if rec is not None and not rec["online"]:
            failed = rec["failed"]
            if not rec["counted"]:
                # The run before this one ended without coming online, and so
                # did any Docker went through unseen in between.
                failed += max(1, restarts - rec["restarts"])
        rec = _runs[instance_id] = {
            "started": started_at, "restarts": restarts, "online": False,
            # First sight of a server that is already down (the manager just
            # started): that is not a start this process watched fail.
            "counted": rec is None and not running,
            "failed": failed,
        }
    if not rec["online"]:
        if running:
            if is_online():
                rec["online"] = True
                rec["failed"] = 0
        elif not rec["counted"]:
            rec["counted"] = True
            rec["failed"] += 1
    return rec["failed"]


def give_up(instance_id: int, attempts: int) -> None:
    _given_up[instance_id] = {"attempts": attempts, "at": datetime.now(UTC).isoformat()}


def given_up(instance_id: int) -> dict | None:
    """{attempts, at} while the server is left stopped after failed starts."""
    return _given_up.get(instance_id)


def forget_run(instance_id: int) -> None:
    """The server was stopped on purpose: its run is not a failed start."""
    _runs.pop(instance_id, None)


def clear(instance_id: int) -> None:
    """Someone started (or deleted) the server: begin counting from nothing."""
    _runs.pop(instance_id, None)
    _given_up.pop(instance_id, None)


def snapshot(instance_id: int) -> dict | None:
    """The count so far, to carry across a start the monitor itself makes."""
    rec = _runs.get(instance_id)
    return dict(rec) if rec else None


def restore(instance_id: int, kept: dict | None) -> None:
    if kept is not None:
        _runs[instance_id] = kept
