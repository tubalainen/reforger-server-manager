"""What a manager may ask of the Docker API — the rules, with no I/O (#204).

The gate (gate/app.py) holds the real Docker socket. Every request a manager
sends goes through route(); container-scoped requests are then checked against
the container's labels with stacks.owns(), and a container create is rewritten
by check_create(). Anything not named here is refused: default deny.

The point is that a manager — even a compromised one — can only see and touch
its own stack's containers, only start containers from the two images it
actually uses, and only mount its own folders. Before this, the socket proxy
filtered by URL alone, so anything that reached it could create a privileged
container that mounted the host's root (security review R1).
"""
import posixpath
import re
from dataclasses import dataclass, field

from docker.utils import parse_repository_tag

import stacks


class Denied(Exception):
    """The request is refused; str(exc) is the reason handed back to the client."""


@dataclass(frozen=True)
class Scope:
    """Everything the rules need to know about the stack this gate serves."""
    stack: str
    # Images containers may be created from: the game server and the helper.
    images: frozenset[str]
    # Of those, the ones allowed host networking — the game server only (#150).
    host_network_images: frozenset[str]
    # Networks containers may join, besides Docker's built-in bridge/none.
    networks: frozenset[str]
    # Host folders bind mounts must live under (this stack's data + server files).
    bind_roots: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Route:
    kind: str
    ref: str | None = None
    action: str | None = None
    write: bool = False


# Docker prefixes most paths with the API version: /v1.47/containers/json.
_VERSION = r"(?:/v\d+\.\d+)?"
# A container or network id or name. No '%': the path is matched raw and
# forwarded raw, so what is checked here is exactly what the daemon receives.
_REF = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
# An image reference: registry/repo:tag or repo@sha256:digest. docker-py sends
# the '@' of a digest as %40, the one escape allowed anywhere in a path here.
_IMAGE = r"[A-Za-z0-9](?:[A-Za-z0-9._/:@-]|%40)*"

_ROUTES = [
    ({"GET", "HEAD"}, r"/_ping", "ping", False),
    ({"GET"}, r"/version", "version", False),
    ({"GET"}, r"/info", "info", False),
    ({"GET"}, r"/_rsm/gate", "gate", False),
    ({"GET"}, r"/containers/json", "list", False),
    ({"POST"}, r"/containers/create", "create", True),
    ({"GET"}, rf"/containers/(?P<ref>{_REF})/(?P<action>json|logs|stats|top)", "container", False),
    ({"POST"}, rf"/containers/(?P<ref>{_REF})/(?P<action>start|stop|restart|kill|wait|update)",
     "container", True),
    ({"DELETE"}, rf"/containers/(?P<ref>{_REF})", "container", True),
    ({"GET"}, rf"/images/(?P<ref>{_IMAGE})/json", "image", False),
    ({"POST"}, r"/images/create", "pull", True),
    ({"GET"}, rf"/networks/(?P<ref>{_REF})", "network", False),
]
_COMPILED = [
    (methods, re.compile(rf"{_VERSION}{pattern}"), kind, write)
    for methods, pattern, kind, write in _ROUTES
]


def route(method: str, raw_path: str) -> Route:
    """Classify a request, or raise Denied."""
    if "//" in raw_path or "/../" in raw_path or raw_path.endswith("/.."):
        raise Denied(f"{method} {raw_path} is not allowed")
    for methods, rx, kind, write in _COMPILED:
        m = rx.fullmatch(raw_path)
        if m and method in methods:
            groups = m.groupdict()
            action = groups.get("action") or ("delete" if method == "DELETE" else None)
            return Route(kind, groups.get("ref"), action, write)
    raise Denied(f"{method} {raw_path} is not allowed through the Docker gate")


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
def image_key(ref: str, tag: str | None = None) -> tuple[str, str]:
    """(repository, tag-or-digest), normalised the way docker-py pulls it."""
    repo, embedded = parse_repository_tag(ref)
    return repo, (tag or embedded or "latest")


def image_allowed(scope: Scope, ref: str, tag: str | None = None) -> bool:
    key = image_key(ref, tag)
    return any(image_key(i) == key for i in scope.images)


