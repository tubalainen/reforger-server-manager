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


def test_scenario_player_count_persists_but_never_reaches_config(logged_in):
    # The scenario's Workshop player count seeds max_players in the wizard (#65).
    # It is wizard metadata: it round-trips through the DB, while config.json only
    # ever carries the resulting maxPlayers.
    spec = _spec("Sized scenario") | {"scenario_player_count": 12, "max_players": 12}
    tid = logged_in.post("/api/templates", json=spec).json()["id"]

    got = logged_in.get(f"/api/templates/{tid}").json()["spec"]
    assert got["scenario_player_count"] == 12
    assert got["max_players"] == 12

    cfg = logged_in.get(f"/api/templates/{tid}/config.json").json()
    assert cfg["game"]["maxPlayers"] == 12
    assert "scenario_player_count" not in json.dumps(cfg)

    # An override is the user's call and must survive a save untouched.
    logged_in.put(f"/api/templates/{tid}", json=spec | {"max_players": 40})
    got = logged_in.get(f"/api/templates/{tid}").json()["spec"]
    assert got["max_players"] == 40
    assert got["scenario_player_count"] == 12  # the recommendation still stands


def test_scenario_player_count_defaults_to_unknown(logged_in):
    # Base-game scenarios (and templates saved before #65) declare no count: the
    # field is simply absent and max_players stays whatever was submitted.
    sent = _spec("Unsized")
    tid = logged_in.post("/api/templates", json=sent).json()["id"]
    spec = logged_in.get(f"/api/templates/{tid}").json()["spec"]
    assert spec["scenario_player_count"] is None
    assert spec["max_players"] == sent["max_players"]


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


# ---- Hand-edited JSON: validate + reconcile (issue #29) ---------------------

def _config_of(logged_in, tid):
    return logged_in.get(f"/api/templates/{tid}/config.json").json()


def test_validate_and_reconcile_require_auth(client):
    assert client.post("/api/templates/validate", json={}).status_code == 401
    assert client.post("/api/templates/reconcile", json={}).status_code == 401


def test_validate_accepts_a_clean_config(logged_in):
    cfg = logged_in.post("/api/templates/preview", json=_spec()).json()
    assert logged_in.post("/api/templates/validate", json=cfg).json() == {
        "errors": [], "warnings": []
    }


def test_validate_warns_on_custom_keys_without_erroring(logged_in):
    cfg = logged_in.post("/api/templates/preview", json=_spec()).json()
    cfg["game"]["gameProperties"]["myScenarioKey"] = 42
    result = logged_in.post("/api/templates/validate", json=cfg).json()
    assert result["errors"] == []
    assert [w["path"] for w in result["warnings"]] == ["game.gameProperties.myScenarioKey"]


def test_validate_errors_on_a_bad_modelled_value(logged_in):
    cfg = logged_in.post("/api/templates/preview", json=_spec()).json()
    cfg["game"]["maxPlayers"] = 9999  # le=256
    result = logged_in.post("/api/templates/validate", json=cfg).json()
    assert [e["path"] for e in result["errors"]] == ["game.maxPlayers"]


def test_reconcile_splits_known_keys_into_the_spec_and_the_rest_into_extras(logged_in):
    spec = _spec()
    cfg = logged_in.post("/api/templates/preview", json=spec).json()
    cfg["game"]["maxPlayers"] = 128                       # modelled -> spec field
    cfg["game"]["gameProperties"]["customKey"] = "mine"   # unmodelled -> extras

    r = logged_in.post("/api/templates/reconcile", json={"spec": spec, "config": cfg})
    assert r.status_code == 200
    out = r.json()["spec"]
    assert out["max_players"] == 128
    assert out["extras"] == {"game": {"gameProperties": {"customKey": "mine"}}}
    # the known key flowed into the spec, so it must NOT also be an extras override
    assert "maxPlayers" not in out["extras"].get("game", {})
    assert [w["path"] for w in r.json()["warnings"]] == ["game.gameProperties.customKey"]


def test_reconcile_keeps_db_only_fields_from_the_incoming_spec(logged_in):
    # name/description/scenario_name/launch aren't in config.json; a raw edit
    # must not wipe them.
    spec = _spec() | {"scenario_name": "Conflict", "launch": {"max_fps": 60}}
    cfg = logged_in.post("/api/templates/preview", json=spec).json()
    out = logged_in.post(
        "/api/templates/reconcile", json={"spec": spec, "config": cfg}
    ).json()["spec"]
    assert out["name"] == "API Server"
    assert out["description"] == "test"
    assert out["scenario_name"] == "Conflict"
    assert out["launch"]["max_fps"] == 60


def test_reconcile_preserves_mod_dependency_metadata_when_mods_are_untouched(logged_in):
    # config.json holds a flat mods[]; taking it back verbatim would drop the
    # dependency graph the mod manager needs (#55).
    spec = _spec() | {"mods": [{
        "modId": "591AF5BDA9F7CE8B", "name": "RHS", "version": "1.0",
        "explicit": True, "dependencies": ["AAAAAAAAAAAAAAAA"], "versions": ["1.0", "0.9"],
    }]}
    cfg = logged_in.post("/api/templates/preview", json=spec).json()
    cfg["game"]["gameProperties"]["customKey"] = 1  # touch something *other* than mods

    out = logged_in.post(
        "/api/templates/reconcile", json={"spec": spec, "config": cfg}
    ).json()["spec"]
    assert out["mods"][0]["dependencies"] == ["AAAAAAAAAAAAAAAA"]
    assert out["mods"][0]["versions"] == ["1.0", "0.9"]
    assert "mods" not in out["extras"].get("game", {})


