import json


def _spec(name="API Server"):
    return {
        "name": name,
        "description": "test",
        "scenario_id": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        "mods": [{"modId": "591AF5BDA9F7CE8B", "name": "RHS", "version": "1.0"}],
        "max_players": 32,
    }


def test_templates_require_auth(client):
    assert client.get("/api/templates").status_code == 401
    assert client.post("/api/templates", json=_spec()).status_code == 401


def test_template_crud_and_download(logged_in):
    # create
    r = logged_in.post("/api/templates", json=_spec())
    assert r.status_code == 201
    tid = r.json()["id"]
    assert r.json()["spec"]["max_players"] == 32

    # list
    listing = logged_in.get("/api/templates").json()
    assert any(t["id"] == tid for t in listing)

    # get + edit
    logged_in.put(f"/api/templates/{tid}", json=_spec() | {"max_players": 100})
    assert logged_in.get(f"/api/templates/{tid}").json()["spec"]["max_players"] == 100

    # download config.json
    dl = logged_in.get(f"/api/templates/{tid}/config.json")
    assert dl.status_code == 200
    assert "attachment" in dl.headers["content-disposition"]
    cfg = json.loads(dl.text)
    assert cfg["game"]["maxPlayers"] == 100

    # delete
    assert logged_in.delete(f"/api/templates/{tid}").status_code == 204
    assert logged_in.get(f"/api/templates/{tid}").status_code == 404


def test_duplicate_name_conflict(logged_in):
    logged_in.post("/api/templates", json=_spec("Dupe"))
    assert logged_in.post("/api/templates", json=_spec("Dupe")).status_code == 409


def test_launch_params_persist_and_roundtrip(logged_in):
    spec = _spec("Launchy")
    spec["launch"] = {"max_fps": 60, "no_backend": True, "auto_reload_scenario": 300}
    r = logged_in.post("/api/templates", json=spec)
    assert r.status_code == 201
    tid = r.json()["id"]
    got = logged_in.get(f"/api/templates/{tid}").json()["spec"]["launch"]
    assert got["max_fps"] == 60
    assert got["no_backend"] is True
    assert got["auto_reload_scenario"] == 300


def test_preview_without_saving(logged_in):
    r = logged_in.post("/api/templates/preview", json=_spec())
    assert r.status_code == 200
    assert r.json()["game"]["scenarioId"].endswith("23_Campaign.conf")
    # nothing persisted
    assert logged_in.get("/api/templates").json() == []


def test_invalid_spec_rejected(logged_in):
    bad = _spec()
    bad["scenario_id"] = ""
    assert logged_in.post("/api/templates", json=bad).status_code == 422


def test_workshop_search_bubbles_unavailable(logged_in, monkeypatch):
    from services import workshop_service

    def boom(q, page=1):
        raise workshop_service.WorkshopError("upstream down")

    monkeypatch.setattr(workshop_service.workshop, "search", boom)
    r = logged_in.get("/api/workshop/search", params={"q": "conflict"})
    assert r.status_code == 502


def test_workshop_bad_id_400(logged_in):
    assert logged_in.get("/api/workshop/asset/notanid").status_code == 400
