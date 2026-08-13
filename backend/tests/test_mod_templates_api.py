"""Mod templates: reusable named mod lists (#166)."""
A = "AAAAAAAAAAAAAAAA"
B = "BBBBBBBBBBBBBBBB"
C = "CCCCCCCCCCCCCCCC"


def _spec(name="Milsim pack", mods=None, description="our usual set"):
    return {
        "name": name,
        "description": description,
        "mods": mods if mods is not None else [
            {"modId": A, "name": "Alpha"},
            {"modId": B, "name": "Bravo", "version": "1.2.3"},
        ],
    }


def test_mod_templates_require_auth(client):
    assert client.get("/api/mod-templates").status_code == 401
    assert client.post("/api/mod-templates", json=_spec()).status_code == 401


def test_mod_template_crud(logged_in):
    created = logged_in.post("/api/mod-templates", json=_spec())
    assert created.status_code == 201
    mid = created.json()["id"]
    assert [m["modId"] for m in created.json()["mods"]] == [A, B]

    listing = logged_in.get("/api/mod-templates").json()
    row = next(t for t in listing if t["id"] == mid)
    assert row["mod_count"] == 2
    assert row["locked_count"] == 1  # only Bravo has a version lock

    # edit: rename, drop one mod, add another
    edited = logged_in.put(
        f"/api/mod-templates/{mid}",
        json=_spec(name="Renamed", mods=[{"modId": B, "name": "Bravo"}, {"modId": C}]),
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "Renamed"
    assert [m["modId"] for m in edited.json()["mods"]] == [B, C]

    assert logged_in.delete(f"/api/mod-templates/{mid}").status_code == 204
    assert logged_in.get(f"/api/mod-templates/{mid}").status_code == 404


def test_duplicate_name_conflict(logged_in):
    logged_in.post("/api/mod-templates", json=_spec("Dupe"))
    assert logged_in.post("/api/mod-templates", json=_spec("Dupe")).status_code == 409
    other = logged_in.post("/api/mod-templates", json=_spec("Other")).json()
    # renaming onto a taken name is refused too, but keeping your own is fine
    assert logged_in.put(
        f"/api/mod-templates/{other['id']}", json=_spec("Dupe")
    ).status_code == 409
    assert logged_in.put(
        f"/api/mod-templates/{other['id']}", json=_spec("Other")
    ).status_code == 200


def test_mods_are_normalised_and_deduplicated(logged_in):
    """Ids arrive as URLs, lower case and repeats; one mod each, order kept."""
    created = logged_in.post("/api/mod-templates", json=_spec(mods=[
        {"modId": f"https://reforger.armaplatform.com/workshop/{A}-Alpha"},
        {"modId": B.lower()},
        {"modId": A},  # a repeat is not a second mod
    ])).json()
    assert [m["modId"] for m in created["mods"]] == [A, B]
    assert all(m["explicit"] for m in created["mods"])


def test_invalid_mod_id_is_rejected(logged_in):
    r = logged_in.post("/api/mod-templates", json=_spec(mods=[{"modId": "not-an-id"}]))
    assert r.status_code == 422


def test_change_log_records_creation_edits_and_reordering(logged_in):
    mid = logged_in.post("/api/mod-templates", json=_spec()).json()["id"]
    log = logged_in.get(f"/api/mod-templates/{mid}/changelog").json()
    # One event, oldest line first within it: the create line leads.
    assert log[0]["summary"] == "Mod template created"
    assert any("Added mod 'Alpha'" in e["summary"] for e in log)

    # rename + remove + add + version change
    logged_in.put(f"/api/mod-templates/{mid}", json=_spec(
        name="Pack v2",
        description="our usual set",
        mods=[{"modId": B, "name": "Bravo"}, {"modId": C, "name": "Charlie"}],
    ))
    summaries = [e["summary"] for e in logged_in.get(
        f"/api/mod-templates/{mid}/changelog"
    ).json()]
    assert "Renamed from 'Milsim pack' to 'Pack v2'" in summaries
    assert any("Removed mod 'Alpha'" in s for s in summaries)
    assert any("Added mod 'Charlie'" in s for s in summaries)
    assert any("version 1.2.3 → latest" in s for s in summaries)

    # a pure reorder is logged as one line, and only mentions what moved
    logged_in.put(f"/api/mod-templates/{mid}", json=_spec(
        name="Pack v2",
        description="our usual set",
        mods=[{"modId": C, "name": "Charlie"}, {"modId": B, "name": "Bravo"}],
    ))
    latest = logged_in.get(f"/api/mod-templates/{mid}/changelog").json()
    assert "Load order changed" in latest[0]["summary"]
    assert "moved" in latest[0]["summary"]

    # a no-op save writes nothing new
    before = len(latest)
    logged_in.put(f"/api/mod-templates/{mid}", json=_spec(
        name="Pack v2",
        description="our usual set",
        mods=[{"modId": C, "name": "Charlie"}, {"modId": B, "name": "Bravo"}],
    ))
    assert len(logged_in.get(f"/api/mod-templates/{mid}/changelog").json()) == before


def test_change_log_is_searchable_and_dies_with_the_mod_template(logged_in):
    mid = logged_in.post("/api/mod-templates", json=_spec()).json()["id"]
    hits = logged_in.get(f"/api/mod-templates/{mid}/changelog", params={"q": "alpha"}).json()
    assert len(hits) == 1 and "Alpha" in hits[0]["summary"]

    logged_in.delete(f"/api/mod-templates/{mid}")
    assert logged_in.get(f"/api/mod-templates/{mid}/changelog").status_code == 404


def test_mods_on_a_mod_template_appear_in_the_overview_and_survive_a_rescan(logged_in):
    """A mod put on a shelf is in use: listed, not orphaned, never pruned (#166)."""
    logged_in.post("/api/mod-templates", json=_spec())
    mods = {m["mod_id"]: m for m in logged_in.get("/api/mods").json()["mods"]}
    assert mods[A]["mod_templates"][0]["name"] == "Milsim pack"
    assert mods[A]["templates"] == []
    assert mods[A]["orphaned"] is False

    assert logged_in.post("/api/mods/rescan").json()["pruned"] == 0
    assert {m["mod_id"] for m in logged_in.get("/api/mods").json()["mods"]} == {A, B}


def test_deleting_a_mod_template_leaves_its_mods_prunable(logged_in):
    mid = logged_in.post("/api/mod-templates", json=_spec()).json()["id"]
    logged_in.delete(f"/api/mod-templates/{mid}")
    overview = logged_in.get("/api/mods").json()["mods"]
    assert all(m["orphaned"] for m in overview)  # kept, but nothing lists them now
    assert logged_in.post("/api/mods/rescan").json()["pruned"] == 2
