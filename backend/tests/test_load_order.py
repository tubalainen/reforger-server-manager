"""The order the engine mounted the mods in, from the server's console.log (#257)."""
import json
import os
import time

import pytest

import config
from services import instance_service, load_order

COE2, KEX, ACE, TF, RHS, DEP = ("60926835F4A7B0CA", "5ED61DC0AFE17E8E", "60C4CE4888FF4621",
                                "5D550926D43F1409", "5CF4A61BE1D960C6", "DEADC0DE00000000")


def _pkg(name, mid=None, base=None):
    path = base or f"/reforger/workshop/addons/{name}_{mid}/"
    return (f"15:23:19.289 ENGINE       : FileSystem: Adding package '{path}' (pak count: 1) "
            f"to filesystem under name {name}")


# The shape of a real console.log: the dirs are listed once before a download and
# again after it; "Available addons" is every folder on disk; the mounts follow.
LOG = "\n".join([
    "15:19:49.945 ENGINE       : Addon dirs:",
    "15:19:49.945  ENGINE       : dir: '/reforger/workshop'",
    "15:20:00.763 BACKEND      : Addon Download started 5CF4A61BE1D960C6 - RHS",
    _pkg("Stale", "1111111111111111"),  # from the first pass: never counts
    "15:23:19.271 ENGINE       : Addon dirs:",
    "15:23:19.271 ENGINE       : Available addons:",
    f"15:23:19.271  ENGINE       : gproj: '/reforger/workshop/addons/ACECore_{ACE}/addon.gproj' "
    f"guid: '{ACE}' (packed)",
    _pkg("TFCore", DEP),
    _pkg("COE2", COE2),
    _pkg("KexScenarioCore", KEX),
    _pkg("ACE_Core", ACE),
    _pkg("TacticalFlava", TF),
    _pkg("RHSStatusQuo", RHS),
    _pkg("ArmaReforger", base="./addons/data/"),
    _pkg("core", base="./addons/core/"),
    "15:23:19.470 RESOURCES    : ResourceDB: loading cache (id=0 name=TFCore ...)",
])


def test_the_mounts_after_the_last_addon_dirs_are_the_load_order():
    mounted = load_order.parse(LOG)
    assert [m["name"] for m in mounted] == [
        "TFCore", "COE2", "KexScenarioCore", "ACE_Core", "TacticalFlava", "RHSStatusQuo",
        "ArmaReforger", "core"]
    assert mounted[1]["modId"] == COE2 and mounted[-1]["modId"] is None
    assert load_order.parse("no mounts here") == []


def test_compared_with_the_list():
    mounted = load_order.parse(LOG)
    # config.json listed RHS, TF, ACE, KEX, COE2: mounted exactly the other way round
    out = load_order.compare(mounted, [RHS, TF, ACE, KEX, COE2])
    assert out["relation"] == "reversed" and out["inverted"] == out["pairs"] == 10
    assert out["unlisted"] == 1  # TFCore: a dependency the server fetched itself
    rows = {r["name"]: r for r in out["mounted"]}
    assert rows["COE2"]["listed_at"] == 5 and rows["TFCore"]["listed_at"] is None
    assert rows["core"]["base_game"] is True

    assert load_order.compare(mounted, [COE2, KEX, ACE, TF, RHS])["relation"] == "same"
    mixed = load_order.compare(mounted, [RHS, TF, COE2, KEX, ACE])
    assert mixed["relation"] == "reordered" and 0 < mixed["inverted"] < mixed["pairs"]
    assert load_order.compare(mounted, [COE2, "2222222222222222"])["not_mounted"] == [
        "2222222222222222"]


@pytest.fixture
def server_dir(tmp_path, monkeypatch, logged_in):
    spec = {"name": "t", "scenario_id": "{ABC}Missions/x.conf"}
    tid = logged_in.post("/api/templates", json=spec).json()["id"]
    iid = logged_in.post("/api/instances", json={"name": "srv", "template_id": tid}).json()["id"]
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    return iid, tmp_path / "instances" / str(iid)


def test_the_latest_session_is_read_against_the_config_it_started_with(server_dir):
    iid, idir = server_dir
    old = idir / "profile" / "logs" / "logs_2026-10-01_10-00-00"
    new = idir / "profile" / "logs" / "logs_2026-10-22_06-21-20"
    for d in (old, new):
        d.mkdir(parents=True)
    (old / "console.log").write_text("Addon dirs:\n" + _pkg("Old", "3333333333333333"))
    (new / "console.log").write_bytes("﻿".encode() + LOG.encode())
    past = time.time() - 3600
    os.utime(old / "console.log", (past, past))
    (idir / "configs").mkdir(parents=True)
    (idir / "configs" / instance_service.CONFIG_FILENAME).write_text(json.dumps(
        {"game": {"mods": [{"modId": m, "name": n} for m, n in
                           ((RHS, "RHS"), (TF, "Tactical Flava"), (ACE, "ACE Core"),
                            (KEX, "Kex"), (COE2, "COE2"))]}}))

    out = instance_service.engine_load_order(iid)

    assert out["available"] is True
    assert out["log"] == "logs/logs_2026-10-22_06-21-20/console.log"
    assert out["relation"] == "reversed"
    assert out["mounted"][1]["listed_name"] == "COE2"


def test_no_log_yet_says_so(server_dir):
    iid, _idir = server_dir
    out = instance_service.engine_load_order(iid)
    assert out["available"] is False and "once the server has started" in out["reason"]


def test_the_endpoint(logged_in, monkeypatch):
    monkeypatch.setattr(instance_service, "engine_load_order",
                        lambda i: {"available": False, "reason": "x"})
    assert logged_in.get("/api/instances/1/load-order").json() == {"available": False, "reason": "x"}
