"""The Supervisor's picture of the machine, built from what the observer reports.

Pure: containers, figures and samples in, one overview out — the stacks, their
servers, the totals and whatever needs the host administrator's attention. No
Docker and no I/O, so every rule here is tested on its own (#204, v0.67.0).
"""
import re
from datetime import UTC, datetime

import stacks

# The first Docker API that can mount one folder of a volume (Engine 26.0). An
# older daemon mounts the whole volume, so the stacks' gates refuse and no game
# server can start (see gate/policy.py SUBPATH_API).
SUBPATH_API = (1, 45)
# The host is called busy past this share of its CPU or memory.
BUSY_PERCENT = 90



def _int(value) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def parse_range(raw) -> tuple[int, int] | None:
    """'2001-2020' → (2001, 2020); a single port '7780' → (7780, 7780)."""
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", str(raw or ""))
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    return (lo, hi) if 0 < lo <= hi <= 65535 else None


def _fmt_range(rng: tuple[int, int]) -> str:
    return str(rng[0]) if rng[0] == rng[1] else f"{rng[0]}-{rng[1]}"


def _version_key(raw) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(raw or "").strip())
    return tuple(int(p) for p in m.groups()) if m else None


def parse_time(raw) -> datetime | None:
    """Docker's RFC 3339 time (nanoseconds and all); None for 'never'."""
    if not raw or str(raw).startswith("0001-01-01"):
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", str(raw))
    if not m:
        return None
    iso = m.group(1) + (("." + m.group(2)[:6]) if m.group(2) else "") + "+00:00"
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _uptime(container: dict, now: datetime) -> int | None:
    state = container.get("state") or {}
    started = parse_time(state.get("started_at"))
    if not state.get("running") or started is None:
        return None
    return max(0, int((now - started).total_seconds()))


def _role(container: dict) -> str | None:
    """A container's role; the unlabelled manager and gate go by their names."""
    role = (container.get("labels") or {}).get(stacks.LABEL_ROLE)
    if role:
        return role
    name, stack = container.get("name") or "", container.get("stack") or ""
    if name == f"{stack}-manager":
        return stacks.ROLE_MANAGER
    if name == f"{stack}-docker-gate":
        return stacks.ROLE_GATE
    return None


def _status(container: dict) -> str:
    state = container.get("state") or {}
    return str(state.get("status") or ("running" if state.get("running") else "unknown"))


# --------------------------------------------------------------------------- #
# Building the overview
# --------------------------------------------------------------------------- #
def _server(container: dict, figures: dict, sample: dict, now: datetime) -> dict:
    labels = container.get("labels") or {}
    instance_id = _int(labels.get(stacks.LABEL_INSTANCE_ID))
    running = bool((container.get("state") or {}).get("running"))
    state = container.get("state") or {}
    return {
        "container_id": container.get("id"),
        "container": container.get("name"),
        "id": instance_id,
        # A server started before v0.67.0 has no name label until it is next started.
        "name": labels.get(stacks.LABEL_NAME) or (
            f"Server {instance_id}" if instance_id is not None else container.get("name")),
        "named": stacks.LABEL_NAME in labels,
        "branch": labels.get(stacks.LABEL_BRANCH),
        "status": _status(container),
        "server_state": figures.get("server_state") if running else None,
        "players": figures.get("players") if running else None,
        "max_players": _int(labels.get(stacks.LABEL_MAX_PLAYERS)),
        "server_fps": figures.get("server_fps") if running else None,
        "cpu_percent": sample.get("cpu_percent") if running else None,
        "mem_bytes": sample.get("mem_bytes") if running else None,
        "uptime_seconds": _uptime(container, now),
        "restart_count": container.get("restart_count"),
        "restarting": bool(state.get("restarting")),
        "oom_killed": bool(state.get("oom_killed")),
        "exit_code": state.get("exit_code"),
        "ports": {
            "game": _int(labels.get(stacks.LABEL_GAME_PORT)),
            "a2s": _int(labels.get(stacks.LABEL_A2S_PORT)),
            "rcon": _int(labels.get(stacks.LABEL_RCON_PORT)),
        },
    }


