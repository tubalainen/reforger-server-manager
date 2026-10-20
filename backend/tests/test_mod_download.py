"""Mod download progress for a starting server (#235)."""
import json

import pytest

from models import Instance, Template
from services import instance_service, mod_download
from services import workshop_service as ws

A, B, C = "AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB", "CCCCCCCCCCCCCCCC"


@pytest.fixture(autouse=True)
def _fresh():
    mod_download._reset()
    yield
    mod_download._reset()


def _config(*ids):
    return json.dumps({"game": {"mods": [{"modId": i, "name": i} for i in ids]}})


def _tree(sizes, edges):
    """A fake resolve_dependencies over a {modId: size} / {root: [deps]} graph."""
    def resolve(mid):
        if mid not in sizes:
            raise ws.WorkshopError("boom")
        ids = [mid, *edges.get(mid, [])]
        return {"mods": [{"modId": i, "size": sizes[i]} for i in ids], "missing": []}
    return resolve


def test_mod_ids_read_the_config_in_order_once_each():
    cfg = json.dumps({"game": {"mods": [
        {"modId": B}, {"modId": A}, {"modId": B}, {"name": "no id"}, "junk",
    ]}})
    assert mod_download.mod_ids(cfg) == [B, A]
    assert mod_download.mod_ids("not json") == []
    assert mod_download.mod_ids(None) == []


def test_a_dependency_shared_by_two_mods_is_counted_once(monkeypatch):
    monkeypatch.setattr(ws.workshop, "resolve_dependencies",
                        _tree({A: 10, B: 5, C: 7}, {A: [C], B: [C]}))
    result = mod_download._resolve((A, B))
    assert result == {"state": "ready", "bytes": 22, "mods": 3, "missing": 0,
                      "ids": frozenset({A, B, C})}


def test_a_mod_the_workshop_cannot_find_is_left_out_and_counted(monkeypatch):
    monkeypatch.setattr(ws.workshop, "resolve_dependencies", _tree({A: 10}, {}))
    result = mod_download._resolve((A, B))
    assert result["state"] == "ready"
    assert result["bytes"] == 10
    assert result["missing"] == 1
    # The server still downloads it, so its folder still counts.
    assert result["ids"] == frozenset({A, B})


def test_no_workshop_at_all_is_a_failed_lookup(monkeypatch):
    monkeypatch.setattr(ws.workshop, "resolve_dependencies", _tree({}, {}))
    assert mod_download._resolve((A,))["state"] == "failed"


def test_the_total_is_resolved_in_the_background_then_cached(monkeypatch):
    calls = []

    def resolve(mid):
        calls.append(mid)
        return {"mods": [{"modId": mid, "size": 100}], "missing": []}

    monkeypatch.setattr(ws.workshop, "resolve_dependencies", resolve)
    # Run the "background" lookup inline so the test is deterministic.
    monkeypatch.setattr(mod_download, "threading", _InlineThreading)
    first = mod_download.expected_total([A])
    assert first["state"] == "resolving"
    assert mod_download.expected_total([A]) == {
        "state": "ready", "bytes": 100, "mods": 1, "missing": 0, "ids": frozenset({A}),
    }
    mod_download.expected_total([A])
    assert calls == [A]


def test_a_template_without_mods_expects_nothing():
    assert mod_download.expected_total([])["state"] == "none"


def test_speed_and_growth_come_from_the_samples():
    assert mod_download.record("c1", 0, now=0.0) == {"rate_bps": 0, "growing": None}
    assert mod_download.record("c1", 500, now=5.0) == {"rate_bps": 100, "growing": True}
    assert mod_download.record("c1", 1000, now=10.0)["growing"] is True
    # Flat for longer than the window: done downloading.
    mod_download.record("c1", 1000, now=25.0)
    still = mod_download.record("c1", 1000, now=45.0)
    assert still == {"rate_bps": 0, "growing": False}


def _mod_dir(root, name, size):
    d = root / name
    d.mkdir(parents=True)
    (d / "data.pak").write_bytes(b"x" * size)


