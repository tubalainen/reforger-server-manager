"""Mod versions on start: frozen by default, updated when asked (#246)."""
import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

import config
import models
from models import Instance
from services import instance_service, mod_versions
from services.workshop_service import WorkshopError

A = "591AF5BDA9F7CE8B"   # a mod the template lists
B = "69377746E02C0389"   # another one
DEP = "58D0FB3206B6F859"  # what A depends on (not in the template)
OLD = "1111111111111111"  # a leftover from an earlier template
LOCKED = "2222222222222222"


def _mod_dir(root: Path, name: str, mid: str, version: str | None, deps=()) -> Path:
    """A mod folder the way the server leaves it (layout seen on a real host)."""
    folder = root / f"{name}_{mid}"
    folder.mkdir(parents=True)
    if version is not None:
        (folder / "ServerData.json").write_text(json.dumps({
            "id": mid, "name": name,
            "revision": {"version": version, "gameVersion": "", "dependencies": []},
        }))
    dep_lines = "".join(f'  "{d}"\n' for d in deps)
    (folder / "addon.gproj").write_text(
        f'GameProject {{\n ID "{name}"\n GUID "{mid}"\n Dependencies {{\n{dep_lines} }}\n}}'
    )
    (folder / "data.pak").write_bytes(b"x" * 100)
    return folder


def _cfg(*mods):
    return {"game": {"mods": [dict(m) for m in mods]}}


# --------------------------------------------------------------------------- #
# Reading the disk
# --------------------------------------------------------------------------- #

def test_installed_versions_come_from_server_data(tmp_path):
    root = tmp_path / "workshop" / "addons"
    _mod_dir(root, "Alpha", A, "1.0.12")
    _mod_dir(root, "Name_With_Underscores", B, "2.3")
    _mod_dir(root, "Broken", OLD, None)  # no ServerData.json: no version known
    (root / "saves").mkdir()             # not a mod folder

    assert mod_versions.installed_versions([root]) == {A: "1.0.12", B: "2.3"}


def test_a_byte_order_mark_does_not_hide_the_version(tmp_path):
    folder = _mod_dir(tmp_path, "Alpha", A, None)
    (folder / "ServerData.json").write_bytes(
        "﻿".encode() + json.dumps({"id": A, "revision": {"version": "1.0.12"}}).encode()
    )
    assert mod_versions.installed_versions([tmp_path]) == {A: "1.0.12"}


def test_a_broken_server_data_falls_back_to_its_text_then_the_manifest_names(tmp_path):
    text = _mod_dir(tmp_path, "Alpha", A, None)
    (text / "ServerData.json").write_text('{"id": "x", "revision": {"version": "2.1", }, }')
    manifest = _mod_dir(tmp_path, "Beta", B, None)
    (manifest / "data.pak_1.0.12_manifest.json").write_text("{}")
    assert mod_versions.installed_versions([tmp_path]) == {A: "2.1", B: "1.0.12"}


def test_a_downloaded_mod_without_a_readable_version_says_so(tmp_path, monkeypatch):
    _mod_dir(tmp_path, "Alpha", A, None)  # folder, but nothing names a version
    monkeypatch.setattr(mod_versions, "latest_versions", lambda ids: {A: "1.0"})
    out = mod_versions.status(_cfg({"modId": A}, {"modId": B}), [tmp_path])
    assert [m["state"] for m in out["mods"]] == ["unreadable", "missing"]


def test_installed_versions_survive_a_missing_addons_dir(tmp_path):
    assert mod_versions.installed_versions([tmp_path / "nope"]) == {}


def test_dependencies_are_followed_through_addon_gproj(tmp_path):
    root = tmp_path / "addons"
    _mod_dir(root, "Alpha", A, "1", deps=[DEP])
    _mod_dir(root, "Dep", DEP, "1")
    _mod_dir(root, "Old", OLD, "1")

    assert mod_versions.with_installed_dependencies([A], [root]) == {A, DEP}


# --------------------------------------------------------------------------- #
# Pinning
# --------------------------------------------------------------------------- #

def test_freeze_pins_what_is_on_disk_and_leaves_locks_and_new_mods_alone():
    cfg = _cfg({"modId": A, "name": "A"}, {"modId": LOCKED, "version": "0.9"},
               {"modId": B, "name": "B"})
    report = mod_versions.pin_versions(cfg, mod_versions.MODE_FREEZE, {A: "1.0", LOCKED: "0.8"})

    mods = {m["modId"]: m for m in cfg["game"]["mods"]}
    assert mods[A]["version"] == "1.0"          # frozen
    assert mods[LOCKED]["version"] == "0.9"     # the template's lock wins over the disk
    assert "version" not in mods[B]             # not downloaded yet: the server takes the latest
    assert report["frozen"] == [A] and report["locked"] == [LOCKED] and report["unpinned"] == [B]


