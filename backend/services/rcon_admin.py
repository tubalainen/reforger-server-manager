"""Acting on a running server through its own RCON: kick, ban, the ban list.

Until now the manager wrote the `rcon` block into config.json and never spoke
RCON itself, so the only thing it could do to a live server was stop its
container: a ban meant editing the template and restarting.

The commands are the game's (Server Management wiki), sent as an RCON client
with the `admin` permission:

    #kick <playerId>
    #ban create <playerId or identityId> <durationInSeconds> <reason>
    #ban list <page>
    #ban remove <id>

There is no command that sends players a message, so a restart still cannot be
announced in the game.

Where the server listens, seen from the manager's container:

- with host networking (Linux), on the Docker host, by the manager's own
  default gateway: the host's address on the manager's own network. Not
  `host.docker.internal`: the compose files map that to the default docker0
  bridge, and Docker drops packets from one bridge network to another bridge's
  address (seen on Docker 29: no answer, while the gateway answered at once).
  A host firewall that drops traffic from the Docker networks to the host
  (ufw's default) is in the way too;
- on the stack's Docker network (Docker Desktop), under the server container's
  name.

The password and port are read from the config.json the server was started
with, not from the template: an edit since then is not what the server knows.

Not yet run against a live server: the protocol is BattlEye's published one and
the commands are the wiki's, and the reply the server gives is shown to the
user as it comes.
"""
import json
import logging
import re
import socket
import struct
from pathlib import Path

from sqlmodel import Session

import config
from models import Instance, get_engine
from services import docker_service, instance_service, rcon
from services.instance_service import InstanceError

logger = logging.getLogger("manager.rcon")

_IDENTITY_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_BAN_ID_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")
REASON_MAX = 120
# A ban for ever, as the game writes it.
FOREVER = 0
DURATION_MAX = 10 * 365 * 86400


def _run_rcon_block(instance_id: int) -> dict | None:
    """The `rcon` object of the config.json this server last started with."""
    path = (Path(config.settings.data_dir) / "instances" / str(instance_id)
            / "configs" / instance_service.CONFIG_FILENAME)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    block = cfg.get("rcon") if isinstance(cfg, dict) else None
    return block if isinstance(block, dict) and block.get("password") else None


def status(instance_id: int) -> dict:
    """{enabled, permission}: whether this server can be reached over RCON, as
    far as its config says. Cheap: one small file read."""
    block = _run_rcon_block(instance_id)
    if not block:
        return {"enabled": False, "permission": None}
    return {"enabled": True, "permission": str(block.get("permission") or "admin")}


def _default_gateway() -> str | None:
    """The container's default gateway, which on a Docker bridge is the host."""
    try:
        lines = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]
    except (OSError, ValueError):
        return None
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "00000000":
            try:
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
            except (ValueError, struct.error):
                return None
    return None


# Only when the container has no default route (it always has one on a bridge).
_HOST_ALIAS = "host.docker.internal"


def _docker_host() -> str:
    """The Docker host as the manager's container reaches it: its own gateway."""
    return _default_gateway() or _HOST_ALIAS


def _host(instance_id: int) -> str:
    if docker_service.use_host_network():
        return _docker_host()
    return docker_service.container_name(f"instance-{instance_id}")


def _send(instance_id: int, commands: list[str], needs_admin: bool = True) -> list[str]:
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Server not found")
        port = inst.rcon_port
        name = inst.name
    if instance_service.container_status(instance_id) != "running":
        raise InstanceError("The server is not running")
    block = _run_rcon_block(instance_id)
    if not block:
        raise InstanceError(
            "RCON is off on this server. Set an RCON password in its template "
            "(Settings step), then restart the server."
        )
    if needs_admin and str(block.get("permission") or "admin") != "admin":
        raise InstanceError(
            "This server's RCON permission is 'monitor', which may only read. Set it "
            "to 'admin' in the template (Advanced settings › RCON), then restart the server."
        )
    host = _host(instance_id)
    try:
        return rcon.run(host, port, str(block["password"]), commands)
    except rcon.RconError as exc:
        logger.warning("RCON to %s (%s:%s) failed: %s", name, host, port, exc)
        hint = ""
        if docker_service.use_host_network():
            hint = (f" The manager reaches it at {host}:{port} (UDP); a firewall on the "
                    "host must allow that from the Docker network.")
        raise InstanceError(f"{exc}.{hint}".replace("..", ".")) from exc


def _clean_reason(reason: str) -> str:
    """One line of ordinary text: a reason is an argument, never a second command."""
    text = " ".join(str(reason or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:REASON_MAX].strip()


def kick(instance_id: int, player_id: int) -> str:
    """Kick a connected player by the server's player id; they can rejoin."""
    if isinstance(player_id, bool) or not isinstance(player_id, int) or player_id < 0:
        raise InstanceError("A player id is a whole number")
    reply = _send(instance_id, [f"#kick {player_id}"])[0]
    logger.info("Kicked player %s from instance %s (RCON: %r)", player_id, instance_id, reply)
    return reply


def ban(instance_id: int, *, player_id: int | None = None, identity_id: str | None = None,
        duration_seconds: int = FOREVER, reason: str = "") -> str:
    """Ban a player on the server's own ban list, for `duration_seconds` (0 = for
    ever). By identity id where there is one: it also works for someone who has
    already left."""
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int) \
            or not 0 <= duration_seconds <= DURATION_MAX:
        raise InstanceError("A ban lasts a number of seconds, or 0 for ever")
    if identity_id:
        if not _IDENTITY_RE.match(identity_id):
            raise InstanceError("That is not an identity id")
        target = identity_id.lower()
    elif player_id is not None and not isinstance(player_id, bool) \
            and isinstance(player_id, int) and player_id >= 0:
        target = str(player_id)
    else:
        raise InstanceError("A ban needs a player id or an identity id")
    command = f"#ban create {target} {duration_seconds}"
    cleaned = _clean_reason(reason)
    if cleaned:
        command += f" {cleaned}"
    reply = _send(instance_id, [command])[0]
    logger.info("Banned %s on instance %s for %s s (RCON: %r)",
                target, instance_id, duration_seconds, reply)
    return reply


def ban_list(instance_id: int, page: int = 1) -> str:
    """The server's ban list as it prints it: BanID ; Player UID ; Duration."""
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 1000:
        raise InstanceError("A page is a number from 1")
    return _send(instance_id, [f"#ban list {page}"], needs_admin=False)[0]


def ban_remove(instance_id: int, ban_id: str) -> str:
    """Lift a ban by the id the ban list shows."""
    ban_id = str(ban_id or "").strip()
    if not _BAN_ID_RE.match(ban_id):
        raise InstanceError("That is not an id from the ban list")
    reply = _send(instance_id, [f"#ban remove {ban_id}"])[0]
    logger.info("Removed ban %s on instance %s (RCON: %r)", ban_id, instance_id, reply)
    return reply
