"""Check the Server Supervisor on a real Docker host, from outside (#204).

    python3 scripts/ci/supervisor_e2e.py http://127.0.0.1:7090 PASSWORD [expect]

Signs in, reads /api/overview and checks it against what the CI job set up: two
stacks, each with its manager, its gate and one stand-in server. `expect` is
'healthy' (the default: no danger warnings) or 'team2-manager-down' (team2's
manager stopped, which takes its own servers down and nobody else's). Also checks
that nothing of the teams' own .env got through. Standard library only: it runs
on the CI runner, not in the image.
"""
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

BASE, PASSWORD = sys.argv[1], sys.argv[2]
EXPECT = sys.argv[3] if len(sys.argv) > 3 else "healthy"
# The teams' passwords, from their .env files in the CI job. Neither may appear.
TEAM_SECRETS = ("e2e-password-one", "e2e-password-two")

opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
failures: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("PASS  " if ok else "FAIL  ") + name, flush=True)
    if not ok:
        failures.append(name)


def call(path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with opener.open(req, timeout=30) as resp:
        return resp.read().decode()


try:
    call("/api/overview")
    check("the overview needs a login", False)
except urllib.error.HTTPError as exc:
    check(f"the overview needs a login ({exc.code})", exc.code == 401)

call("/api/auth/login", {"username": "admin", "password": PASSWORD})
raw = call("/api/overview")
data = json.loads(raw)
print(json.dumps(data["totals"]), flush=True)
for w in data["warnings"]:
    print(f"  warning [{w['severity']}] {w['title']}", flush=True)

check("no team password in the answer", not any(s in raw for s in TEAM_SECRETS))
check("no environment in the answer", "ADMIN_PASSWORD" not in raw and "SESSION_SECRET" not in raw)
stacks = {s["name"]: s for s in data["stacks"]}
check(f"both stacks are there ({sorted(stacks)})", set(stacks) == {"reforger", "team2"})
check("the host is described", bool((data.get("host") or {}).get("docker_version")))

for name, st in stacks.items():
    manager_should_run = not (EXPECT == "team2-manager-down" and name == "team2")
    manager = st["manager"] or {}
    check(f"{name}: manager running = {manager_should_run}",
          bool(manager.get("running")) == manager_should_run)
    check(f"{name}: manager reports its version", bool(manager.get("version")))
    check(f"{name}: gate found and running", bool((st["gate"] or {}).get("running")))
    check(f"{name}: ports from the manager's labels", bool((st["ports"] or {}).get("web")))
    servers = {s["name"]: s for s in st["servers"]}
    if not manager_should_run:
        # A manager removes its servers on the way down, and only its own.
        check(f"{name}: its manager took its servers down with it", not servers)
        continue
    srv = servers.get(f"{name} e2e server") or {}
    check(f"{name}: the stand-in server is running", srv.get("status") == "running")
    check(f"{name}: its players and limit ({srv.get('players')}/{srv.get('max_players')})",
          srv.get("players") == 3 and srv.get("max_players") == 64)
    check(f"{name}: it is online", srv.get("server_state") == "online")

dangers = {w["id"] for w in data["warnings"] if w["severity"] == "danger"}
if EXPECT == "healthy":
    check(f"no danger warnings ({sorted(dangers)})", not dangers)
else:
    check(f"the stopped manager is flagged ({sorted(dangers)})", "manager-down-team2" in dangers)

print(f"== {len(failures)} failure(s)", flush=True)
sys.exit(1 if failures else 0)
