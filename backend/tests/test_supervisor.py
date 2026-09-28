"""The Server Supervisor (#204, v0.67.0): every stack on a machine, view only."""
import time
from datetime import UTC, datetime

import httpx
import pytest

import stacks
from supervisor import collector as collector_mod
from supervisor import overview

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
HOST = {"docker_version": "28.1.1", "api_version": "1.49", "ncpu": 8, "mem_total": 16 * 2**30}


def _c(cid, name, stack, labels=None, running=True, **state):
    return {
        "id": cid, "name": name, "stack": stack, "image": "x",
        "labels": {stacks.LABEL_STACK: stack, **(labels or {})},
        "state": {"status": "running" if running else "exited", "running": running,
                  "restarting": False, "oom_killed": False, "exit_code": 0,
                  "started_at": "2026-09-28T11:00:00.123456789Z", **state},
        "restart_count": 0, "ports": [],
    }


def _manager(stack, web="7780", game="2001-2020", a2s="17777-17796", rcon="19999-20018",
             version="0.67.0", **kw):
    labels = {stacks.LABEL_ROLE: "manager", stacks.LABEL_WEB_PORT: web,
              stacks.LABEL_GAME_PORTS: game, stacks.LABEL_A2S_PORTS: a2s,
              stacks.LABEL_RCON_PORTS: rcon}
    if version:
        labels[stacks.LABEL_VERSION] = version
    return _c(f"{stack}-m", f"{stack}-manager", stack, labels, **kw)


def _gate(stack, **kw):
    return _c(f"{stack}-g", f"{stack}-docker-gate", stack, {stacks.LABEL_ROLE: "gate"}, **kw)


def _server(stack, iid, game, a2s, rcon, name=None, max_players="64", **kw):
    labels = {stacks.LABEL_ROLE: "instance", stacks.LABEL_INSTANCE_ID: str(iid),
              stacks.LABEL_BRANCH: "stable", stacks.LABEL_GAME_PORT: str(game),
              stacks.LABEL_A2S_PORT: str(a2s), stacks.LABEL_RCON_PORT: str(rcon)}
    if name:
        labels[stacks.LABEL_NAME] = name
    if max_players:
        labels[stacks.LABEL_MAX_PLAYERS] = max_players
    return _c(f"{stack}-s{iid}", f"{stack}-instance-{iid}", stack, labels, **kw)


def _two_stacks():
    return [
        _manager("reforger"), _gate("reforger"),
        _server("reforger", 1, 2001, 17777, 19999, name="Conflict"),
        _manager("team2", web="7781", game="2021-2040", a2s="17797-17816", rcon="20019-20038"),
        _gate("team2"),
        _server("team2", 1, 2021, 17797, 20019, name="Training", max_players="16"),
    ]


def _ids(warnings):
    return {w["id"] for w in warnings}


# --------------------------------------------------------------------------- #
# The overview
# --------------------------------------------------------------------------- #
def test_two_healthy_stacks():
    figures = {"reforger-s1": {"server_state": "online", "players": 12, "server_fps": 58.0},
               "team2-s1": {"server_state": "starting", "players": None}}
    samples = {"reforger-s1": {"cpu_percent": 20.5, "mem_bytes": 3 * 2**30},
               "team2-s1": {"cpu_percent": 5.0, "mem_bytes": 2**30}}
    data = overview.build(HOST, _two_stacks(), figures, samples, "0.67.0", NOW)

    assert [s["name"] for s in data["stacks"]] == ["reforger", "team2"]
    ref = data["stacks"][0]
    assert ref["manager"]["running"] and ref["manager"]["version"] == "0.67.0"
    assert ref["manager"]["uptime_seconds"] == 3599  # 59:59.88, nanoseconds and all
    assert ref["gate"]["running"]
    assert ref["ports"] == {"web": "7780", "game": "2001-2020", "a2s": "17777-17796",
                            "rcon": "19999-20018"}
    srv = ref["servers"][0]
    assert (srv["name"], srv["players"], srv["max_players"], srv["server_state"]) == (
        "Conflict", 12, 64, "online")
    assert srv["ports"] == {"game": 2001, "a2s": 17777, "rcon": 19999}
    assert data["totals"] == {
        "stacks": 2, "managers_up": 2, "servers": 2, "running": 2, "online": 1,
        "players": 12, "max_players": 80, "cpu_percent": 25.5, "mem_bytes": 4 * 2**30,
    }
    assert data["warnings"] == []


def test_a_server_from_before_v067_has_no_name_yet():
    containers = [_manager("reforger"), _gate("reforger"),
                  _server("reforger", 3, 2001, 17777, 19999, max_players=None)]
    srv = overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["stacks"][0]["servers"][0]
    assert srv["name"] == "Server 3" and srv["named"] is False
    assert srv["max_players"] is None


