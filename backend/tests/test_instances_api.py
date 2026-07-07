"""Instance API tests. Docker is mocked (ping()=False in conftest), so these
cover DB-backed lifecycle, port leasing and validation — not real containers.
"""


def _template(logged_in, name="tpl"):
    spec = {
        "name": name,
        "scenario_id": "{ABC}Missions/x.conf",
        "mods": [{"modId": "AAA", "name": "A", "version": "1.0"}],
    }
    return logged_in.post("/api/templates", json=spec).json()["id"]


def test_instances_require_auth(client):
    assert client.get("/api/instances").status_code == 401


def test_create_leases_distinct_ports(logged_in):
    tid = _template(logged_in)
    a = logged_in.post("/api/instances", json={"name": "a", "template_id": tid, "branch": "stable"})
    b = logged_in.post("/api/instances", json={"name": "b", "template_id": tid, "branch": "experimental"})
    assert a.status_code == 201 and b.status_code == 201
    pa, pb = a.json(), b.json()
    # different instances never share a host port
    assert pa["game_port"] != pb["game_port"]
    assert pa["a2s_port"] != pb["a2s_port"]
    assert pa["rcon_port"] != pb["rcon_port"]
    assert pb["branch"] == "experimental"


def test_create_with_custom_ports(logged_in):
    tid = _template(logged_in)
    r = logged_in.post("/api/instances", json={
        "name": "custom", "template_id": tid, "branch": "stable",
        "game_port": 7780, "a2s_port": 7781, "rcon_port": 7782,
    })
    assert r.status_code == 201
    body = r.json()
    assert (body["game_port"], body["a2s_port"], body["rcon_port"]) == (7780, 7781, 7782)


def test_create_custom_port_conflict(logged_in):
    tid = _template(logged_in)
    logged_in.post("/api/instances", json={
        "name": "a", "template_id": tid, "game_port": 7790, "a2s_port": 7791, "rcon_port": 7792,
    })
    r = logged_in.post("/api/instances", json={
        "name": "b", "template_id": tid, "game_port": 7790, "a2s_port": 8001, "rcon_port": 8002,
    })
    assert r.status_code == 409
    assert "already used" in r.json()["detail"]


def test_create_duplicate_name_conflict(logged_in):
    tid = _template(logged_in)
    logged_in.post("/api/instances", json={"name": "dup", "template_id": tid})
    r = logged_in.post("/api/instances", json={"name": "dup", "template_id": tid})
    assert r.status_code == 409


def test_create_unknown_template_conflict(logged_in):
    r = logged_in.post("/api/instances", json={"name": "x", "template_id": 999})
    assert r.status_code == 409


def test_create_unknown_branch_conflict(logged_in):
    tid = _template(logged_in)
    r = logged_in.post("/api/instances", json={"name": "x", "template_id": tid, "branch": "nightly"})
    assert r.status_code == 409


def test_start_without_docker_conflicts(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]
    # conftest forces docker ping False -> start reports daemon unreachable
    r = logged_in.post(f"/api/instances/{iid}/start")
    assert r.status_code == 409
    assert "Docker" in r.json()["detail"]


def test_auto_restart_toggle(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]
    r = logged_in.put(f"/api/instances/{iid}/auto-restart", json={"auto_restart": False})
    assert r.status_code == 200 and r.json()["auto_restart"] is False


def test_stop_and_delete(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]
    assert logged_in.post(f"/api/instances/{iid}/stop").status_code == 200
    assert logged_in.delete(f"/api/instances/{iid}").status_code == 204
    assert logged_in.get(f"/api/instances/{iid}").status_code == 404


def test_stats_endpoint_shape(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]
    r = logged_in.get(f"/api/instances/{iid}/stats")
    assert r.status_code == 200
    body = r.json()
    # docker mocked down -> container absent, live fields stay None
    assert body["game_port"] and "players" in body and "cpu_percent" in body


def test_stats_unknown_instance_404(logged_in):
    assert logged_in.get("/api/instances/999/stats").status_code == 404


def test_status_absent_when_no_container(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]
    # docker mocked -> status reported as 'unknown' (ping False path)
    assert logged_in.get(f"/api/instances/{iid}").json()["status"] == "unknown"
