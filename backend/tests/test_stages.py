"""Test → Dev → Prod release stages for templates and mod lists (#223)."""
import json

from sqlmodel import Session, select

import models

A = "AAAAAAAAAAAAAAAA"
B = "BBBBBBBBBBBBBBBB"
TAB_A = {"X-Client-Id": "tab-a"}
TAB_B = {"X-Client-Id": "tab-b"}


def _spec(name="Conflict", **over):
    return {
        "name": name,
        "description": "test",
        "scenario_id": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        "mods": [{"modId": A, "name": "Alpha"}],
        "max_players": 32,
    } | over


def _mods(name="Pack", mods=None):
    return {"name": name, "description": "", "mods": mods or [{"modId": A, "name": "Alpha"}]}


def _create(client, name="Conflict", **over):
    r = client.post("/api/templates", json=_spec(name, **over))
    assert r.status_code == 201, r.text
    return r.json()


def _promote(client, tid, expect=200, headers=None, **body):
    r = client.post(f"/api/templates/{tid}/promote", json=body, headers=headers or {})
    assert r.status_code == expect, r.text
    return r.json()


def _to_prod(client, tid, **body):
    _promote(client, tid)
    return _promote(client, tid, **body)


def _log(client, tid):
    return client.get(f"/api/templates/{tid}/changelog").json()


def _listing(client, tid):
    return next(t for t in client.get("/api/templates").json() if t["id"] == tid)


# --------------------------------------------------------------------------- #
# Server templates
# --------------------------------------------------------------------------- #

def test_a_new_template_starts_in_test(logged_in):
    t = _create(logged_in)
    assert t["stage"] == "test"
    assert t["prod_version"] is None
    assert t["based_on"] is None
    row = _listing(logged_in, t["id"])
    assert row["stage"] == "test"
    assert row["server_count"] == 0


def test_test_to_dev_to_prod_in_place(logged_in):
    t = _create(logged_in)
    dev = _promote(logged_in, t["id"], note="ready for regression")
    assert dev["stage"] == "dev"
    assert dev["id"] == t["id"]

    prod = _promote(logged_in, t["id"], note="first golden release")
    assert prod["stage"] == "prod"
    assert prod["prod_version"] == 1
    assert prod["id"] == t["id"]

    with Session(models.get_engine()) as s:
        versions = s.exec(select(models.TemplateVersion)).all()
        row = s.get(models.Template, t["id"])
    assert [(v.version, v.note) for v in versions] == [(1, "first golden release")]
    assert versions[0].config_json == row.config_json  # v1 is the head

    summaries = [e["summary"] for e in _log(logged_in, t["id"])]
    assert "Moved from Test to Dev" in summaries
    assert "Released to Prod as v1" in summaries
    assert "first golden release" in summaries
    notes = [e for e in _log(logged_in, t["id"]) if e["category"] == "note"]
    assert {n["summary"] for n in notes} == {"ready for regression", "first golden release"}


def test_moving_stages_in_place_does_not_bump_updated_at(logged_in):
    # A running server would otherwise wrongly be told its template changed.
    t = _create(logged_in)
    before = t["updated_at"]
    _promote(logged_in, t["id"])
    after = _promote(logged_in, t["id"])
    assert after["updated_at"] == before


def test_prod_is_locked(logged_in):
    t = _create(logged_in)
    _to_prod(logged_in, t["id"])

    r = logged_in.put(f"/api/templates/{t['id']}", json=_spec(max_players=64))
    assert r.status_code == 409
    assert "Prod" in r.json()["detail"]
    # never edit-locked either: nobody can open it for editing
    assert logged_in.post(f"/api/templates/{t['id']}/lock", headers=TAB_A).status_code == 409
    # and the Mods overview can't add to it
    r = logged_in.post("/api/mods/add-to-template", json={"template_id": t["id"], "mod_ids": [B]})
    assert r.status_code == 409
    # already as high as it goes
    _promote(logged_in, t["id"], expect=400)


def test_prod_can_still_be_copied_and_read(logged_in):
    t = _create(logged_in)
    _to_prod(logged_in, t["id"])
    assert logged_in.get(f"/api/templates/{t['id']}/config.json").status_code == 200
    copy = logged_in.post(f"/api/templates/{t['id']}/copy").json()
    assert copy["stage"] == "test"
    assert copy["based_on"] == {"id": t["id"], "name": "Conflict", "version": 1}
    assert any(
        e["summary"] == "Copied from 'Conflict' v1 (Prod)" for e in _log(logged_in, copy["id"])
    )


