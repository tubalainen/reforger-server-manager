"""Commands for whoever manages the machine, run inside the manager container.

    docker exec -u app <stack>-manager python manage.py reset-password
    docker exec -u app <stack>-manager python manage.py servers
    docker exec -u app <stack>-manager python manage.py start|stop|restart ID

`rsm reset-password` runs the first; `rsm cli` (#237) the others. Team admins
never need these: they exist for the host administrator, who has a shell where
team admins have only the GUI (#204).
"""
import os
import sys

import httpx

import auth
import models

# The manager itself, inside its own container (the Dockerfile's CMD).
MANAGER_URL = "http://127.0.0.1:8080"
# A start can create the container, a stop waits up to 30 s for the server.
ACTION_TIMEOUT = 120.0


def reset_password() -> int:
    """Forget the password set in the GUI; ADMIN_PASSWORD from .env applies again."""
    models.init_db()
    if not auth.clear_gui_password():
        print("No password was set in the GUI: the one in .env (ADMIN_PASSWORD) is in effect.")
        return 0
    # Whoever had the forgotten password is logged out too.
    auth.rotate_session_salt()
    print("The password set in the GUI was removed, and every session was logged out.")
    print("Sign in with ADMIN_PASSWORD from this install's .env, then set a new one under")
    print("System › Account.")
    return 0


# --------------------------------------------------------------------------- #
# The game servers, through the running manager (#237)
# --------------------------------------------------------------------------- #
# Asked of the manager over HTTP rather than done here: it is the manager's
# process that remembers which servers it saw running, so a stop done anywhere
# else would look like a crash to it, and a server set to restart on a crash
# would be started again a minute later.
def _client() -> httpx.Client:
    return httpx.Client(
        base_url=MANAGER_URL,
        cookies={auth.cookie_name(): auth.session_token()},
        timeout=httpx.Timeout(ACTION_TIMEOUT, connect=5.0),
    )


def _detail(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("detail") or resp.text[:200])
    except ValueError:
        return resp.text[:200] or f"HTTP {resp.status_code}"


def _field(value) -> str:
    """One tab-separated field: '-' for nothing, no tabs or line breaks inside."""
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        value = f"{value:.1f}"
    return " ".join(str(value).split())


# The columns `servers` prints, in order. rsm reads them by position.
SERVER_COLUMNS = ("id", "status", "server_state", "players", "max_players",
                  "server_fps", "cpu_percent", "mem_bytes", "uptime_seconds", "name")


def servers(_args: list[str]) -> int:
    """This stack's game servers, one tab-separated line each (SERVER_COLUMNS)."""
    try:
        with _client() as http:
            resp = http.get("/api/instances/summary")
    except httpx.HTTPError as exc:
        print(f"The manager did not answer ({exc}).", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"The manager said {resp.status_code}: {_detail(resp)}", file=sys.stderr)
        return 1
    for srv in resp.json().get("servers", []):
        print("\t".join(_field(srv.get(col)) for col in SERVER_COLUMNS))
    return 0


def _action(verb: str):
    def run(args: list[str]) -> int:
        if not args[0].isdigit():
            print(f"usage: python manage.py {verb} SERVER_ID", file=sys.stderr)
            return 2
        try:
            with _client() as http:
                resp = http.post(f"/api/instances/{args[0]}/{verb}")
        except httpx.HTTPError as exc:
            print(f"The manager did not answer ({exc}).", file=sys.stderr)
            return 1
        if resp.status_code != 200:
            print(_detail(resp), file=sys.stderr)
            return 1
        view = resp.json()
        print(f"{view.get('name', 'Server ' + args[0])}: {view.get('status', '?')}")
        return 0
    return run


COMMANDS = {
    "reset-password": lambda _args: reset_password(),
    "servers": servers,
    "start": _action("start"),
    "stop": _action("stop"),
    "restart": _action("restart"),
}
# How many arguments each takes.
ARITY = {"reset-password": 0, "servers": 0, "start": 1, "stop": 1, "restart": 1}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS or len(argv) - 1 != ARITY[argv[0]]:
        print(f"usage: python manage.py {{{'|'.join(COMMANDS)}}} [SERVER_ID]", file=sys.stderr)
        return 2
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Files written as root (the database journal, the session salt) would be
        # unreadable to the manager, which runs as 'app'.
        print("Run this as the app user: docker exec -u app <stack>-manager "
              "python manage.py " + " ".join(argv), file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
