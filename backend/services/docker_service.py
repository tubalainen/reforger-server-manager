"""Thin wrapper around the Docker SDK for managing sibling containers.

The manager runs inside a container but talks to the HOST's Docker daemon
through the mounted socket. Containers it creates are therefore siblings,
not children: they attach to the shared compose network, and every bind
mount handed to the daemon must be expressed as a host path (host_path_for).
"""
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

import docker
from docker.errors import DockerException

import config

logger = logging.getLogger("manager.docker")

LABEL_MANAGED = "reforger-manager.managed"
LABEL_ROLE = "reforger-manager.role"
LABEL_BRANCH = "reforger-manager.branch"
LABEL_INSTANCE_ID = "reforger-manager.instance_id"

ROLE_STEAMCMD = "steamcmd"
ROLE_INSTANCE = "instance"

# Applied to every container this manager creates. Blocks a process inside from
# gaining privileges through setuid binaries, so a compromised Workshop mod (the
# game servers run untrusted mod code as root) cannot escalate that way. Cheap,
# and supported by every modern Docker daemon (security review R8).
SECURITY_OPT = ["no-new-privileges:true"]

_client: docker.DockerClient | None = None
_self_mounts: list | None = None
_info: dict | None = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def daemon_info() -> dict:
    """`docker info`, cached ({} when the daemon is unreachable).

    Only SUCCESSES are cached. Caching the failure would pin the answer for the
    lifetime of the process: one unlucky call at startup and is_docker_desktop()
    would stay False forever, so a Windows host would be shown the Linux firewall
    command for good (#85).
    """
    global _info
    if _info is not None:
        return _info
    try:
        _info = get_client().info()
    except Exception as exc:  # DockerException, or a client that cannot answer
        logger.warning("Could not read docker info: %s", exc)
        return {}  # not cached: ask again next time
    return _info


def is_docker_desktop() -> bool:
    """True when the daemon is Docker Desktop (Windows/macOS, WSL2 backend).

    Published ports still land on the real host, but the firewall the user must
    open is the Windows one — hence the distinction (issue #51).
    """
    return "docker desktop" in str(daemon_info().get("OperatingSystem", "")).lower()


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


def instance_containers() -> dict[int, object]:
    """Every managed instance container, indexed by instance id.

    ONE daemon query for the whole set, so an endpoint that reports on N instances
    costs one list instead of N lookups. Endpoints that report on every instance
    (the list, the summary) used to call find_instance_container() — and ping() —
    once per instance, which is the amplification this exists to kill (#87).

    docker-py's list() is non-sparse: it inspects each match, so the containers
    handed back are already fresh and need no reload().
    """
    try:
        containers = get_client().containers.list(
            all=True, filters={"label": [f"{LABEL_ROLE}={ROLE_INSTANCE}"]}
        )
    except DockerException as exc:
        logger.warning("Instance container listing failed: %s", exc)
        return {}
    by_instance: dict[int, object] = {}
    for container in containers:
        raw = (container.labels or {}).get(LABEL_INSTANCE_ID)
        try:
            by_instance[int(raw)] = container
        except (TypeError, ValueError):
            continue  # a managed container without a usable instance label
    return by_instance


def remove_exited(role: str) -> None:
    """Clean up leftover exited containers of ours with the given role."""
    for container in find_containers(role, status="exited"):
        try:
            logger.info("Removing stale %s container %s", role, container.name)
            container.remove(force=True)
        except DockerException as exc:
            logger.warning("Could not remove %s: %s", container.name, exc)


def use_host_network() -> bool:
    """Should game servers use host networking rather than NAT'd bridge?

    Bridge networking puts Docker's userland `docker-proxy` in front of every
    published UDP port and rewrites the source address to the bridge gateway.
    An Arma server then sees every player arriving from 172.x.0.1, which breaks
    joins and BattlEye (#150). Host networking removes NAT entirely: the server
    binds its own ports on the host and sees real client addresses — the normal
    way to run a game server in Docker.

    Docker Desktop is the exception: its Linux VM cannot provide true host
    networking, so bridge remains the only workable mode there.
    """
    mode = (config.settings.instance_network_mode or "auto").lower()
    if mode == "host":
        return True
    if mode == "bridge":
        return False
    return not is_docker_desktop()      # auto


