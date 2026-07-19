"""Server instance lifecycle: one Docker container per Arma Reforger server.

Instances are sibling containers created from REFORGER_SERVER_IMAGE (ACE Mod's
image by default). Each instance gets unique host UDP ports, a per-instance
config.json rendered from its template with those ports baked in, and its own
profile / workshop / configs directories under DATA_DIR. Containers are labeled
so they are rediscovered after a manager restart.

Server-file reuse: the shared ./serverfiles/<branch> folder (populated by the
Downloads page) is mounted at /reforger, so instances of the same branch share
one install instead of each re-downloading. The image still validates on start
(SKIP_INSTALL=false) so a server also works if the folder was never pre-warmed.
"""
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from docker.errors import DockerException, ImageNotFound, NotFound
from sqlmodel import Session, select

import config
from models import Instance, Template, get_engine
from services import docker_service, ports

# Pure log parsing lives in server_log (#88). Re-exported so callers and tests keep
# using instance_service.parse_* unchanged.
from services.server_log import (  # noqa: F401
    _FPS_RE,
    _MEM_RE,
    _ONLINE_RE,
    _PLAYERS_CONN_RE,
    _PLAYERS_STAT_RE,
    _REGISTERED_ADDR_RE,
    STATE_ONLINE,
    STATE_STARTING,
    parse_public_address,
    parse_server_state,
    parse_server_status,
)
from services.template_service import LaunchParams

logger = logging.getLogger("manager.instance")

# docker's one-shot stats endpoint only answers after it has collected TWO CPU
# samples, so _docker_cpu_mem blocks for a second or two. It used to be called
# straight from instance_stats, i.e. on every 5-second poll of an open detail page,
# tying up a worker for that whole time (#87). It is now sampled in the background
# and the request serves the last reading — a couple of seconds stale at worst, for
# a number that is a moving average anyway.
_CPU_SAMPLE_TTL = 12.0
_cpu_cache: dict[int, tuple[float, dict]] = {}
_cpu_sampling: set[int] = set()
_cpu_lock = threading.Lock()


def cpu_mem_for(instance_id: int, container) -> dict:
    """The last CPU/memory sample for an instance; refreshes it in the background.

    Never blocks the caller. The first ever call returns {} (the GUI shows "—"),
    and the sample lands a second or two later.
    """
    now = time.monotonic()
    sampled_at, sample = _cpu_cache.get(instance_id, (0.0, {}))
    if now - sampled_at < _CPU_SAMPLE_TTL:
        return sample

    with _cpu_lock:
        if instance_id in _cpu_sampling:
            return sample  # a refresh is already in flight
        _cpu_sampling.add(instance_id)

    def refresh():
        try:
            fresh = _docker_cpu_mem(container)
            if fresh:
                _cpu_cache[instance_id] = (time.monotonic(), fresh)
        finally:
            with _cpu_lock:
                _cpu_sampling.discard(instance_id)

    threading.Thread(target=refresh, daemon=True).start()
    return sample


def forget_cpu_sample(instance_id: int) -> None:
    """Drop an instance's cached CPU/memory sample (it is being deleted)."""
    _cpu_cache.pop(instance_id, None)