def _manager(container: dict | None, now: datetime) -> dict | None:
    if container is None:
        return None
    labels = container.get("labels") or {}
    state = container.get("state") or {}
    return {
        "container": container.get("name"),
        "status": _status(container),
        "running": bool(state.get("running")),
        "health": state.get("health"),
        "exit_code": state.get("exit_code"),
        "version": labels.get(stacks.LABEL_VERSION),
        "uptime_seconds": _uptime(container, now),
    }


def _ports(container: dict | None) -> dict | None:
    """The host ports a stack takes, from the labels on its manager."""
    if container is None:
        return None
    labels = container.get("labels") or {}
    ranges = {
        "web": parse_range(labels.get(stacks.LABEL_WEB_PORT)),
        "game": parse_range(labels.get(stacks.LABEL_GAME_PORTS)),
        "a2s": parse_range(labels.get(stacks.LABEL_A2S_PORTS)),
        "rcon": parse_range(labels.get(stacks.LABEL_RCON_PORTS)),
    }
    if not any(ranges.values()):
        return None
    return {kind: _fmt_range(rng) if rng else None for kind, rng in ranges.items()}


def _sum(values) -> float | int | None:
    present = [v for v in values if isinstance(v, (int, float))]
    return sum(present) if present else None


def build(host: dict | None, containers: list[dict], figures: dict[str, dict],
          samples: dict[str, dict], version: str, now: datetime | None = None) -> dict:
    """The overview the Supervisor's page shows.

    `figures` and `samples` are keyed by container id: a game server's log
    figures, and any running container's CPU/memory sample.
    """
    now = now or datetime.now(UTC)
    by_stack: dict[str, list[dict]] = {}
    for c in containers:
        if c.get("stack"):
            by_stack.setdefault(c["stack"], []).append(c)

    out_stacks = []
    for name in sorted(by_stack):
        mine = by_stack[name]
        managers = [c for c in mine if _role(c) == stacks.ROLE_MANAGER]
        gates = [c for c in mine if _role(c) == stacks.ROLE_GATE]
        # The compose container is the one named for the stack; anything else
        # claiming the role is not (the gate refuses to create one anyway).
        manager = next((c for c in managers if c.get("name") == f"{name}-manager"), None)
        gate = next((c for c in gates if c.get("name") == f"{name}-docker-gate"), None)
        servers = sorted(
            (_server(c, figures.get(c.get("id"), {}), samples.get(c.get("id"), {}), now)
             for c in mine if _role(c) == stacks.ROLE_INSTANCE),
            key=lambda s: (s["id"] is None, s["id"] or 0, s["name"]),
        )
        running = [s for s in servers if s["status"] == "running"]
        out_stacks.append({
            "name": name,
            "manager": _manager(manager, now),
            "gate": None if gate is None else {"status": _status(gate),
                                               "running": bool(gate["state"].get("running"))},
            "ports": _ports(manager),
            "downloads": sum(1 for c in mine if _role(c) == stacks.ROLE_STEAMCMD
                             and (c.get("state") or {}).get("running")),
            "servers": servers,
            "totals": {
                "servers": len(servers),
                "running": len(running),
                "online": sum(1 for s in running if s["server_state"] == "online"),
                "players": sum(s["players"] or 0 for s in running),
                "cpu_percent": _round(_sum(s["cpu_percent"] for s in running)),
                "mem_bytes": _sum(s["mem_bytes"] for s in running),
            },
        })

    every = [s for st in out_stacks for s in st["servers"]]
    running = [s for s in every if s["status"] == "running"]
    totals = {
        "stacks": len(out_stacks),
        "managers_up": sum(1 for st in out_stacks if (st["manager"] or {}).get("running")),
        "servers": len(every),
        "running": len(running),
        "online": sum(1 for s in running if s["server_state"] == "online"),
        "players": sum(s["players"] or 0 for s in running),
        "max_players": _sum(s["max_players"] for s in running),
        "cpu_percent": _round(_sum(s["cpu_percent"] for s in running)),
        "mem_bytes": _sum(s["mem_bytes"] for s in running),
    }
    return {
        "generated_at": now.isoformat(),
        "version": version,
        "host": host,
        "totals": totals,
        "stacks": out_stacks,
        "warnings": warnings(host, out_stacks, totals, version),
    }


