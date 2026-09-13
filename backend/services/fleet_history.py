"""Recent figures behind the sparklines on the Servers overview and server pages (#189).

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
    """Append one point built from an instances_summary() result.

    Besides the host totals, a point keeps each running server's own figures under
    its id (as a string, the way JSON will carry it), so a server's page can draw
    its own lines from the same history.
    """
    per_server = {
        str(s["id"]): {
            "players": s.get("players"),
            "server_fps": s.get("server_fps"),
            "cpu_percent": s.get("cpu_percent"),
            "mem_bytes": s.get("mem_bytes"),
        }
        for s in summary.get("servers", [])
        if s.get("status") == "running"
    }
    point = {
        "t": int(now if now is not None else time.time()),
        "running": summary.get("running", 0),
        "players": summary.get("players_total", 0),
        "cpu_percent": summary.get("cpu_percent"),
        "mem_bytes": summary.get("mem_bytes"),
        "servers": per_server,
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
