"""Pure parsing of an Arma Reforger server's console log.

Extracted from instance_service (#88): none of this needs Docker, a database or a
container — it is text in, facts out — and it is where the recurring bugs live
(unanchored regexes reading the server's own CONFIG echo as live stats: #76, #85).
Isolated, it is trivially testable, which is the point.
"""
import re
from datetime import datetime

# With -logStats enabled (see STATS_LOG_INTERVAL_MS) the server prints a
# periodic performance line, e.g.:
#   FPS: 60.0, frame time (...), Mem: 1190747 kB, Player: 2, AI: 104, ...
# Player count also shows up on its own NETWORK line on connect/disconnect:
#   NETWORK      : Players connected: 1 / 1
# The field order varies between builds (and "Player:" is sometimes "Players:"),
# so match each field independently and keep the most recent value seen for each
# — FPS and the player count legitimately arrive on different lines.
# Every one of these is anchored on a word boundary, because the server also logs
# its own CONFIGURATION: "maxPlayers: 64, maxFPS: 60" must not be read as 64
# players online at 60 FPS. Unanchored, `Players?:` matches inside `maxPlayers:`
# — that is the bug this guard exists to prevent (#85), and the FPS half of it
# shipped once already. Keep the lookbehind on any pattern added here.
_FPS_RE = re.compile(r"(?<![A-Za-z])FPS:\s*([\d.]+)")
_MEM_RE = re.compile(r"(?<![A-Za-z])Mem:\s*(\d+)\s*kB")
_PLAYERS_STAT_RE = re.compile(r"(?<![A-Za-z])Players?:\s*(\d+)")   # -logStats line
_PLAYERS_CONN_RE = re.compile(r"Players connected:\s*(\d+)")       # NETWORK event line

# When PUBLIC_ADDRESS isn't set, the server's own registration line reveals the
# public IP the Reforger backend sees (issue #46), e.g.:
#   BACKEND      : Server registered with address: 203.0.113.7:2001
_REGISTERED_ADDR_RE = re.compile(
    r"registered with address:\s*(\d{1,3}(?:\.\d{1,3}){3}):\d+"
)


def parse_public_address(log_text: str) -> str | None:
    """The public IP the server registered with, scraped from the log (#46).

    Returns the most recent IP seen, or None. Used only as a fallback when
    PUBLIC_ADDRESS is not configured.
    """
    found = None
    for line in log_text.splitlines():
        m = _REGISTERED_ADDR_RE.search(line)
        if m:
            found = m.group(1)
    return found


# A container that is "running" is not a server players can join: Reforger spends
# minutes downloading mods and loading the world first (issue #76). The server
# announces the transition itself:
#   BACKEND      : Server registered with address: 203.0.113.7:2001
#   DEFAULT      : Entered online game state.
# A private (unlisted) server never registers with the backend but still enters
# the online game state, so either line is proof enough. The periodic -logStats
# line is a third witness: the engine only prints it once the world is running,
# and it keeps printing every STATS_LOG_INTERVAL_MS — which matters because the
# one-shot lines above scroll out of the log tail we read on a long-lived server.
_ONLINE_RE = re.compile(
    r"Entered online game state|registered with address:", re.IGNORECASE
)

STATE_STARTING = "starting"
STATE_ONLINE = "online"


def parse_server_state(log_text: str) -> str:
    """STATE_ONLINE once the server's log says it is up; STATE_STARTING before.

    `log_text` must cover the CURRENT run only (see current_run_log): Docker keeps
    a container's log across restarts, and a previous run's "online" line would
    otherwise report a still-loading server as online.
    """
    for line in log_text.splitlines():
        if _ONLINE_RE.search(line) or _FPS_RE.search(line):
            return STATE_ONLINE
    return STATE_STARTING