def _docker_cpu_mem(container) -> dict:
    """One-shot CPU%/memory from docker stats (best-effort; {} on failure).

    Slow by construction — see _CPU_SAMPLE_TTL. Call it through cpu_mem_for(),
    not from a request.
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

# Ports are published 1:1 — the host port equals the container bind port equals
# the advertised public port, which is what Reforger's networking (and the A2S
# server-browser query) requires. The server binds these exact ports inside the
# container (config.json + SERVER_BIND_PORT), and Docker publishes them unchanged.
CONFIG_FILENAME = "server.json"

# The server only prints its periodic FPS/player status line when launched with
# -logStats <interval-ms> (issue #38). The manager injects it automatically so
# the GUI status bar has data; a shorter interval keeps a fresh line inside the
# tail we read, at the cost of slightly noisier logs.
STATS_LOG_INTERVAL_MS = 10000
STATS_LOG_ARG = "-logStats"
# Reforger logs are chatty, so read a generous tail to be sure a periodic stats
# line (emitted every STATS_LOG_INTERVAL_MS) is inside the window we parse.
STATS_LOG_TAIL = 400

# The dedicated-server binary the image launches from /reforger (WORKDIR).
# Its presence is our proof that a branch's server files are installed.
SERVER_BINARY = "ArmaReforgerServer"

# Where the server keeps its own state INSIDE the container. Both are bind-mounted
# to data/instances/<id>/{profile,workshop} on the host, so a save survives a
# container rebuild, an image update, and a `docker rm` (issue #79).
#
# The image takes these from ARMA_PROFILE / ARMA_WORKSHOP_DIR (acemod's launch.py
# passes them as -profile and -addonsDir/-addonDownloadDir) and merely DEFAULTS
# them to these paths. We set them explicitly rather than inherit the defaults:
# if the image ever changed one, the server would write the persistent save into
# the container's own filesystem — where the next container rebuild would take it
# with it, silently. The mount and the env now say the same thing on purpose.
#
# The persistent save lands under <profile>/.save/game.
PROFILE_DIR = "/home/profile"
WORKSHOP_DIR = "/reforger/workshop"
CONFIGS_DIR = "/reforger/Configs"


class InstanceError(Exception):
    """User-facing instance operation failure."""


def server_files_ready(branch: str) -> bool:
    """True when the branch's server files have been downloaded.

    We require the actual server binary (not just Steam's manifest) so a
    partial or interrupted download does not count as ready.
    """
    binary = Path(config.settings.serverfiles_dir) / branch / SERVER_BINARY
    return binary.is_file()


# --------------------------------------------------------------------------- #
# Config rendering
# --------------------------------------------------------------------------- #

def render_instance_config(template_config: str, inst: Instance, public_address: str) -> dict:
    """Template config.json + this instance's ports/address baked in.

    Ports are used identically inside and outside the container (host port ==
    bind port == public port), which is what Reforger's networking expects.
    """
    cfg = json.loads(template_config)
    cfg["bindPort"] = inst.game_port
    cfg["publicPort"] = inst.game_port
    if public_address:
        cfg["publicAddress"] = public_address
    a2s = cfg.get("a2s") or {}
    a2s["port"] = inst.a2s_port
    a2s.setdefault("address", "0.0.0.0")
    cfg["a2s"] = a2s
    if cfg.get("rcon"):
        cfg["rcon"]["port"] = inst.rcon_port
    return cfg


# --------------------------------------------------------------------------- #
# Filesystem helpers
# --------------------------------------------------------------------------- #

def _instance_dir(inst: Instance) -> Path:
    return Path(config.settings.data_dir) / "instances" / str(inst.id)


def _write_config(inst: Instance, template_config: str) -> Path:
    """Write the per-instance config.json; return its in-container path."""
    configs_dir = _instance_dir(inst) / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    (_instance_dir(inst) / "profile").mkdir(parents=True, exist_ok=True)
    (_instance_dir(inst) / "workshop").mkdir(parents=True, exist_ok=True)
    rendered = render_instance_config(template_config, inst, config.settings.public_address)
    (configs_dir / CONFIG_FILENAME).write_text(json.dumps(rendered, indent=2), encoding="utf-8")
    return configs_dir / CONFIG_FILENAME


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def _all_instances(session: Session) -> list[Instance]:
    return list(session.exec(select(Instance)).all())


def used_ports(session: Session, exclude_id: int | None = None) -> tuple[set, set, set]:
    game, a2s, rcon = set(), set(), set()
    for inst in _all_instances(session):
        if inst.id == exclude_id:
            continue
        game.add(inst.game_port)
        a2s.add(inst.a2s_port)
        rcon.add(inst.rcon_port)
    return game, a2s, rcon


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

def create_instance(
    name: str,
    template_id: int,
    branch: str,
    game_port: int | None = None,
    a2s_port: int | None = None,
    rcon_port: int | None = None,
) -> Instance:
    """Create an instance. Ports are auto-leased unless explicitly given.

    Explicit ports are validated against the other instances so two servers
    never collide on a host port; auto-leased ports come from the .env ranges.
    """
    if branch not in config.BRANCHES:
        raise InstanceError(f"Unknown branch '{branch}'")
    with Session(get_engine()) as session:
        if session.exec(select(Instance).where(Instance.name == name)).first():
            raise InstanceError(f"An instance named '{name}' already exists")
        if not session.get(Template, template_id):
            raise InstanceError("Template not found")
        g_used, a_used, r_used = used_ports(session)

        game = _resolve_port("game", game_port, g_used, config.settings.game_port_range)
        a2s = _resolve_port("A2S", a2s_port, a_used, config.settings.a2s_port_range)
        rcon = _resolve_port("RCON", rcon_port, r_used, config.settings.rcon_port_range)

        inst = Instance(
            name=name, template_id=template_id, branch=branch,
            game_port=game, a2s_port=a2s, rcon_port=rcon,
        )
        session.add(inst)
        session.commit()
        session.refresh(inst)
        logger.info("Created instance %s (ports g=%s a2s=%s rcon=%s)", name, game, a2s, rcon)
        return inst


def _resolve_port(kind: str, requested: int | None, used: set[int], rng: tuple[int, int]) -> int:
    """Validate an explicit port, or auto-lease the first free one in range."""
    if requested is not None:
        if not (1 <= requested <= 65535):
            raise InstanceError(f"{kind} port {requested} is out of range")
        if requested in used:
            raise InstanceError(f"{kind} port {requested} is already used by another instance")
        return requested
    try:
        return ports.first_free(*rng, used)
    except ports.PortExhaustedError as exc:
        raise InstanceError(str(exc)) from exc


def update_ports(
    instance_id: int,
    game_port: int | None,
    a2s_port: int | None,
    rcon_port: int | None,
) -> None:
    """Change a stopped instance's host ports (issue #8).

    The container bakes ports in at creation, so any existing container is
    removed here and recreated with the new ports on the next start.
    """
    if container_status(instance_id) == "running":
        raise InstanceError("Stop the instance before changing its ports")
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        g_used, a_used, r_used = used_ports(session, exclude_id=instance_id)
        inst.game_port = _resolve_port(
            "game", game_port if game_port is not None else inst.game_port,
            g_used, config.settings.game_port_range,
        )
        inst.a2s_port = _resolve_port(
            "A2S", a2s_port if a2s_port is not None else inst.a2s_port,
            a_used, config.settings.a2s_port_range,
        )
        inst.rcon_port = _resolve_port(
            "RCON", rcon_port if rcon_port is not None else inst.rcon_port,
            r_used, config.settings.rcon_port_range,
        )
        session.add(inst)
        session.commit()
    container = docker_service.find_instance_container(instance_id)
    if container:
        try:
            container.remove(force=True)  # recreated with new ports on next start
        except DockerException as exc:
            logger.warning("Could not remove container for %s: %s", instance_id, exc)


def set_instance_template(instance_id: int, template_id: int) -> None:
    """Repoint a stopped instance at a different template (issue #31).

    The container bakes its rendered config at creation, so any existing
    container is removed here and recreated from the new template on next start
    (same mechanism as a port change). Blocked while the server is running.
    """
    if container_status(instance_id) == "running":
        raise InstanceError("Stop the instance before changing its template")
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        if not session.get(Template, template_id):
            raise InstanceError("Template not found")
        inst.template_id = template_id
        session.add(inst)
        session.commit()
    container = docker_service.find_instance_container(instance_id)
    if container:
        try:
            container.remove(force=True)  # recreated from the new template on next start
        except DockerException as exc:
            logger.warning("Could not remove container for %s: %s", instance_id, exc)


def instances_using_template(template_id: int) -> list[dict]:
    """Instances bound to a template, as [{id, name, status}] (issue #31).

    Used to block deleting a template that instances still reference.
    """
    with Session(get_engine()) as session:
        insts = [i for i in _all_instances(session) if i.template_id == template_id]
    docker_up = docker_service.ping()
    return [
        {
            "id": i.id,
            "name": i.name,
            "status": container_status(i.id) if docker_up else "unknown",
        }
        for i in insts
    ]


def edit_instance(
    instance_id: int, name: str | None = None, branch: str | None = None
) -> None:
    """Edit an instance's name and/or branch after creation (issue #27).

    Renaming is allowed anytime (the container is keyed by id, not name).
    Changing branch swaps the mounted server files + Steam app id, so it is
    only allowed while stopped and rebuilds the container on next start.
    """
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        branch_changing = branch is not None and branch != inst.branch
        if branch_changing:
            if branch not in config.BRANCHES:
                raise InstanceError(f"Unknown branch '{branch}'")
            if container_status(instance_id) == "running":
                raise InstanceError("Stop the instance before changing its branch")
        if name is not None and name != inst.name:
            clash = session.exec(
                select(Instance).where(Instance.name == name, Instance.id != instance_id)
            ).first()
            if clash:
                raise InstanceError(f"An instance named '{name}' already exists")
            inst.name = name
        if branch_changing:
            inst.branch = branch
        session.add(inst)
        session.commit()
    if branch_changing:
        # Recreate the container for the new branch's files/app id on next start.
        container = docker_service.find_instance_container(instance_id)
        if container:
            try:
                container.remove(force=True)
            except DockerException as exc:
                logger.warning("Could not remove container for %s: %s", instance_id, exc)


def _template_config(session: Session, inst: Instance) -> str:
    template = session.get(Template, inst.template_id)
    if not template:
        raise InstanceError("Template for this instance no longer exists")
    return template.config_json


def _template_launch(session: Session, inst: Instance) -> LaunchParams:
    template = session.get(Template, inst.template_id)
    raw = (template.launch_params_json if template else None) or "{}"
    try:
        return LaunchParams.model_validate_json(raw)
    except ValueError:
        return LaunchParams()


def _desired_port_bindings(inst: Instance) -> dict:
    """The 1:1 UDP publish map (host == container) for this instance's ports."""
    return {
        f"{inst.game_port}/udp": inst.game_port,
        f"{inst.a2s_port}/udp": inst.a2s_port,
        f"{inst.rcon_port}/udp": inst.rcon_port,
    }


def _container_ports_match(container, inst: Instance) -> bool:
    """True if the container already publishes this instance's exact ports.

    Docker fixes port bindings at container creation, so an instance whose
    container predates a port-model change (e.g. the A2S 1:1 fix in v0.16.0)
    keeps the old, broken mapping until the container is recreated. start_instance
    uses this to self-heal instead of silently reusing a stale container.
    On any read failure we return True — never destroy a container we can't inspect.
    """
    try:
        container.reload()
        bindings = (container.attrs.get("HostConfig") or {}).get("PortBindings") or {}
    except (DockerException, NotFound, AttributeError):
        return True
    actual: dict[str, int] = {}
    for cport, binds in bindings.items():
        if binds:
            try:
                actual[cport] = int(binds[0].get("HostPort"))
            except (TypeError, ValueError):
                pass
    return actual == _desired_port_bindings(inst)


def _desired_environment(inst: Instance, launch: "LaunchParams | None") -> dict:
    """The environment an instance's container should be created with."""
    environment = {
        "STEAM_APPID": config.BRANCHES[inst.branch]["app_id"],
        # Never self-install: instances run the files fetched on the Downloads
        # tab and mounted at /reforger. Downloading is gated in start_instance.
        "SKIP_INSTALL": "true",
        "ARMA_CONFIG": CONFIG_FILENAME,
        # Pin the profile and addons dirs to the paths we bind-mount, so the
        # persistent save (<profile>/.save/game) and the baked mods always land
        # on the host and never inside the container (#79).
        "ARMA_PROFILE": PROFILE_DIR,
        "ARMA_WORKSHOP_DIR": WORKSHOP_DIR,
        "SERVER_BIND_PORT": str(inst.game_port),
        "SERVER_PUBLIC_PORT": str(inst.game_port),
    }
    if config.settings.public_address:
        environment["SERVER_PUBLIC_ADDRESS"] = config.settings.public_address
    arma_params, max_fps = launch.render() if launch is not None else ("", None)
    environment["ARMA_PARAMS"] = _inject_stats_logging(arma_params)
    if max_fps is not None:
        environment["ARMA_MAX_FPS"] = str(max_fps)
    return environment


def _container_env_matches(container, desired: dict) -> bool:
    """True if the container already carries the environment we would give it now.

    Docker bakes environment variables at container CREATION, so a template's
    engine launch parameters (ARMA_PARAMS / ARMA_MAX_FPS) edited after the fact
    were silently ignored on a plain stop→start — the container simply kept the
    old ones (#79). start_instance recreates on a mismatch, which also subsumes
    the old -logStats check (#38), since -logStats is part of what we want.

    Only the keys we set are compared; the image's own variables are ignored.
    On any read failure we return True — never destroy a container we cannot
    inspect.
    """
    try:
        container.reload()
        env_list = (container.attrs.get("Config") or {}).get("Env") or []
    except (DockerException, NotFound, AttributeError):
        return True
    actual = {}
    for entry in env_list:
        key, _, value = str(entry).partition("=")
        actual[key] = value
    return all(actual.get(key) == value for key, value in desired.items())


def _container_network_ok(container) -> bool:
    """True if the container is attached to the configured Docker network AND
    that attachment points at the network that exists right now.

    A container can silently lose its network: `docker network disconnect`
    (the manual workaround people used for the compose-down failure, #113),
    or the network being removed and recreated (compose down/up) while the
    stopped container still references the old network id. Such a container
    starts fine but has no working DNS — the server logs 'Curl error=Could
    not resolve hostname' and never reaches the Reforger backend (#113).
    start_instance recreates it instead, which re-attaches it cleanly.

    On any read failure return True — never destroy a container we cannot
    inspect. An unreadable/absent network also returns True: recreating the
    container cannot conjure the network, so let the start fail loudly.
    """
    net_name = config.settings.docker_network
    try:
        container.reload()
        endpoints = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
        endpoint = endpoints.get(net_name)
        if not endpoint:
            return False  # disconnected (or never attached) — recreate
        current = docker_service.get_client().networks.get(net_name)
        endpoint_id = endpoint.get("NetworkID")
        # Stopped containers may report an empty NetworkID; nothing to compare then.
        return not endpoint_id or endpoint_id == current.id
    except (DockerException, NotFound, AttributeError):
        return True


def start_instance(instance_id: int) -> None:
    if not docker_service.ping():
        raise InstanceError("Docker daemon is not reachable")
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        if not server_files_ready(inst.branch):
            raise InstanceError(
                f"The {inst.branch} server files are not downloaded yet — "
                "download them on the Downloads tab first"
            )
        template_config = _template_config(session, inst)
        launch = _template_launch(session, inst)

        # Reuse an existing container if present, else create one. But recreate
        # it when its baked-in settings are stale, since Docker fixes them at
        # creation: published ports (e.g. predating the A2S 1:1 fix) or a missing
        # -logStats arg (instances created before the FPS/players fix, #38).
        container = docker_service.find_instance_container(inst.id)
        if container is not None:
            reason = None
            if not _container_ports_match(container, inst):
                reason = "published ports changed"
            elif not _container_env_matches(container, _desired_environment(inst, launch)):
                reason = "launch parameters or server environment changed"
            elif not _container_network_ok(container):
                reason = "network attachment is missing or stale"
            if reason:
                logger.info("Recreating container for %s: %s", inst.name, reason)
                try:
                    container.remove(force=True)
                except DockerException as exc:
                    logger.warning("Could not remove stale container for %s: %s", inst.name, exc)
                container = None
        if container is None:
            self_config_path = _write_config(inst, template_config)
            try:
                container = _create_container(inst, self_config_path, launch)
            except ImageNotFound as exc:
                raise InstanceError(
                    f"Server image '{config.settings.reforger_server_image}' not found — "
                    "pull it on the Downloads tab (or set REFORGER_SERVER_IMAGE)"
                ) from exc
            except DockerException as exc:
                raise InstanceError(f"Could not create container: {exc}") from exc
        else:
            # Re-render config in case the template changed since last start.
            _write_config(inst, template_config)

        try:
            container.start()
        except DockerException as exc:
            raise InstanceError(f"Could not start container: {exc}") from exc
        # A fresh run must prove it is online from its own log (#76).
        forget_run(container.id)
        inst.desired_state = "running"
        session.add(inst)
        session.commit()
        logger.info("Started instance %s (container %s)", inst.name, container.id[:12])


def _create_container(inst: Instance, config_path: Path, launch: "LaunchParams | None" = None):
    """Create (not start) the sibling server container for an instance."""
    idir = _instance_dir(inst)
    serverfiles_host = docker_service.host_path_for(
        f"{config.settings.serverfiles_dir}/{inst.branch}"
    )
    Path(f"{config.settings.serverfiles_dir}/{inst.branch}").mkdir(parents=True, exist_ok=True)

    volumes = {
        serverfiles_host: {"bind": "/reforger", "mode": "rw"},
        docker_service.host_path_for(str(idir / "configs")): {"bind": CONFIGS_DIR, "mode": "rw"},
        docker_service.host_path_for(str(idir / "profile")): {"bind": PROFILE_DIR, "mode": "rw"},
        docker_service.host_path_for(str(idir / "workshop")): {"bind": WORKSHOP_DIR, "mode": "rw"},
    }
    # Publish each UDP port unchanged (host == container). The game port used to
    # be remapped to a fixed internal 2001, but A2S/RCON had no matching override,
    # so the server bound the host port number inside the container while Docker
    # forwarded a *different* internal port — breaking A2S/server-browser queries.
    port_bindings = _desired_port_bindings(inst)
    environment = _desired_environment(inst, launch)

    return docker_service.get_client().containers.create(
        config.settings.reforger_server_image,
        name=f"reforger-instance-{inst.id}",
        detach=True,
        environment=environment,
        volumes=volumes,
        ports=port_bindings,
        labels={
            docker_service.LABEL_MANAGED: "true",
            docker_service.LABEL_ROLE: docker_service.ROLE_INSTANCE,
            docker_service.LABEL_BRANCH: inst.branch,
            docker_service.LABEL_INSTANCE_ID: str(inst.id),
        },
        network=config.settings.docker_network,
        restart_policy={"Name": _restart_policy(inst)},
    )


def _inject_stats_logging(arma_params: str) -> str:
    """Ensure -logStats is present so the server emits FPS/player lines (#38).

    Left untouched if the template already set it via extra_args, so a user's
    explicit interval wins.
    """
    if STATS_LOG_ARG in arma_params:
        return arma_params
    return f"{STATS_LOG_ARG} {STATS_LOG_INTERVAL_MS} {arma_params}".strip()


def _restart_policy(inst: Instance) -> str:
    """Map the two toggles to a Docker restart policy (issue #26).

    - auto_start  -> 'unless-stopped': Docker restarts it on daemon/host
      restart (and, inherently, on crash).
    - auto_restart only -> 'on-failure': restarts on a crash but not on a
      clean daemon restart.
    - neither -> 'no'.
    """
    if inst.auto_start:
        return "unless-stopped"
    if inst.auto_restart:
        return "on-failure"
    return "no"


def stop_instance(instance_id: int) -> None:
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        inst.desired_state = "stopped"
        session.add(inst)
        session.commit()
    forget_seen_running(instance_id)  # a user stop is not a crash to recover from (#115)
    container = docker_service.find_instance_container(instance_id)
    if container:
        forget_run(container.id)  # whatever it was, it is not online any more (#76)
        try:
            container.stop(timeout=30)
        except DockerException as exc:
            logger.warning("Stop of instance %s failed: %s", instance_id, exc)


def delete_instance(instance_id: int) -> None:
    container = docker_service.find_instance_container(instance_id)
    if container:
        forget_run(container.id)  # don't leak the "this run was online" memo (#88)
        try:
            container.remove(force=True)
        except DockerException as exc:
            logger.warning("Removing container for instance %s failed: %s", instance_id, exc)
    forget_cpu_sample(instance_id)
    forget_seen_running(instance_id)
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if inst:
            session.delete(inst)
            session.commit()
    logger.info("Deleted instance %s", instance_id)


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

def status_of(container) -> str:
    """running | exited | created | absent (no container) | unknown."""
    if container is None:
        return "absent"
    try:
        return container.status
    except (DockerException, NotFound, AttributeError):
        return "unknown"


def container_status(instance_id: int, containers: dict | None = None) -> str:
    """Status of one instance's container.

    Pass `containers` (from docker_service.instance_containers()) when reporting on
    several instances at once: the container is then read out of that one snapshot
    instead of costing its own daemon lookup (#87). The containers in a snapshot are
    already inspected, so no reload() is needed either way.
    """
    if containers is not None:
        return status_of(containers.get(instance_id))
    return status_of(docker_service.find_instance_container(instance_id))


def _template_changed_since_start(container, template_updated_at) -> bool:
    """True if the template was edited after this running container started (#116).

    The container keeps the config.json it was created with, so a template
    edited since then is not live until the server is restarted. updated_at
    comes back naive (UTC) from SQLite; StartedAt is tz-aware — coerce before
    comparing. On any missing piece, don't claim a change.
    """
    if container is None or template_updated_at is None:
        return False
    started = _started_at(container)
    if started is None:
        return False
    updated = template_updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return updated > started


def instance_view(
    inst: Instance,
    template_name: str | None,
    containers: dict | None = None,
    docker_up: bool | None = None,
    template_updated_at=None,
) -> dict:
    # docker_up/containers are passed in by list_views, which resolves them ONCE for
    # the whole page. This used to ping the daemon per instance, inside a list
    # comprehension (#87).
    if docker_up is None:
        docker_up = docker_service.ping()
    status = container_status(inst.id, containers) if docker_up else "unknown"
    container = containers.get(inst.id) if containers else None
    template_changed = (
        status == "running"
        and _template_changed_since_start(container, template_updated_at)
    )
    return {
        "id": inst.id,
        "name": inst.name,
        "branch": inst.branch,
        "template_id": inst.template_id,
        "template_name": template_name,
        # True when the template has been edited since this running server
        # started, so its live config is stale until restarted (#116).
        "template_changed": template_changed,
        "game_port": inst.game_port,
        "a2s_port": inst.a2s_port,
        "rcon_port": inst.rcon_port,
        "desired_state": inst.desired_state,
        "auto_restart": inst.auto_restart,
        "auto_start": inst.auto_start,
        "restart_times": schedule_times(inst),
        "next_restart": next_restart_label(inst),
        "status": status,
        "server_files_ready": server_files_ready(inst.branch),
    }


def running_instance_names_for_branch(branch: str) -> list[str]:
    """Names of instances on this branch whose container is currently running."""
    names = []
    with Session(get_engine()) as session:
        instances = [i for i in _all_instances(session) if i.branch == branch]
    for inst in instances:
        if container_status(inst.id) == "running":
            names.append(inst.name)
    return names


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


def instance_stats(instance_id: int) -> dict:
    """Live runtime info for the instance status bar.

    status/uptime come from the Docker container; players/FPS/server-memory come
    from the server's own periodic status line in the log; CPU and container
    memory come from docker stats. connect is the advertised address (when set).
    """
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        game_port = inst.game_port

    public = config.settings.public_address
    # ONE lookup for the container, reused for status, uptime, logs and cpu/mem.
    # This used to look it up for the status, look it up AGAIN, and then reload()
    # it — three daemon round-trips for one container (#87).
    container = docker_service.find_instance_container(instance_id)
    stats: dict = {
        "public_address": public or None,
        "public_address_detected": False,
        "game_port": game_port,
        "connect": f"{public}:{game_port}" if public else None,
        "status": status_of(container),
        # Whether the game server inside a running container is still loading
        # (mods, world) or actually joinable (#76). None when not running.
        "server_state": None,
        "uptime_seconds": None,
        "players": None,
        "server_fps": None,
        "server_mem_kb": None,
        "cpu_percent": None,
        "mem_bytes": None,
        "mem_limit_bytes": None,
    }

    if container is None or stats["status"] != "running":
        return stats

    # The container came from a non-sparse list(), i.e. it is already inspected.
    stats["uptime_seconds"] = _container_uptime_seconds(container)

    try:
        log_text = current_run_log(container)
        stats["server_state"] = server_state(container, log_text)
        server = parse_server_status(log_text)
        if server:
            stats["players"] = server["players"]
            stats["server_fps"] = server["fps"]
            stats["server_mem_kb"] = server["mem_kb"]
        # Fall back to the IP the server registered with when PUBLIC_ADDRESS
        # isn't set, so the Connect field works without manual config (#46).
        if not public:
            detected = parse_public_address(log_text)
            if detected:
                stats["public_address"] = detected
                stats["public_address_detected"] = True
                stats["connect"] = f"{detected}:{game_port}"
    except DockerException as exc:
        logger.debug("Could not read logs for stats (instance %s): %s", instance_id, exc)

    stats.update(cpu_mem_for(instance_id, container))
    return stats


# --------------------------------------------------------------------------- #
# Log files (crash reports, console logs) under the per-instance profile dir
# --------------------------------------------------------------------------- #

_LOG_SUFFIXES = {".log", ".rpt", ".txt", ".mdmp", ".dmp"}
_MAX_LOG_FILES = 300


def _profile_dir(instance_id: int) -> Path:
    return Path(config.settings.data_dir) / "instances" / str(instance_id) / "profile"


def list_log_files(instance_id: int) -> list[dict]:
    """List log/crash files under an instance's profile dir, newest first."""
    root = _profile_dir(instance_id)
    if not root.is_dir():
        return []
    files = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _LOG_SUFFIXES:
            st = p.stat()
            files.append({
                "path": p.relative_to(root).as_posix(),
                "size": st.st_size,
                "modified": st.st_mtime,
            })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return files[:_MAX_LOG_FILES]


def resolve_log_file(instance_id: int, relpath: str) -> Path:
    """Resolve a log file path, guarding against directory traversal."""
    root = _profile_dir(instance_id).resolve()
    target = (root / relpath).resolve()
    if root not in target.parents and target != root:
        raise InstanceError("Invalid log path")
    if not target.is_file() or target.suffix.lower() not in _LOG_SUFFIXES:
        raise InstanceError("Log file not found")
    return target


# --------------------------------------------------------------------------- #
# Stored data: what an instance accumulates on disk, and wiping it (issue #79)
# --------------------------------------------------------------------------- #
#
# Three kinds of state build up under data/instances/<id>/:
#
#   workshop/   the addons dir. The server DOWNLOADS and BAKES the template's
#               mods into it on startup and reuses that bake forever after. This
#               is why editing a template's mods and merely restarting can leave
#               the server running the old content — the fix is to throw the bake
#               away so the next start rebuilds it from the config.
#   profile/    the server's own profile: persistent save games (the world your
#               players built), plus logs and crash dumps.
#   configs/    the rendered config.json — never wiped here; it is rewritten from
#               the template on every start anyway.
#
# The server container runs as root, so the files it writes are root-owned and
# the manager (uid 1000) cannot delete them itself. A short-lived sibling
# container does the removal, exactly as steam_service.remove_files does.

# Persistence artifacts inside the profile, by name. Reforger has moved these
# around between builds (save/, .save/, and the .db the Enfusion persistence
# system writes), so match on any of them rather than one blessed path.
_SAVE_DIR_NAMES = {"save", "saves", ".save"}
_SAVE_SUFFIXES = {".db"}

DATA_MODS = "mods"
DATA_SAVES = "saves"
DATA_LOGS = "logs"
DATA_TARGETS = (DATA_MODS, DATA_SAVES, DATA_LOGS)


def _dir_usage(paths) -> tuple[int, int]:
    """(bytes, file count) over the given files/dirs; unreadable parts count 0."""
    total = files = 0
    for path in paths:
        try:
            if path.is_file():
                total += path.stat().st_size
                files += 1
                continue
            for child in path.rglob("*"):
                try:
                    if child.is_file():
                        total += child.stat().st_size
                        files += 1
                except OSError:
                    continue
        except OSError:
            continue
    return total, files


def _save_paths(profile: Path) -> list[Path]:
    """Persistence artifacts inside an instance's profile dir."""
    if not profile.is_dir():
        return []
    found = []
    try:
        for entry in profile.iterdir():
            name = entry.name.lower()
            if name in _SAVE_DIR_NAMES or Path(name).suffix in _SAVE_SUFFIXES:
                found.append(entry)
    except OSError:
        return []
    return found


def _log_paths(profile: Path) -> list[Path]:
    """Log/crash artifacts inside an instance's profile dir."""
    if not profile.is_dir():
        return []
    found = []
    try:
        for entry in profile.iterdir():
            if entry.is_dir() and entry.name.lower() in ("logs", "crash", "crashes"):
                found.append(entry)
            elif entry.is_file() and entry.suffix.lower() in _LOG_SUFFIXES:
                found.append(entry)
    except OSError:
        return []
    return found


def _target_paths(instance_id: int, target: str) -> list[Path]:
    idir = Path(config.settings.data_dir) / "instances" / str(instance_id)
    profile = idir / "profile"
    if target == DATA_MODS:
        # The addons dir, plus the copy some builds keep inside the profile.
        return [p for p in (idir / "workshop", profile / "addons") if p.exists()]
    if target == DATA_SAVES:
        return _save_paths(profile)
    if target == DATA_LOGS:
        return _log_paths(profile)
    raise InstanceError(f"Unknown data target '{target}'")


_TARGET_MOUNT = {
    DATA_MODS: WORKSHOP_DIR,
    DATA_SAVES: PROFILE_DIR,
    DATA_LOGS: PROFILE_DIR,
}


def instance_data(instance_id: int) -> dict:
    """What this instance has on disk, per target, so the GUI can offer to wipe it."""
    with Session(get_engine()) as session:
        if not session.get(Instance, instance_id):
            raise InstanceError("Instance not found")
    running = container_status(instance_id) == "running" if docker_service.ping() else False
    idir = Path(config.settings.data_dir) / "instances" / str(instance_id)
    items = []
    for target in DATA_TARGETS:
        paths = _target_paths(instance_id, target)
        size, files = _dir_usage(paths)
        items.append({
            "target": target,
            "size_bytes": size,
            "files": files,
            # The actual names on disk, so the user can see what is about to go.
            "paths": sorted(p.name for p in paths),
            # Where the server writes it inside its container.
            "mount": _TARGET_MOUNT[target],
        })
    return {
        "running": running,
        "items": items,
        # Where all of it really lives, on the host — none of it is in the image,
        # so it survives container rebuilds and image updates (#79).
        "host_path": docker_service.host_path_for(str(idir)),
    }


def clear_instance_data(instance_id: int, targets: list[str]) -> dict:
    """Delete the selected stored data for a stopped instance.

    Returns what was removed. The instance must be stopped: wiping the addons or
    the save out from under a live server would be a fine way to corrupt both.
    """
    chosen = [t for t in targets if t in DATA_TARGETS]
    if not chosen:
        raise InstanceError("Nothing selected to clear")
    with Session(get_engine()) as session:
        if not session.get(Instance, instance_id):
            raise InstanceError("Instance not found")
    if container_status(instance_id) == "running":
        raise InstanceError("Stop the server before clearing its data")

    idir = Path(config.settings.data_dir) / "instances" / str(instance_id)
    if not idir.is_dir():
        raise InstanceError("Instance has no data directory yet")

    removed = []
    victims: list[Path] = []
    for target in chosen:
        paths = _target_paths(instance_id, target)
        size, files = _dir_usage(paths)
        victims.extend(paths)
        removed.append({"target": target, "size_bytes": size, "files": files})

    if victims:
        # Paths are handed to the cleanup container relative to the instance dir,
        # and each one came from _target_paths — no user-supplied path ever
        # reaches the shell.
        rel = [p.relative_to(idir).as_posix() for p in victims]
        script = " ".join(f"rm -rf '/idata/{r}';" for r in rel) + " true"
        host_dir = docker_service.host_path_for(str(idir))
        try:
            docker_service.get_client().containers.run(
                config.settings.steamcmd_image,
                entrypoint="/bin/sh",
                command=["-c", script],
                remove=True,
                volumes={host_dir: {"bind": "/idata", "mode": "rw"}},
                labels={docker_service.LABEL_MANAGED: "true"},
            )
        except DockerException as exc:
            raise InstanceError(f"Could not clear instance data: {exc}") from exc

    # The server expects these to exist; it will refill them on the next start.
    (idir / "workshop").mkdir(parents=True, exist_ok=True)
    (idir / "profile").mkdir(parents=True, exist_ok=True)

    logger.info("Cleared %s for instance %s", ", ".join(chosen), instance_id)
    return {"removed": removed}


def prune_old_logs() -> int:
    """Delete per-session log dirs older than LOG_RETENTION_DAYS. Returns count.

    Reforger writes each session under <profile>/logs/<session>/; only those
    are pruned, never the rest of the profile.
    """
    import shutil
    import time as _time

    cutoff = _time.time() - config.settings.log_retention_days * 86400
    removed = 0
    instances_root = Path(config.settings.data_dir) / "instances"
    if not instances_root.is_dir():
        return 0
    for logs_dir in instances_root.glob("*/profile/logs"):
        if not logs_dir.is_dir():
            continue
        for session_dir in logs_dir.iterdir():
            try:
                if session_dir.is_dir() and session_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(session_dir, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    if removed:
        logger.info("Pruned %d expired log session dir(s)", removed)
    return removed


def instances_summary() -> dict:
    """Aggregate + per-server snapshot for the summary status bar (issue #12)."""
    public = config.settings.public_address
    docker_up = docker_service.ping()
    # One listing for every server on the bar; the same containers then serve both
    # the status and the log read, instead of being looked up twice each (#87).
    containers = docker_service.instance_containers() if docker_up else {}
    with Session(get_engine()) as session:
        instances = _all_instances(session)

    servers = []
    running = 0
    players_total = 0
    for inst in instances:
        status = container_status(inst.id, containers) if docker_up else "unknown"
        players = None
        state = None
        address = public  # env-configured PUBLIC_ADDRESS wins
        if status == "running":
            running += 1
            container = containers.get(inst.id)
            if container:
                try:
                    log_text = current_run_log(container)
                    state = server_state(container, log_text)
                    parsed = parse_server_status(log_text)
                    if parsed and parsed["players"] is not None:
                        players = parsed["players"]
                        players_total += players
                    if not address:  # fall back to the registered public IP (#46)
                        address = parse_public_address(log_text)
                except DockerException:
                    pass
        servers.append({
            "id": inst.id,
            "name": inst.name,
            "branch": inst.branch,
            "status": status,
            "server_state": state,
            "players": players,
            "connect": f"{address}:{inst.game_port}" if address else None,
        })

    return {
        "total": len(instances),
        "running": running,
        "players_total": players_total,
        # (no top-level public_address: each server carries its own `connect`,
        # which is what the bar actually renders — #88)
        "servers": servers,
    }


def list_views() -> list[dict]:
    # One ping and one container listing for the whole page, not one of each per
    # instance (#87).
    docker_up = docker_service.ping()
    containers = docker_service.instance_containers() if docker_up else {}
    with Session(get_engine()) as session:
        instances = _all_instances(session)
        templates = session.exec(select(Template)).all()
        names = {t.id: t.name for t in templates}
        updated = {t.id: t.updated_at for t in templates}
        return [
            instance_view(
                i, names.get(i.template_id), containers, docker_up,
                updated.get(i.template_id),
            )
            for i in instances
        ]


def set_restart_settings(
    instance_id: int, auto_restart: bool | None, auto_start: bool | None
) -> None:
    """Update the crash-restart and/or start-on-boot toggles (issue #26)."""
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        if auto_restart is not None:
            inst.auto_restart = auto_restart
        if auto_start is not None:
            inst.auto_start = auto_start
        policy = _restart_policy(inst)
        session.add(inst)
        session.commit()
    # Apply the new policy to a live container immediately.
    container = docker_service.find_instance_container(instance_id)
    if container:
        try:
            container.update(restart_policy={"Name": policy})
        except DockerException as exc:
            logger.warning("Could not update restart policy for %s: %s", instance_id, exc)


# --------------------------------------------------------------------------- #
# Scheduled restarts
# --------------------------------------------------------------------------- #

# Skip a scheduled restart whose window is more than this far in the past, so a
# long manager outage doesn't restart a server the moment it comes back up.
SCHEDULE_CATCHUP_GRACE_SECONDS = 3600

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _normalise_times(raw: list[str]) -> list[str]:
    """Validate/normalise "HH:MM" daily restart times; sorted, de-duplicated.

    Raises InstanceError on any malformed entry so the user gets a clear 409.
    """
    seen: set[tuple[int, int]] = set()
    for entry in raw:
        m = _TIME_RE.match((entry or "").strip())
        if not m:
            raise InstanceError(f"Invalid time '{entry}' — use 24-hour HH:MM")
        seen.add((int(m.group(1)), int(m.group(2))))
    return [f"{h:02d}:{mm:02d}" for h, mm in sorted(seen)]


def schedule_times(inst: Instance) -> list[str]:
    """The instance's configured daily restart times, or [] if none/invalid."""
    raw = inst.restart_schedule_json or ""
    if not raw:
        return []
    try:
        times = json.loads(raw).get("times", [])
        return _normalise_times(times)
    except (ValueError, InstanceError, AttributeError):
        return []


def set_restart_schedule(instance_id: int, times: list[str]) -> None:
    """Set (or clear) an instance's daily restart schedule.

    Storing the schedule also stamps last_scheduled_restart to 'now' so an
    occurrence already past earlier today does not fire immediately.
    """
    normalised = _normalise_times(times)
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        if normalised:
            inst.restart_schedule_json = json.dumps({"times": normalised})
            inst.last_scheduled_restart = datetime.now()
        else:
            inst.restart_schedule_json = ""
            inst.last_scheduled_restart = None
        session.add(inst)
        session.commit()
    logger.info("Set restart schedule for instance %s: %s", instance_id, normalised or "none")


def _due_scheduled_restart(
    times: list[str], now: datetime, last: datetime | None, grace_seconds: int
) -> datetime | None:
    """Return the most recent restart occurrence that is due now, else None.

    An occurrence (today at HH:MM, local time) is due when it is at or before
    `now`, has not already been serviced (`last` is before it), and `now` is
    still within `grace_seconds` of it. Pure and side-effect free for testing.
    """
    due: datetime | None = None
    for hhmm in times:
        hh, mm = int(hhmm[:2]), int(hhmm[3:])
        occ = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if occ > now:
            continue
        if (now - occ).total_seconds() > grace_seconds:
            continue
        if last is not None and last >= occ:
            continue
        if due is None or occ > due:
            due = occ
    return due


def _next_scheduled_restart(times: list[str], now: datetime) -> datetime | None:
    """The next upcoming restart occurrence (today if still ahead, else the
    earliest time tomorrow). None when there are no times. Pure for testing."""
    if not times:
        return None
    candidates = []
    for hhmm in times:
        hh, mm = int(hhmm[:2]), int(hhmm[3:])
        occ = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if occ <= now:
            occ += timedelta(days=1)
        candidates.append(occ)
    return min(candidates)


def next_restart_label(inst: Instance, now: datetime | None = None) -> str | None:
    """Server-local 'YYYY-MM-DD HH:MM' of this instance's next restart, or None.

    Rendered server-side (not as a bare timestamp) so the UI shows it in the
    server's local time — the same frame the schedule is defined in.
    """
    times = schedule_times(inst)
    nxt = _next_scheduled_restart(times, now or datetime.now())
    return nxt.strftime("%Y-%m-%d %H:%M") if nxt else None


def restart_instance(instance_id: int) -> None:
    """Stop then start an instance (used by the API and the scheduler)."""
    stop_instance(instance_id)
    start_instance(instance_id)


def apply_scheduled_restarts() -> None:
    """Restart instances whose daily schedule has come due. Non-fatal.

    Called from the background monitor. Only instances that should be running
    (desired_state == 'running') are considered; the occurrence is marked
    serviced before the restart so a slow stop/start isn't retried next tick.
    """
    if not docker_service.ping():
        return
    now = datetime.now()
    with Session(get_engine()) as session:
        instances = _all_instances(session)
    for inst in instances:
        if inst.desired_state != "running":
            continue
        times = schedule_times(inst)
        if not times:
            continue
        due = _due_scheduled_restart(
            times, now, inst.last_scheduled_restart, SCHEDULE_CATCHUP_GRACE_SECONDS
        )
        if due is None:
            continue
        with Session(get_engine()) as session:
            fresh = session.get(Instance, inst.id)
            if not fresh:
                continue
            fresh.last_scheduled_restart = now
            session.add(fresh)
            session.commit()
        logger.info("Scheduled restart of instance %s (%02d:%02d)", inst.name, due.hour, due.minute)
        try:
            restart_instance(inst.id)
        except InstanceError as exc:
            logger.warning("Scheduled restart of %s failed: %s", inst.name, exc)


# --------------------------------------------------------------------------- #
# Crash monitor / reconciliation
# --------------------------------------------------------------------------- #

def shutdown_all_instances() -> None:
    """Gracefully stop and remove every managed server container (#113).

    Called when the manager itself shuts down (docker compose down/stop).
    The server containers are siblings outside the compose project, so any
    that were left attached to the compose network made `docker compose down`
    fail with 'network reforger-net: resource is still in use'. Containers
    are disposable by design — every piece of state lives in bind mounts —
    so removing them frees the network cleanly. Their desired_state stays
    'running' in the DB, so reconcile_and_recover brings the auto_start ones
    back on the next boot (and leaves auto_start=off ones down — #115).
    """
    if not docker_service.ping():
        return
    containers = docker_service.instance_containers()
    if not containers:
        return
    running = [c for c in containers.values() if getattr(c, "status", "") == "running"]
    logger.info(
        "Manager shutdown: stopping %d running server(s), removing %d container(s)",
        len(running), len(containers),
    )

    def _stop(container) -> None:
        forget_run(container.id)
        try:
            container.stop(timeout=30)
        except DockerException as exc:
            logger.warning("Stop of %s failed: %s", container.name, exc)

    # Stop every running server AT ONCE, not one after another: serial stops
    # (up to 30s each) would blow through the compose stop_grace_period on a
    # multi-server host and get the manager SIGKILLed with servers still on the
    # network (#113). Concurrency is capped below docker-py's connection pool
    # (25) so the stop calls don't queue on a full pool; 16 covers any realistic
    # host in a single ~30s window, and even more servers just take one extra
    # window — still well inside the 2m grace period.
    if running:
        with ThreadPoolExecutor(max_workers=min(16, len(running))) as pool:
            list(pool.map(_stop, running))
    for container in containers.values():
        try:
            container.remove(force=True)
        except DockerException as exc:
            logger.warning("Removing %s failed: %s", container.name, exc)


# Instance ids this manager process has actually observed running. It is the
# difference between a crash and a reboot: an exited server we have seen up is a
# crash (honour auto_restart); one that has merely been down since the manager
# booted is not (only auto_start may start it). Resets on manager restart — which
# is exactly a reboot, so nothing is in it on the first pass after one (#115).
_seen_running: set[int] = set()


def forget_seen_running(instance_id: int) -> None:
    _seen_running.discard(instance_id)


def reconcile_and_recover() -> None:
    """Restart instances that should be running but whose container is down.

    Backs up Docker's own restart policy, keeping the two toggles' distinct
    meanings (#115):

      * auto_start ("start on host/Docker restart") — recover whenever the
        server is down, including the manager's first pass after a reboot.
        Mirrors Docker's 'unless-stopped'.
      * auto_restart ("restart on crash") — recover ONLY a server we have seen
        running under this manager that has since gone down, i.e. a genuine
        crash. Mirrors 'on-failure', which Docker does NOT re-run after a clean
        daemon restart.

    Without the seen-running gate an auto_restart / no-auto_start server was
    dragged back up after every reboot — the #115 bug. Called periodically and
    on startup. Non-fatal: logs and moves on.
    """
    if not docker_service.ping():
        return
    with Session(get_engine()) as session:
        instances = _all_instances(session)
    for inst in instances:
        if inst.desired_state != "running":
            continue
        status = container_status(inst.id)
        if status == "running":
            _seen_running.add(inst.id)  # remember it for crash detection
            continue
        if status not in ("exited", "absent", "created"):
            continue  # 'unknown' — a daemon hiccup; don't act on a blind read
        crashed = inst.auto_restart and inst.id in _seen_running
        if not (inst.auto_start or crashed):
            continue
        if not server_files_ready(inst.branch):
            continue  # can't run without server files; don't spam restart attempts
        logger.info("Recovering instance %s (was %s)", inst.name, status)
        try:
            start_instance(inst.id)
            _seen_running.add(inst.id)
        except InstanceError as exc:
            logger.warning("Recovery of %s failed: %s", inst.name, exc)
