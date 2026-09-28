"""Gathering the Supervisor's overview from the observer (#204, v0.67.0).

The observer answers over a unix socket, one question at a time. This asks the
questions — the host, every container of ours, each running game server's
figures — and hands the answers to overview.build(). CPU and memory are the
slow question (Docker takes a second or two per container), so they are sampled
in the background and the last sample is used, as the manager does.
"""
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

import config
import stacks
from supervisor import overview

logger = logging.getLogger("supervisor")

OBSERVER_SOCKET = os.environ.get("RSM_OBSERVER_SOCKET", "/run/rsm-observer/observer.sock")

# Several open pages share one overview this young.
CACHE_SECONDS = 4.0
# A CPU/memory sample is refreshed once it is this old.
SAMPLE_TTL = 12.0
# The totals' sparklines: one point a minute, the last hour.
HISTORY_POINTS = 60
HISTORY_EVERY = 60.0


class ObserverError(Exception):
    """The observer could not be asked, or said no."""


class Observer:
    """The observer's few questions, over its unix socket."""

    def __init__(self, socket_path: str = OBSERVER_SOCKET, transport=None):
        self._http = httpx.Client(
            transport=transport or httpx.HTTPTransport(uds=socket_path),
            base_url="http://observer",
            timeout=httpx.Timeout(15.0, connect=3.0),
        )

    def _get(self, path: str):
        try:
            resp = self._http.get(path)
        except httpx.HTTPError as exc:
            raise ObserverError(f"the observer did not answer ({exc})") from exc
        if resp.status_code != 200:
            try:
                message = resp.json().get("message")
            except ValueError:
                message = resp.text[:200]
            raise ObserverError(f"the observer said {resp.status_code}: {message}")
        return resp.json()

    def gate(self) -> dict:
        return self._get("/_rsm/gate")

    def host(self) -> dict:
        return self._get("/_rsm/host")

    def containers(self) -> list[dict]:
        return self._get("/_rsm/containers")

    def server(self, container_id: str) -> dict:
        return self._get(f"/_rsm/containers/{container_id}/server")

    def stats(self, container_id: str) -> dict:
        return self._get(f"/_rsm/containers/{container_id}/stats")


class Collector:
    def __init__(self, observer: Observer, version: str = config.APP_VERSION):
        self.observer = observer
        self.version = version
        self._lock = threading.Lock()
        self._cached: tuple[float, dict] | None = None
        self._samples: dict[str, tuple[float, dict]] = {}
        self._sampling: set[str] = set()
        self._sample_lock = threading.Lock()
        self._history: deque = deque(maxlen=HISTORY_POINTS)
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="observer")

    # --- CPU / memory, in the background ------------------------------------
    def _sample(self, container_id: str) -> dict:
        """The last sample for a container; refreshes a stale one in the background."""
        sampled_at, sample = self._samples.get(container_id, (0.0, {}))
        if time.monotonic() - sampled_at < SAMPLE_TTL:
            return sample
        with self._sample_lock:
            if container_id in self._sampling:
                return sample
            self._sampling.add(container_id)

        def refresh():
            try:
                self._samples[container_id] = (time.monotonic(),
                                               self.observer.stats(container_id))
            except ObserverError as exc:
                logger.debug("No CPU sample for %s: %s", container_id[:12], exc)
            finally:
                with self._sample_lock:
                    self._sampling.discard(container_id)

        self._pool.submit(refresh)
        return sample

    def _forget_gone(self, live: set[str]) -> None:
        for cid in list(self._samples):
            if cid not in live:
                self._samples.pop(cid, None)

    # --- The overview ---------------------------------------------------------
    def _figures(self, container_id: str) -> dict:
        try:
            return self.observer.server(container_id)
        except ObserverError as exc:
            logger.debug("No figures for %s: %s", container_id[:12], exc)
            return {}

    def _build(self) -> dict:
        try:
            host = self.observer.host()
            containers = self.observer.containers()
        except ObserverError as exc:
            logger.warning("Cannot read the machine: %s", exc)
            data = overview.build(None, [], {}, {}, self.version)
            data["warnings"].insert(0, {
                "id": "observer-unreachable", "severity": "danger", "stack": None,
                "title": "The Supervisor cannot see Docker",
                "detail": f"Its observer ({exc}) is not answering. On the host, see "
                          "rsm supervisor status and rsm supervisor logs.",
            })
            return data
        running = [c for c in containers
                   if (c.get("state") or {}).get("running")
                   and (c.get("labels") or {}).get(stacks.LABEL_ROLE) == stacks.ROLE_INSTANCE]
        ids = [c["id"] for c in running]
        figures = dict(zip(ids, self._pool.map(self._figures, ids), strict=True))
        samples = {cid: self._sample(cid) for cid in ids}
        self._forget_gone(set(ids))
        return overview.build(host, containers, figures, samples, self.version)

    def overview(self) -> dict:
        """The current overview, shared by every page that asks within a few seconds."""
        with self._lock:
            now = time.monotonic()
            if self._cached and now - self._cached[0] < CACHE_SECONDS:
                data = self._cached[1]
            else:
                data = self._build()
                self._cached = (now, data)
        return {**data, "history": list(self._history)}

    def record(self) -> None:
        """One point for the totals' sparklines."""
        totals = self.overview()["totals"]
        self._history.append({
            "t": int(datetime.now(UTC).timestamp()),
            "running": totals["running"],
            "players": totals["players"],
            "cpu_percent": totals["cpu_percent"],
            "mem_bytes": totals["mem_bytes"],
        })