# The active-player roster (#126, #213). A join, as a real Reforger 1.x server
# logs it (BattlEye's lines first, then the engine's):
#   DEFAULT : BattlEye Server: Adding player identity=0x00000000, name='Reidond'
#   DEFAULT : BattlEye Server: 'Player #0 Reidond (46.8.28.102:63583) connected'
#   DEFAULT : BattlEye Server: Setting GUID for player identity=0x00000000, GUID=76561198117480403
#   DEFAULT : BattlEye Server: 'Player #0 Reidond - BE GUID: 0ff29685078407615ef8faeadc59b566'
#   NETWORK : ### Updating player: PlayerId=1, Name=Reidond, IdentityId=25c9bb9d-fdd7-4be9-898e-cac20a551dd2
# and a leave:
#   DEFAULT : BattlEye Server: 'Player #0 Reidond disconnected'
# The "#N" session number is the key join/leave events are folded on. The name
# may contain spaces, so it is captured non-greedily and bounded by what follows:
# the address — in parentheses on real servers; #126 only expected it bare and
# so swallowed "(ip:port)" into the name (#213) — then "connected". The engine's
# PlayerId (what RCON's #kick takes) and the Bohemia identity id (what the ban
# list and whitelist match on) come from the NETWORK line, which carries only the
# name, so it is tied back by name. Every field but the name is best-effort: the
# wording is build-dependent, and a server that prints none of these lines simply
# yields an empty roster — always additive to the authoritative player COUNT
# parsed above, never a replacement for it. \bconnected\b intentionally does not
# match inside "disconnected" (no word boundary between "dis" and "connected").
_ADDR = (
    r"\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?"   # IPv4, optional port
    r"|\[[0-9A-Fa-f:]+\](?::\d+)?"        # [IPv6]:port
    r"|[0-9A-Fa-f]*:[0-9A-Fa-f:]+"        # bare IPv6
)
_PLAYER_CONNECT_RE = re.compile(
    r"Player #(\d+)\s+(.+?)"
    rf"(?:\s+\(({_ADDR})\)|\s+({_ADDR}))?"
    r"\s+connected\b"
)
_PLAYER_DISCONNECT_RE = re.compile(r"Player #(\d+)\s+(.+?)\s+disconnected\b")
_BE_ADDING_RE = re.compile(r"Adding player identity=(0x[0-9A-Fa-f]+), name='(.*)'")
_BE_STEAM_GUID_RE = re.compile(r"Setting GUID for player identity=(0x[0-9A-Fa-f]+), GUID=(.+?)\s*$")
_BE_GUID_RE = re.compile(r"Player #(\d+)\s+.*\s-\s+BE GUID:\s*([0-9A-Fa-f]{32})")
_ENGINE_PLAYER_RE = re.compile(
    r"### Updating player: PlayerId=(\d+), Name=(.*?), IdentityId=([0-9A-Fa-f-]{36})"
)
_STEAM64_RE = re.compile(r"7656119\d{10}")
# What BattlEye prints in place of a Steam id for a console player (as observed
# by ReforgerJS, which reads the same lines). Not documented by Bohemia.
_CONSOLE_GUID = "[u8; 64]"


def _ip_of(addr: str | None) -> str | None:
    """The IP from a logged address, without the client's (ephemeral) port."""
    if not addr:
        return None
    if addr.startswith("["):
        return addr[1:addr.index("]")]
    if addr.count(":") == 1:
        return addr.split(":", 1)[0]
    return addr


def _apply_guid(player: dict, guid: str) -> None:
    if _STEAM64_RE.fullmatch(guid):
        player["steam_id"] = guid
        player["platform"] = "PC"
    elif guid == _CONSOLE_GUID:
        player["platform"] = "Console"


