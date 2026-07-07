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
from pathlib import Path

from docker.errors import DockerException, ImageNotFound, NotFound
from sqlmodel import Session, select

import config
from models import Instance, Template, get_engine
from services import docker_service, ports
from services.template_service import spec_from_config

logger = logging.getLogger("manager.instance")

# The server prints a periodic status line, e.g.:
#   FPS: 60.0, frame time (...), Mem: 1190747 kB, Player: 0, AI: 0, ...
SERVER_STATUS_RE = re.compile(
    r"FPS:\s*([\d.]+),.*?Mem:\s*(\d+)\s*kB,\s*Player:\s*(\d+)"
)


def parse_server_status(log_text: str) -> dict | None:
    """Return the most recent {fps, mem_kb, players} from server log output."""
    last = None
    for m in SERVER_STATUS_RE.finditer(log_text):
        last = m
    if not last:
        return None
    return {
        "fps": float(last.group(1)),
        "mem_kb": int(last.group(2)),
        "players": int(last.group(3)),
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

# Container-internal ports (fixed); host ports are the leased ones, mapped 1:1
CONTAINER_GAME_PORT = 2001
CONTAINER_A2S_PORT = 17777
CONTAINER_RCON_PORT = 19999

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


def _template_config(session: Session, inst: Instance) -> str:
    template = session.get(Template, inst.template_id)
    if not template:
        raise InstanceError("Template for this instance no longer exists")
    return template.config_json


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

        # Reuse an existing container if present, else create one.
        container = docker_service.find_instance_container(inst.id)
        if container is None:
            self_config_path = _write_config(inst, template_config)
            try:
                container = _create_container(inst, self_config_path)
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


def _create_container(inst: Instance, config_path: Path):
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
    port_bindings = {
        f"{CONTAINER_GAME_PORT}/udp": inst.game_port,
        f"{CONTAINER_A2S_PORT}/udp": inst.a2s_port,
        f"{CONTAINER_RCON_PORT}/udp": inst.rcon_port,
    }
    environment = {
        "STEAM_APPID": config.BRANCHES[inst.branch]["app_id"],
        # Never self-install: instances run the files fetched on the Downloads
        # tab and mounted at /reforger. Downloading is gated in start_instance.
        "SKIP_INSTALL": "true",
        "ARMA_CONFIG": CONFIG_FILENAME,
        "SERVER_BIND_PORT": str(CONTAINER_GAME_PORT),
        "SERVER_PUBLIC_PORT": str(inst.game_port),
    }
    if config.settings.public_address:
        environment["SERVER_PUBLIC_ADDRESS"] = config.settings.public_address

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
        restart_policy={"Name": "no"},
    )


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
        "status": status,
        "server_files_ready": server_files_ready(inst.branch),
        "created_at": inst.created_at.isoformat(),
    }


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


def list_views() -> list[dict]:
    with Session(get_engine()) as session:
        instances = _all_instances(session)
        names = {t.id: t.name for t in session.exec(select(Template)).all()}
        return [instance_view(i, names.get(i.template_id)) for i in instances]


def set_auto_restart(instance_id: int, value: bool) -> None:
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Instance not found")
        inst.auto_restart = value
        session.add(inst)
        session.commit()


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
        if inst.desired_state != "running" or not inst.auto_restart:
            continue
        if not server_files_ready(inst.branch):
            continue  # can't run without server files; don't spam restart attempts
        status = container_status(inst.id)
        if status in ("exited", "absent", "created"):
            logger.info("Auto-restarting instance %s (was %s)", inst.name, status)
            try:
                start_instance(inst.id)
            except InstanceError as exc:
                logger.warning("Auto-restart of %s failed: %s", inst.name, exc)
