"""The Supervisor's observer: a read-only window on every stack (#204, v0.67.0).

Like a stack's Docker gate (gate/app.py) it is its own container, the only one of
the Supervisor's that holds the Docker socket, and it has no network: the
Supervisor reaches it over a unix socket in a volume only the two of them share.

Unlike a stack's gate it is not a Docker API proxy. It answers a handful of
questions of its own, and each answer is built here from what Docker says, so
nothing the Supervisor is not meant to see can slip through a field nobody
thought about:

  * only containers of ours — anything carrying a reforger-manager.* label, plus
    the unlabelled manager and gate of a stack from an older compose file;
  * of each, a fixed handful of fields: name, image, state, restart policy,
    network mode, published ports and our own labels. Never its environment
    (a manager's holds its team's password), its command line or its mounts;
  * of a game server's log, only the figures read from it — players, FPS,
    whether it is online — never the text, which names the players;
  * nothing that changes anything: every route is a GET.

    python -m gate.observer
"""
import logging
import os
import re
import time
from pathlib import Path

import docker
import uvicorn
from docker.errors import DockerException, NotFound
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import config
import stacks
from services import container_reading
from services.server_log import parse_server_status

logger = logging.getLogger("observer")

DOCKER_SOCKET = "/var/run/docker.sock"
OBSERVER_SOCKET = os.environ.get("RSM_OBSERVER_SOCKET", "/run/rsm-observer/observer.sock")

# A container id as Docker hands it out; names are not accepted, so a request
# cannot go looking for containers by guessing.
_ID_RE = re.compile(r"[0-9a-f]{12,64}")
# The containers a stack's compose file names without labels before v0.67.0.
_COMPOSE_NAME_RE = re.compile(r"(?P<stack>[a-z0-9][a-z0-9_]{0,30})-(?P<what>manager|docker-gate)")


# --------------------------------------------------------------------------- #
# What is ours, and what of it is passed on (pure)
# --------------------------------------------------------------------------- #
def our_labels(labels: dict | None) -> dict:
    """Only our own labels: the image's and anyone else's stay behind."""
    return {k: str(v) for k, v in (labels or {}).items()
            if isinstance(k, str) and k.startswith(stacks.LABEL_PREFIX)}


def stack_of(labels: dict | None) -> str | None:
    """The stack a container belongs to, as a stack's gate would judge it."""
    labels = labels or {}
    owner = labels.get(stacks.LABEL_STACK)
    if owner:
        return owner
    # Made before stacks existed: only the default stack could have.
    return stacks.DEFAULT_STACK if labels.get(stacks.LABEL_MANAGED) == "true" else None


def _name(summary: dict) -> str:
    names = summary.get("Names") or [""]
    return str(names[0]).lstrip("/")


def select(summaries: list[dict]) -> list[dict]:
    """The container-list entries the Supervisor may see.

    Labelled ones first; then a stack's manager and gate that carry no label —
    a compose file older than v0.67.0 did not label the gate — but only for a
    stack already known from a labelled container, so an unrelated container
    that happens to be called 'something-manager' never becomes a stack.
    """
    labelled = [s for s in summaries if our_labels(s.get("Labels"))]
    known = {stack_of(s.get("Labels")) for s in labelled} - {None}
    unlabelled = []
    for s in summaries:
        if our_labels(s.get("Labels")):
            continue
        m = _COMPOSE_NAME_RE.fullmatch(_name(s))
        if m and m.group("stack") in known:
            unlabelled.append(s)
    return labelled + unlabelled


def _published(host: dict) -> list[dict]:
    out = []
    for port, binds in sorted((host.get("PortBindings") or {}).items()):
        for bind in binds or []:
            out.append({"port": port, "host_ip": (bind or {}).get("HostIp") or "",
                        "host_port": (bind or {}).get("HostPort") or ""})
    return out


