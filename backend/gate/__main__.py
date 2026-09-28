"""Run the Docker gate: `python -m gate` (the compose file's entrypoint, #204).

Needs the real Docker socket at /var/run/docker.sock and serves the filtered API
on a unix socket in the volume it shares with its manager. The volumes this
stack's containers may mount are the ones the compose file names after the
stack — <stack>-data and <stack>-serverfiles-<branch> — so the gate needs no
access to what is in them.
"""
import logging
import os
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


def stack_volumes(stack: str) -> frozenset[str]:
    """This stack's named volumes, as the compose files name them."""
    return frozenset(
        {f"{stack}-data"} | {f"{stack}-serverfiles-{branch}" for branch in config.BRANCHES}
    )


def _daemon_api() -> tuple[int, int]:
    """The daemon's API version, waiting for Docker to answer."""
    while True:
        try:
            raw = docker.APIClient(base_url=f"unix://{DOCKER_SOCKET}").version()["ApiVersion"]
            major, minor = (int(p) for p in str(raw).split(".")[:2])
            return major, minor
        except (docker.errors.DockerException, KeyError, ValueError) as exc:
            logger.warning("Waiting for Docker to answer (%s)", exc)
            time.sleep(3)


def build_scope() -> policy.Scope:
    s = config.settings
    error = stacks.stack_name_error(s.rsm_stack)
    if error:
        sys.exit(f"Refusing to start: {error}")
    api = _daemon_api()
    if api < policy.SUBPATH_API:
        # Fail closed: without subpaths no server can mount its own folder.
        logger.error(
            "Docker Engine too old: this daemon speaks API %d.%d, and mounting a folder "
            "of a volume needs 1.45 (Docker Engine 26.0) or newer. Servers cannot start "
            "until Docker is updated.", *api,
        )
    return policy.Scope(
        stack=s.rsm_stack,
        images=frozenset({s.reforger_server_image, s.steamcmd_image}),
        host_network_images=frozenset({s.reforger_server_image}),
        networks=frozenset({s.docker_network}),
        volumes=stack_volumes(s.rsm_stack),
        api=api,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scope = build_scope()
    logger.info("Docker gate for stack '%s' — images %s, network %s, volumes %s, API %d.%d",
                scope.stack, sorted(scope.images), sorted(scope.networks),
                sorted(scope.volumes), *scope.api)
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
