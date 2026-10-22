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
                      "ids": frozenset({A, B, C}), "sizes": {A: 10, B: 5, C: 7}}


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
        "sizes": {A: 100},
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
    # A's folder holds no version yet: a download under way, 300 of 1000
    assert (md["plan_mods"], md["plan_kind"], md["plan_bytes"], md["plan_done_bytes"]) == (
        1, "download", 1000, 300)


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


# --------------------------------------------------------------------------- #
# The per-run plan (#246)
# --------------------------------------------------------------------------- #

def _addon(root, name, mid, version=None, size=0):
    folder = root / f"{name}_{mid}"
    folder.mkdir(parents=True, exist_ok=True)
    if version:
        (folder / "ServerData.json").write_text(json.dumps({"revision": {"version": version}}))
    (folder / "data.pak").write_bytes(b"x" * size)
    return folder


def _ready(sizes):
    return {"state": "ready", "bytes": sum(sizes.values()), "mods": len(sizes), "missing": 0,
            "ids": frozenset(sizes), "sizes": sizes}


def _progress(monkeypatch, root, sizes, pinned, key="run-1"):
    monkeypatch.setattr(mod_download, "expected_total", lambda ids: _ready(sizes))
    return mod_download.progress(key, [root], _config(*sizes), roots=[root], pinned=pinned,
                                 names={A: "Alpha"})


def test_an_update_counts_only_the_mods_it_changes(monkeypatch, tmp_path):
    # The bar read "20.45 GB of 19.28 GB" on a real host: everything already on
    # disk was counted against the whole template.
    _addon(tmp_path, "Alpha", A, "1.0", size=5000)   # pinned to 1.1: an update
    _addon(tmp_path, "Beta", B, "2.0", size=9000)    # already at its pin
    md = _progress(monkeypatch, tmp_path, {A: 100, B: 200}, {A: "1.1", B: "2.0"})
    assert (md["plan_mods"], md["plan_done"], md["plan_bytes"], md["plan_done_bytes"]) == (1, 0, 100, 0)
    assert md["plan_kind"] == "update" and md["plan_names"] == ["Alpha"]

    _addon(tmp_path, "Alpha", A, "1.1")  # the server finished it
    md = _progress(monkeypatch, tmp_path, {A: 100, B: 200}, {A: "1.1", B: "2.0"})
    assert (md["plan_mods"], md["plan_done"], md["plan_done_bytes"]) == (1, 1, 100)


def test_a_first_download_counts_new_folders_capped_at_their_size(monkeypatch, tmp_path):
    md = _progress(monkeypatch, tmp_path, {A: 100, B: 200}, {})
    assert (md["plan_mods"], md["plan_bytes"], md["plan_done_bytes"]) == (2, 300, 0)
    assert md["plan_kind"] == "download"

    _addon(tmp_path, "Alpha", A, size=60)         # still downloading
    _addon(tmp_path, "Beta", B, "2.0", size=999)  # done, and bigger on disk
    md = _progress(monkeypatch, tmp_path, {A: 100, B: 200}, {})
    assert (md["plan_done"], md["plan_done_bytes"]) == (1, 60 + 200)


def test_the_plan_is_taken_once_per_run(monkeypatch, tmp_path):
    _addon(tmp_path, "Alpha", A, "1.0")
    _progress(monkeypatch, tmp_path, {A: 100}, {A: "1.1"})
    _addon(tmp_path, "Alpha", A, "1.1")
    # Done, but still in this run's plan: the total does not shrink under the bar.
    assert _progress(monkeypatch, tmp_path, {A: 100}, {A: "1.1"})["plan_mods"] == 1
    # A new run (a restart) plans afresh: nothing left to fetch.
    assert _progress(monkeypatch, tmp_path, {A: 100}, {A: "1.1"}, key="run-2")["plan_mods"] == 0


def test_without_a_known_total_there_is_no_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(mod_download, "expected_total", lambda ids: {
        "state": "resolving", "bytes": None, "mods": 0, "missing": 0})
    md = mod_download.progress("k", [tmp_path], _config(A), roots=[tmp_path], pinned={})
    assert "plan_mods" not in md


def test_an_online_server_reports_no_download(monkeypatch, tmp_path):
    _stats_setup(monkeypatch, tmp_path, "online")
    assert instance_service.instance_stats(1)["mod_download"] is None
