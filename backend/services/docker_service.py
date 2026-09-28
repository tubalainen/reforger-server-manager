"""Thin wrapper around the Docker SDK for managing sibling containers.

The manager runs inside a container but talks to the HOST's Docker daemon
(through its stack's Docker gate). Containers it creates are therefore
siblings, not children: they attach to the shared compose network, and every
folder they mount must be named the way the daemon sees it (mount_for).
"""
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

import docker
from docker.errors import DockerException

import config
import stacks

logger = logging.getLogger("manager.docker")

LABEL_MANAGED = stacks.LABEL_MANAGED
LABEL_STACK = stacks.LABEL_STACK
LABEL_ROLE = stacks.LABEL_ROLE
LABEL_BRANCH = stacks.LABEL_BRANCH
LABEL_INSTANCE_ID = stacks.LABEL_INSTANCE_ID

ROLE_STEAMCMD = stacks.ROLE_STEAMCMD
ROLE_INSTANCE = stacks.ROLE_INSTANCE

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


def _own_mount(container_path: str) -> tuple[dict, str] | None:
    """(the manager's own mount holding this path, the path inside it)."""
    for mount in _own_mounts():
        dest = (mount.get("Destination") or "").rstrip("/")
        if dest and (container_path == dest or container_path.startswith(dest + "/")):
            return mount, container_path[len(dest):].strip("/")
    return None


def mount_for(container_path: str, target: str, read_only: bool = False) -> dict:
    """A mount of one of this manager's folders into a sibling container.

    Since v0.65.0 the data and server-file folders are named volumes, and a
    folder inside one is mounted as the volume plus a subpath (#206). The daemon
    opens every part of a subpath with symbolic links refused and mounts what it
    opened, so a link planted in the folder cannot lead the mount anywhere else
    — which a host path, resolved by the daemon later, could not promise. The
    Docker gate accepts nothing else.

    On a compose file from before v0.65.0 the folders are host folders, and so
    is the mount. Outside a container (development) the path is used as it is.
    Returned in the Docker API's shape, for the SDK's `mounts=[...]`.
    """
    found = _own_mount(container_path)
    if found is None:
        return {"Type": "bind", "Source": container_path, "Target": target,
                "ReadOnly": read_only}
    mount, inside = found
    if mount.get("Type") == "volume" and mount.get("Name"):
        spec = {"Type": "volume", "Source": mount["Name"], "Target": target,
                "ReadOnly": read_only}
        if inside:
            spec["VolumeOptions"] = {"Subpath": inside}
        return spec
    source = mount["Source"].rstrip("/") + (f"/{inside}" if inside else "")
    return {"Type": "bind", "Source": source, "Target": target, "ReadOnly": read_only}


_volume_dirs: dict[str, str] = {}


def _volume_dir(mount: dict) -> str:
    """Where a volume's files are on the host: the folder a bind volume points
    at (Linux: ./data next to the compose file), else Docker's own directory."""
    name = mount["Name"]
    if name not in _volume_dirs:
        try:
            attrs = get_client().volumes.get(name).attrs
        except DockerException as exc:
            logger.info("Could not inspect volume %s: %s", name, exc)
            return mount["Source"].rstrip("/")  # not cached: ask again next time
        options = attrs.get("Options") or {}
        device = options.get("device") if "bind" in str(options.get("o", "")).split(",") else ""
        _volume_dirs[name] = str(device or attrs.get("Mountpoint") or mount["Source"]).rstrip("/")
    return _volume_dirs[name]


def host_path_for(container_path: str) -> str:
    """Where a path inside this container lives on the host — for showing it.

    Outside a container (local development) the path is returned unchanged.
    """
    found = _own_mount(container_path)
    if found is None:
        return container_path
    mount, inside = found
    if mount.get("Type") == "volume" and mount.get("Name"):
        base = _volume_dir(mount)
    else:
        base = mount["Source"].rstrip("/")
    return base + (f"/{inside}" if inside else "")


# --------------------------------------------------------------------------- #
# This manager's stack (#204)
# --------------------------------------------------------------------------- #
# Several managers can share one Docker host. Everything below only ever sees
# and touches this stack's containers. Behind the Docker gate the daemon's
# answers are already scoped; the filtering here is the second, independent
# layer — and the only one for a manager still on an older compose file.

def stack() -> str:
    return config.settings.rsm_stack


