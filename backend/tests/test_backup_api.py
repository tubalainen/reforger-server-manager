"""Backup & restore of server and mod templates (#173)."""
import json

A = "AAAAAAAAAAAAAAAA"
B = "BBBBBBBBBBBBBBBB"


def _spec(name="Backup Server", **over):
    return {
        "name": name,
        "description": "the one we run on Fridays",
        "scenario_id": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        "scenario_name": "Conflict Everon",
        "scenario_player_count": 48,
        "mods": [{"modId": A, "name": "Alpha", "version": "1.0"}],
        "max_players": 32,
        "admin_password": "s3cret",
        "extras": {"game": {"gameProperties": {"customKey": "keep-me"}}},
        "launch": {"max_fps": 60},
    } | over


def _mod_spec(name="Milsim pack", mods=None):
    return {
        "name": name,
        "description": "our usual set",
        "mods": mods if mods is not None else [{"modId": A, "name": "Alpha"}],
    }


def _export(logged_in) -> dict:
    r = logged_in.get("/api/backup/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    return json.loads(r.text)


def _preview(logged_in, payload) -> dict:
    r = logged_in.post("/api/backup/preview", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _import(logged_in, payload, decisions, expect=200) -> dict:
    r = logged_in.post(
        "/api/backup/import", json={"backup": payload, "decisions": decisions}
    )
    assert r.status_code == expect, r.text
    return r.json()


def test_backup_requires_auth(client):
    assert client.get("/api/backup/export").status_code == 401
    assert client.get("/api/backup/summary").status_code == 401
    assert client.post("/api/backup/preview", json={}).status_code == 401
    assert client.post("/api/backup/import", json={}).status_code == 401


def test_export_holds_both_kinds_and_nothing_about_instances(logged_in):
    logged_in.post("/api/templates", json=_spec())
    logged_in.post("/api/mod-templates", json=_mod_spec())

    payload = _export(logged_in)
    assert payload["format"] == "reforger-server-manager/backup@1"
    assert [t["name"] for t in payload["server_templates"]] == ["Backup Server"]
    assert [m["name"] for m in payload["mod_templates"]] == ["Milsim pack"]
    # Instances are deliberately out of scope — no key, not even an empty one.
    assert "instances" not in payload

    assert logged_in.get("/api/backup/summary").json() == {
        "server_templates": 1, "mod_templates": 1
    }


def test_export_carries_what_a_restore_needs(logged_in):
    logged_in.post("/api/templates", json=_spec())
    entry = _export(logged_in)["server_templates"][0]

    assert entry["scenario_name"] == "Conflict Everon"
    assert entry["scenario_player_count"] == 48
    assert entry["launch"]["max_fps"] == 60
    assert entry["extras"] == {"game": {"gameProperties": {"customKey": "keep-me"}}}
    # The enriched mod list, not the flat one config.json holds (#55).
    assert entry["mods"][0]["modId"] == A
    assert entry["mods"][0]["explicit"] is True
    # A backup restores a working server, so the passwords ride along (#173).
    assert entry["config"]["game"]["passwordAdmin"] == "s3cret"


def test_importing_into_an_empty_manager_restores_everything(logged_in):
    logged_in.post("/api/templates", json=_spec())
    logged_in.post("/api/mod-templates", json=_mod_spec())
    payload = _export(logged_in)

    # Wipe both shelves, then restore from the file.
    tid = logged_in.get("/api/templates").json()[0]["id"]
    mid = logged_in.get("/api/mod-templates").json()[0]["id"]
    logged_in.delete(f"/api/templates/{tid}")
    logged_in.delete(f"/api/mod-templates/{mid}")

    preview = _preview(logged_in, payload)
    assert [t["status"] for t in preview["server_templates"]] == ["new"]
    assert [m["status"] for m in preview["mod_templates"]] == ["new"]

    result = _import(
        logged_in, payload,
        {"server_templates": {"Backup Server": "create"},
         "mod_templates": {"Milsim pack": "create"}},
    )
    assert result["counts"] == {"created": 2, "overwritten": 0, "skipped": 0}

    restored = logged_in.get("/api/templates").json()[0]
    spec = logged_in.get(f"/api/templates/{restored['id']}").json()["spec"]
    assert spec["scenario_name"] == "Conflict Everon"
    assert spec["max_players"] == 32
    assert spec["admin_password"] == "s3cret"
    assert spec["extras"] == {"game": {"gameProperties": {"customKey": "keep-me"}}}
    assert spec["launch"]["max_fps"] == 60
    assert [m["modId"] for m in spec["mods"]] == [A]
    assert logged_in.get("/api/mod-templates").json()[0]["mod_count"] == 1


def test_an_unchanged_shelf_previews_as_identical(logged_in):
    logged_in.post("/api/templates", json=_spec())
    logged_in.post("/api/mod-templates", json=_mod_spec())
    payload = _export(logged_in)

    preview = _preview(logged_in, payload)
    assert preview["server_templates"][0]["status"] == "identical"
    assert preview["server_templates"][0]["changes"] == []
    assert preview["mod_templates"][0]["status"] == "identical"


def test_preview_names_what_differs_and_never_shows_a_password(logged_in):
    created = logged_in.post("/api/templates", json=_spec()).json()
    payload = _export(logged_in)
    # Edit the live template: fewer players, a new password, one more mod.
    logged_in.put(
        f"/api/templates/{created['id']}",
        json=_spec(max_players=100, admin_password="moved-on",
                   mods=[{"modId": A, "name": "Alpha", "version": "1.0"},
                         {"modId": B, "name": "Bravo"}]),
    )

    item = _preview(logged_in, payload)["server_templates"][0]
    assert item["status"] == "differs"
    assert item["existing_id"] == created["id"]
    joined = " | ".join(item["changes"])
    assert "game.maxPlayers: 100 → 32" in joined
    assert "Removed mod 'Bravo'" in joined
    # Secrets are reported as "changed", never printed (#112's rule).
    assert "game.passwordAdmin changed" in joined
    assert "s3cret" not in joined and "moved-on" not in joined


def test_preview_reports_launch_parameter_differences(logged_in):
    # Launch params live outside config.json, so the change-log diff can't see
    # them — without the backup's own line an overwrite would look like a no-op.
    created = logged_in.post("/api/templates", json=_spec()).json()
    payload = _export(logged_in)
    logged_in.put(f"/api/templates/{created['id']}", json=_spec(launch={"max_fps": 120}))

    item = _preview(logged_in, payload)["server_templates"][0]
    assert item["status"] == "differs"
    assert any("Launch parameter max_fps: 120 → 60" in c for c in item["changes"])


def test_keep_overwrite_and_copy(logged_in):
    created = logged_in.post("/api/templates", json=_spec()).json()
    payload = _export(logged_in)
    logged_in.put(f"/api/templates/{created['id']}", json=_spec(max_players=100))

    # Keep: the live template is left exactly as it is.
    _import(logged_in, payload, {"server_templates": {"Backup Server": "skip"}})
    assert logged_in.get(f"/api/templates/{created['id']}").json()["spec"]["max_players"] == 100

    # Copy: the file's version lands beside it under a free name.
    result = _import(logged_in, payload, {"server_templates": {"Backup Server": "copy"}})
    assert result["server_templates"][0]["name"] == "Backup Server (imported)"
    assert result["counts"]["created"] == 1
    names = [t["name"] for t in logged_in.get("/api/templates").json()]
    assert sorted(names) == ["Backup Server", "Backup Server (imported)"]
    assert logged_in.get(f"/api/templates/{created['id']}").json()["spec"]["max_players"] == 100

    # A second copy doesn't collide with the first.
    again = _import(logged_in, payload, {"server_templates": {"Backup Server": "copy"}})
    assert again["server_templates"][0]["name"] == "Backup Server (imported 2)"

    # Overwrite: same row (so instances follow it), the file's values back.
    result = _import(logged_in, payload, {"server_templates": {"Backup Server": "overwrite"}})
    assert result["server_templates"][0]["id"] == created["id"]
    assert result["counts"] == {"created": 0, "overwritten": 1, "skipped": 0}
    assert logged_in.get(f"/api/templates/{created['id']}").json()["spec"]["max_players"] == 32


def test_anything_left_undecided_is_skipped(logged_in):
    logged_in.post("/api/templates", json=_spec())
    logged_in.post("/api/mod-templates", json=_mod_spec())
    payload = _export(logged_in)
    logged_in.delete(f"/api/templates/{logged_in.get('/api/templates').json()[0]['id']}")

    result = _import(logged_in, payload, {})
    assert result["counts"] == {"created": 0, "overwritten": 0, "skipped": 2}
    assert logged_in.get("/api/templates").json() == []


def test_import_is_all_or_nothing(logged_in):
    logged_in.post("/api/templates", json=_spec("Kept"))
    payload = _export(logged_in)
    logged_in.post("/api/mod-templates", json=_mod_spec("Shelf"))
    payload["mod_templates"] = [_mod_spec("Shelf") | {"mods": [{"modId": B}]}]

    # "create" onto a name that is taken fails — and the server template that
    # would have been created before it must not survive the failure.
    logged_in.delete(f"/api/templates/{logged_in.get('/api/templates').json()[0]['id']}")
    r = logged_in.post("/api/backup/import", json={
        "backup": payload,
        "decisions": {"server_templates": {"Kept": "create"},
                      "mod_templates": {"Shelf": "create"}},
    })
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]
    assert logged_in.get("/api/templates").json() == []


def test_overwrite_is_refused_while_someone_else_edits_that_template(logged_in):
    created = logged_in.post("/api/templates", json=_spec()).json()
    payload = _export(logged_in)
    # Another tab holds the edit lock (#102).
    assert logged_in.post(
        f"/api/templates/{created['id']}/lock", headers={"X-Client-Id": "other-tab"}
    ).status_code == 200

    r = logged_in.post(
        "/api/backup/import",
        json={"backup": payload,
              "decisions": {"server_templates": {"Backup Server": "overwrite"}}},
        headers={"X-Client-Id": "my-tab"},
    )
    assert r.status_code == 423
    assert "being edited in another session" in r.json()["detail"]


def test_import_writes_the_change_log(logged_in):
    created = logged_in.post("/api/templates", json=_spec()).json()
    payload = _export(logged_in)
    logged_in.put(f"/api/templates/{created['id']}", json=_spec(max_players=100))
    _import(logged_in, payload, {"server_templates": {"Backup Server": "overwrite"}})

    log = logged_in.get(f"/api/templates/{created['id']}/changelog").json()
    summaries = [e["summary"] for e in log]
    assert "Overwritten by a backup import" in summaries
    assert any("game.maxPlayers: 100 → 32" in s for s in summaries)

    # A newly imported template opens its own log saying where it came from.
    _import(logged_in, payload, {"server_templates": {"Backup Server": "copy"}})
    copy_id = next(
        t["id"] for t in logged_in.get("/api/templates").json()
        if t["name"] == "Backup Server (imported)"
    )
    copied_log = [e["summary"] for e in logged_in.get(f"/api/templates/{copy_id}/changelog").json()]
    assert "Template imported from a backup file" in copied_log
    assert any("Added mod 'Alpha'" in s for s in copied_log)


def test_imported_mods_reach_the_mods_overview(logged_in):
    # A restored template's mods count as in use, exactly as a saved one's do (#131).
    def registered():
        return [m["mod_id"] for m in logged_in.get("/api/mods").json()["mods"]]

    logged_in.post("/api/templates", json=_spec())
    payload = _export(logged_in)
    tid = logged_in.get("/api/templates").json()[0]["id"]
    assert logged_in.delete(f"/api/templates/{tid}").status_code == 204
    logged_in.post("/api/mods/rescan")  # prune what no template uses any more
    assert A not in registered()

    _import(logged_in, payload, {"server_templates": {"Backup Server": "create"}})
    assert A in registered()  # registered by the import itself, with no rescan


def test_a_file_that_is_not_a_backup_is_refused_with_a_reason(logged_in):
    for bad, expected in [
        ({"hello": "world"}, "not a Reforger Server Manager backup"),
        ({"format": "reforger-server-manager/backup@9"}, "format @9"),
        ({"format": "reforger-server-manager/backup@1"}, "holds no templates"),
    ]:
        r = logged_in.post("/api/backup/preview", json=bad)
        assert r.status_code == 400
        assert expected in r.json()["detail"]


def test_a_damaged_template_entry_names_itself(logged_in):
    payload = {
        "format": "reforger-server-manager/backup@1",
        "server_templates": [{"name": "Broken", "config": {"game": {}}}],
    }
    r = logged_in.post("/api/backup/preview", json=payload)
    assert r.status_code == 400
    assert "Broken" in r.json()["detail"] and "scenarioId" in r.json()["detail"]

    dupes = {
        "format": "reforger-server-manager/backup@1",
        "mod_templates": [_mod_spec("Same"), _mod_spec("Same")],
    }
    r = logged_in.post("/api/backup/preview", json=dupes)
    assert r.status_code == 400
    assert "more than one mod template named 'Same'" in r.json()["detail"]


def test_a_template_saved_before_the_enriched_mod_list_still_round_trips(logged_in):
    # Templates from before #55 keep only the flat mods[] in config.json; the
    # export lifts those so the restored template still has a usable mod list.
    from sqlmodel import Session, select

    from models import Template, get_engine

    logged_in.post("/api/templates", json=_spec("Legacy"))
    with Session(get_engine()) as session:
        row = session.exec(select(Template).where(Template.name == "Legacy")).one()
        row.mods_json = ""  # what a pre-#55 row looks like
        session.add(row)
        session.commit()

    entry = _export(logged_in)["server_templates"][0]
    assert [m["modId"] for m in entry["mods"]] == [A]
    assert entry["mods"][0]["explicit"] is True

    logged_in.delete(f"/api/templates/{logged_in.get('/api/templates').json()[0]['id']}")
    _import(logged_in, _export(logged_in) | {"server_templates": [entry]},
            {"server_templates": {"Legacy": "create"}})
    restored = logged_in.get("/api/templates").json()[0]
    spec = logged_in.get(f"/api/templates/{restored['id']}").json()["spec"]
    assert [m["modId"] for m in spec["mods"]] == [A]