def _round(value):
    return round(value, 1) if isinstance(value, float) else value


# --------------------------------------------------------------------------- #
# What needs the host administrator
# --------------------------------------------------------------------------- #
def _warning(wid: str, severity: str, title: str, detail: str, stack: str | None = None) -> dict:
    return {"id": wid, "severity": severity, "stack": stack, "title": title, "detail": detail}


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def port_overlaps(stack_list: list[dict]) -> list[dict]:
    """Pairs of stacks whose ports meet — web among web (TCP), UDP among UDP."""
    entries = []  # (stack, kind, range)
    for st in stack_list:
        for kind, raw in (st.get("ports") or {}).items():
            rng = parse_range(raw)
            if rng:
                entries.append((st["name"], kind, rng))
    found = []
    for i, (s1, k1, r1) in enumerate(entries):
        for s2, k2, r2 in entries[i + 1:]:
            if s1 == s2 or (k1 == "web") != (k2 == "web") or not _overlap(r1, r2):
                continue
            found.append(_warning(
                f"overlap-{s1}-{k1}-{s2}-{k2}", "danger",
                f"{s1} and {s2} share ports",
                f"{s1}'s {k1} ports ({_fmt_range(r1)}) overlap {s2}'s {k2} ports "
                f"({_fmt_range(r2)}). Two servers could be given the same port and one "
                f"of them would not start. Give one stack other ports in its .env "
                f"(rsm config --stack NAME), then restart it.",
            ))
    return found


def _server_port_warnings(stack_list: list[dict]) -> list[dict]:
    found = []
    taken: dict[int, tuple[str, str]] = {}
    for st in stack_list:
        ranges = {k: parse_range(v) for k, v in (st.get("ports") or {}).items()}
        for srv in st["servers"]:
            for kind, port in srv["ports"].items():
                if port is None:
                    continue
                rng = ranges.get(kind)
                if rng and not rng[0] <= port <= rng[1]:
                    found.append(_warning(
                        f"outside-{st['name']}-{srv['id']}-{kind}", "warning",
                        f"{srv['name']} ({st['name']}) uses a port outside its stack's range",
                        f"Its {kind} port {port} is not in {st['name']}'s {kind} range "
                        f"{_fmt_range(rng)}, so the firewall rules for that range do not "
                        f"cover it, and another stack may be handed the same port. The "
                        f"team can move it on the server's Settings page.",
                        st["name"],
                    ))
                other = taken.get(port)
                label = f"{srv['name']} ({st['name']})"
                if other and other[0] != st["name"]:
                    found.append(_warning(
                        f"clash-{port}", "danger",
                        f"Port {port} is used twice",
                        f"{label} and {other[1]} are both set up to use UDP port {port}; "
                        f"only one of them can run at a time.",
                    ))
                else:
                    taken.setdefault(port, (st["name"], label))
    return found