def test_update_pins_the_workshop_release():
    cfg = _cfg({"modId": A}, {"modId": B}, {"modId": LOCKED, "version": "0.9"})
    report = mod_versions.pin_versions(
        cfg, mod_versions.MODE_UPDATE, {A: "1.0", B: "2.0"}, {A: "1.1", B: "2.0", LOCKED: "5.0"},
    )

    mods = {m["modId"]: m for m in cfg["game"]["mods"]}
    assert mods[A]["version"] == "1.1"
    assert mods[B]["version"] == "2.0"
    assert mods[LOCKED]["version"] == "0.9"
    assert report["updated"] == [(A, "1.0", "1.1")]
    assert "1 updated" in mod_versions.summarise(report)


def test_update_keeps_the_disk_version_when_the_workshop_does_not_answer():
    # Better a known version than one the server picks on its own.
    cfg = _cfg({"modId": A}, {"modId": B})
    mod_versions.pin_versions(cfg, mod_versions.MODE_UPDATE, {A: "1.0"}, {A: None, B: None})
    mods = {m["modId"]: m for m in cfg["game"]["mods"]}
    assert mods[A]["version"] == "1.0"
    assert "version" not in mods[B]


def test_latest_versions_reads_the_current_release(monkeypatch):
    def fake_asset(mid):
        if mid == B:
            raise WorkshopError("down")
        return {"version": None, "versions": ["3.0", "2.0"]} if mid == DEP else {"version": "1.1"}

    monkeypatch.setattr(mod_versions.workshop, "get_asset", fake_asset)
    assert mod_versions.latest_versions([A, B, DEP]) == {A: "1.1", B: None, DEP: "3.0"}


def test_status_names_the_state_of_each_mod(tmp_path, monkeypatch):
    root = tmp_path / "addons"
    _mod_dir(root, "A", A, "1.0")
    _mod_dir(root, "B", B, "2.0")
    _mod_dir(root, "D", DEP, "1.0")
    monkeypatch.setattr(mod_versions, "latest_versions",
                        lambda ids: {A: "1.1", B: "2.0", DEP: None, LOCKED: "9"})
    cfg = _cfg({"modId": A, "name": "Alpha"}, {"modId": B}, {"modId": DEP},
               {"modId": LOCKED, "version": "1"}, {"modId": OLD})

    out = mod_versions.status(cfg, [root])

    states = {m["modId"]: m["state"] for m in out["mods"]}
    assert states == {A: "behind", B: "current", DEP: "unknown", LOCKED: "missing", OLD: "missing"}
    assert out["updates_available"] == 1
    assert out["all_locked"] is False
    assert out["mods"][0]["name"] == "Alpha"


def test_status_says_when_every_mod_is_locked(tmp_path, monkeypatch):
    root = tmp_path / "addons"
    _mod_dir(root, "L", LOCKED, "1")
    monkeypatch.setattr(mod_versions, "latest_versions", lambda ids: {})
    out = mod_versions.status(_cfg({"modId": LOCKED, "version": "1"}), [root])
    assert out["mods"][0]["state"] == "locked" and out["mods"][0]["installed"] == "1"
    assert out["all_locked"] is True


def test_mods_directly_in_the_workshop_folder_are_found(tmp_path):
    # Not the layout seen on a real host (workshop/addons/...), but one the
    # addons dir setting allows; a version must not go missing because of it.
    idir = tmp_path / "instances" / "1"
    _mod_dir(idir / "workshop", "A", A, "1.0")
    assert mod_versions.installed_versions(mod_versions.addon_roots(idir)) == {A: "1.0"}


# --------------------------------------------------------------------------- #
# Starting a server
# --------------------------------------------------------------------------- #

def _inst(**over):
    base = dict(id=1, name="srv", template_id=1, branch="stable",
                game_port=2005, a2s_port=17780, rcon_port=20002)
    base.update(over)
    return Instance(**base)


def _template_json():
    return json.dumps({"game": {"mods": [
        {"modId": A, "name": "A"}, {"modId": B, "name": "B"},
        {"modId": LOCKED, "name": "L", "version": "0.9"},
    ]}})