def parse_roster(log, now: datetime | None = None) -> list[dict]:
    """The players currently connected, folded from join/leave lines (#126, #213).

    `log` is the log text, or (timestamp, line) pairs as current_run_log_lines
    returns them — with timestamps (and `now`) each player also gets when they
    joined and for how long they have been on.

    Walks the log in order: a "connected" line adds (or refreshes) a player keyed
    by their "#N" session number, a "disconnected" line removes them, and the
    lines around a join fill in what else the server said about that player.
    Returns one dict per player, in the order they joined:
      player_num, name                     always
      ip, connected_at, online_seconds,    None when the log didn't say
      platform, steam_id, be_guid,
      player_id, identity_id
    Empty when the log carries no such lines (older builds, or a server that never
    had anyone join) — callers pair this with the numeric count, which is the
    source of truth for *how many* are online.
    """
    pairs = ((None, line) for line in log.splitlines()) if isinstance(log, str) else log
    roster: dict[int, dict] = {}
    be_names: dict[str, str] = {}     # BattlEye identity -> name, from "Adding player"
    be_guids: dict[str, str] = {}     # BattlEye identity -> Steam id / console marker
    be_slots: dict[str, int] = {}     # BattlEye identity -> "#N", once it connected
    for ts, line in pairs:
        m = _PLAYER_CONNECT_RE.search(line)
        if m:
            num, name = int(m.group(1)), m.group(2).strip()
            player = {
                "player_num": num,
                "name": name,
                "ip": _ip_of(m.group(3) or m.group(4)),
                "connected_at": ts.isoformat() if ts else None,
                "online_seconds": (
                    max(0, int((now - ts).total_seconds())) if ts and now else None
                ),
                "platform": None,
                "steam_id": None,
                "be_guid": None,
                "player_id": None,
                "identity_id": None,
            }
            roster.pop(num, None)  # a rejoin moves to the end, like a fresh join
            roster[num] = player
            for ident, ident_name in list(be_names.items()):
                if ident_name == name:
                    be_slots[ident] = num
                    del be_names[ident]
                    if ident in be_guids:
                        _apply_guid(player, be_guids.pop(ident))
            continue
        m = _PLAYER_DISCONNECT_RE.search(line)
        if m:
            num = int(m.group(1))
            roster.pop(num, None)
            for ident in [i for i, n in be_slots.items() if n == num]:
                del be_slots[ident]
            continue
        m = _BE_ADDING_RE.search(line)
        if m:
            be_names[m.group(1)] = m.group(2)
            continue
        m = _BE_STEAM_GUID_RE.search(line)
        if m:
            ident, guid = m.group(1), m.group(2)
            if be_slots.get(ident) in roster:
                _apply_guid(roster[be_slots[ident]], guid)
            else:
                be_guids[ident] = guid  # arrived before the "connected" line
            continue
        m = _BE_GUID_RE.search(line)
        if m:
            player = roster.get(int(m.group(1)))
            if player:
                player["be_guid"] = m.group(2).lower()
            continue
        m = _ENGINE_PLAYER_RE.search(line)
        if m:
            name = m.group(2).strip()
            # Tied back by name: the latest joiner of that name not yet given an
            # engine id (the line follows that player's own join), else the latest.
            same = [p for p in roster.values() if p["name"] == name]
            fresh = [p for p in same if p["player_id"] is None]
            if same:
                player = (fresh or same)[-1]
                player["player_id"] = int(m.group(1))
                player["identity_id"] = m.group(3).lower()
    return list(roster.values())


def parse_server_status(log_text: str) -> dict | None:
    """Return the most recent {fps, mem_kb, players} from server log output.

    Each field is tracked independently: the newest FPS reading, the newest
    memory reading, and the newest player count (from either the -logStats
    "Player: N" line or a "Players connected: N / M" network line) win. Returns
    None only when the log carries none of the three.
    """
    fps = mem_kb = players = None
    for line in log_text.splitlines():
        m = _FPS_RE.search(line)
        if m:
            fps = float(m.group(1))
        m = _MEM_RE.search(line)
        if m:
            mem_kb = int(m.group(1))
        # "Players connected:" must be tried first — the generic "Players?:"
        # pattern would not match it, but checking it explicitly keeps intent clear.
        m = _PLAYERS_CONN_RE.search(line)
        if m:
            players = int(m.group(1))
        else:
            m = _PLAYERS_STAT_RE.search(line)
            if m:
                players = int(m.group(1))
    if fps is None and mem_kb is None and players is None:
        return None
    return {"fps": fps, "mem_kb": mem_kb, "players": players}
