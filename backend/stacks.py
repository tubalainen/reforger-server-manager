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

# Every install from before stacks existed is this one, and keeps every name it
# had: reforger-manager, reforger-instance-<id>, reforger-net, ...
DEFAULT_STACK = "reforger"

# Lower-case letters, digits and dashes, starting with a letter or digit. It
# prefixes container, network and volume names, and it is what compose accepts
# as a project name, so it stays well inside all three.
_STACK_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def stack_name_error(name: str) -> str | None:
    """Why `name` cannot be a stack name, or None when it can."""
    if _STACK_RE.fullmatch(name or ""):
        return None
    return (
        f"RSM_STACK={name!r} is not a valid stack name: use 1-31 lower-case letters, "
        f"digits and dashes, starting with a letter or digit (e.g. 'team2')."
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
