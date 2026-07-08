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
from datetime import datetime, timedelta
from pathlib import Path

from docker.errors import DockerException, ImageNotFound, NotFound
from sqlmodel import Session, select

import config
from models import Instance, Template, get_engine
from services import docker_service, ports
from services.template_service import LaunchParams, spec_from_config

logger = logging.getLogger("manager.instance")

# The server prints a periodic status line, e.g.:
#   FPS: 60.0, frame time (...), Mem: 1190747 kB, Player: 0, AI: 0, ...
# The exact shape and field order vary between server builds (and "Player:" is
# sometimes "Players:"), so match each field independently rather than as one
# rigid pattern — otherwise a small format change silently zeroes the WebGUI.
_FPS_RE = re.compile(r"FPS:\s*([\d.]+)")
_MEM_RE = re.compile(r"Mem:\s*(\d+)\s*kB")
_PLAYERS_RE = re.compile(r"Players?:\s*(\d+)")


def parse_server_status(log_text: str) -> dict | None:
    """Return the most recent {fps, mem_kb, players} from server log output.

    The most recent line carrying an FPS reading wins; memory and player count
    are filled in from that same line when present (else None).
    """
    target = None
    for line in log_text.splitlines():
        if _FPS_RE.search(line):
            target = line
    if target is None:
        return None
    mem = _MEM_RE.search(target)
    players = _PLAYERS_RE.search(target)
    return {
        "fps": float(_FPS_RE.search(target).group(1)),
        "mem_kb": int(mem.group(1)) if mem else None,
        "players": int(players.group(1)) if players else None,
    }


def _docker_cpu_mem(container) -> dict:
    """One-shot CPU%/memory from docker stats (best-effort; {} on failure)."""
    try:
        s = container.stats(stream=False)
    except (DockerException, KeyError, ValueError):
        return {}
    try:
        cpu = s["cpu_stats"]
        pre = s["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        cpu_pct = (cpu_delta / sys_delta) * online * 100 if sys_delta > 0 else 0.0
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

# The dedicated-server binary the image launches from /reforger (WORKDIR).
# Its presence is our proof that a branch's server files are installed.
SERVER_BINARY = "ArmaReforgerServer"


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

        # Reuse an existing container if present, else create one. But if its
        # published ports no longer match (e.g. an instance created before the
        # A2S 1:1 port fix), remove it so it is recreated with the right mapping
        # — Docker port bindings can't be changed on an existing container.
        container = docker_service.find_instance_container(inst.id)
        if container is not None and not _container_ports_match(container, inst):
            logger.info("Recreating container for %s: published ports changed", inst.name)
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
                    "pull it or set REFORGER_SERVER_IMAGE"
                ) from exc
            except DockerException as exc:
                raise InstanceError(f"Could not create container: {exc}") from exc
            inst.container_id = container.id[:12]
        else:
            # Re-render config in case the template changed since last start.
            _write_config(inst, template_config)

        try:
            container.start()
        except DockerException as exc:
            raise InstanceError(f"Could not start container: {exc}") from exc
        inst.desired_state = "running"
        session.add(inst)
        session.commit()
        logger.info("Started instance %s (container %s)", inst.name, inst.container_id)


def _create_container(inst: Instance, config_path: Path, launch: "LaunchParams | None" = None):
    """Create (not start) the sibling server container for an instance."""
    idir = _instance_dir(inst)
    serverfiles_host = docker_service.host_path_for(
        f"{config.settings.serverfiles_dir}/{inst.branch}"
    )
    Path(f"{config.settings.serverfiles_dir}/{inst.branch}").mkdir(parents=True, exist_ok=True)

    volumes = {
        serverfiles_host: {"bind": "/reforger", "mode": "rw"},
        docker_service.host_path_for(str(idir / "configs")): {"bind": "/reforger/Configs", "mode": "rw"},
        docker_service.host_path_for(str(idir / "profile")): {"bind": "/home/profile", "mode": "rw"},
        docker_service.host_path_for(str(idir / "workshop")): {"bind": "/reforger/workshop", "mode": "rw"},
    }
    # Publish each UDP port unchanged (host == container). The game port used to
    # be remapped to a fixed internal 2001, but A2S/RCON had no matching override,
    # so the server bound the host port number inside the container while Docker
    # forwarded a *different* internal port — breaking A2S/server-browser queries.
    port_bindings = _desired_port_bindings(inst)
    environment = {
        "STEAM_APPID": config.BRANCHES[inst.branch]["app_id"],
        # Never self-install: instances run the files fetched on the Downloads
        # tab and mounted at /reforger. Downloading is gated in start_instance.
        "SKIP_INSTALL": "true",
        "ARMA_CONFIG": CONFIG_FILENAME,
        "SERVER_BIND_PORT": str(inst.game_port),
        "SERVER_PUBLIC_PORT": str(inst.game_port),
    }
    if config.settings.public_address:
        environment["SERVER_PUBLIC_ADDRESS"] = config.settings.public_address
    if launch is not None:
        arma_params, max_fps = launch.render()
        if arma_params:
            environment["ARMA_PARAMS"] = arma_params
        if max_fps is not None:
            environment["ARMA_MAX_FPS"] = str(max_fps)

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
    container = docker_service.find_instance_container(instance_id)
    if container:
        try:
            container.stop(timeout=30)
        except DockerException as exc:
            logger.warning("Stop of instance %s failed: %s", instance_id, exc)


