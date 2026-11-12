"""Leave the game servers running while the manager itself is replaced.

A manager that shuts down normally stops and removes every game server
container (#113): `docker compose down` cannot remove the stack's network while
a server is still attached to it. But `rsm update` only swaps the manager for a
newer one. A manager update never changes the game build or a server's config,
and the new manager takes over the servers it finds running, so stopping them
just disconnected every player for minutes.

Worse, whether it happened was down to timing: `docker compose up -d` recreates
the Docker gate in the same breath, and when the gate went first the old
manager's stop requests never arrived (seen on a real host, Docker 29).

So `rsm update` asks first (`manage.py keep-servers`), and the shutdown honours
that once. The request is a file in the manager's data folder, holding when it
was made: one older than MAX_AGE_SECONDS is ignored, and every manager start
removes it, so a request that was never followed by a restart cannot keep a
later `docker compose down` from stopping the servers.
"""
import logging
import time
from pathlib import Path

import config

logger = logging.getLogger("manager.handover")

MARKER_NAME = ".keep-servers"
MAX_AGE_SECONDS = 600


def _marker() -> Path:
    return Path(config.settings.data_dir) / MARKER_NAME


def request() -> None:
    """Ask the next shutdown of this manager to leave the game servers running."""
    _marker().write_text(str(time.time()), encoding="utf-8")


def clear() -> None:
    """Forget any request: called at every manager start."""
    _marker().unlink(missing_ok=True)


def take(now: float | None = None) -> bool:
    """True (once) when a fresh request says to leave the servers running."""
    marker = _marker()
    try:
        made = float(marker.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        made = 0.0
    marker.unlink(missing_ok=True)
    fresh = 0 <= (now or time.time()) - made <= MAX_AGE_SECONDS
    if made and not fresh:
        logger.info("Ignoring an old request to leave the game servers running")
    return bool(made) and fresh