def project(attrs: dict) -> dict:
    """The fields of an inspected container the Supervisor gets. Nothing else."""
    state = attrs.get("State") or {}
    host = attrs.get("HostConfig") or {}
    cfg = attrs.get("Config") or {}
    name = str(attrs.get("Name") or "").lstrip("/")
    labels = our_labels(cfg.get("Labels"))
    m = _COMPOSE_NAME_RE.fullmatch(name)
    return {
        "id": attrs.get("Id"),
        "name": name,
        "image": cfg.get("Image"),
        "created": attrs.get("Created"),
        "stack": stack_of(labels) or (m.group("stack") if m else None),
        "labels": labels,
        "state": {
            "status": state.get("Status"),
            "running": bool(state.get("Running")),
            "restarting": bool(state.get("Restarting")),
            "oom_killed": bool(state.get("OOMKilled")),
            "exit_code": state.get("ExitCode"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "health": (state.get("Health") or {}).get("Status"),
        },
        "restart_count": attrs.get("RestartCount"),
        "restart_policy": (host.get("RestartPolicy") or {}).get("Name"),
        "network_mode": host.get("NetworkMode"),
        "ports": _published(host),
    }


def host_facts(info: dict, version: dict) -> dict:
    """The machine, without the host-wide container counts or names."""
    return {
        "docker_version": version.get("Version") or info.get("ServerVersion"),
        "api_version": version.get("ApiVersion"),
        "os": info.get("OperatingSystem"),
        "os_type": info.get("OSType"),
        "kernel": info.get("KernelVersion"),
        "architecture": info.get("Architecture"),
        "ncpu": info.get("NCPU"),
        "mem_total": info.get("MemTotal"),
    }


# --------------------------------------------------------------------------- #
# The app
# --------------------------------------------------------------------------- #
def _not_found(ref: str) -> JSONResponse:
    return JSONResponse({"message": f"No such container: {ref}"}, status_code=404)


def create_app(client: docker.DockerClient) -> Starlette:
    """The observer's ASGI app, reading Docker through `client`.

    Plain `def` handlers: docker-py blocks, and Starlette runs these in its
    threadpool.
    """

    def ours(ref: str):
        """The container `ref`, if it is one the Supervisor may see; else None."""
        if not _ID_RE.fullmatch(ref):
            return None
        try:
            container = client.containers.get(ref)
        except NotFound:
            return None
        if our_labels((container.attrs.get("Config") or {}).get("Labels")):
            return container
        m = _COMPOSE_NAME_RE.fullmatch(str(container.attrs.get("Name") or "").lstrip("/"))
        if not m:
            return None
        known = {stack_of(s.get("Labels"))
                 for s in select(client.api.containers(all=True))} - {None}
        return container if m.group("stack") in known else None

    def gate(_request: Request):
        return JSONResponse({"gate": "rsm", "mode": "observer", "version": config.APP_VERSION})

    def host(_request: Request):
        return JSONResponse(host_facts(client.info(), client.version()))

    def containers(_request: Request):
        out = []
        for summary in select(client.api.containers(all=True)):
            try:
                out.append(project(client.api.inspect_container(summary["Id"])))
            except NotFound:
                continue  # removed between the list and the inspect
        return JSONResponse(out)

    def server(request: Request):
        """A game server's figures, read from its current run's log."""
        ref = request.path_params["ref"]
        container = ours(ref)
        labels = (container.attrs.get("Config") or {}).get("Labels") if container else None
        if container is None or (labels or {}).get(stacks.LABEL_ROLE) != stacks.ROLE_INSTANCE:
            return _not_found(ref)
        facts = {"server_state": None, "players": None, "server_fps": None}
        if (container.attrs.get("State") or {}).get("Running"):
            log = container_reading.current_run_log(container)
            facts["server_state"] = container_reading.server_state(container, log)
            parsed = parse_server_status(log)
            if parsed:
                facts["players"] = parsed["players"]
                facts["server_fps"] = parsed["fps"]
        return JSONResponse(facts)

    def stats(request: Request):
        """CPU and memory of one running container. Takes a second or two."""
        ref = request.path_params["ref"]
        container = ours(ref)
        if container is None:
            return _not_found(ref)
        if not (container.attrs.get("State") or {}).get("Running"):
            return JSONResponse({})
        return JSONResponse(container_reading._docker_cpu_mem(container))

    async def refused(request: Request):
        return JSONResponse({"message": f"rsm-observer: {request.method} {request.url.path} "
                                        "is not something the observer answers"},
                            status_code=403)

    def guarded(handler):
        def run(request: Request):
            try:
                return handler(request)
            except DockerException as exc:
                logger.warning("Docker did not answer %s: %s", request.url.path, exc)
                return JSONResponse({"message": "rsm-observer: the Docker daemon is not reachable"},
                                    status_code=502)
        return run

    return Starlette(routes=[
        Route("/_rsm/gate", guarded(gate)),
        Route("/_rsm/host", guarded(host)),
        Route("/_rsm/containers", guarded(containers)),
        Route("/_rsm/containers/{ref}/server", guarded(server)),
        Route("/_rsm/containers/{ref}/stats", guarded(stats)),
        Route("/{path:path}", refused,
              methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]),
    ])


def _client() -> docker.DockerClient:
    """A Docker client, once the daemon answers."""
    while True:
        try:
            client = docker.DockerClient(base_url=f"unix://{DOCKER_SOCKET}")
            client.ping()
            return client
        except DockerException as exc:
            logger.warning("Waiting for Docker to answer (%s)", exc)
            time.sleep(3)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = _client()
    path = Path(OBSERVER_SOCKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # left over from the last run; bind would fail
    logger.info("Supervisor observer v%s: read-only, on %s", config.APP_VERSION, path)
    # uvicorn makes the socket 0666; only the Supervisor mounts the volume.
    uvicorn.run(create_app(client), uds=str(path), log_level="warning",
                access_log=False, timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
