"""Instance API tests. Docker is mocked (ping()=False in conftest), so these
cover DB-backed lifecycle, port leasing and validation — not real containers.
"""
from pathlib import Path


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


def test_edit_name_and_branch(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "orig", "template_id": tid}).json()["id"]
    r = logged_in.put(f"/api/instances/{iid}", json={"name": "renamed", "branch": "experimental"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "renamed" and body["branch"] == "experimental"


def test_edit_name_conflict(logged_in):
    tid = _template(logged_in)
    logged_in.post("/api/instances", json={"name": "taken", "template_id": tid})
    iid = logged_in.post("/api/instances", json={"name": "other", "template_id": tid}).json()["id"]
    r = logged_in.put(f"/api/instances/{iid}", json={"name": "taken"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_edit_unknown_branch_conflict(logged_in):
    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "b", "template_id": tid}).json()["id"]
    r = logged_in.put(f"/api/instances/{iid}", json={"branch": "nightly"})
    assert r.status_code == 409
    assert "Unknown branch" in r.json()["detail"]


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


def test_delete_passes_purge_data_flag_through(logged_in, monkeypatch):
    from services import instance_service

    tid = _template(logged_in)
    iid = logged_in.post("/api/instances", json={"name": "s", "template_id": tid}).json()["id"]

    seen = {}
    monkeypatch.setattr(
        instance_service, "delete_instance",
        lambda instance_id, purge_data=False: seen.update(id=instance_id, purge=purge_data),
    )

    assert logged_in.delete(f"/api/instances/{iid}").status_code == 204
    assert seen == {"id": iid, "purge": False}          # default leaves data on disk

    assert logged_in.delete(f"/api/instances/{iid}?purge_data=true").status_code == 204
    assert seen == {"id": iid, "purge": True}           # opt-in wipes it too


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


# --- saved game backups (#179) ------------------------------------------------

def _instance_with_state(logged_in, tmp_path, monkeypatch):
    import shutil

    import config

    # The session signing salt lives in DATA_DIR (auth._salt_path), so moving
    # DATA_DIR out from under a logged-in client invalidates its cookie and every
    # call here comes back 401. Carry the salt over with it.
    salt = Path(config.settings.data_dir) / "session_salt"
    if salt.is_file():
        shutil.copy(salt, tmp_path / "session_salt")
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    tid = _template(logged_in, "ff-template")
    iid = logged_in.post(
        "/api/instances", json={"name": "ff", "template_id": tid}
    ).json()["id"]
    save = tmp_path / "instances" / str(iid) / "profile" / "profile" / ".save"
    save.mkdir(parents=True)
    (save / "game.bin").write_bytes(b"world")
    return tid, iid


def test_backups_require_auth(client):
    assert client.get("/api/instances/1/backups").status_code == 401


def test_backup_create_list_download_and_delete(logged_in, tmp_path, monkeypatch):
    _, iid = _instance_with_state(logged_in, tmp_path, monkeypatch)

    created = logged_in.post(f"/api/instances/{iid}/backups", json={"label": "pre-swap"})
    assert created.status_code == 201
    bid = created.json()["id"]

    listed = logged_in.get(f"/api/instances/{iid}/backups").json()
    assert [b["id"] for b in listed["backups"]] == [bid]
    assert listed["state"]["files"] == 1 and listed["keep"] == 10

    dl = logged_in.get(f"/api/instances/{iid}/backups/{bid}/download")
    assert dl.status_code == 200
    assert dl.headers["content-disposition"].endswith(f'{bid}.tar.gz"')

    assert logged_in.delete(f"/api/instances/{iid}/backups/{bid}").status_code == 204
    assert logged_in.get(f"/api/instances/{iid}/backups").json()["backups"] == []


def test_backup_of_an_instance_with_nothing_saved_conflicts(logged_in, tmp_path, monkeypatch):
    _, iid = _instance_with_state(logged_in, tmp_path, monkeypatch)
    import shutil

    shutil.rmtree(tmp_path / "instances" / str(iid) / "profile")
    r = logged_in.post(f"/api/instances/{iid}/backups", json={})
    assert r.status_code == 409
    assert "no saved game data" in r.json()["detail"]


def test_changing_template_can_back_the_old_world_up_first(logged_in, tmp_path, monkeypatch):
    tid, iid = _instance_with_state(logged_in, tmp_path, monkeypatch)
    other = _template(logged_in, "next-scenario")

    r = logged_in.put(
        f"/api/instances/{iid}/template",
        json={"template_id": other, "backup_first": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["template_id"] == other
    # ...and the backup is labelled with the template that actually wrote it
    assert body["backup"]["template_name"] == "ff-template"
    assert body["backup"]["source"] == "template-switch"


def test_uploading_a_file_that_is_not_a_backup_is_refused(logged_in, tmp_path, monkeypatch):
    _, iid = _instance_with_state(logged_in, tmp_path, monkeypatch)
    r = logged_in.post(
        f"/api/instances/{iid}/backups/upload",
        files={"file": ("holiday.tar.gz", b"not gzip at all", "application/gzip")},
    )
    assert r.status_code == 400
    assert "not a readable" in r.json()["detail"]


def test_uploading_a_backup_puts_it_on_the_shelf(logged_in, tmp_path, monkeypatch):
    _, iid = _instance_with_state(logged_in, tmp_path, monkeypatch)
    bid = logged_in.post(f"/api/instances/{iid}/backups", json={}).json()["id"]
    blob = logged_in.get(f"/api/instances/{iid}/backups/{bid}/download").content
    logged_in.delete(f"/api/instances/{iid}/backups/{bid}")

    r = logged_in.post(
        f"/api/instances/{iid}/backups/upload",
        files={"file": ("carried.tar.gz", blob, "application/gzip")},
    )
    assert r.status_code == 201
    assert r.json()["source"] == "upload"
    assert len(logged_in.get(f"/api/instances/{iid}/backups").json()["backups"]) == 1
