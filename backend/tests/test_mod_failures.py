"""The mods that stopped a server's last start, from its console.log (#270)."""
import json
from pathlib import Path

import pytest

import config
from services import instance_service, mod_failures, mod_versions

FIXTURE = Path(__file__).parent / "fixtures" / "console_missing_dependency.log"

SCOPES, NATO, RU, SOUNDS, WSCRIPTS = ("62A668F513428630", "615806DC6C57AF02", "615818DA7C0343FD",
                                      "631C3C1AEE9C90BC", "68F006D910E7546F")
CUSTOMIZE = "6AAAAAAAAAAAAAAA"  # the log names it, but lists no folder for it


def _real_log() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_a_real_failed_start_names_the_mod_and_its_missing_dependency():
    out = mod_failures.parse(_real_log())

    assert out["failed"] is True and out["online"] is False
    assert out["dependencies"] == [
        {"name": "WCS_Scopes", "modId": SCOPES, "missing": [NATO]},
        {"name": "EscapistsWCSFORTEXCustomize", "modId": None, "missing": [SCOPES]},
    ]
    # Only the warnings of the base game's compile: no script errors.
    assert out["script_errors"] == []
    assert "ENGINE    (E): Cannot create game!" in out["fatal"]
    assert any(f.startswith("ENGINE    (E): Addon loading failed") for f in out["fatal"])


def test_the_base_games_noise_is_not_a_failure():
    """The Rally missions' resource errors and the shutdown's leak report are on
    a healthy start too."""
    log = "\n".join(line for line in _real_log().splitlines()
                    if "(E)" not in line or "RESOURCES" in line)
    assert mod_failures.parse(log)["failed"] is False


def test_a_server_that_came_online_is_never_failed():
    """A mod may log errors and the server still be playable: that is not ours."""
    log = "\n".join([
        "10:00:00.000 ENGINE       : Addon dirs:",
        "10:00:01.000  SCRIPT       : SCRIPT       : Compiling Game scripts",
        "10:00:02.000  SCRIPT       : Module: Game; loaded 5722x files",
        "10:01:00.000 SCRIPT    (E): Virtual Machine Exception",
        "10:01:00.000 ENGINE    (E): Addon loading failed {X}",
        "10:02:00.000 DEFAULT      : Entered online game state.",
    ])
    out = mod_failures.parse(log)
    assert out["failed"] is False and out["online"] is True


def test_script_errors_come_from_the_compile_only():
    # The shape the wiki and forum describe; not yet seen in a real log (#270).
    log = "\n".join([
        "10:00:01.000  SCRIPT       : SCRIPT       : Compiling Game scripts",
        '10:00:01.500  SCRIPT    (W): @"scripts/Game/A.c,1": \'x\' is obsolete',
        '10:00:01.600  SCRIPT    (E): @"scripts/Game/ADM_Wing.c,12": Can\'t find variable \'GraphDialog\'',
        '10:00:01.700 SCRIPT    (E): Can\'t compile "Game" script module!',
        "10:00:02.000 ENGINE       : Game destroyed.",
        "10:00:03.000 SCRIPT    (E): runtime, after the compile",
    ])
    out = mod_failures.parse(log)
    assert out["failed"] is True
    assert out["script_errors"] == [
        'SCRIPT    (E): @"scripts/Game/ADM_Wing.c,12": Can\'t find variable \'GraphDialog\'',
        'SCRIPT    (E): Can\'t compile "Game" script module!',
    ]


def test_addon_names_cover_folder_and_project_file():
    names = mod_failures.addon_names(_real_log())
    assert names["wcsscopes"] == SCOPES
    # TaskForceMattockWeapons_5B3ED33ADA805340/TFMattockWeapons.gproj
    assert names["taskforcemattockweapons"] == names["tfmattockweapons"] == "5B3ED33ADA805340"