def _written(tmp_path):
    cfg = json.loads((tmp_path / "instances" / "1" / "configs" / instance_service.CONFIG_FILENAME)
                     .read_text())
    return {m["modId"]: m.get("version") for m in cfg["game"]["mods"]}


@pytest.fixture
def instance_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    root = tmp_path / "instances" / "1" / "workshop" / "addons"
    _mod_dir(root, "A", A, "1.0", deps=[DEP])
    _mod_dir(root, "B", B, "2.0")
    _mod_dir(root, "Dep", DEP, "1.0")
    _mod_dir(root, "Old", OLD, "1.0")
    return tmp_path


def test_an_ordinary_start_freezes_the_mods_and_asks_the_workshop_nothing(instance_dir, monkeypatch):
    def no_network(ids):
        raise AssertionError("a freezing start must not look anything up")

    monkeypatch.setattr(mod_versions, "latest_versions", no_network)
    instance_service._write_config(_inst(), _template_json())
    assert _written(instance_dir) == {A: "1.0", B: "2.0", LOCKED: "0.9"}


def test_an_updating_start_pins_the_newest_releases(instance_dir, monkeypatch):
    asked = []

    def latest(ids):
        asked.append(list(ids))
        return {A: "1.1", B: "2.0"}

    monkeypatch.setattr(mod_versions, "latest_versions", latest)
    instance_service._write_config(_inst(), _template_json(), mod_versions.MODE_UPDATE)
    assert _written(instance_dir) == {A: "1.1", B: "2.0", LOCKED: "0.9"}
    assert asked == [[A, B]]  # a locked mod is not looked up


def _with_template(monkeypatch):
    """Point every server at the test template without a Template row."""
    target = SimpleNamespace(content=SimpleNamespace(config_json=_template_json()), label="T")
    monkeypatch.setattr(instance_service, "_run_target", lambda session, inst: target)


def _add_instance(**over):
    with Session(models.get_engine()) as session:
        session.add(_inst(**over))
        session.commit()


def test_force_update_restarts_a_running_server_with_updates(monkeypatch):
    calls = []
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "running")
    monkeypatch.setattr(instance_service, "restart_instance",
                        lambda i, update_mods=False: calls.append(("restart", update_mods)))
    monkeypatch.setattr(instance_service, "start_instance",
                        lambda i, update_mods=False: calls.append(("start", update_mods)))
    instance_service.force_update_mods(1)
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "exited")
    instance_service.force_update_mods(1)
    assert calls == [("restart", True), ("start", True)]


def test_redownload_deletes_only_the_templates_mods_and_their_dependencies(instance_dir, monkeypatch):
    _add_instance()
    _with_template(monkeypatch)
    idir = instance_dir / "instances" / "1"
    removed, started = [], []
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "running")
    monkeypatch.setattr(instance_service, "stop_instance", lambda i: started.append("stop"))
    monkeypatch.setattr(instance_service, "start_instance",
                        lambda i, update_mods=False: started.append(("start", update_mods)))

    def fake_remove(_id, root, victims):
        removed.extend(sorted(p.relative_to(root).as_posix() for p in victims))

    monkeypatch.setattr(instance_service, "_remove_in_container", fake_remove)

    out = instance_service.redownload_mods(1)

    assert removed == [f"workshop/addons/A_{A}", f"workshop/addons/B_{B}",
                       f"workshop/addons/Dep_{DEP}"]
    assert (idir / "workshop" / "addons" / f"Old_{OLD}").exists()  # never looked at
    assert started == ["stop", ("start", True)]
    assert out["removed_mods"] == 3 and out["size_bytes"] > 0


def test_mod_status_reports_the_setting_and_the_schedule(instance_dir, monkeypatch):
    _add_instance(mod_auto_update=True, restart_schedule_json='{"times": ["04:00"]}')
    _with_template(monkeypatch)
    monkeypatch.setattr(mod_versions, "latest_versions", lambda ids: {A: "1.1", B: "2.0"})

    out = instance_service.mod_status(1)

    assert out["auto_update"] is True and out["scheduled"] is True
    assert out["updates_available"] == 1
    # A, B and A's dependency, not the leftover from an older template
    assert out["redownload"]["folders"] == 3 and out["redownload"]["size_bytes"] > 300


def test_set_mod_auto_update_is_stored_and_shown(monkeypatch):
    _add_instance()
    instance_service.set_mod_auto_update(1, True)
    with Session(models.get_engine()) as session:
        assert session.get(Instance, 1).mod_auto_update is True


