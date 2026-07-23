"""Mods Overview registry API (#131)."""
from sqlmodel import Session

import models

RHS = "591AF5BDA9F7CE8B"


def _spec(name="API Server", mods=None):
    return {
        "name": name,
        "description": "test",
        "scenario_id": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        "mods": mods if mods is not None else [{"modId": RHS, "name": "RHS"}],
        "max_players": 32,
    }


def _mods(client):
    return client.get("/api/mods").json()["mods"]


def _entry(client, mod_id):
    return next((m for m in _mods(client) if m["mod_id"] == mod_id), None)


def test_mods_require_auth(client):
    assert client.get("/api/mods").status_code == 401
    assert client.post("/api/mods/rescan").status_code == 401
    assert client.delete(f"/api/mods/{RHS}").status_code == 401


def test_template_mods_enter_the_overview(logged_in):
    logged_in.post("/api/templates", json=_spec())
    e = _entry(logged_in, RHS)
    assert e is not None
    assert e["name"] == "RHS"
    assert e["persist"] is False
    assert e["orphaned"] is False
    assert any(t["name"] == "API Server" for t in e["templates"])


def test_mod_stays_after_its_template_is_deleted(logged_in):
    # "Once added, always on the overview": deleting the template that carried a
    # mod leaves the mod on the overview, flagged as no longer used.
    t = logged_in.post("/api/templates", json=_spec()).json()
    logged_in.delete(f"/api/templates/{t['id']}")
    e = _entry(logged_in, RHS)
    assert e is not None
    assert e["orphaned"] is True
    assert e["templates"] == []


def test_overview_reports_instances_and_version(logged_in):
    t = logged_in.post(
        "/api/templates",
        json=_spec("WithInst", mods=[{"modId": RHS, "name": "RHS", "version": "1.0"}]),
    ).json()
    with Session(models.get_engine()) as s:
        s.add(models.Instance(
            name="inst1", template_id=t["id"],
            game_port=2001, a2s_port=17777, rcon_port=19999,
        ))
        s.commit()
    e = _entry(logged_in, RHS)
    assert len(e["instances"]) == 1
    assert e["instances"][0]["name"] == "inst1"
    assert e["instances"][0]["template"] == "WithInst"
    # The version the template configures (a lock here) is what the instance runs.
    assert e["instances"][0]["version"] == "1.0"


def test_overview_flags_scenario_publishing_mods(logged_in):
    # A mod whose template entry marks it as publishing its own scenario(s) is
    # flagged in the overview, so the UI can distinguish it even offline (#131).
    logged_in.post("/api/templates", json=_spec(
        "ScenMod",
        mods=[{"modId": RHS, "name": "RHS", "provides_scenarios": True}],
    ))
    assert _entry(logged_in, RHS)["provides_scenarios"] is True


def test_persist_flag_toggles(logged_in):
    logged_in.post("/api/templates", json=_spec())
    r = logged_in.patch(f"/api/mods/{RHS}", json={"persist": True})
    assert r.status_code == 200 and r.json()["persist"] is True
    assert _entry(logged_in, RHS)["persist"] is True
    logged_in.patch(f"/api/mods/{RHS}", json={"persist": False})
    assert _entry(logged_in, RHS)["persist"] is False


def test_persist_patch_validates_body(logged_in):
    logged_in.post("/api/templates", json=_spec())
    assert logged_in.patch(f"/api/mods/{RHS}", json={}).status_code == 400
    assert logged_in.patch(f"/api/mods/{RHS}", json={"persist": "yes"}).status_code == 400


def test_patch_unknown_mod_is_404(logged_in):
    assert logged_in.patch(f"/api/mods/{RHS}", json={"persist": True}).status_code == 404


def test_delete_refuses_persisted_then_allows_after_clear(logged_in):
    logged_in.post("/api/templates", json=_spec())
    logged_in.patch(f"/api/mods/{RHS}", json={"persist": True})
    assert logged_in.delete(f"/api/mods/{RHS}").status_code == 409  # persisted -> refused
    logged_in.patch(f"/api/mods/{RHS}", json={"persist": False})
    assert logged_in.delete(f"/api/mods/{RHS}").status_code == 204
    assert _entry(logged_in, RHS) is None


def test_delete_unknown_mod_is_404(logged_in):
    assert logged_in.delete(f"/api/mods/{RHS}").status_code == 404


def test_rescan_prunes_unpersisted_orphans(logged_in):
    t = logged_in.post("/api/templates", json=_spec()).json()
    logged_in.delete(f"/api/templates/{t['id']}")  # mod now orphaned
    r = logged_in.post("/api/mods/rescan")
    assert r.status_code == 200 and r.json()["pruned"] == 1
    assert _entry(logged_in, RHS) is None