def test_only_the_wanted_mod_folders_count(tmp_path):
    addons = tmp_path / "addons"
    _mod_dir(addons, A, 100)                     # bare id
    _mod_dir(addons, f"Some Mod_{B.lower()}", 20)  # name_id, any case
    _mod_dir(addons, C, 5000)                    # an earlier template's mod
    _mod_dir(addons, "tmp", 3)                   # not a mod folder: always counts
    (tmp_path / "loose.bin").write_bytes(b"x" * 7)
    paths = [tmp_path]
    assert mod_download.downloaded_bytes(paths, frozenset({A, B})) == 130
    # Total not known yet: everything counts, as it did before.
    assert mod_download.downloaded_bytes(paths, None) == 5130
    assert mod_download.downloaded_bytes([tmp_path / "nope"], None) == 0


def test_a_new_container_starts_a_fresh_history():
    mod_download.record("old", 0, now=0.0)
    mod_download.record("old", 900, now=5.0)
    assert mod_download.record("new", 900, now=6.0) == {"rate_bps": 0, "growing": None}


class _InlineThread:
    def __init__(self, target, args=(), **_kw):
        self._run = lambda: target(*args)

    def start(self):
        self._run()


class _InlineThreading:
    """Stands in for the threading module inside mod_download only."""
    Thread = _InlineThread


# --------------------------------------------------------------------------- #
# instance_stats wiring
# --------------------------------------------------------------------------- #

class _Container:
    id = "container-1"


def _stats_setup(monkeypatch, tmp_path, state):
    from sqlmodel import Session

    import config
    import models

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    with Session(models.get_engine()) as s:
        s.add(Template(id=1, name="t", config_json=_config(A)))
        s.add(Instance(id=1, name="srv", template_id=1, branch="stable",
                       game_port=2005, a2s_port=17780, rcon_port=20002))
        s.commit()
    workshop = tmp_path / "instances" / "1" / "workshop" / A
    workshop.mkdir(parents=True)
    (workshop / "data.pak").write_bytes(b"x" * 300)

    monkeypatch.setattr(instance_service.docker_service,
                        "find_instance_container", lambda _id: _Container())
    monkeypatch.setattr(instance_service, "status_of", lambda _c: "running")
    monkeypatch.setattr(instance_service, "_container_uptime_seconds", lambda _c: 5)
    monkeypatch.setattr(instance_service, "current_run_log_lines", lambda _c, tail=None: [])
    monkeypatch.setattr(instance_service, "server_state", lambda _c, _t: state)
    monkeypatch.setattr(instance_service, "cpu_mem_for", lambda _id, _c: {})
    monkeypatch.setattr(ws.workshop, "resolve_dependencies", _tree({A: 1000}, {}))
    monkeypatch.setattr(mod_download, "threading", _InlineThreading)


def test_a_starting_server_reports_its_mod_download(monkeypatch, tmp_path):
    _stats_setup(monkeypatch, tmp_path, "starting")
    instance_service.instance_stats(1)  # the first poll starts the size lookup
    md = instance_service.instance_stats(1)["mod_download"]
    assert md["downloaded_bytes"] == 300
    assert md["expected_bytes"] == 1000
    assert md["expected_state"] == "ready"
    assert md["mods"] == 1


def test_an_earlier_templates_mods_are_not_counted(monkeypatch, tmp_path):
    # #235: switched from a big template to a smaller one, the old mods stayed on
    # disk and the bar read "28.84 GB of 10.47 GB".
    _stats_setup(monkeypatch, tmp_path, "starting")
    _mod_dir(tmp_path / "instances" / "1" / "workshop", f"Old_{C}", 50_000)
    # The first poll starts the lookup; until it answers, everything counts.
    assert instance_service.instance_stats(1)["mod_download"]["downloaded_bytes"] == 50_300
    md = instance_service.instance_stats(1)["mod_download"]
    assert md["expected_state"] == "ready"
    assert (md["downloaded_bytes"], md["expected_bytes"]) == (300, 1000)


def test_an_online_server_reports_no_download(monkeypatch, tmp_path):
    _stats_setup(monkeypatch, tmp_path, "online")
    assert instance_service.instance_stats(1)["mod_download"] is None
