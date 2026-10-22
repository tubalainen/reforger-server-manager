"""Start after a template switch offers to clear the old template's data."""
import sqlite3
import tempfile
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

import models
from models import Instance
from services import instance_backup, instance_service


def _template(logged_in, name):
    spec = {"name": name, "scenario_id": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf"}
    r = logged_in.post("/api/templates", json=spec)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _server(logged_in, tid):
    r = logged_in.post("/api/instances", json={"name": f"srv-{tid}", "template_id": tid})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set(iid, **fields):
    with Session(models.get_engine()) as session:
        inst = session.get(Instance, iid)
        for k, v in fields.items():
            setattr(inst, k, v)
        session.add(inst)
        session.commit()


def test_the_view_says_when_the_template_changed_since_the_last_start(logged_in):
    a, b = _template(logged_in, "Alpha"), _template(logged_in, "Bravo")
    iid = _server(logged_in, a)

    # never started: nothing on disk came from any template yet
    assert logged_in.get(f"/api/instances/{iid}").json()["template_switched"] is False

    _set(iid, last_started_template_id=a)
    assert logged_in.get(f"/api/instances/{iid}").json()["template_switched"] is False

    _set(iid, template_id=b)  # what Settings › Template does
    view = logged_in.get(f"/api/instances/{iid}").json()
    assert view["template_switched"] is True
    assert view["previous_template_name"] == "Alpha"
    # the Servers list reads the summary, which says the same
    row = next(s for s in logged_in.get("/api/instances/summary").json()["servers"] if s["id"] == iid)
    assert (row["template_switched"], row["previous_template_name"]) == (True, "Alpha")

    _set(iid, template_id=a)  # back to the one its data came from
    assert logged_in.get(f"/api/instances/{iid}").json()["template_switched"] is False


def test_start_clears_what_was_chosen_backing_up_the_save_first(logged_in, monkeypatch):
    iid = _server(logged_in, _template(logged_in, "Alpha"))
    calls = []
    monkeypatch.setattr(instance_backup, "create_backup",
                        lambda i, label, source: calls.append(("backup", source)) or {"id": "b1"})
    monkeypatch.setattr(instance_service, "clear_instance_data",
                        lambda i, targets: calls.append(("clear", list(targets))))
    monkeypatch.setattr(instance_service, "start_instance", lambda i: calls.append(("start",)))

    r = logged_in.post(f"/api/instances/{iid}/start",
                       json={"clear": ["mods", "saves", "logs"], "backup_first": True})

    assert r.status_code == 200, r.text
    assert calls == [("backup", instance_backup.SOURCE_TEMPLATE_SWITCH),
                     ("clear", ["mods", "saves", "logs"]), ("start",)]
    assert r.json()["backup"] == {"id": "b1"}


def test_a_plain_start_clears_nothing(logged_in, monkeypatch):
    iid = _server(logged_in, _template(logged_in, "Alpha"))
    calls = []
    monkeypatch.setattr(instance_service, "clear_instance_data",
                        lambda i, targets: calls.append("clear"))
    monkeypatch.setattr(instance_service, "start_instance", lambda i: calls.append("start"))

    assert logged_in.post(f"/api/instances/{iid}/start").status_code == 200
    # no backup without saves to clear, even when asked
    assert logged_in.post(f"/api/instances/{iid}/start",
                          json={"clear": ["mods"], "backup_first": True}).status_code == 200
    assert calls == ["start", "clear", "start"]
    assert logged_in.post(f"/api/instances/{iid}/start",
                          json={"clear": ["../etc"]}).status_code == 422


def test_upgrading_takes_the_current_template_as_the_last_started_one():
    db = Path(tempfile.mkdtemp(prefix="rsm-mig-")) / "manager.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE template (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
            description VARCHAR NOT NULL DEFAULT '', config_json VARCHAR NOT NULL DEFAULT '{}',
            created_at DATETIME, updated_at DATETIME);
        CREATE TABLE instance (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
            template_id INTEGER, branch VARCHAR, game_port INTEGER, a2s_port INTEGER,
            rcon_port INTEGER, desired_state VARCHAR, auto_restart BOOLEAN, created_at DATETIME);
        INSERT INTO instance (name, template_id, branch, game_port, a2s_port, rcon_port,
            desired_state, auto_restart, created_at)
        VALUES ('one', 7, 'stable', 2001, 17777, 19999, 'stopped', 1, '2026-10-01 10:00:00');
    """)
    con.commit()
    con.close()
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    models._migrate(engine)
    models._migrate(engine)
    with Session(engine) as s:
        inst = s.exec(select(models.Instance)).one()
    # nobody is asked until they actually switch
    assert inst.last_started_template_id == 7