# --------------------------------------------------------------------------- #
# Is the Docker API reachable from the game servers? (v0.64.1)
# --------------------------------------------------------------------------- #
# The socket proxy sits on an 'internal' network, which reads as "only the
# manager can reach it". It is not: Docker gives an internal network's bridge an
# address ON THE HOST, and the host can reach every container on it. Game
# servers use host networking (#150), so network-wise they are the host — and
# they run untrusted Workshop mods as root. Gateway mode 'isolated' (Docker
# Engine 28+) removes that address. Pulling the image never updates the compose
# file that sets it, so the manager checks for itself and says so in the GUI.
GW_MODE_IPV4 = "com.docker.network.bridge.gateway_mode_ipv4"
GW_MODE_IPV6 = "com.docker.network.bridge.gateway_mode_ipv6"
ISOLATED_MIN_ENGINE = 28

_exposure: dict | None = None
_exposure_known = False


def _engine_major(version: str) -> int | None:
    head = str(version or "").split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _proxy_network(proxy_ip: str) -> tuple[str, str] | None:
    """(name, id) of the manager's own network that the proxy address is on."""
    me = get_client().containers.get(socket.gethostname())
    endpoints = (me.attrs.get("NetworkSettings") or {}).get("Networks") or {}
    target = ipaddress.ip_address(proxy_ip)
    for name, ep in endpoints.items():
        ip, prefix = ep.get("IPAddress"), ep.get("IPPrefixLen")
        if not ip or not prefix:
            continue
        if target in ipaddress.ip_network(f"{ip}/{prefix}", strict=False):
            return name, ep.get("NetworkID") or name
    return None


def _assess_exposure() -> tuple[dict | None, bool]:
    """(warning or None, whether that answer is certain).

    Only a certain answer may be cached. Anything we cannot read gives no
    warning at all — a false alarm on a blind read would teach people to ignore
    the banner — and is asked again next time.
    """
    url = urlsplit(os.environ.get("DOCKER_HOST", ""))
    if url.scheme not in ("tcp", "http", "https") or not url.hostname:
        # No network proxy: the manager holds a local socket, which no network
        # path leads to.
        return None, True
    info = daemon_info()
    if not info:
        return None, False  # daemon not answering yet
    if not use_host_network():
        # Bridge-networked servers are not in the host's network namespace.
        return None, True
    try:
        proxy_ip = socket.gethostbyname(url.hostname)
        found = _proxy_network(proxy_ip)
        if found is None:
            return None, False  # a custom setup we cannot map; do not guess
        name, net_id = found
        net = get_client().networks.get(net_id).attrs
        engine = str(get_client().version().get("Version", ""))
    except (DockerException, OSError, ValueError, AttributeError) as exc:
        logger.info("Could not check whether the Docker API is exposed: %s", exc)
        return None, False

    options = net.get("Options") or {}
    major = _engine_major(engine)
    refresh = (
        "Whoever manages this machine should refresh the compose file and recreate "
        "the stack. With the Linux installer's rsm command: run 'sudo rsm update', "
        "accept the new compose file, then run 'sudo rsm restart'. Manual installs: "
        "download the new compose file, then run 'docker compose down' and "
        "'docker compose up -d'. The v0.64.1 release notes have the details."
    )
    if major is not None and major < ISOLATED_MIN_ENGINE:
        reason = (
            f"This host runs Docker Engine {engine}; 28.0 is the first version that "
            f"can take the Docker API network off the host."
        )
        action = (
            "Whoever manages this machine should update Docker Engine to 28 or newer "
            "first, then apply the v0.64.1 compose file as its release notes describe."
        )
    elif not net.get("Internal"):
        reason = f"The network the manager reaches Docker over ({name}) is not an internal network."
        action = refresh
    elif options.get(GW_MODE_IPV4) != "isolated" or (
        net.get("EnableIPv6") and options.get(GW_MODE_IPV6) != "isolated"
    ):
        reason = (
            f"The network {name} was created by a compose file older than v0.64.1, "
            f"which leaves it an address on the host."
        )
        action = refresh
    else:
        return None, True

    return {
        "id": "docker_api_exposed",
        "severity": "danger",
        "title": "Security: the game servers can reach the Docker API",
        "detail": (
            "Game servers use host networking, so they — and any Workshop mod running "
            "in them — can reach the Docker socket proxy, which can create containers "
            "on this host. " + reason
        ),
        "action": action,
    }, True


def exposure_known() -> bool:
    """True once docker_api_exposure() has a certain answer cached."""
    return _exposure_known


def docker_api_exposure() -> dict | None:
    """A GUI warning when the Docker API is reachable from the game servers.

    Cached once certain: the answer can only change when the stack is recreated
    or Docker restarts, and both restart this process.
    """
    global _exposure, _exposure_known
    if _exposure_known:
        return _exposure
    warning, certain = _assess_exposure()
    if certain:
        _exposure, _exposure_known = warning, True
    return warning
