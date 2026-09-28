"""Stacks: one manager's own slice of a shared Docker host (#204).

A stack is one team's complete install — its manager, its Docker gate, its game
servers, networks and folders. Several can run on one machine. Every container a
manager creates carries its stack's name as a label, and this module holds the
single rule for "is this container mine?", shared by the manager (which filters
what it sees) and the gate (which enforces it).
"""
import re

LABEL_MANAGED = "reforger-manager.managed"
LABEL_STACK = "reforger-manager.stack"
LABEL_ROLE = "reforger-manager.role"
LABEL_BRANCH = "reforger-manager.branch"
LABEL_INSTANCE_ID = "reforger-manager.instance_id"
# Every label of ours starts with this; the Supervisor's observer passes on
# nothing else (#204).
LABEL_PREFIX = "reforger-manager."

# What a container is. The manager creates the last two; the compose file gives
# the first two to the manager and its gate.
ROLE_MANAGER = "manager"
ROLE_GATE = "gate"
ROLE_INSTANCE = "instance"
ROLE_STEAMCMD = "steamcmd"

# On a game server: what the Server Supervisor shows for it (v0.67.0). Labels
# are fixed when a container is created, so the manager recreates a server whose
# labels no longer match, the next time it starts it.
LABEL_NAME = "reforger-manager.name"
LABEL_MAX_PLAYERS = "reforger-manager.max_players"
LABEL_GAME_PORT = "reforger-manager.game_port"
LABEL_A2S_PORT = "reforger-manager.a2s_port"
LABEL_RCON_PORT = "reforger-manager.rcon_port"

# On a manager, from its compose file: what the stack takes on the host.
LABEL_WEB_PORT = "reforger-manager.web_port"
LABEL_GAME_PORTS = "reforger-manager.game_ports"
LABEL_A2S_PORTS = "reforger-manager.a2s_ports"
LABEL_RCON_PORTS = "reforger-manager.rcon_ports"
# On the image: the version it was built as (the Dockerfile sets it).
LABEL_VERSION = "reforger-manager.version"

# Every install from before stacks existed is this one, and keeps every name it
# had: reforger-manager, reforger-instance-<id>, reforger-net, ...
DEFAULT_STACK = "reforger"

# Lower-case letters, digits and underscores, starting with a letter or digit.
# It prefixes container, network and volume names, and it is what compose
# accepts as a project name, so it stays well inside all three. No dashes: the
# dash is what separates the stack from the rest of a name, so with one allowed
# stack 'team2' could take the names of a stack 'team2-x' (team2-x-instance-1).
_STACK_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,30}$")


def stack_name_error(name: str) -> str | None:
    """Why `name` cannot be a stack name, or None when it can."""
    if _STACK_RE.fullmatch(name or ""):
        return None
    return (
        f"RSM_STACK={name!r} is not a valid stack name: use 1-31 lower-case letters, "
        f"digits and underscores, starting with a letter or digit — no dashes "
        f"(e.g. 'team2' or 'milsim_eu')."
    )


def owns(labels: dict | None, stack: str, write: bool = False) -> bool:
    """Does a container with these labels belong to `stack`?

    A container without a stack label was created before stacks existed
    (pre-v0.65.0); only the default stack can have made it, so only the default
    stack adopts it — and only if the manager made it at all.

    `write` asks the stricter question: may the stack change it (start, stop,
    remove)? Only containers the manager itself created qualify — not, say, the
    manager's own compose container, which carries the stack label so it can
    inspect itself.
    """
    labels = labels or {}
    managed = labels.get(LABEL_MANAGED) == "true"
    owner = labels.get(LABEL_STACK)
    mine = (stack == DEFAULT_STACK and managed) if owner is None else owner == stack
    return mine and (managed or not write)
