"""Recent host-wide totals behind the Servers overview's sparklines (#189).

The background monitor records one point a minute from the same summary the page
polls, and keeps the last hour. It lives in memory only: a manager restart starts
the lines over, which is fine for "what has the last hour looked like" and not
worth a table.

A point's CPU and memory come from the instance CPU sampler, which refreshes in the
background (see instance_service.cpu_mem_for). With nobody watching, a point can
therefore carry a reading up to a minute old; with the page open it is seconds.
"""
import threading
import time
from collections import deque

HISTORY_POINTS = 60
# How often the monitor records a point: every Nth pass of its 15-second loop.
SAMPLE_EVERY_TICKS = 4

_points: deque = deque(maxlen=HISTORY_POINTS)
_lock = threading.Lock()


def record(summary: dict, now: float | None = None) -> None:
    """Append one point built from an instances_summary() result."""
    point = {
        "t": int(now if now is not None else time.time()),
        "running": summary.get("running", 0),
        "players": summary.get("players_total", 0),
        "cpu_percent": summary.get("cpu_percent"),
        "mem_bytes": summary.get("mem_bytes"),
    }
    with _lock:
        _points.append(point)


def points() -> list[dict]:
    """Oldest first."""
    with _lock:
        return list(_points)


def clear() -> None:
    with _lock:
        _points.clear()