def test_a_stopped_server_shows_no_live_figures():
    containers = [_manager("reforger"), _gate("reforger"),
                  _server("reforger", 1, 2001, 17777, 19999, running=False)]
    figures = {"reforger-s1": {"players": 5}}
    srv = overview.build(HOST, containers, figures, {}, "0.67.0", NOW)["stacks"][0]["servers"][0]
    assert srv["status"] == "exited"
    assert srv["players"] is None and srv["uptime_seconds"] is None


def test_a_down_manager_and_gate_are_flagged():
    containers = [_manager("team2", running=False, exit_code=1), _gate("team2", running=False)]
    found = _ids(overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"])
    assert {"manager-down-team2", "gate-down-team2"} <= found


def test_a_stack_without_a_gate_can_reach_everyone():
    containers = [_manager("reforger")]
    warnings = overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"]
    gate = next(w for w in warnings if w["id"] == "gate-missing-reforger")
    assert gate["severity"] == "danger"


def test_servers_without_a_manager_are_flagged():
    containers = [_server("team3", 1, 2041, 17817, 20039)]
    assert "manager-missing-team3" in _ids(
        overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"])


def test_overlapping_stacks_are_flagged():
    containers = [_manager("reforger"), _gate("reforger"),
                  _manager("team2", web="7780", game="2015-2034", a2s="17797-17816",
                           rcon="17790-17800"),
                  _gate("team2")]
    warnings = overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"]
    found = _ids(warnings)
    assert "overlap-reforger-web-team2-web" in found
    assert "overlap-reforger-game-team2-game" in found
    # UDP meets UDP whatever the kind: team2's RCON range runs into reforger's A2S.
    assert "overlap-reforger-a2s-team2-rcon" in found
    assert "overlap-reforger-a2s-team2-a2s" not in found  # 17797-17816 is clear
    assert all(w["severity"] == "danger" for w in warnings if w["id"].startswith("overlap"))


def test_disjoint_ranges_do_not_overlap():
    stacks_ = [{"name": "a", "ports": {"web": "7780", "game": "2001-2020"}},
               {"name": "b", "ports": {"web": "7781", "game": "2021-2040"}}]
    assert overview.port_overlaps(stacks_) == []
    # A GUI port is TCP and a game port UDP: 2001 as both is no clash.
    stacks_ = [{"name": "a", "ports": {"web": "2001"}}, {"name": "b", "ports": {"game": "2001-2020"}}]
    assert overview.port_overlaps(stacks_) == []


def test_a_port_two_stacks_use_is_flagged():
    containers = _two_stacks() + [_server("team2", 2, 2001, 17798, 20020, name="Squatter")]
    warnings = overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"]
    assert "clash-2001" in _ids(warnings)
    # ...and it is outside team2's own game range as well.
    assert "outside-team2-2-game" in _ids(warnings)


def test_an_old_docker_engine_is_flagged():
    host = {**HOST, "api_version": "1.44", "docker_version": "25.0.5"}
    assert "docker-engine-too-old" in _ids(
        overview.build(host, _two_stacks(), {}, {}, "0.67.0", NOW)["warnings"])


def test_an_older_manager_is_noted():
    containers = [_manager("reforger", version="0.66.0"), _gate("reforger"),
                  _manager("team2", web="7781", version=None), _gate("team2")]
    warnings = overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"]
    old = {w["id"]: w for w in warnings}
    assert old["manager-old-reforger"]["severity"] == "info"
    assert "older than v0.67.0" in old["manager-old-team2"]["title"]


def test_a_crash_looping_server_is_flagged():
    containers = [_manager("reforger"), _gate("reforger"),
                  _server("reforger", 1, 2001, 17777, 19999, restarting=True, status="restarting")]
    assert "restarting-reforger-reforger-instance-1" in _ids(
        overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"])


def test_a_busy_machine_is_flagged():
    samples = {"reforger-s1": {"cpu_percent": 60.0, "mem_bytes": 8 * 2**30},
               "team2-s1": {"cpu_percent": 35.0, "mem_bytes": 7 * 2**30}}
    found = _ids(overview.build(HOST, _two_stacks(), {}, samples, "0.67.0", NOW)["warnings"])
    assert {"host-cpu", "host-memory"} <= found


def test_dangers_come_first():
    containers = [_manager("reforger", version="0.60.0")]  # info + danger (no gate)
    severities = [w["severity"] for w in
                  overview.build(HOST, containers, {}, {}, "0.67.0", NOW)["warnings"]]
    assert severities == sorted(severities, key=["danger", "warning", "info"].index)


@pytest.mark.parametrize("raw,rng", [("2001-2020", (2001, 2020)), ("7780", (7780, 7780)),
                                     (" 1 - 2 ", (1, 2)), ("20-10", None), ("x", None),
                                     (None, None), ("0-5", None), ("1-70000", None)])
def test_parse_range(raw, rng):
    assert overview.parse_range(raw) == rng


# --------------------------------------------------------------------------- #
# The collector, against a fake observer
# --------------------------------------------------------------------------- #
def _observer(routes: dict, calls: list | None = None):
    def handle(request: httpx.Request):
        if calls is not None:
            calls.append(request.url.path)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"message": "No such container"})
        return httpx.Response(200, json=body)
    return collector_mod.Observer(transport=httpx.MockTransport(handle))


def test_the_collector_asks_only_about_running_servers():
    containers = _two_stacks()
    containers[5]["state"]["running"] = False
    calls = []
    obs = _observer({
        "/_rsm/host": HOST,
        "/_rsm/containers": containers,
        "/_rsm/containers/reforger-s1/server": {"server_state": "online", "players": 4},
        "/_rsm/containers/reforger-s1/stats": {"cpu_percent": 3.0, "mem_bytes": 100},
    }, calls)
    col = collector_mod.Collector(obs, version="0.67.0")
    data = col.overview()
    assert data["stacks"][0]["servers"][0]["players"] == 4
    assert "/_rsm/containers/team2-s1/server" not in calls
    assert "/_rsm/containers/reforger-m/server" not in calls
    for _ in range(200):  # the CPU sample lands in the background
        if "reforger-s1" in col._samples:
            break
        time.sleep(0.01)
    col._cached = None
    assert col.overview()["stacks"][0]["servers"][0]["cpu_percent"] == 3.0


def test_the_collector_shares_one_overview_between_pages():
    calls = []
    col = collector_mod.Collector(
        _observer({"/_rsm/host": HOST, "/_rsm/containers": []}, calls), version="0.67.0")
    col.overview()
    col.overview()
    assert calls.count("/_rsm/containers") == 1


def test_an_unreachable_observer_is_one_clear_warning():
    col = collector_mod.Collector(_observer({}), version="0.67.0")
    data = col.overview()
    assert data["stacks"] == []
    assert data["warnings"][0]["id"] == "observer-unreachable"


def test_history_records_the_totals():
    col = collector_mod.Collector(
        _observer({"/_rsm/host": HOST, "/_rsm/containers": _two_stacks()}), version="0.67.0")
    col.record()
    col.record()
    history = col.overview()["history"]
    assert len(history) == 2 and history[0]["running"] == 2


# --------------------------------------------------------------------------- #
# The app
# --------------------------------------------------------------------------- #
@pytest.fixture()
def supervisor(monkeypatch):
    from fastapi.testclient import TestClient

    import auth
    import config
    from supervisor import main

    monkeypatch.setattr(config.settings, "session_cookie_name", "rsm_supervisor")
    monkeypatch.setattr(main, "collector", collector_mod.Collector(
        _observer({"/_rsm/host": HOST, "/_rsm/containers": _two_stacks()}), version="0.67.0"))
    auth._attempts.clear()
    with TestClient(main.app) as c:
        yield c


def test_the_overview_needs_a_login(supervisor):
    assert supervisor.get("/api/overview").status_code == 401
    assert "version" not in supervisor.get("/api/version").json()


def test_signed_in_the_overview_is_there(supervisor):
    r = supervisor.post("/api/auth/login",
                        json={"username": "testadmin", "password": "testpass-123"})
    assert r.status_code == 200
    assert "rsm_supervisor=" in r.headers["set-cookie"]
    data = supervisor.get("/api/overview").json()
    assert data["totals"]["stacks"] == 2
    assert supervisor.get("/api/version").json()["name"] == "Reforger Server Supervisor"


def test_the_supervisor_changes_nothing(supervisor):
    """No route of the Supervisor's own writes anything but its login."""
    from supervisor import main

    writes = {
        route.path for route in main.app.routes
        if set(getattr(route, "methods", ()) or ()) - {"GET", "HEAD"}
    }
    assert writes <= {"/api/auth/login", "/api/auth/logout", "/api/auth/logout-all",
                      "/api/auth/password"}


def test_its_password_is_only_the_env_one(supervisor):
    supervisor.post("/api/auth/login", json={"username": "testadmin", "password": "testpass-123"})
    r = supervisor.post("/api/auth/password", json={"current_password": "testpass-123",
                                                    "new_password": "a-new-long-password"})
    assert r.status_code == 400 and ".env" in r.json()["detail"]
    import auth
    assert not auth.gui_password_set()
