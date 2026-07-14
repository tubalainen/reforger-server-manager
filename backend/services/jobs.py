"""One long-running background job with progress, streamed to WebSocket clients.

The steamcmd download and the server-image pull are the same object wearing
different labels: both track percent/bytes/phase, keep a ring buffer of log lines
for late joiners, fan events out to subscriber queues, and finish once. They had
two near-identical copies of that machinery, and each WebSocket endpoint
hand-rolled the same accept → drain → disconnect pump (#88).

A concrete job subclasses ProgressJob to add its own identity (a branch, an image)
and extends snapshot() with it.
"""
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("manager.jobs")

LOG_RING_SIZE = 1000

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


@dataclass
class ProgressJob:
    started_at: float = field(default_factory=time.time)
    status: str = STATUS_RUNNING  # running | success | error
    phase: str = "starting"
    percent: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    error: str = ""
    finished_at: float | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_RING_SIZE))
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    # ---- reporting -----------------------------------------------------------

    def snapshot(self) -> dict:
        """The job's state as the GUI sees it. Subclasses add their identity."""
        return {
            "status": self.status,
            "phase": self.phase,
            "percent": self.percent,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @property
    def running(self) -> bool:
        return self.status == STATUS_RUNNING

    # ---- streaming -----------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    def broadcast(self, event: dict) -> None:
        # list() because a subscriber may be removed while we iterate.
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def emit_log(self, line: str) -> None:
        """Ring-buffer a log line (for late joiners) and fan it out."""
        self.log.append(line)
        self.broadcast({"type": "log", "line": line})

    def finish(self, status: str, error: str = "") -> None:
        self.status = status
        self.error = error
        self.finished_at = time.time()
        if status == STATUS_SUCCESS:
            self.percent = 100.0


async def stream_job(websocket, job: ProgressJob | None, first: dict) -> None:
    """The WebSocket pump every job endpoint used to write out by hand.

    Sends `first` (the snapshot the endpoint composes, since each carries its own
    extra state), then forwards events until the client goes away.
    """
    from fastapi import WebSocketDisconnect

    await websocket.send_json(first)
    if job is None:
        await websocket.close()
        return
    queue = job.subscribe()
    try:
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # a broken socket must never take the app down
        logger.debug("Job stream ended: %s", exc)
    finally:
        job.unsubscribe(queue)