def test_a_copy_of_a_copy_still_stems_from_the_prod_original(logged_in):
    t = _create(logged_in)
    _to_prod(logged_in, t["id"])
    first = logged_in.post(f"/api/templates/{t['id']}/copy").json()
    second = logged_in.post(f"/api/templates/{first['id']}/copy").json()
    assert second["based_on"]["id"] == t["id"]
    # a copy of something that never was Prod stems from nothing
    plain = _create(logged_in, "Plain")
    assert logged_in.post(f"/api/templates/{plain['id']}/copy").json()["based_on"] is None


def test_release_as_the_next_version_of_a_prod_template(logged_in):
    prod = _create(logged_in, "Conflict")
    _to_prod(logged_in, prod["id"])
    copy = logged_in.post(f"/api/templates/{prod['id']}/copy").json()
    spec = _spec(copy["name"], max_players=64, description="bigger")
    logged_in.put(f"/api/templates/{copy['id']}", json=spec | {"comment": "more slots"})
    _promote(logged_in, copy["id"])
    server = logged_in.post(
        "/api/instances", json={"name": "on-dev", "template_id": copy["id"]}
    ).json()

    result = _promote(logged_in, copy["id"], note="v2: 64 players", into_id=prod["id"])

    assert result["id"] == prod["id"]
    assert result["name"] == "Conflict"  # the Prod template keeps its name
    assert result["prod_version"] == 2
    assert result["description"] == "bigger"
    assert result["spec"]["max_players"] == 64
    # the Dev template is used up, and its server moved along to latest
    assert logged_in.get(f"/api/templates/{copy['id']}").status_code == 404
    assert logged_in.get(f"/api/instances/{server['id']}").json()["template_id"] == prod["id"]

    with Session(models.get_engine()) as s:
        versions = s.exec(
            select(models.TemplateVersion).order_by(models.TemplateVersion.version)
        ).all()
    assert [v.version for v in versions] == [1, 2]
    assert json.loads(versions[0].config_json)["game"]["maxPlayers"] == 32  # v1 kept
    assert json.loads(versions[1].config_json)["game"]["maxPlayers"] == 64

    summaries = [e["summary"] for e in _log(logged_in, prod["id"])]
    assert f"Released as v2 from '{copy['name']}' (Dev)" in summaries
    assert "v2: 64 players" in summaries
    # the Dev history moved over, saying where it happened
    assert f"In '{copy['name']}': more slots" in summaries
    assert "game.maxPlayers: 32 → 64" in summaries


def test_release_into_needs_a_prod_target(logged_in):
    dev = _create(logged_in, "Dev one")
    _promote(logged_in, dev["id"])
    other = _create(logged_in, "Not prod")
    _promote(logged_in, dev["id"], expect=400, into_id=other["id"])
    _promote(logged_in, dev["id"], expect=400, into_id=dev["id"])
    _promote(logged_in, dev["id"], expect=400, into_id=999999)


def test_promote_refuses_while_another_tab_edits_it(logged_in):
    t = _create(logged_in)
    assert logged_in.post(f"/api/templates/{t['id']}/lock", headers=TAB_A).status_code == 200
    _promote(logged_in, t["id"], expect=423, headers=TAB_B)
    _promote(logged_in, t["id"], headers=TAB_A)  # the editor itself may


def test_a_save_comment_is_logged_with_the_save(logged_in):
    t = _create(logged_in)
    logged_in.put(
        f"/api/templates/{t['id']}",
        json=_spec(max_players=40) | {"comment": "raised the cap for Friday"},
    )
    log = _log(logged_in, t["id"])
    note = next(e for e in log if e["category"] == "note")
    change = next(e for e in log if e["summary"] == "game.maxPlayers: 32 → 40")
    assert note["summary"] == "raised the cap for Friday"
    assert note["changed_at"] == change["changed_at"]  # same save event


def test_a_comment_on_a_save_that_changed_nothing_is_dropped(logged_in):
    t = _create(logged_in)
    logged_in.put(f"/api/templates/{t['id']}", json=_spec() | {"comment": "nothing really"})
    assert not [e for e in _log(logged_in, t["id"]) if e["category"] == "note"]