def test_reconcile_takes_the_new_mods_when_they_were_edited_by_hand(logged_in):
    spec = _spec()
    cfg = logged_in.post("/api/templates/preview", json=spec).json()
    cfg["game"]["mods"] = [{"modId": "BBBBBBBBBBBBBBBB", "name": "Hand added"}]
    out = logged_in.post(
        "/api/templates/reconcile", json={"spec": spec, "config": cfg}
    ).json()["spec"]
    assert [m["modId"] for m in out["mods"]] == ["BBBBBBBBBBBBBBBB"]
    assert "mods" not in out["extras"].get("game", {})


def test_reconcile_rejects_an_invalid_config(logged_in):
    spec = _spec()
    cfg = logged_in.post("/api/templates/preview", json=spec).json()
    cfg["game"]["scenarioId"] = ""
    r = logged_in.post("/api/templates/reconcile", json={"spec": spec, "config": cfg})
    assert r.status_code == 400
    assert "scenarioId" in r.json()["detail"]


def test_reconcile_rejects_a_malformed_payload(logged_in):
    assert logged_in.post("/api/templates/reconcile", json={"spec": {}}).status_code == 400


def test_custom_keys_survive_saving_and_editing_a_template(logged_in):
    # The end-to-end regression the overlay exists to prevent (#29).
    extras = {"game": {"gameProperties": {"customKey": "keep-me"}}}
    tid = logged_in.post("/api/templates", json=_spec() | {"extras": extras}).json()["id"]

    assert _config_of(logged_in, tid)["game"]["gameProperties"]["customKey"] == "keep-me"

    # reload as the wizard does, change an unrelated field, save
    spec = logged_in.get(f"/api/templates/{tid}").json()["spec"]
    assert spec["extras"] == extras
    spec["name"] = "API Server"
    spec["max_players"] = 100
    logged_in.put(f"/api/templates/{tid}", json=spec)

    cfg = _config_of(logged_in, tid)
    assert cfg["game"]["gameProperties"]["customKey"] == "keep-me"
    assert cfg["game"]["maxPlayers"] == 100


def test_templates_without_extras_are_unaffected(logged_in):
    tid = logged_in.post("/api/templates", json=_spec()).json()["id"]
    assert logged_in.get(f"/api/templates/{tid}").json()["spec"]["extras"] == {}


def test_extras_paths_matches_the_editor_warnings(logged_in):
    # The badge and the editor's warning list must never disagree about how many
    # custom keys a template has, so both read the same server-side definition:
    # top-most unknown paths, not leaf scalars (customBlock counts once, not twice).
    extras = {"game": {"gameProperties": {
        "myScenarioTickRate": 30,
        "customBlock": {"enabled": True, "mode": "hardcore"},
    }}}
    tid = logged_in.post("/api/templates", json=_spec() | {"extras": extras}).json()["id"]

    paths = logged_in.get(f"/api/templates/{tid}").json()["extras_paths"]
    assert paths == ["game.gameProperties.myScenarioTickRate", "game.gameProperties.customBlock"]

    cfg = _config_of(logged_in, tid)
    warned = [w["path"] for w in logged_in.post("/api/templates/validate", json=cfg).json()["warnings"]]
    assert warned == paths


def test_extras_paths_is_empty_for_a_plain_template(logged_in):
    tid = logged_in.post("/api/templates", json=_spec()).json()["id"]
    assert logged_in.get(f"/api/templates/{tid}").json()["extras_paths"] == []


def test_reconcile_works_before_the_template_is_named(logged_in):
    # "Edit JSON" is reachable from step 1, but the wizard doesn't collect a name
    # until step 4. The name isn't part of config.json, so it must not gate Apply.
    spec = _spec() | {"name": ""}
    cfg = logged_in.post("/api/templates/preview", json=_spec()).json()
    cfg["game"]["gameProperties"]["customKey"] = 1

    r = logged_in.post("/api/templates/reconcile", json={"spec": spec, "config": cfg})
    assert r.status_code == 200
    out = r.json()["spec"]
    assert out["name"] == ""  # still the user's to fill in — not the placeholder
    assert out["extras"] == {"game": {"gameProperties": {"customKey": 1}}}


def test_deleting_a_managed_key_does_not_become_a_permanent_override(logged_in):
    # Removing a key the wizard owns must not store a null that silently kills the
    # matching GUI control; the wizard re-renders it instead.
    spec = _spec()
    cfg = logged_in.post("/api/templates/preview", json=spec).json()
    del cfg["game"]["gameProperties"]["battlEye"]

    out = logged_in.post(
        "/api/templates/reconcile", json={"spec": spec, "config": cfg}
    ).json()["spec"]
    assert out["extras"] == {}
    # and it comes back on the next render, still controlled by the wizard
    rendered = logged_in.post("/api/templates/preview", json=out | {"name": "x"}).json()
    assert rendered["game"]["gameProperties"]["battlEye"] is True


def test_deleting_a_custom_key_removes_it_from_extras(logged_in):
    # The other direction still works: custom keys are the user's to delete.
    extras = {"game": {"gameProperties": {"customKey": "bye"}}}
    spec = _spec() | {"extras": extras}
    cfg = logged_in.post("/api/templates/preview", json=spec).json()
    assert cfg["game"]["gameProperties"]["customKey"] == "bye"
    del cfg["game"]["gameProperties"]["customKey"]

    out = logged_in.post(
        "/api/templates/reconcile", json={"spec": spec, "config": cfg}
    ).json()["spec"]
    assert out["extras"] == {}