def warnings(host: dict | None, stack_list: list[dict], totals: dict, version: str) -> list[dict]:
    found = []
    api = str((host or {}).get("api_version") or "")
    m = re.fullmatch(r"(\d+)\.(\d+)", api)
    if m and (int(m.group(1)), int(m.group(2))) < SUBPATH_API:
        found.append(_warning(
            "docker-engine-too-old", "danger", "Docker is too old to start game servers",
            f"Docker Engine {host.get('docker_version') or '?'} (API {api}) cannot mount "
            f"one folder of a volume, which every game server needs since v0.65.0. "
            f"Update Docker to 26.0 or newer, then restart every stack.",
        ))

    for st in stack_list:
        name, manager, gate = st["name"], st["manager"], st["gate"]
        if manager is None:
            if st["servers"]:
                found.append(_warning(
                    f"manager-missing-{name}", "warning", f"{name}: no manager found",
                    f"There are game servers of stack {name} but no container named "
                    f"{name}-manager: it was removed, renamed, or runs a compose file older "
                    f"than v0.65.0. Its servers are not being looked after.", name))
        elif not manager["running"]:
            code = manager.get("exit_code")
            found.append(_warning(
                f"manager-down-{name}", "danger", f"{name}: the manager is not running",
                f"{name}-manager is {manager['status']}"
                f"{f' (exit code {code})' if code not in (None, 0) else ''}. The team cannot "
                f"reach its GUI, and crashed servers are not restarted. Look at "
                f"rsm logs --stack {name}, then rsm start --stack {name}.", name))
        elif manager.get("health") == "unhealthy":
            found.append(_warning(
                f"manager-unhealthy-{name}", "warning", f"{name}: the manager is unhealthy",
                f"Docker reports {name}-manager as unhealthy. See rsm logs --stack {name}.",
                name))
        if manager is not None and gate is None:
            found.append(_warning(
                f"gate-missing-{name}", "danger", f"{name}: no Docker gate",
                f"Stack {name} runs a compose file older than v0.65.0: its manager has "
                f"the whole Docker socket, and can see and change every other stack's "
                f"containers. Update it: sudo rsm update --stack {name} (answer yes to "
                f"both questions).", name))
        elif gate is not None and not gate["running"]:
            found.append(_warning(
                f"gate-down-{name}", "danger", f"{name}: the Docker gate is not running",
                f"{name}-docker-gate is {gate['status']}, so the manager cannot reach "
                f"Docker: servers cannot be started or stopped. Try "
                f"rsm restart --stack {name}.", name))
        if manager is not None:
            have, want = _version_key(manager.get("version")), _version_key(version)
            if manager.get("version") is None:
                found.append(_warning(
                    f"manager-old-{name}", "info", f"{name}: manager older than v0.67.0",
                    f"Update it (sudo rsm update --stack {name}) to show its version and "
                    f"its servers' names here.", name))
            elif have and want and have < want:
                found.append(_warning(
                    f"manager-old-{name}", "info",
                    f"{name}: manager v{manager['version']} is older than this Supervisor",
                    f"This Supervisor is v{version}. Update the stack with "
                    f"sudo rsm update --stack {name}.", name))
        for srv in st["servers"]:
            if srv["restarting"] or (srv["status"] == "restarting"):
                found.append(_warning(
                    f"restarting-{name}-{srv['container']}", "warning",
                    f"{srv['name']} ({name}) keeps restarting",
                    f"Docker is restarting it (restarted {srv['restart_count'] or 0} times). "
                    f"The team can read its log in their GUI.", name))
            elif srv["oom_killed"]:
                found.append(_warning(
                    f"oom-{name}-{srv['container']}", "warning",
                    f"{srv['name']} ({name}) ran out of memory",
                    "The system stopped it for using too much memory.", name))

    found += port_overlaps(stack_list)
    found += _server_port_warnings(stack_list)

    ncpu, mem_total = (host or {}).get("ncpu"), (host or {}).get("mem_total")
    cpu, mem = totals.get("cpu_percent"), totals.get("mem_bytes")
    if isinstance(cpu, (int, float)) and cpu >= BUSY_PERCENT:
        found.append(_warning(
            "host-cpu", "warning", "The machine's CPU is nearly full",
            f"The game servers use {cpu:.0f}% of all {ncpu or '?'} cores together. "
            f"Servers start dropping frames when the CPU runs out."))
    if isinstance(mem, (int, float)) and mem_total and mem / mem_total * 100 >= BUSY_PERCENT:
        found.append(_warning(
            "host-memory", "warning", "The machine's memory is nearly full",
            f"The game servers use {mem / mem_total * 100:.0f}% of its memory together. "
            f"When it runs out, the system stops one of them."))
    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda w: order.get(w["severity"], 3))
