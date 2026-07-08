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


def test_edit_ports_when_stopped(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "e", "template_id": tid}).json()["id"]
    r = logged_in.put(f"/api/instances/{iid}/ports",
                      json={"game_port": 2015, "a2s_port": 17790, "rcon_port": 20010})
    assert r.status_code == 200
    body = r.json()
    assert (body["game_port"], body["a2s_port"], body["rcon_port"]) == (2015, 17790, 20010)


def test_edit_ports_conflict(logged_in):
    tid = _template(logged_in)
    a = logged_in.post("/api/instances", json={"name": "pa", "template_id": tid}).json()
    iid = logged_in.post("/api/instances", json={"name": "pb", "template_id": tid}).json()["id"]
    # try to take instance a's game port
    r = logged_in.put(f"/api/instances/{iid}/ports", json={"game_port": a["game_port"]})
    assert r.status_code == 409
    assert "already used" in r.json()["detail"]


def test_edit_ports_partial_keeps_others(logged_in):
    tid = _template(logged_in)
    created = logged_in.post("/api/instances", json={"name": "pp", "template_id": tid}).json()
    iid = created["id"]
    r = logged_in.put(f"/api/instances/{iid}/ports", json={"game_port": 2018})
    assert r.status_code == 200
    body = r.json()
    assert body["game_port"] == 2018
    assert body["a2s_port"] == created["a2s_port"]  # unchanged


def test_restart_settings_split_toggles(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]
    # both default on
    v = logged_in.get(f"/api/instances/{iid}").json()
    assert v["auto_restart"] is True and v["auto_start"] is True
    # toggle each independently
    r = logged_in.put(f"/api/instances/{iid}/restart-settings", json={"auto_start": False})
    assert r.status_code == 200
    assert r.json()["auto_start"] is False and r.json()["auto_restart"] is True
    r = logged_in.put(f"/api/instances/{iid}/restart-settings", json={"auto_restart": False})
    assert r.json()["auto_restart"] is False and r.json()["auto_start"] is False


def test_schedule_set_normalise_and_clear(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "sch", "template_id": tid}).json()["id"]
    # none by default
    assert logged_in.get(f"/api/instances/{iid}").json()["restart_times"] == []
    # set: normalised (padded, sorted, de-duped)
    r = logged_in.put(f"/api/instances/{iid}/schedule", json={"times": ["16:00", "4:00", "04:00"]})
    assert r.status_code == 200
    assert r.json()["restart_times"] == ["04:00", "16:00"]
    # a next-restart label is surfaced while a schedule is set
    assert r.json()["next_restart"]
    # clear with an empty list
    r = logged_in.put(f"/api/instances/{iid}/schedule", json={"times": []})
    assert r.status_code == 200 and r.json()["restart_times"] == []
    assert r.json()["next_restart"] is None


def test_schedule_rejects_bad_time(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "schbad", "template_id": tid}).json()["id"]
    r = logged_in.put(f"/api/instances/{iid}/schedule", json={"times": ["25:00"]})
    assert r.status_code == 409
    assert "Invalid time" in r.json()["detail"]


def test_repoint_instance_template(logged_in):
    t1 = _template(logged_in, "t-one")
    t2 = _template(logged_in, "t-two")
    iid = logged_in.post("/api/instances", json={"name": "swap", "template_id": t1}).json()["id"]
    # swap to the second template (instance is stopped: docker mocked down)
    r = logged_in.put(f"/api/instances/{iid}/template", json={"template_id": t2})
    assert r.status_code == 200
    assert r.json()["template_id"] == t2 and r.json()["template_name"] == "t-two"


def test_repoint_unknown_template_conflicts(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "swap2", "template_id": tid}).json()["id"]
    r = logged_in.put(f"/api/instances/{iid}/template", json={"template_id": 9999})
    assert r.status_code == 409
    assert "Template not found" in r.json()["detail"]


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


def test_summary_aggregates(logged_in):
    tid = _template(logged_in)
    logged_in.post("/api/instances", json={"name": "a", "template_id": tid, "branch": "stable"})
    logged_in.post("/api/instances", json={"name": "b", "template_id": tid, "branch": "experimental"})
    r = logged_in.get("/api/instances/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["running"] == 0  # docker mocked down
    assert len(body["servers"]) == 2
    assert {s["name"] for s in body["servers"]} == {"a", "b"}


def test_summary_requires_auth(client):
    assert client.get("/api/instances/summary").status_code == 401


def test_status_absent_when_no_container(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]
    # docker mocked -> status reported as 'unknown' (ping False path)
    assert logged_in.get(f"/api/instances/{iid}").json()["status"] == "unknown"
