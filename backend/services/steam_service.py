"""SteamCMD server-file downloads, run as one-shot sibling containers.

One download job may run per branch (stable / experimental). SteamCMD's
stdout is streamed from the container, parsed for progress, kept in a ring
buffer for late joiners, and fanned out to WebSocket subscribers.
"""
import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from docker.errors import DockerException

import config
from services import docker_service

logger = logging.getLogger("manager.steam")

# " Update state (0x61) downloading, progress: 26.34 (2416320529 / 9173824512)"
PROGRESS_RE = re.compile(
    r"Update state \(0x[0-9a-fA-F]+\) (\w[\w ]*?), progress: ([0-9.]+) \((\d+) / (\d+)\)"
)
SUCCESS_RE = re.compile(r"Success! App '(\d+)' fully installed")
ERROR_RE = re.compile(r"ERROR! (.+)")

BUILDID_RE = re.compile(r'"buildid"\s+"(\d+)"')
LASTUPDATED_RE = re.compile(r'"LastUpdated"\s+"(\d+)"')
SIZE_RE = re.compile(r'"SizeOnDisk"\s+"(\d+)"')

# The public-branch buildid inside `app_info_print` VDF output. [^{}] keeps the
# match inside the "public" block so we don't grab another branch's buildid.
PUBLIC_BUILD_RE = re.compile(r'"public"\s*\{[^{}]*?"buildid"\s+"(\d+)"', re.S)

LOG_RING_SIZE = 1000


def parse_latest_build(text: str) -> str | None:
    """Extract the public-branch build id from steamcmd app_info_print output."""
    m = PUBLIC_BUILD_RE.search(text)
    return m.group(1) if m else None


def parse_line(line: str) -> dict | None:
    """Classify one SteamCMD output line into a structured event."""
    m = PROGRESS_RE.search(line)
    if m:
        return {
            "kind": "progress",
            "phase": m.group(1),
            "percent": float(m.group(2)),
            "bytes_done": int(m.group(3)),
            "bytes_total": int(m.group(4)),
        }
    if SUCCESS_RE.search(line):
        return {"kind": "success"}
    m = ERROR_RE.search(line)
    if m:
        return {"kind": "error", "message": m.group(1).strip()}
    return None