def container_name(suffix: str) -> str:
    """This stack's name for a container: reforger-instance-3, team2-instance-3."""
    return f"{stack()}-{suffix}"


def managed_labels(**extra: str) -> dict:
    """Labels for a container this manager creates: managed, and this stack's."""
    return {LABEL_MANAGED: "true", LABEL_STACK: stack(), **extra}


def is_mine(container) -> bool:
    return stacks.owns(getattr(container, "labels", None) or {}, stack())


def is_current_stack(container) -> bool:
    """Carries this stack's label (not merely adopted from before stacks)."""
    return (getattr(container, "labels", None) or {}).get(LABEL_STACK) == stack()


def _scoped_list(label_filters: list[str], status: str | None = None) -> list:
    """List containers matching the label filters, keeping only this stack's.

    Docker cannot filter on a label being ABSENT, so the default stack — which
    also adopts containers from before stacks — is filtered here; any other stack
    asks the daemon for its own label directly and is filtered here too.
    """
    filters: dict = {"label": list(label_filters)}
    if stack() != stacks.DEFAULT_STACK:
        filters["label"].append(f"{LABEL_STACK}={stack()}")
    if status:
        filters["status"] = status
    found = get_client().containers.list(all=True, filters=filters)
    # A stack-labelled container sorts before an adopted one for the same thing.
    return sorted((c for c in found if is_mine(c)), key=lambda c: not is_current_stack(c))


def find_containers(role: str, status: str | None = None, branch: str | None = None) -> list:
    labels = [f"{LABEL_ROLE}={role}"]
    if branch:
        labels.append(f"{LABEL_BRANCH}={branch}")
    try:
        return _scoped_list(labels, status)
    except DockerException as exc:
        logger.warning("Container lookup (%s) failed: %s", role, exc)
        return []


def find_instance_container(instance_id: int):
    """Return the single container for an instance id, or None."""
    try:
        found = _scoped_list([f"{LABEL_INSTANCE_ID}={instance_id}"])
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
        containers = _scoped_list([f"{LABEL_ROLE}={ROLE_INSTANCE}"])
    except DockerException as exc:
        logger.warning("Instance container listing failed: %s", exc)
        return {}
    by_instance: dict[int, object] = {}
    for container in containers:
        raw = (container.labels or {}).get(LABEL_INSTANCE_ID)
        try:
            # setdefault: the stack-labelled one sorts first and wins.
            by_instance.setdefault(int(raw), container)
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
# Engine 28+) removes that address. Since v0.65.0 there is no such network at
# all: the manager reaches Docker through its gate over a unix socket (#204).
# Pulling the image never updates the compose file, so the manager checks for
# itself and says so in the GUI.
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
    if major is not None and major < ISOLATED_MIN_ENGINE:
        reason = (
            f"This host runs Docker Engine {engine}, which cannot take that network "
            f"off the host (28.0 is the first that can)."
        )
    elif not net.get("Internal"):
        reason = f"The network the manager reaches Docker over ({name}) is not an internal network."
    elif options.get(GW_MODE_IPV4) != "isolated" or (
        net.get("EnableIPv6") and options.get(GW_MODE_IPV6) != "isolated"
    ):
        reason = (
            f"The network {name} was created by a compose file older than v0.64.1, "
            f"which leaves it an address on the host."
        )
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
        # The v0.65.0 compose file replaces that network with a unix socket, on
        # any Docker Engine, so every case has the same fix.
        "action": (
            "Whoever manages this machine should refresh the compose file and recreate "
            "the stack. With the Linux installer's rsm command: run 'sudo rsm update' "
            "and accept the new compose file and the restart it offers. Manual "
            "installs: download the new compose file, then run 'docker compose down' "
            "and 'docker compose up -d'. Windows: re-run the installer. The v0.65.0 "
            "release notes have the details."
        ),
    }, True


# --------------------------------------------------------------------------- #
# Is Docker reached through this stack's gate? (#204)
# --------------------------------------------------------------------------- #
# The gate answers GET /_rsm/gate with a description of itself. The older
# socket proxy refuses that path and the Docker daemon does not know it, so any
# other answer means this manager still runs on a pre-v0.65.0 compose file.
GATE_PATH = "/_rsm/gate"

_gate: dict | None = None
_gate_known = False


