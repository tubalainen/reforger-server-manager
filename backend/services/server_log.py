"""Pure parsing of an Arma Reforger server's console log.

Extracted from instance_service (#88): none of this needs Docker, a database or a
container — it is text in, facts out — and it is where the recurring bugs live
(unanchored regexes reading the server's own CONFIG echo as live stats: #76, #85).
Isolated, it is trivially testable, which is the point.
"""
import re

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
