"""Thin wrapper around the Docker SDK for managing sibling containers.

The manager runs inside a container but talks to the HOST's Docker daemon
through the mounted socket. Containers it creates are therefore siblings,
not children: they attach to the shared compose network, and every bind
mount handed to the daemon must be expressed as a host path (host_path_for).
"""
import logging
import socket

import docker
from docker.errors import DockerException

logger = logging.getLogger("manager.docker")

LABEL_MANAGED = "reforger-manager.managed"
LABEL_ROLE = "reforger-manager.role"
LABEL_BRANCH = "reforger-manager.branch"
LABEL_INSTANCE_ID = "reforger-manager.instance_id"

ROLE_STEAMCMD = "steamcmd"
ROLE_INSTANCE = "instance"

_client: docker.DockerClient | None = None
_self_mounts: list | None = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def ping() -> bool:
    try:
        get_client().ping()
        return True
    except DockerException as exc:
        logger.warning("Docker daemon unreachable: %s", exc)
        return False


def _own_mounts() -> list:
    """Mounts of the manager's own container ([] when not containerized).

    The container hostname is the short container id unless overridden,
    which lets the manager inspect itself through the daemon.
    """
    global _self_mounts
    if _self_mounts is None:
        try:
            me = get_client().containers.get(socket.gethostname())
            _self_mounts = me.attrs.get("Mounts", [])
        except DockerException:
            logger.warning(
                "Could not inspect own container; assuming a non-containerized run"
            )
            _self_mounts = []
    return _self_mounts


def host_path_for(container_path: str) -> str:
    """Translate a path inside this container to the host path backing it.

    Sibling containers can only mount host paths. Outside a container
    (local development) the path is returned unchanged.
    """
    for mount in _own_mounts():
        dest = (mount.get("Destination") or "").rstrip("/")
        if dest and (container_path == dest or container_path.startswith(dest + "/")):
            return mount["Source"].rstrip("/") + container_path[len(dest):]
    return container_path


def find_containers(role: str, status: str | None = None, branch: str | None = None) -> list:
    filters = {"label": [f"{LABEL_ROLE}={role}"]}
    if status:
        filters["status"] = status
    if branch:
        filters["label"].append(f"{LABEL_BRANCH}={branch}")
    try:
        return get_client().containers.list(all=True, filters=filters)
    except DockerException as exc:
        logger.warning("Container lookup (%s) failed: %s", role, exc)
        return []


def find_instance_container(instance_id: int):
    """Return the single container for an instance id, or None."""
    filters = {"label": [f"{LABEL_INSTANCE_ID}={instance_id}"]}
    try:
        found = get_client().containers.list(all=True, filters=filters)
    except DockerException as exc:
        logger.warning("Instance container lookup (%s) failed: %s", instance_id, exc)
        return None
    return found[0] if found else None


def remove_exited(role: str) -> None:
    """Clean up leftover exited containers of ours with the given role."""
    for container in find_containers(role, status="exited"):
        try:
            logger.info("Removing stale %s container %s", role, container.name)
            container.remove(force=True)
        except DockerException as exc:
            logger.warning("Could not remove %s: %s", container.name, exc)