def gate_info() -> dict | None:
    """The gate's description of itself ({"gate", "stack", "version"}), or None.

    None when Docker is reached some other way — or when that is not known yet
    (daemon unreachable), which is not cached and is asked again next time.
    """
    global _gate, _gate_known
    if _gate_known:
        return _gate
    if not daemon_info():
        return None
    try:
        api = get_client().api
        resp = api.get(api.base_url + GATE_PATH, timeout=5)
        body = resp.json() if resp.status_code == 200 else {}
    except (DockerException, OSError, ValueError, AttributeError) as exc:
        logger.info("Could not ask whether Docker is reached through the gate: %s", exc)
        return None
    _gate = body if isinstance(body, dict) and body.get("gate") == "rsm" else None
    _gate_known = True
    return _gate


def gate_known() -> bool:
    """True once gate_info() has a certain answer cached."""
    return _gate_known


def gate_warning() -> dict | None:
    """A GUI warning when this manager is not behind its own stack's gate."""
    info = gate_info()
    if not _gate_known:
        return None  # not known yet: never warn on a blind read
    if info is None:
        return {
            "id": "docker_gate_missing",
            "severity": "warning",
            "title": "This manager's compose file is older than v0.65.0",
            "detail": (
                "It reaches Docker without its Docker gate, so nothing below the "
                "manager itself keeps it to its own containers and folders. Since "
                "v0.65.0 every install runs its own gate, which is also what lets "
                "several managers share one machine safely."
            ),
            "action": (
                "Whoever manages this machine should refresh the compose file and "
                "recreate the stack. With the Linux installer's rsm command: run "
                "'sudo rsm update' and accept the new compose file. Manual installs: "
                "download the new compose file, then run 'docker compose down' and "
                "'docker compose up -d'. Windows: re-run the installer. The v0.65.0 "
                "release notes have the details."
            ),
        }
    if info.get("stack") != stack():
        return {
            "id": "docker_gate_stack_mismatch",
            "severity": "danger",
            "title": "This manager and its Docker gate disagree about the stack",
            "detail": (
                f"The manager is stack '{stack()}' but its gate serves stack "
                f"'{info.get('stack')}', so the gate stamps new servers with the "
                f"other name and this manager will not find them again."
            ),
            "action": (
                "Whoever manages this machine should make RSM_STACK the same for both "
                "(it comes from .env; the compose file passes it to both containers), "
                "then recreate the stack."
            ),
        }
    return None


# --------------------------------------------------------------------------- #
# Is Docker new enough to mount a folder of a volume? (#206)
# --------------------------------------------------------------------------- #
# VolumeOptions.Subpath arrived with API 1.45, Docker Engine 26.0. An older
# daemon rejects nothing — it ignores the subpath and mounts the whole volume —
# so the gate refuses such mounts there, and the GUI says why servers fail.
SUBPATH_MIN_API = (1, 45)

_engine: tuple[str, tuple[int, int]] | None = None


def _engine_version() -> tuple[str, tuple[int, int]] | None:
    """(engine version, API version) of the daemon, or None when unreadable."""
    global _engine
    if _engine is None:
        try:
            v = get_client().version()
            major, minor = (int(p) for p in str(v.get("ApiVersion", "")).split(".")[:2])
        except (DockerException, OSError, ValueError, AttributeError) as exc:
            logger.info("Could not read the Docker version: %s", exc)
            return None  # not cached: ask again next time
        _engine = (str(v.get("Version", "")), (major, minor))
    return _engine


def uses_volumes() -> bool:
    """Is this manager's data a named volume (the compose files since v0.65.0)?"""
    found = _own_mount(config.settings.data_dir)
    return found is not None and found[0].get("Type") == "volume"


def engine_warning() -> dict | None:
    """A GUI warning when Docker is too old for the volume mounts servers need."""
    if not uses_volumes():
        return None
    engine = _engine_version()
    if engine is None or engine[1] >= SUBPATH_MIN_API:
        return None  # new enough — or not known yet, and never warn on a blind read
    return {
        "id": "docker_engine_too_old",
        "severity": "danger",
        "title": f"Docker Engine {engine[0]} is too old: servers cannot start",
        "detail": (
            "Since v0.65.0 each server mounts only its own folder of this install's "
            "data volume, which needs Docker Engine 26.0 or newer. Until Docker is "
            "updated, starting a server, downloading server files and the other "
            "jobs that run in a container fail."
        ),
        "action": (
            "Whoever manages this machine should update Docker. On Linux the official "
            "installer upgrades an existing install: curl -fsSL https://get.docker.com "
            "| sudo sh. On Windows, update Docker Desktop."
        ),
    }


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
