"""Run the Docker gate: `python -m gate` (the compose file's entrypoint, #204).

Needs the real Docker socket at /var/run/docker.sock and serves the filtered API
on a unix socket in the volume it shares with its manager. The host folders this
stack's containers may mount are read off the gate's own mounts under /stack —
the compose file mounts the same data and server-file folders there as it gives
the manager — so the gate and the manager can never disagree about them.
"""
import logging
import os
import socket
import sys
import time
from pathlib import Path

import docker
import httpx
import uvicorn

import config
import stacks
from gate import policy
from gate.app import create_app

logger = logging.getLogger("gate")

DOCKER_SOCKET = "/var/run/docker.sock"
GATE_SOCKET = os.environ.get("RSM_GATE_SOCKET", "/run/rsm-gate/docker.sock")
STACK_MOUNTS = "/stack"


def _bind_roots() -> tuple[str, ...]:
    """Host paths behind this container's /stack mounts, waiting for Docker."""
    while True:
        try:
            client = docker.DockerClient(base_url=f"unix://{DOCKER_SOCKET}")
            me = client.containers.get(socket.gethostname())
            break
        except docker.errors.DockerException as exc:
            logger.warning("Waiting for Docker to answer (%s)", exc)
            time.sleep(3)
    roots = []
    for mount in me.attrs.get("Mounts") or []:
        dest = (mount.get("Destination") or "").rstrip("/")
        source = (mount.get("Source") or "").rstrip("/")
        if source and (dest == STACK_MOUNTS or dest.startswith(STACK_MOUNTS + "/")):
            roots.append(source)
    return tuple(roots)


def build_scope() -> policy.Scope:
    s = config.settings
    error = stacks.stack_name_error(s.rsm_stack)
    if error:
        sys.exit(f"Refusing to start: {error}")
    roots = _bind_roots()
    if not roots:
        # Fail closed: without them every bind mount — so every server — is refused.
        logger.error("No folders are mounted under %s: every bind mount will be refused",
                     STACK_MOUNTS)
    return policy.Scope(
        stack=s.rsm_stack,
        images=frozenset({s.reforger_server_image, s.steamcmd_image}),
        host_network_images=frozenset({s.reforger_server_image}),
        networks=frozenset({s.docker_network}),
        bind_roots=roots,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scope = build_scope()
    logger.info("Docker gate for stack '%s' — images %s, network %s, folders %s",
                scope.stack, sorted(scope.images), sorted(scope.networks), list(scope.bind_roots))
    upstream = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET),
        # No read timeout: followed logs and `wait` legitimately stay open.
        timeout=httpx.Timeout(None, connect=10.0),
    )
    path = Path(GATE_SOCKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # left over from the last run; bind would fail
    # uvicorn makes the socket 0666; only this stack's manager mounts the volume.
    uvicorn.run(create_app(scope, upstream), uds=str(path), log_level="warning",
                access_log=False, timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