# --------------------------------------------------------------------------- #
# Container create
# --------------------------------------------------------------------------- #
# HostConfig settings that hand a container the host, or another container:
# refused whenever they are set to anything at all.
_MUST_BE_EMPTY = (
    "Privileged", "CapAdd", "Devices", "DeviceRequests", "DeviceCgroupRules",
    "VolumesFrom", "Sysctls", "CgroupParent", "VolumeDriver", "Links", "Annotations",
)
# Set to an empty list, these UNMASK /proc and /sys — so refused when present.
_MUST_BE_ABSENT = ("ReadonlyPaths", "MaskedPaths")
# Namespace settings: 'host' or another container's namespace is refused.
_NAMESPACES = ("PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode")
_BUILTIN_NETWORKS = {"", "default", "bridge", "none"}
_NO_NEW_PRIVS = {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"}


def _under_roots(path: str, roots: tuple[str, ...]) -> bool:
    if not path.startswith("/"):
        return False  # a named volume, not a host folder
    norm = posixpath.normpath(path)
    return any(norm == r or norm.startswith(r.rstrip("/") + "/") for r in roots)


def _check_binds(host: dict, scope: Scope) -> None:
    for entry in host.get("Binds") or []:
        source = str(entry).split(":", 1)[0]
        if not _under_roots(source, scope.bind_roots):
            raise Denied(f"bind mount of {source!r} is outside this stack's folders")
    for mount in host.get("Mounts") or []:
        kind = (mount or {}).get("Type", "volume")
        if kind == "tmpfs":
            continue
        source = str((mount or {}).get("Source") or "")
        if kind != "bind" or not _under_roots(source, scope.bind_roots):
            raise Denied(f"{kind} mount of {source!r} is outside this stack's folders")


def _check_network(body: dict, host: dict, image: str, scope: Scope) -> None:
    allowed = _BUILTIN_NETWORKS | set(scope.networks)
    mode = str(host.get("NetworkMode") or "")
    if mode == "host":
        if not any(image_key(image) == image_key(i) for i in scope.host_network_images):
            raise Denied("host networking is only allowed for the game server image")
    elif mode not in allowed:
        raise Denied(f"network {mode!r} is not this stack's")
    endpoints = ((body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {})
    for name in endpoints:
        if name not in allowed:
            raise Denied(f"network {name!r} is not this stack's")


def check_create(body: dict, name: str | None, scope: Scope) -> dict:
    """Validate a container-create request; return the body to send on.

    The returned body carries this stack's labels whatever the client sent, so a
    container can never be created outside the stack the gate serves.
    """
    if not isinstance(body, dict):
        raise Denied("container create needs a JSON object")
    if name is not None and not name.startswith(f"{scope.stack}-"):
        raise Denied(f"container name {name!r} must start with '{scope.stack}-'")
    image = str(body.get("Image") or "")
    if not image_allowed(scope, image):
        raise Denied(f"image {image!r} is not one this stack runs")

    host = body.get("HostConfig")
    host = {} if host is None else host
    if not isinstance(host, dict):
        raise Denied("HostConfig must be an object")
    for key in _MUST_BE_EMPTY:
        if host.get(key):
            raise Denied(f"HostConfig.{key} is not allowed")
    for key in _MUST_BE_ABSENT:
        if host.get(key) is not None:
            raise Denied(f"HostConfig.{key} is not allowed")
    for key in _NAMESPACES:
        value = str(host.get(key) or "")
        if value == "host" or value.startswith("container:"):
            raise Denied(f"HostConfig.{key}={value!r} is not allowed")
    for opt in host.get("SecurityOpt") or []:
        if str(opt) not in _NO_NEW_PRIVS:
            raise Denied(f"security option {opt!r} is not allowed")
    if str(host.get("Runtime") or "runc") != "runc":
        raise Denied(f"runtime {host.get('Runtime')!r} is not allowed")
    _check_binds(host, scope)
    _check_network(body, host, image, scope)

    labels = body.get("Labels") or {}
    if not isinstance(labels, dict):
        raise Denied("Labels must be an object")
    return {
        **body,
        "Labels": {**labels, stacks.LABEL_MANAGED: "true", stacks.LABEL_STACK: scope.stack},
    }


def check_update(body: dict) -> dict:
    """A container update may change its restart policy and nothing else.

    The manager uses it for the auto-start / auto-restart toggles (#26). The same
    endpoint also takes resource settings, device rules among them, so the body
    is held to exactly that one key.
    """
    if not isinstance(body, dict) or set(body) - {"RestartPolicy"}:
        raise Denied("a container update may only change its restart policy")
    return body


# --------------------------------------------------------------------------- #
# Answers that are filtered on the way back
# --------------------------------------------------------------------------- #
def filter_list(containers: list, scope: Scope) -> list:
    """Only this stack's containers — the rest of the host does not exist."""
    return [c for c in containers if stacks.owns((c or {}).get("Labels"), scope.stack)]


# `docker info` without the host-wide counts and names: a manager needs to know
# what it runs on, not how busy the other stacks are.
_INFO_KEYS = (
    "OperatingSystem", "OSType", "OSVersion", "Architecture", "KernelVersion",
    "ServerVersion", "NCPU", "MemTotal",
)


def filter_info(info: dict) -> dict:
    return {k: info[k] for k in _INFO_KEYS if k in info}