@pytest.mark.parametrize("enabled", [True, False])
def test_the_scheduled_restart_updates_mods_only_when_enabled(monkeypatch, enabled):
    from datetime import datetime, timedelta

    _add_instance(desired_state="running", mod_auto_update=enabled,
                  restart_schedule_json='{"times": ["04:00"]}',
                  last_scheduled_restart=datetime.now() - timedelta(days=2))
    calls = []
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service, "_due_scheduled_restart",
                        lambda *a: datetime.now().replace(hour=4, minute=0))
    monkeypatch.setattr(instance_service, "restart_instance",
                        lambda i, update_mods=False: calls.append((i, update_mods)))

    instance_service.apply_scheduled_restarts()

    assert calls == [(1, enabled)]


def test_a_stopped_server_skips_its_restart_and_is_not_restarted_later(monkeypatch):
    from datetime import datetime, timedelta

    _add_instance(desired_state="stopped", mod_auto_update=True,
                  restart_schedule_json='{"times": ["04:00"]}',
                  last_scheduled_restart=datetime.now() - timedelta(days=2))
    calls = []
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service, "restart_instance",
                        lambda i, update_mods=False: calls.append(i))

    # 04:00 passes while it is stopped: nothing is restarted...
    monkeypatch.setattr(instance_service, "datetime",
                        type("D", (datetime,), {"now": staticmethod(
                            lambda: datetime.now().replace(hour=4, minute=0, second=10))}))
    instance_service.apply_scheduled_restarts()
    assert calls == []
    # ...and starting it at 04:30 does not get it restarted for the missed 04:00.
    with Session(models.get_engine()) as session:
        inst = session.get(Instance, 1)
        inst.desired_state = "running"
        session.add(inst)
        session.commit()
    monkeypatch.setattr(instance_service, "datetime",
                        type("D", (datetime,), {"now": staticmethod(
                            lambda: datetime.now().replace(hour=4, minute=30, second=0))}))
    instance_service.apply_scheduled_restarts()
    assert calls == []


def test_every_other_restart_keeps_the_mods(monkeypatch):
    calls = []
    monkeypatch.setattr(instance_service, "stop_instance", lambda i: None)
    monkeypatch.setattr(instance_service, "start_instance",
                        lambda i, update_mods=False: calls.append(update_mods))
    instance_service.restart_instance(1)
    assert calls == [False]


# --------------------------------------------------------------------------- #
# Upgrading an existing database
# --------------------------------------------------------------------------- #

_OLD_SCHEMA = """
CREATE TABLE template (
    id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
    description VARCHAR NOT NULL DEFAULT '', config_json VARCHAR NOT NULL DEFAULT '{}',
    created_at DATETIME, updated_at DATETIME
);
CREATE TABLE instance (
    id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, template_id INTEGER,
    branch VARCHAR, game_port INTEGER, a2s_port INTEGER, rcon_port INTEGER,
    desired_state VARCHAR, auto_restart BOOLEAN, auto_start BOOLEAN,
    restart_schedule_json VARCHAR NOT NULL DEFAULT '', last_scheduled_restart DATETIME,
    created_at DATETIME
);
INSERT INTO instance (name, template_id, branch, game_port, a2s_port, rcon_port,
    desired_state, auto_restart, auto_start, restart_schedule_json, created_at)
VALUES ('nightly', 1, 'stable', 2001, 17777, 19999, 'running', 1, 1,
        '{"times": ["04:00"]}', '2026-10-01 10:00:00'),
       ('adhoc', 1, 'stable', 2002, 17778, 19998, 'running', 1, 1, '',
        '2026-10-01 10:00:00');
"""


def test_upgrading_ticks_the_box_for_servers_that_already_restart_daily():
    db = Path(tempfile.mkdtemp(prefix="rsm-mig-")) / "manager.db"
    con = sqlite3.connect(db)
    con.executescript(_OLD_SCHEMA)
    con.commit()
    con.close()
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})

    SQLModel.metadata.create_all(engine)
    models._migrate(engine)
    models._migrate(engine)  # a second startup changes nothing

    with Session(engine) as s:
        rows = {i.name: i.mod_auto_update for i in s.exec(select(models.Instance)).all()}
    assert rows == {"nightly": True, "adhoc": False}
    with engine.connect() as conn:
        assert "mod_auto_update" in {r[1] for r in conn.execute(text("PRAGMA table_info(instance)"))}
