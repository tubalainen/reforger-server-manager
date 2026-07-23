"""Unit tests for the mod-registry service (#131)."""
import json

from sqlmodel import Session, select

import models
from models import ModRegistryEntry, Template
from services import mod_registry


def _template(mods_json="", config_mods=None, name="T"):
    config = {"game": {"mods": config_mods or []}}
    return Template(
        name=name,
        config_json=json.dumps(config),
        mods_json=mods_json,
    )


def test_enriched_mods_prefers_mods_json_then_falls_back_to_config():
    enriched = _template(
        mods_json=json.dumps([{"modId": "AAAAAAAAAAAAAAAA", "name": "A", "version": "2"}]),
        config_mods=[{"modId": "BBBBBBBBBBBBBBBB", "name": "B"}],
    )
    got = mod_registry._enriched_mods(enriched)
    assert [m["modId"] for m in got] == ["AAAAAAAAAAAAAAAA"]

    flat = _template(mods_json="", config_mods=[{"modId": "BBBBBBBBBBBBBBBB", "name": "B"}])
    assert [m["modId"] for m in mod_registry._enriched_mods(flat)] == ["BBBBBBBBBBBBBBBB"]


def test_register_and_backfill_are_idempotent():
    with Session(models.get_engine()) as s:
        s.add(_template(
            mods_json=json.dumps([{"modId": "aaaaaaaaaaaaaaaa", "name": "A"}]), name="T1"
        ))
        s.commit()
        first = mod_registry.backfill_from_templates(s)
        second = mod_registry.backfill_from_templates(s)
        assert first == 1
        assert second == 0
        rows = s.exec(select(ModRegistryEntry)).all()
        # Lower-case id in the template normalises to upper-case in the registry.
        assert [r.mod_id for r in rows] == ["AAAAAAAAAAAAAAAA"]


def test_delete_refuses_persisted():
    with Session(models.get_engine()) as s:
        s.add(ModRegistryEntry(mod_id="AAAAAAAAAAAAAAAA", name="A", persist=True))
        s.commit()
        try:
            mod_registry.delete(s, "AAAAAAAAAAAAAAAA")
            raise AssertionError("expected PermissionError")
        except PermissionError:
            pass


def test_resolve_tree_merges_roots_and_reports_missing(monkeypatch):
    graph = {
        "AAAAAAAAAAAAAAAA": {
            "mods": [
                {"modId": "AAAAAAAAAAAAAAAA", "name": "A", "kind": "scenario",
                 "tags": ["Conflict"], "dependencies": ["CCCCCCCCCCCCCCCC"]},
                {"modId": "CCCCCCCCCCCCCCCC", "name": "Core", "kind": "addon",
                 "tags": [], "dependencies": []},
            ],
            "missing": [],
        },
        "BBBBBBBBBBBBBBBB": {
            "mods": [
                {"modId": "BBBBBBBBBBBBBBBB", "name": "B", "kind": "terrain",
                 "tags": [], "dependencies": ["CCCCCCCCCCCCCCCC"]},
            ],
            "missing": ["DDDDDDDDDDDDDDDD"],
        },
    }
    monkeypatch.setattr(mod_registry.workshop, "resolve_dependencies", lambda mid: graph[mid])
    tree = mod_registry.resolve_tree(["AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB"])
    assert tree["edges"]["AAAAAAAAAAAAAAAA"] == ["CCCCCCCCCCCCCCCC"]
    assert tree["edges"]["BBBBBBBBBBBBBBBB"] == ["CCCCCCCCCCCCCCCC"]
    assert tree["names"]["CCCCCCCCCCCCCCCC"] == "Core"
    # Workshop type/tags ride along per mod (#131).
    assert tree["types"]["AAAAAAAAAAAAAAAA"] == {"kind": "scenario", "tags": ["Conflict"]}
    assert tree["types"]["BBBBBBBBBBBBBBBB"]["kind"] == "terrain"
    assert tree["missing"] == ["DDDDDDDDDDDDDDDD"]
    assert tree["resolved"] is True