def test_rescan_keeps_persisted_orphans_and_current_mods(logged_in):
    kept = logged_in.post("/api/templates", json=_spec("Keeper")).json()
    gone = logged_in.post(
        "/api/templates", json=_spec("Gone", mods=[{"modId": "AAAAAAAAAAAAAAAA", "name": "Old"}])
    ).json()
    logged_in.delete(f"/api/templates/{gone['id']}")     # AAAA... orphaned
    logged_in.patch("/api/mods/AAAAAAAAAAAAAAAA", json={"persist": True})
    logged_in.post("/api/mods/rescan")
    ids = {m["mod_id"] for m in _mods(logged_in)}
    assert RHS in ids                    # still used by Keeper
    assert "AAAAAAAAAAAAAAAA" in ids     # orphaned but persisted
    assert kept  # (kept referenced to satisfy linters on unused var)


def test_tree_resolves_dependencies(logged_in, monkeypatch):
    from services import mod_registry

    logged_in.post("/api/templates", json=_spec())

    def fake_resolve(mid):
        assert mid == RHS
        return {
            "mods": [
                {"modId": RHS, "name": "RHS", "kind": "addon",
                 "tags": ["Vehicles"], "dependencies": ["DEADBEEFDEADBEEF"]},
                {"modId": "DEADBEEFDEADBEEF", "name": "Core", "kind": "addon",
                 "tags": [], "dependencies": []},
            ],
            "missing": [],
        }

    monkeypatch.setattr(mod_registry.workshop, "resolve_dependencies", fake_resolve)
    tree = logged_in.get("/api/mods/tree").json()
    assert tree["resolved"] is True
    assert tree["edges"][RHS] == ["DEADBEEFDEADBEEF"]
    assert tree["names"]["DEADBEEFDEADBEEF"] == "Core"
    # The Workshop type/tags ride along so the UI can label each mod (#131).
    assert tree["types"][RHS] == {"kind": "addon", "tags": ["Vehicles"]}
    assert tree["missing"] == []


def test_tree_degrades_when_workshop_unreachable(logged_in, monkeypatch):
    from services import mod_registry
    from services.workshop_service import WorkshopError

    logged_in.post("/api/templates", json=_spec())

    def boom(mid):
        raise WorkshopError("down")

    monkeypatch.setattr(mod_registry.workshop, "resolve_dependencies", boom)
    tree = logged_in.get("/api/mods/tree").json()
    assert tree["resolved"] is False
    assert RHS in tree["missing"]


def test_add_mods_to_template(logged_in, monkeypatch):
    from services import mod_registry

    new_id = "ABCDEF0123456789"
    t = logged_in.post("/api/templates", json=_spec("Target")).json()

    def fake_get(mid, use_cache=True):
        return {"id": mid, "name": "New Mod", "versions": ["1.2"], "scenarios": []}

    monkeypatch.setattr(mod_registry.workshop, "get_asset", fake_get)

    r = logged_in.post(
        "/api/mods/add-to-template", json={"template_id": t["id"], "mod_ids": [new_id]}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["added"][0]["mod_id"] == new_id
    assert body["added"][0]["name"] == "New Mod"
    assert body["template"]["id"] == t["id"]

    # The template now carries the mod...
    spec = logged_in.get(f"/api/templates/{t['id']}").json()["spec"]
    added = next(m for m in spec["mods"] if m["modId"] == new_id)
    assert added["explicit"] is True
    assert added["versions"] == ["1.2"]
    # ...it lands in the overview...
    assert _entry(logged_in, new_id) is not None
    # ...and the add is logged.
    log = logged_in.get(f"/api/templates/{t['id']}/changelog").json()
    assert any("New Mod" in e["summary"] for e in log)


def test_add_mods_to_template_skips_duplicates(logged_in, monkeypatch):
    from services import mod_registry

    monkeypatch.setattr(
        mod_registry.workshop, "get_asset",
        lambda mid, use_cache=True: {"id": mid, "name": "RHS", "versions": [], "scenarios": []},
    )
    t = logged_in.post("/api/templates", json=_spec("Dup")).json()  # already has RHS
    r = logged_in.post(
        "/api/mods/add-to-template", json={"template_id": t["id"], "mod_ids": [RHS]}
    )
    assert r.status_code == 200
    assert r.json()["added"] == []
    assert r.json()["skipped"] == [RHS]


def test_add_mods_to_template_respects_edit_lock(logged_in):
    t = logged_in.post("/api/templates", json=_spec("Locked")).json()
    # Another tab holds the lock.
    logged_in.post(f"/api/templates/{t['id']}/lock", headers={"X-Client-Id": "other-tab"})
    r = logged_in.post(
        "/api/mods/add-to-template",
        json={"template_id": t["id"], "mod_ids": ["ABCDEF0123456789"]},
        headers={"X-Client-Id": "me"},
    )
    assert r.status_code == 423


def test_add_mods_to_template_validates_body(logged_in):
    t = logged_in.post("/api/templates", json=_spec("V")).json()
    assert logged_in.post(
        "/api/mods/add-to-template", json={"template_id": t["id"], "mod_ids": []}
    ).status_code == 400
    assert logged_in.post(
        "/api/mods/add-to-template", json={"mod_ids": [RHS]}
    ).status_code == 400
    assert logged_in.post(
        "/api/mods/add-to-template", json={"template_id": 99999, "mod_ids": [RHS]}
    ).status_code == 404