def test_failing_mods_list_every_missing_dependency_and_the_knock_on():
    parsed = mod_failures.parse(_real_log())
    on_disk = {SCOPES, SOUNDS, WSCRIPTS}
    listed = {mod_failures.name_key("Escapists WCS FORTEX Customize"): CUSTOMIZE}
    disk = {SCOPES: [NATO, RU, SOUNDS, WSCRIPTS]}

    out = mod_failures.failing_mods(parsed, on_disk, listed, disk, {SCOPES: "WCS_Scopes"})

    scopes, customize = out
    assert scopes["modId"] == SCOPES and scopes["knock_on"] is False
    assert [(n["modId"], n["state"]) for n in scopes["needs"]] == [
        (NATO, "not_installed"), (RU, "not_installed")]
    assert customize["modId"] == CUSTOMIZE and customize["knock_on"] is True
    assert customize["needs"] == [{"modId": SCOPES, "name": "WCS_Scopes", "state": "failed"}]


def test_a_project_file_not_called_addon_is_read(tmp_path):
    folder = tmp_path / "TaskForceMattockWeapons_5B3ED33ADA805340"
    folder.mkdir()
    (folder / "TFMattockWeapons.gproj").write_text(
        'GameProject {\n Dependencies {\n  "58D0FB3206B6F859" "5614BBCCBB55ED1C"\n }\n}\n')
    assert mod_versions.gproj_dependencies(folder) == ["58D0FB3206B6F859", "5614BBCCBB55ED1C"]


@pytest.fixture
def server_dir(tmp_path, monkeypatch, logged_in):
    spec = {"name": "t", "scenario_id": "{ABC}Missions/x.conf"}
    tid = logged_in.post("/api/templates", json=spec).json()["id"]
    iid = logged_in.post("/api/instances", json={"name": "srv", "template_id": tid}).json()["id"]
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    return iid, tmp_path / "instances" / str(iid)


def test_the_server_view_joins_log_disk_config_and_workshop(server_dir, monkeypatch):
    iid, idir = server_dir
    session = idir / "profile" / "logs" / "logs_2026-11-08_03-30-12"
    session.mkdir(parents=True)
    (session / "console.log").write_bytes("﻿".encode() + _real_log().encode())
    scopes = idir / "workshop" / "addons" / f"WCS_Scopes_{SCOPES}"
    scopes.mkdir(parents=True)
    (scopes / "addon.gproj").write_text(f'Dependencies {{ "{NATO}" "{RU}" "{SOUNDS}" }}')
    (idir / "workshop" / "addons" / f"WCS_Sounds_{SOUNDS}").mkdir()
    (idir / "configs").mkdir(parents=True)
    (idir / "configs" / instance_service.CONFIG_FILENAME).write_text(json.dumps({"game": {"mods": [
        {"modId": SCOPES, "name": "WCS Scopes"},
        {"modId": CUSTOMIZE, "name": "Escapists WCS FORTEX Customize"},
    ]}}))
    asked = []

    def fake_asset(mid):
        asked.append(mid)
        return {"name": {NATO: "WCS_NATO", RU: "WCS_RU"}[mid]}

    monkeypatch.setattr(instance_service.workshop, "get_asset", fake_asset)

    out = instance_service.mod_failures(iid)

    assert out["failed"] is True and out["log"] == "logs/logs_2026-11-08_03-30-12/console.log"
    first, second = out["mods"]
    assert first["name"] == "WCS Scopes" and first["in_template"] is True
    assert [(n["name"], n["state"], n["in_template"]) for n in first["needs"]] == [
        ("WCS_NATO", "not_installed", False), ("WCS_RU", "not_installed", False)]
    assert second["modId"] == CUSTOMIZE and second["knock_on"] is True
    assert sorted(asked) == sorted([NATO, RU])


def test_a_healthy_last_start_reports_nothing(server_dir):
    iid, idir = server_dir
    session = idir / "profile" / "logs" / "logs_2026-11-08_04-00-00"
    session.mkdir(parents=True)
    (session / "console.log").write_text("Addon dirs:\nEntered online game state.\n")
    out = instance_service.mod_failures(iid)
    assert out["available"] is True and out["failed"] is False and out["mods"] == []


def test_no_log_yet(server_dir):
    iid, _idir = server_dir
    assert instance_service.mod_failures(iid) == {"available": False, "failed": False}


def test_the_endpoint(logged_in, monkeypatch):
    monkeypatch.setattr(instance_service, "mod_failures", lambda i: {"available": False, "failed": False})
    assert logged_in.get("/api/instances/1/mod-failures").json() == {"available": False, "failed": False}
