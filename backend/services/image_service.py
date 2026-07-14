"""Pull the Arma Reforger runtime image with live progress (issue #50).

Each server instance is created from REFORGER_SERVER_IMAGE — a Docker image
that is entirely separate from the steamcmd-downloaded server *files*. On a
clean host it is never present (`docker compose up` only pulls the manager
image, and the server image is not a compose service), so starting an
instance fails with ImageNotFound. This service lets the Downloads tab pull
it, streaming progress with the same job/WebSocket shape as steam downloads.
"""
import asyncio
import logging
import time
from dataclasses import dataclass

from docker.errors import DockerException, ImageNotFound

import config
from services import docker_service
from services.jobs import ProgressJob

logger = logging.getLogger("manager.image")

LOG_RING_SIZE = 500


def split_image_ref(ref: str) -> tuple[str, str]:
    """Split 'repo:tag' into (repo, tag), leaving a registry host:port intact.

    A tag is only present when the final path segment (after the last '/')
    contains a colon, so 'ghcr.io/acemod/arma-reforger:latest' splits but
    'localhost:5000/img' does not.
    """
    last_segment = ref.rsplit("/", 1)[-1]
    if ":" in last_segment:
        repo, tag = ref.rsplit(":", 1)
        return repo, tag
    return ref, "latest"


@dataclass
class PullJob(ProgressJob):
    """A docker image pull. Progress/log/streaming come from ProgressJob (#88)."""

    image: str = ""

    def snapshot(self) -> dict:
        return {"image": self.image, **super().snapshot()}


class ImageService:
    """Manages a single at-a-time pull of the reforger server image."""

    def __init__(self):
        self.job: PullJob | None = None
        self._task: asyncio.Task | None = None

    # ---- public API ----------------------------------------------------------

    def present(self) -> bool:
        """True if the server image is already available to the daemon."""
        try:
            docker_service.get_client().images.get(config.settings.reforger_server_image)
            return True
        except ImageNotFound:
            return False
        except DockerException as exc:
            logger.warning("Could not check for server image: %s", exc)
            return False

    async def start(self) -> PullJob:
        """Kick off an image pull. Raises RuntimeError if one is running."""
        if self.job and self.job.status == "running":
            raise RuntimeError("An image pull is already running")
        job = PullJob(
            image=config.settings.reforger_server_image, started_at=time.time()
        )
        self.job = job
        self._task = asyncio.create_task(self._run(job))
        return job

    # ---- job lifecycle --------------------------------------------------------

    async def _run(self, job: PullJob) -> None:
        loop = asyncio.get_running_loop()
        logger.info("Pulling server image: %s", job.image)
        job.broadcast({"type": "status", "job": job.snapshot(), "present": None})
        try:
            await asyncio.to_thread(self._pull, job, loop)
        except Exception as exc:  # a pull failure must never take the app down
            self._finish(job, "error", _clean_error(str(exc)))
            return
        self._finish(job, "success", "")

    def _pull(self, job: PullJob, loop) -> None:
        """Blocking (thread): stream low-level pull events to the event loop.

        Raises on any reported error so `_run` can mark the job failed.
        """
        repo, tag = split_image_ref(job.image)
        api = docker_service.get_client().api
        # Per-layer download byte counts, aggregated into one overall percentage.
        layers: dict[str, tuple[int, int]] = {}
        last_status: dict[str, str] = {}

        for event in api.pull(repo, tag=tag, stream=True, decode=True):
            if "error" in event:
                raise RuntimeError(event["error"])
            status = event.get("status")
            layer_id = event.get("id")
            detail = event.get("progressDetail") or {}

            # Log each layer's status transitions (not every byte tick) plus any
            # id-less lines like "Status: Downloaded newer image for ...".
            if status and layer_id:
                if last_status.get(layer_id) != status:
                    last_status[layer_id] = status
                    loop.call_soon_threadsafe(self._on_log, job, f"{layer_id}: {status}")
            elif status:
                loop.call_soon_threadsafe(self._on_log, job, status)

            if status == "Downloading" and detail.get("total"):
                layers[layer_id] = (detail.get("current", 0), detail["total"])
                self._emit_progress(job, layers, "Downloading", loop)
            elif status in ("Download complete", "Pull complete") and layer_id in layers:
                _, total = layers[layer_id]
                layers[layer_id] = (total, total)
                self._emit_progress(job, layers, status, loop)

    def _emit_progress(self, job, layers, phase, loop) -> None:
        done = sum(c for c, _ in layers.values())
        total = sum(t for _, t in layers.values())
        percent = round(100 * done / total, 1) if total else 0.0
        loop.call_soon_threadsafe(
            self._on_progress, job, phase, percent, done, total
        )

    # ---- loop-thread mutations ------------------------------------------------

    def _on_log(self, job: PullJob, line: str) -> None:
        job.emit_log(line)

    def _on_progress(self, job, phase, percent, done, total) -> None:
        job.phase = phase
        job.percent = percent
        job.bytes_done = done
        job.bytes_total = total
        job.broadcast({
            "type": "progress",
            "phase": phase,
            "percent": percent,
            "bytes_done": done,
            "bytes_total": total,
        })

    def _finish(self, job: PullJob, status: str, error: str) -> None:
        job.finish(status, error)
        if status == "success":
            job.phase = "completed"
            logger.info("Server image pulled: %s", job.image)
        else:
            job.phase = "failed"
            logger.warning("Server image pull failed: %s: %s", job.image, error)
        job.broadcast({
            "type": "status",
            "job": job.snapshot(),
            "present": status == "success",
        })


def _clean_error(message: str) -> str:
    """Trim docker's verbose pull errors to something readable in the UI."""
    message = message.strip()
    if len(message) > 300:
        message = message[:297] + "..."
    return message or "Image pull failed"


image = ImageService()