@dataclass
class DownloadJob:
    branch: str
    started_at: float
    status: str = "running"  # running | success | error
    phase: str = "starting"
    percent: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    error: str = ""
    container_id: str = ""
    finished_at: float | None = None
    saw_success: bool = False
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_RING_SIZE))
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "branch": self.branch,
            "status": self.status,
            "phase": self.phase,
            "percent": self.percent,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SteamService:
    def __init__(self):
        self.jobs: dict[str, DownloadJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ---- public API ----------------------------------------------------------

    def job(self, branch: str) -> DownloadJob | None:
        return self.jobs.get(branch)

    def installed_info(self, branch: str) -> dict | None:
        """Read Steam's appmanifest from the branch install dir, if present."""
        app_id = config.BRANCHES[branch]["app_id"]
        acf = (
            Path(config.settings.serverfiles_dir)
            / branch
            / "steamapps"
            / f"appmanifest_{app_id}.acf"
        )
        try:
            text = acf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        def find(rx: re.Pattern) -> str | None:
            m = rx.search(text)
            return m.group(1) if m else None

        return {
            "app_id": app_id,
            "build_id": find(BUILDID_RE),
            "last_updated": int(find(LASTUPDATED_RE) or 0),
            "size_bytes": int(find(SIZE_RE) or 0),
        }

    def latest_build_id(self, branch: str) -> str | None:
        """Query Steam for the current public build id of the branch's app.

        Runs a short one-shot steamcmd container (app_info_print) and parses
        its VDF output. Returns None on any failure — callers treat that as
        "unknown", never fatal.
        """
        app_id = config.BRANCHES[branch]["app_id"]
        command = [
            "+login", "anonymous",
            "+app_info_update", "1",
            "+app_info_print", app_id,
            "+quit",
        ]
        try:
            container = docker_service.get_client().containers.run(
                config.settings.steamcmd_image,
                command,
                detach=True,
                name=f"reforger-steamcheck-{branch}-{int(time.time())}",
                labels={docker_service.LABEL_MANAGED: "true"},
            )
        except DockerException as exc:
            logger.warning("Could not start steam version check: %s", exc)
            return None
        try:
            container.wait(timeout=120)
            output = container.logs().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Steam version check failed: %s", exc)
            output = ""
        finally:
            try:
                container.remove(force=True)
            except DockerException:
                pass
        return parse_latest_build(output)

    def remove_files(self, branch: str) -> None:
        """Wipe a branch's downloaded server files so it can be re-downloaded.

        The manager mounts serverfiles read-only, so a short cleanup sibling
        container (steamcmd image, rw mount) does the delete. Raises
        RuntimeError on failure.
        """
        host_dir = docker_service.host_path_for(
            f"{config.settings.serverfiles_dir}/{branch}"
        )
        Path(f"{config.settings.serverfiles_dir}/{branch}").mkdir(parents=True, exist_ok=True)
        try:
            docker_service.get_client().containers.run(
                config.settings.steamcmd_image,
                entrypoint="/bin/sh",
                command=["-c", "rm -rf /serverfiles/* /serverfiles/.[!.]* 2>/dev/null; true"],
                remove=True,
                volumes={host_dir: {"bind": "/serverfiles", "mode": "rw"}},
                labels={docker_service.LABEL_MANAGED: "true"},
            )
        except DockerException as exc:
            raise RuntimeError(f"Could not remove server files: {exc}") from exc
        # Clear any cached install/job state for the branch
        self.jobs.pop(branch, None)
        logger.info("Removed %s server files", branch)

    def update_status(self, branch: str) -> dict:
        """Installed vs latest build id, and whether an update is available."""
        installed = self.installed_info(branch)
        installed_build = installed.get("build_id") if installed else None
        latest_build = self.latest_build_id(branch)
        update_available = bool(
            installed_build and latest_build and installed_build != latest_build
        )
        return {
            "installed_build": installed_build,
            "latest_build": latest_build,
            "update_available": update_available,
        }

    async def start(self, branch: str) -> DownloadJob:
        """Kick off a download job. Raises RuntimeError if one is running."""
        existing = self.jobs.get(branch)
        if existing and existing.status == "running":
            raise RuntimeError(f"A {branch} download is already running")
        # An orphaned container from a previous manager process may still be
        # writing to the same folder (it finishes on its own; we just must
        # not start a second writer).
        orphans = await asyncio.to_thread(
            docker_service.find_containers,
            docker_service.ROLE_STEAMCMD,
            "running",
            branch,
        )
        if orphans:
            raise RuntimeError(
                f"A {branch} download container ({orphans[0].name}) is still "
                "running from a previous manager start; wait for it to finish"
            )

        job = DownloadJob(branch=branch, started_at=time.time())
        self.jobs[branch] = job
        self._tasks[branch] = asyncio.create_task(self._run(job))
        return job

    def subscribe(self, branch: str) -> asyncio.Queue | None:
        job = self.jobs.get(branch)
        if not job:
            return None
        queue: asyncio.Queue = asyncio.Queue()
        job.subscribers.append(queue)
        return queue

    def unsubscribe(self, branch: str, queue: asyncio.Queue) -> None:
        job = self.jobs.get(branch)
        if job and queue in job.subscribers:
            job.subscribers.remove(queue)

    # ---- job lifecycle --------------------------------------------------------

    async def _run(self, job: DownloadJob) -> None:
        loop = asyncio.get_running_loop()
        app_id = config.BRANCHES[job.branch]["app_id"]
        host_dir = docker_service.host_path_for(
            f"{config.settings.serverfiles_dir}/{job.branch}"
        )
        command = [
            "+force_install_dir", "/serverfiles",
            "+login", "anonymous",
            "+app_update", app_id, "validate",
            "+quit",
        ]
        try:
            container = await asyncio.to_thread(
                docker_service.get_client().containers.run,
                config.settings.steamcmd_image,
                command,
                detach=True,
                name=f"reforger-steamcmd-{job.branch}-{int(job.started_at)}",
                volumes={host_dir: {"bind": "/serverfiles", "mode": "rw"}},
                labels={
                    docker_service.LABEL_MANAGED: "true",
                    docker_service.LABEL_ROLE: docker_service.ROLE_STEAMCMD,
                    docker_service.LABEL_BRANCH: job.branch,
                },
            )
        except DockerException as exc:
            logger.warning("Could not start steamcmd container: %s", exc)
            self._finish(job, "error", f"Could not start SteamCMD container: {exc}")
            return

        job.container_id = container.id[:12]
        logger.info(
            "SteamCMD download started: branch=%s app=%s container=%s",
            job.branch, app_id, job.container_id,
        )
        self._broadcast(job, {"type": "status", "job": job.snapshot(), "installed": None})

        exit_code: int | None = None
        try:
            exit_code = await asyncio.wait_for(
                asyncio.to_thread(self._stream_logs, job, container, loop),
                timeout=config.settings.steamcmd_timeout_minutes * 60,
            )
        except asyncio.TimeoutError:
            await asyncio.to_thread(self._force_remove, container)
            self._finish(
                job, "error",
                f"Download timed out after {config.settings.steamcmd_timeout_minutes} minutes",
            )
            return
        except Exception as exc:  # log streaming must never take the app down
            logger.warning("SteamCMD log streaming failed: %s", exc)
            self._finish(job, "error", f"Lost contact with the download container: {exc}")
            await asyncio.to_thread(self._force_remove, container)
            return

        await asyncio.to_thread(self._force_remove, container)
        if exit_code == 0 and job.saw_success:
            self._finish(job, "success", "")
        else:
            self._finish(
                job, "error",
                job.error or f"SteamCMD exited with code {exit_code} without reporting success",
            )

    def _stream_logs(self, job: DownloadJob, container, loop) -> int:
        """Blocking (thread): forward log lines to the event loop, return exit code."""
        buf = b""
        for chunk in container.logs(stream=True, follow=True):
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                if line:
                    loop.call_soon_threadsafe(self._on_line, job, line)
        result = container.wait()
        return result.get("StatusCode", -1)

    def _on_line(self, job: DownloadJob, line: str) -> None:
        job.log.append(line)
        self._broadcast(job, {"type": "log", "line": line})
        event = parse_line(line)
        if not event:
            return
        if event["kind"] == "progress":
            job.phase = event["phase"]
            job.percent = event["percent"]
            job.bytes_done = event["bytes_done"]
            job.bytes_total = event["bytes_total"]
            self._broadcast(job, {"type": "progress", **{k: event[k] for k in
                             ("phase", "percent", "bytes_done", "bytes_total")}})
        elif event["kind"] == "success":
            job.saw_success = True
        elif event["kind"] == "error" and not job.error:
            job.error = event["message"]

    def _finish(self, job: DownloadJob, status: str, error: str) -> None:
        job.status = status
        job.error = error
        job.finished_at = time.time()
        if status == "success":
            job.percent = 100.0
            job.phase = "completed"
            logger.info("SteamCMD download finished: branch=%s", job.branch)
        else:
            job.phase = "failed"
            logger.warning("SteamCMD download failed: branch=%s: %s", job.branch, error)
        self._broadcast(job, {
            "type": "status",
            "job": job.snapshot(),
            "installed": self.installed_info(job.branch),
        })

    def _broadcast(self, job: DownloadJob, event: dict) -> None:
        for queue in list(job.subscribers):
            queue.put_nowait(event)

    @staticmethod
    def _force_remove(container) -> None:
        try:
            container.remove(force=True)
        except DockerException as exc:
            logger.warning("Could not remove container %s: %s", container.name, exc)


steam = SteamService()