def test_deleting_prod_removes_its_versions_and_unlinks_copies(logged_in):
    t = _create(logged_in)
    _to_prod(logged_in, t["id"])
    copy = logged_in.post(f"/api/templates/{t['id']}/copy").json()
    assert logged_in.delete(f"/api/templates/{t['id']}").status_code == 204
    with Session(models.get_engine()) as s:
        assert not s.exec(select(models.TemplateVersion)).all()
    assert logged_in.get(f"/api/templates/{copy['id']}").json()["based_on"] is None


def test_deleting_prod_is_still_refused_while_a_server_uses_it(logged_in):
    t = _create(logged_in)
    _to_prod(logged_in, t["id"])
    logged_in.post("/api/instances", json={"name": "golden", "template_id": t["id"]})
    r = logged_in.delete(f"/api/templates/{t['id']}")
    assert r.status_code == 409
    assert "golden" in r.json()["detail"]


def test_listing_counts_servers(logged_in):
    t = _create(logged_in)
    logged_in.post("/api/instances", json={"name": "one", "template_id": t["id"]})
    logged_in.post("/api/instances", json={"name": "two", "template_id": t["id"]})
    assert _listing(logged_in, t["id"])["server_count"] == 2


def test_mods_only_an_old_version_uses_survive_a_rescan(logged_in):
    t = _create(logged_in, mods=[{"modId": A, "name": "Alpha"}])
    _to_prod(logged_in, t["id"])
    copy = logged_in.post(f"/api/templates/{t['id']}/copy").json()
    logged_in.put(
        f"/api/templates/{copy['id']}",
        json=_spec(copy["name"], mods=[{"modId": B, "name": "Bravo"}]),
    )
    _promote(logged_in, copy["id"])
    _promote(logged_in, copy["id"], into_id=t["id"])  # v2 drops Alpha
    logged_in.post("/api/mods/rescan")
    ids = {m["mod_id"] for m in logged_in.get("/api/mods").json()["mods"]}
    assert A in ids  # v1 still has it, and v1 can still be copied


# --------------------------------------------------------------------------- #
# Mod lists
# --------------------------------------------------------------------------- #

