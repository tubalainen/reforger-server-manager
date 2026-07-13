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


def test_delete_blocked_while_instance_uses_template(logged_in):
    tid = logged_in.post("/api/templates", json=_spec("InUse")).json()["id"]
    logged_in.post("/api/instances", json={"name": "bound", "template_id": tid})
    r = logged_in.delete(f"/api/templates/{tid}")
    assert r.status_code == 409
    assert "bound" in r.json()["detail"]  # names the offending instance
    # still there
    assert logged_in.get(f"/api/templates/{tid}").status_code == 200


def test_list_reports_persistence_and_hive(logged_in):
    plain = logged_in.post("/api/templates", json=_spec("Plain")).json()["id"]
    persisted = logged_in.post(
        "/api/templates",
        json=_spec("Persisted") | {"persistence_enabled": True, "hive_id": 7},
    ).json()["id"]
    rows = {t["id"]: t for t in logged_in.get("/api/templates").json()}
    assert rows[plain]["persistence"] is False and rows[plain]["hive_id"] is None
    assert rows[persisted]["persistence"] is True and rows[persisted]["hive_id"] == 7


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


def test_mods_dependency_metadata_persists(logged_in):
    # The enriched mod list (explicit/dependency edges) survives save+load, while
    # config.json still gets the clean flat mods[] (#55).
    spec = _spec("Modded")
    spec["mods"] = [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "ACE", "version": "1.0",
         "explicit": True, "from_scenario": False,
         "dependencies": ["BBBBBBBBBBBBBBBB"], "versions": ["1.1", "1.0"]},
        {"modId": "BBBBBBBBBBBBBBBB", "name": "ACE Core", "version": "1.0",
         "explicit": False, "from_scenario": False, "dependencies": []},
    ]
    tid = logged_in.post("/api/templates", json=spec).json()["id"]

    got = logged_in.get(f"/api/templates/{tid}").json()["spec"]["mods"]
    by_id = {m["modId"]: m for m in got}
    assert by_id["AAAAAAAAAAAAAAAA"]["explicit"] is True
    assert by_id["AAAAAAAAAAAAAAAA"]["dependencies"] == ["BBBBBBBBBBBBBBBB"]
    # the version-lock picker's history survives the round trip too (#60)
    assert by_id["AAAAAAAAAAAAAAAA"]["versions"] == ["1.1", "1.0"]
    assert by_id["BBBBBBBBBBBBBBBB"]["explicit"] is False

    # config.json is still the clean flat list the server understands
    cfg = logged_in.get(f"/api/templates/{tid}/config.json").json()
    assert cfg["game"]["mods"] == [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "ACE", "version": "1.0"},
        {"modId": "BBBBBBBBBBBBBBBB", "name": "ACE Core", "version": "1.0"},
    ]


def test_scenario_name_persists_and_roundtrips(logged_in):
    # The scenario display name survives save+load so the edit wizard can show
    # the current scenario (#59), but stays out of the server's config.json.
    spec = _spec("Named scenario") | {"scenario_name": "Conflict Everon"}
    tid = logged_in.post("/api/templates", json=spec).json()["id"]
    assert logged_in.get(f"/api/templates/{tid}").json()["spec"]["scenario_name"] == "Conflict Everon"

    cfg = logged_in.get(f"/api/templates/{tid}/config.json").json()
    assert "scenario_name" not in json.dumps(cfg)

    # replacing the scenario updates the stored name too
    logged_in.put(
        f"/api/templates/{tid}",
        json=spec | {"scenario_id": "{DEAD}Missions/Other.conf", "scenario_name": "Other"},
    )
    got = logged_in.get(f"/api/templates/{tid}").json()["spec"]
    assert got["scenario_id"] == "{DEAD}Missions/Other.conf"
    assert got["scenario_name"] == "Other"


def test_preview_without_saving(logged_in):
    r = logged_in.post("/api/templates/preview", json=_spec())
    assert r.status_code == 200
    assert r.json()["game"]["scenarioId"].endswith("23_Campaign.conf")
    # nothing persisted
    assert logged_in.get("/api/templates").json() == []


def test_import_config_roundtrips_into_spec(logged_in):
    # Export a template's config.json, then import it back and confirm the
    # wizard fields are reconstructed (#35).
    tid = logged_in.post(
        "/api/templates", json=_spec("Exported") | {"max_players": 48}
    ).json()["id"]
    cfg = logged_in.get(f"/api/templates/{tid}/config.json").json()

    r = logged_in.post("/api/templates/import", json=cfg)
    assert r.status_code == 200
    spec = r.json()["spec"]
    assert spec["max_players"] == 48
    assert spec["scenario_id"].endswith("23_Campaign.conf")
    assert spec["mods"][0]["modId"] == "591AF5BDA9F7CE8B"


def test_import_config_requires_auth(client):
    assert client.post("/api/templates/import", json={"game": {}}).status_code == 401


def test_import_config_rejects_malformed(logged_in):
    # "game" must be an object; a string makes the mapping fail -> 400.
    r = logged_in.post("/api/templates/import", json={"game": "not-an-object"})
    assert r.status_code == 400


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