def delete_instance(instance_id: int) -> None:
    container = docker_service.find_instance_container(instance_id)
    if container:
        try:
            container.remove(force=True)
        except DockerException as exc:
            logger.warning("Removing container for instance %s failed: %s", instance_id, exc)
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if inst:
            session.delete(inst)
            session.commit()
    logger.info("Deleted instance %s", instance_id)


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

def container_status(instance_id: int) -> str:
    """running | exited | created | absent (no container) | unknown."""
    container = docker_service.find_instance_container(instance_id)
    if container is None:
        return "absent"
    try:
        container.reload()
        return container.status
    except (DockerException, NotFound):
        return "unknown"


def instance_view(inst: Instance, template_name: str | None) -> dict:
    status = container_status(inst.id) if docker_service.ping() else "unknown"
    return {
        "id": inst.id,
        "name": inst.name,
        "branch": inst.branch,
        "template_id": inst.template_id,
        "template_name": template_name,
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
        "created_at": inst.created_at.isoformat(),
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


def instance_stats(instance_id: int) -> dict:
    """Live status for the top bar: connect info, players/FPS/mem, CPU/mem.

    Player/FPS/server-memory come from the server's own periodic status line
    in the container log; CPU and container memory come from docker stats.
    """
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        game_port = inst.game_port

    public = config.settings.public_address
    stats: dict = {
        "public_address": public or None,
        "game_port": game_port,
        "connect": f"{public}:{game_port}" if public else None,
        "status": container_status(instance_id),
        "players": None,
        "server_fps": None,
        "server_mem_kb": None,
        "cpu_percent": None,
        "mem_bytes": None,
        "mem_limit_bytes": None,
    }

    container = docker_service.find_instance_container(instance_id)
    if container is None or stats["status"] != "running":
        return stats

    try:
        log_text = container.logs(tail=100).decode("utf-8", errors="replace")
        server = parse_server_status(log_text)
        if server:
            stats["players"] = server["players"]
            stats["server_fps"] = server["fps"]
            stats["server_mem_kb"] = server["mem_kb"]
    except DockerException as exc:
        logger.debug("Could not read logs for stats (instance %s): %s", instance_id, exc)

    stats.update(_docker_cpu_mem(container))
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
    with Session(get_engine()) as session:
        instances = _all_instances(session)

    servers = []
    running = 0
    players_total = 0
    for inst in instances:
        status = container_status(inst.id) if docker_up else "unknown"
        players = None
        if status == "running":
            running += 1
            container = docker_service.find_instance_container(inst.id)
            if container:
                try:
                    log_text = container.logs(tail=80).decode("utf-8", errors="replace")
                    parsed = parse_server_status(log_text)
                    if parsed:
                        players = parsed["players"]
                        players_total += players
                except DockerException:
                    pass
        servers.append({
            "id": inst.id,
            "name": inst.name,
            "branch": inst.branch,
            "status": status,
            "players": players,
            "connect": f"{public}:{inst.game_port}" if public else None,
        })

    return {
        "total": len(instances),
        "running": running,
        "players_total": players_total,
        "public_address": public or None,
        "servers": servers,
    }


def list_views() -> list[dict]:
    with Session(get_engine()) as session:
        instances = _all_instances(session)
        names = {t.id: t.name for t in session.exec(select(Template)).all()}
        return [instance_view(i, names.get(i.template_id)) for i in instances]


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

def reconcile_and_recover() -> None:
    """Restart instances that should be running but whose container died.

    Called periodically and on startup. Non-fatal: logs and moves on.
    """
    if not docker_service.ping():
        return
    with Session(get_engine()) as session:
        instances = _all_instances(session)
    for inst in instances:
        # Backs up Docker's restart policy: recover a server that should be
        # running if either toggle asks us to (crash-restart or start-on-boot).
        if inst.desired_state != "running" or not (inst.auto_restart or inst.auto_start):
            continue
        if not server_files_ready(inst.branch):
            continue  # can't run without server files; don't spam restart attempts
        status = container_status(inst.id)
        if status in ("exited", "absent", "created"):
            logger.info("Recovering instance %s (was %s)", inst.name, status)
            try:
                start_instance(inst.id)
            except InstanceError as exc:
                logger.warning("Recovery of %s failed: %s", inst.name, exc)