def _mod_promote(client, mid, expect=200, **body):
    r = client.post(f"/api/mod-templates/{mid}/promote", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def test_mod_lists_walk_the_same_stages(logged_in):
    mt = logged_in.post("/api/mod-templates", json=_mods()).json()
    assert mt["stage"] == "test"
    assert _mod_promote(logged_in, mt["id"])["stage"] == "dev"
    prod = _mod_promote(logged_in, mt["id"], note="golden pack")
    assert (prod["stage"], prod["prod_version"]) == ("prod", 1)

    r = logged_in.put(f"/api/mod-templates/{mt['id']}", json=_mods())
    assert r.status_code == 409
    listing = logged_in.get("/api/mod-templates").json()
    assert listing[0]["stage"] == "prod"


def test_mod_list_copy_and_release_as_next_version(logged_in):
    mt = logged_in.post("/api/mod-templates", json=_mods("Pack")).json()
    _mod_promote(logged_in, mt["id"])
    _mod_promote(logged_in, mt["id"])

    r = logged_in.post(f"/api/mod-templates/{mt['id']}/copy")
    assert r.status_code == 201
    copy = r.json()
    assert copy["name"] == "Copy of Pack"
    assert copy["stage"] == "test"
    assert copy["based_on"] == {"id": mt["id"], "name": "Pack", "version": 1}

    logged_in.put(
        f"/api/mod-templates/{copy['id']}",
        json=_mods("Copy of Pack", [{"modId": A}, {"modId": B, "name": "Bravo"}])
        | {"comment": "added Bravo"},
    )
    _mod_promote(logged_in, copy["id"])
    result = _mod_promote(logged_in, copy["id"], into_id=mt["id"])
    assert result["id"] == mt["id"]
    assert result["name"] == "Pack"
    assert result["prod_version"] == 2
    assert [m["modId"] for m in result["mods"]] == [A, B]
    assert logged_in.get(f"/api/mod-templates/{copy['id']}").status_code == 404
    summaries = [e["summary"] for e in logged_in.get(f"/api/mod-templates/{mt['id']}/changelog").json()]
    assert "In 'Copy of Pack': added Bravo" in summaries
    assert "Released as v2 from 'Copy of Pack' (Dev)" in summaries


def test_deleting_a_prod_mod_list_removes_its_versions(logged_in):
    mt = logged_in.post("/api/mod-templates", json=_mods()).json()
    _mod_promote(logged_in, mt["id"])
    _mod_promote(logged_in, mt["id"])
    assert logged_in.delete(f"/api/mod-templates/{mt['id']}").status_code == 204
    with Session(models.get_engine()) as s:
        assert not s.exec(select(models.ModTemplateVersion)).all()


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #

def _export(client):
    return json.loads(client.get("/api/backup/export").text)


def _wipe():
    with Session(models.get_engine()) as s:
        for model in (
            models.TemplateChange, models.TemplateVersion, models.Template,
            models.ModTemplateChange, models.ModTemplateVersion, models.ModTemplate,
        ):
            for row in s.exec(select(model)).all():
                s.delete(row)
        s.commit()


def test_backup_round_trips_stages_and_versions(logged_in):
    prod = _create(logged_in, "Conflict")
    _to_prod(logged_in, prod["id"], note="first")
    copy = logged_in.post(f"/api/templates/{prod['id']}/copy").json()
    logged_in.put(f"/api/templates/{copy['id']}", json=_spec(copy["name"], max_players=50))
    _promote(logged_in, copy["id"])
    _promote(logged_in, copy["id"], into_id=prod["id"], note="second")
    later = logged_in.post(f"/api/templates/{prod['id']}/copy").json()

    payload = _export(logged_in)
    entries = {t["name"]: t for t in payload["server_templates"]}
    assert entries["Conflict"]["stage"] == "prod"
    assert [v["version"] for v in entries["Conflict"]["versions"]] == [1, 2]
    assert entries["Conflict"]["versions"][0]["config"]["game"]["maxPlayers"] == 32
    assert entries[later["name"]]["based_on"] == {"name": "Conflict", "version": 2}

    _wipe()
    r = logged_in.post("/api/backup/import", json={"backup": payload, "decisions": {
        "server_templates": {"Conflict": "create", later["name"]: "create"},
    }})
    assert r.status_code == 200, r.text
    rows = {t["name"]: t for t in logged_in.get("/api/templates").json()}
    assert (rows["Conflict"]["stage"], rows["Conflict"]["prod_version"]) == ("prod", 2)
    assert rows[later["name"]]["stage"] == "test"
    assert rows[later["name"]]["based_on"]["id"] == rows["Conflict"]["id"]
    with Session(models.get_engine()) as s:
        notes = [v.note for v in s.exec(
            select(models.TemplateVersion).order_by(models.TemplateVersion.version)
        ).all()]
    assert notes == ["first", "second"]


def test_an_old_backup_without_stages_lands_in_dev(logged_in):
    _create(logged_in, "Old")
    payload = _export(logged_in)
    for entry in payload["server_templates"]:
        for key in ("stage", "based_on", "versions"):
            entry.pop(key, None)
    _wipe()
    logged_in.post("/api/backup/import", json={
        "backup": payload, "decisions": {"server_templates": {"Old": "create"}},
    })
    assert logged_in.get("/api/templates").json()[0]["stage"] == "dev"


def test_backup_may_not_overwrite_prod_but_may_copy_it_into_test(logged_in):
    t = _create(logged_in, "Golden")
    payload = _export(logged_in)
    _to_prod(logged_in, t["id"])

    preview = logged_in.post("/api/backup/preview", json=payload).json()
    assert preview["server_templates"][0]["existing_stage"] == "prod"

    r = logged_in.post("/api/backup/import", json={
        "backup": payload, "decisions": {"server_templates": {"Golden": "overwrite"}},
    })
    assert r.status_code == 409

    r = logged_in.post("/api/backup/import", json={
        "backup": payload, "decisions": {"server_templates": {"Golden": "copy"}},
    })
    assert r.status_code == 200, r.text
    copy = next(x for x in logged_in.get("/api/templates").json() if x["name"] == "Golden (imported)")
    assert copy["stage"] == "test"
    assert copy["based_on"]["id"] == t["id"]
