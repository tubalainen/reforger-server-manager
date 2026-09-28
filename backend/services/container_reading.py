"""Reading a server's container: its current run's log, uptime, CPU and memory.

Shared by the manager (instance_service) and the Supervisor's observer gate
(#204), which reports the same figures for every stack on the machine. Nothing
here knows about the database or which stack a container is in: each function
takes a docker-py container and reads it.
"""
import re
from datetime import UTC, datetime

from docker.errors import DockerException

from services.server_log import STATE_ONLINE, parse_server_state

# Reforger logs are chatty, so read a generous tail to be sure a periodic stats
# line (emitted every STATS_LOG_INTERVAL_MS, see instance_service) is inside the
# window we parse.
STATS_LOG_TAIL = 400


def _docker_cpu_mem(container) -> dict:
    """One-shot CPU%/memory from docker stats (best-effort; {} on failure).

    Slow by construction: Docker answers only after it has collected two CPU
    samples, a second or two apart. Call it from a background sampler (the
    manager's cpu_mem_for(), the Supervisor's collector), not from a request.
    """
    try:
        s = container.stats(stream=False)
    except (DockerException, KeyError, ValueError):
        return {}
    try:
        cpu = s["cpu_stats"]
        pre = s["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        # Share of the WHOLE machine: system_cpu_usage is the host's total CPU
        # time summed over every core, so cpu_delta/sys_delta is 0-1 and *100 is
        # a real 0-100% where 100% = every core/thread maxed. The Docker CLI
        # multiplies this by the core count to get a per-core figure (100% = one
        # core), which is what showed ~291% for ~3 busy cores and read as "over
        # max" to users. Clamp for sampling jitter between the two counters.
        cpu_pct = (cpu_delta / sys_delta) * 100 if sys_delta > 0 else 0.0
        cpu_pct = min(100.0, max(0.0, cpu_pct))
        mem = s["memory_stats"]
        return {
            "cpu_percent": round(cpu_pct, 1),
            "mem_bytes": mem.get("usage", 0),
            "mem_limit_bytes": mem.get("limit", 0),
        }
    except (KeyError, ZeroDivisionError, TypeError):
        return {}


def _started_at(container) -> datetime | None:
    """When the container's current run began, from its Docker StartedAt."""
    try:
        started = (container.attrs.get("State") or {}).get("StartedAt")
    except AttributeError:
        return None
    if not started or started.startswith("0001-01-01"):  # never started
        return None
    # Docker stamps nanoseconds (up to 9 digits); trim to microseconds for fromisoformat
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", started)
    if not m:
        return None
    iso = m.group(1) + (("." + m.group(2)[:6]) if m.group(2) else "") + "+00:00"
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _container_uptime_seconds(container) -> int | None:
    """How long the container has been running, from its Docker StartedAt."""
    started_dt = _started_at(container)
    if started_dt is None:
        return None
    return max(0, int((datetime.now(UTC) - started_dt).total_seconds()))


_LOG_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?\s(.*)$", re.DOTALL
)


def _split_log_timestamp(line: str) -> tuple[datetime | None, str]:
    """Split docker's RFC3339 timestamp prefix off a log line."""
    m = _LOG_TS_RE.match(line)
    if not m:
        return None, line
    iso = m.group(1) + (("." + m.group(2)[:6]) if m.group(2) else "") + "+00:00"
    try:
        return datetime.fromisoformat(iso), m.group(3)
    except ValueError:
        return None, line


def current_run_log(container, tail: int = STATS_LOG_TAIL) -> str:
    """The tail of the log for the container's CURRENT run only.

    Docker keeps a container's log across restarts, so a plain tail serves up the
    previous run's output too — which would report a restarting server as still
    online (#76) and its old FPS/player numbers as current.

    Docker's own `since` filter is passed, but it is NOT trusted on its own: the
    SDK truncates it to whole seconds (docker.utils.datetime_to_timestamp), so a
    line the previous run wrote in the same second the new run began still comes
    through — and on a fast restart that is exactly where the last stats line
    lands. Each line therefore carries its timestamp and is checked against
    StartedAt at full precision; the prefix is stripped again so the parsers see
    an ordinary log.
    """
    started_dt = _started_at(container)
    if started_dt is None:
        # Unknown start time: no honest way to draw the boundary, so read as before.
        return container.logs(tail=tail).decode("utf-8", errors="replace")

    raw = container.logs(tail=tail, since=started_dt, timestamps=True)
    kept = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        ts, text = _split_log_timestamp(line)
        if ts is not None and ts < started_dt:
            continue  # belongs to a previous run of this container
        kept.append(text)
    return "\n".join(kept)


# Runs already seen online: {container id: StartedAt}. Once a server has come up
# it stays up until its container restarts, so remember it rather than re-deriving
# it from a log window that will eventually scroll past the evidence. Keyed by
# StartedAt so a restart of the same container is a fresh run.
_online_runs: dict[str, str] = {}


def forget_run(container_id: str | None) -> None:
    """Drop the remembered 'this run is online' fact for a container.

    Called whenever the manager stops or starts one: the next run has to prove
    itself from its own log again, no matter what the previous one did (#76).
    """
    if container_id:
        _online_runs.pop(container_id, None)


def server_state(container, log_text: str) -> str:
    """STATE_STARTING while the game server loads, STATE_ONLINE once it is up."""
    cid = getattr(container, "id", "") or ""
    started = str((container.attrs.get("State") or {}).get("StartedAt", ""))
    if cid and _online_runs.get(cid) == started:
        return STATE_ONLINE
    state = parse_server_state(log_text)
    if state == STATE_ONLINE and cid:
        _online_runs[cid] = started
    return state
