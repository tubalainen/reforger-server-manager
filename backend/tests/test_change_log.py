"""Per-template change log (#112)."""
from services import change_log


def _snap(name="T", description="", scenario_name="", **game):
    mods = game.pop("mods", [])
    scenario_id = game.pop("scenario_id", "")
    cfg = {"game": {"scenarioId": scenario_id, "mods": mods, **game}, "operating": {}}
    return {"name": name, "description": description,
            "scenario_name": scenario_name, "config": cfg}


def test_diff_reports_rename_and_description():
    old = _snap(name="Old", description="a")
    new = _snap(name="New", description="b")
    lines = [s for _, s in change_log.diff(old, new)]
    assert "Renamed from 'Old' to 'New'" in lines
    assert "Description updated" in lines


def test_diff_reports_mod_add_remove_and_version():
    old = _snap(mods=[{"modId": "AAA", "name": "Keep", "version": "1.0"},
                      {"modId": "BBB", "name": "Gone"}])
    new = _snap(mods=[{"modId": "AAA", "name": "Keep", "version": "2.0"},
                      {"modId": "CCC", "name": "Fresh"}])
    cats = change_log.diff(old, new)
    lines = [s for _, s in cats]
    assert "Added mod 'Fresh' (CCC)" in lines
    assert "Removed mod 'Gone' (BBB)" in lines
    assert "Mod 'Keep' (AAA) version 1.0 → 2.0" in lines


def test_diff_reports_scenario_change_and_clear():
    changed = change_log.diff(
        _snap(scenario_id="{A}x.conf"),
        _snap(scenario_id="{B}y.conf", scenario_name="Everon Conflict"),
    )
    assert ("scenario", "Scenario changed to Everon Conflict") in changed
    cleared = change_log.diff(_snap(scenario_id="{A}x.conf"), _snap(scenario_id=""))
    assert ("scenario", "Scenario cleared") in cleared


def test_diff_reports_setting_change_with_path_and_values():
    old = _snap(maxPlayers=64)
    new = _snap(maxPlayers=32)
    assert ("setting", "game.maxPlayers: 64 → 32") in change_log.diff(old, new)


def test_secret_values_are_never_written_to_the_log():
    old = _snap(password="old-secret")
    new = _snap(password="new-secret")
    lines = [s for _, s in change_log.diff(old, new)]
    assert "game.password changed" in lines
    assert "old-secret" not in " ".join(lines)
    assert "new-secret" not in " ".join(lines)


def test_no_change_yields_no_lines():
    snap = _snap(name="Same", maxPlayers=10, mods=[{"modId": "AAA", "name": "M"}])
    assert change_log.diff(snap, snap) == []


# --- end-to-end through the API ------------------------------------------- #

def _spec(name="Logged", **over):
    base = {
        "name": name,
        "description": "d",
        "scenario_id": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        "mods": [{"modId": "591AF5BDA9F7CE8B", "name": "RHS", "version": "1.0"}],
        "max_players": 32,
    }
    base.update(over)
    return base


def test_changelog_starts_at_creation(logged_in):
    tid = logged_in.post("/api/templates", json=_spec()).json()["id"]
    log = logged_in.get(f"/api/templates/{tid}/changelog").json()
    summaries = [e["summary"] for e in log]
    assert "Template created" in summaries
    assert any("Added mod 'RHS'" in s for s in summaries)


def test_changelog_records_edits_newest_first(logged_in):
    tid = logged_in.post("/api/templates", json=_spec()).json()["id"]
    logged_in.put(f"/api/templates/{tid}", json=_spec() | {"max_players": 100})
    log = logged_in.get(f"/api/templates/{tid}/changelog").json()
    summaries = [e["summary"] for e in log]
    assert "game.maxPlayers: 32 → 100" in summaries
    # the edit is newer than creation, so it sorts above "Template created"
    assert summaries.index("game.maxPlayers: 32 → 100") < summaries.index("Template created")


def test_changelog_is_searchable(logged_in):
    tid = logged_in.post("/api/templates", json=_spec()).json()["id"]
    logged_in.put(f"/api/templates/{tid}", json=_spec() | {"max_players": 100})
    hits = logged_in.get(f"/api/templates/{tid}/changelog", params={"q": "maxplayers"}).json()
    assert hits and all("maxPlayers" in e["summary"] for e in hits)


def test_changelog_dies_with_the_template(logged_in):
    tid = logged_in.post("/api/templates", json=_spec("Doomed")).json()["id"]
    assert logged_in.get(f"/api/templates/{tid}/changelog").json()  # has entries
    logged_in.delete(f"/api/templates/{tid}")
    # a new template reusing the id-space must not inherit the old log
    assert logged_in.get(f"/api/templates/{tid}/changelog").status_code == 404


def test_changelog_has_no_mutation_endpoint(logged_in):
    tid = logged_in.post("/api/templates", json=_spec()).json()["id"]
    # the log is read-only: there is no way to edit or delete a line
    assert logged_in.post(f"/api/templates/{tid}/changelog").status_code == 405
    assert logged_in.delete(f"/api/templates/{tid}/changelog").status_code == 405


def test_changelog_requires_auth(client):
    assert client.get("/api/templates/1/changelog").status_code == 401
