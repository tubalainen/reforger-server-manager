import json
from datetime import datetime
from pathlib import Path

import pytest

from models import Instance
from services import instance_service
from services.template_service import TemplateSpec, render_config_json


def _inst(**over):
    base = dict(id=1, name="srv", template_id=1, branch="stable",
                game_port=2005, a2s_port=17780, rcon_port=20002)
    base.update(over)
    return Instance(**base)


def _template_config(**over):
    spec = TemplateSpec(
        name="t", scenario_id="{ABC}Missions/x.conf",
        mods=[{"modId": "AAA", "name": "A", "version": "1.0"}],
        rcon_password="secret", **over,
    )
    return render_config_json(spec)


def test_render_bakes_instance_ports():
    cfg = instance_service.render_instance_config(_template_config(), _inst(), "203.0.113.5")
    assert cfg["bindPort"] == 2005
    assert cfg["publicPort"] == 2005
    assert cfg["publicAddress"] == "203.0.113.5"
    assert cfg["a2s"]["port"] == 17780
    assert cfg["rcon"]["port"] == 20002


def test_render_without_public_address_leaves_it():
    cfg = instance_service.render_instance_config(_template_config(), _inst(), "")
    # empty public address must not overwrite the template's value
    assert "publicAddress" not in cfg or cfg["publicAddress"] == ""


def test_restart_policy_mapping():
    assert instance_service._restart_policy(_inst(auto_start=True, auto_restart=True)) == "unless-stopped"
    assert instance_service._restart_policy(_inst(auto_start=True, auto_restart=False)) == "unless-stopped"
    assert instance_service._restart_policy(_inst(auto_start=False, auto_restart=True)) == "on-failure"
    assert instance_service._restart_policy(_inst(auto_start=False, auto_restart=False)) == "no"


def test_parse_server_status_reads_latest_line():
    log = (
        "some boot noise\n"
        "FPS: 30.0, frame time (avg: 33 ms), Mem: 900000 kB, Player: 1, AI: 0\n"
        "more noise\n"
        "FPS: 59.9, frame time (avg: 16.7 ms), Mem: 1190747 kB, Player: 12, AI: 40, Veh: 3\n"
    )
    s = instance_service.parse_server_status(log)
    assert s == {"fps": 59.9, "mem_kb": 1190747, "players": 12}


def test_parse_server_status_none_when_absent():
    assert instance_service.parse_server_status("no status here") is None


def test_parse_server_status_tolerates_variants():
    # plural "Players:", reordered fields, and a line with FPS but no player/mem
    log = (
        "FPS: 20.0 (starting up)\n"  # newer line, FPS only -> mem/players None
    )
    s = instance_service.parse_server_status(log)
    assert s == {"fps": 20.0, "mem_kb": None, "players": None}

    log2 = "Player: 3, Mem: 500000 kB, FPS: 45.5, AI: 10\n"  # fields reordered
    s2 = instance_service.parse_server_status(log2)
    assert s2 == {"fps": 45.5, "mem_kb": 500000, "players": 3}

    log3 = "FPS: 60.0, Mem: 900000 kB, Players: 7\n"  # plural Players
    assert instance_service.parse_server_status(log3)["players"] == 7


def test_list_and_resolve_log_files(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    profile = tmp_path / "instances" / "1" / "profile" / "logs" / "session1"
    profile.mkdir(parents=True)
    (profile / "console.log").write_text("hello")
    (profile / "crash.mdmp").write_bytes(b"\x00\x01")
    (profile / "ignore.dat").write_text("nope")  # non-log suffix filtered out

    files = instance_service.list_log_files(1)
    paths = {f["path"] for f in files}
    assert "logs/session1/console.log" in paths
    assert "logs/session1/crash.mdmp" in paths
    assert not any("ignore.dat" in p for p in paths)

    resolved = instance_service.resolve_log_file(1, "logs/session1/console.log")
    assert resolved.read_text() == "hello"


def test_resolve_log_file_blocks_traversal(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    (tmp_path / "instances" / "1" / "profile").mkdir(parents=True)
    (tmp_path / "secret.log").write_text("secret")
    with pytest.raises(instance_service.InstanceError):
        instance_service.resolve_log_file(1, "../../../../secret.log")


def test_prune_old_logs_removes_stale_sessions(tmp_path, monkeypatch):
    import os
    import time

    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "log_retention_days", 7)
    logs = tmp_path / "instances" / "1" / "profile" / "logs"
    old = logs / "old_session"
    new = logs / "new_session"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "console.log").write_text("x")
    old_time = time.time() - 10 * 86400
    os.utime(old, (old_time, old_time))

    assert instance_service.prune_old_logs() == 1
    assert not old.exists()
    assert new.exists()


def test_server_files_ready_detects_binary(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "serverfiles_dir", str(tmp_path))
    assert instance_service.server_files_ready("stable") is False
    branch_dir = tmp_path / "stable"
    branch_dir.mkdir()
    (branch_dir / instance_service.SERVER_BINARY).write_text("#!/bin/sh\n")
    assert instance_service.server_files_ready("stable") is True
    # a different branch is still not ready
    assert instance_service.server_files_ready("experimental") is False


def test_render_no_rcon_when_template_has_none():
    spec = TemplateSpec(name="t", scenario_id="{ABC}Missions/x.conf")  # no rcon_password
    cfg = instance_service.render_instance_config(render_config_json(spec), _inst(), "")
    assert "rcon" not in cfg
    # a2s still gets the instance port
    assert cfg["a2s"]["port"] == 17780


def test_render_preserves_scenario_and_mods():
    cfg = instance_service.render_instance_config(_template_config(), _inst(), "")
    assert cfg["game"]["scenarioId"] == "{ABC}Missions/x.conf"
    assert cfg["game"]["mods"][0]["modId"] == "AAA"


def test_create_container_uses_acemod_contract(tmp_path, monkeypatch):
    """Guard the container spec: image, volumes, ports, env, labels.

    This is what would break a real launch, so verify it without a daemon.
    """
    import config
    from services import docker_service

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "serverfiles_dir", str(tmp_path / "sf"))
    monkeypatch.setattr(config.settings, "reforger_server_image", "test/image:1")
    monkeypatch.setattr(config.settings, "public_address", "203.0.113.5")
    # host_path_for is identity outside a container
    monkeypatch.setattr(docker_service, "host_path_for", lambda p: p)

    captured = {}

    class FakeContainers:
        def create(self, image, **kw):
            captured["image"] = image
            captured.update(kw)
            class C:
                id = "deadbeefcafe0000"
            return C()

    class FakeClient:
        containers = FakeContainers()

    monkeypatch.setattr(docker_service, "get_client", lambda: FakeClient())

    inst = _inst(branch="experimental")
    cfg_path = tmp_path / "instances" / "1" / "configs" / "server.json"
    instance_service._create_container(inst, cfg_path)

    assert captured["image"] == "test/image:1"
    assert captured["name"] == "reforger-instance-1"
    # ACE Mod contract: use our mounted config, don't self-generate
    assert captured["environment"]["ARMA_CONFIG"] == "server.json"
    # never self-install: instances run the pre-downloaded files
    assert captured["environment"]["SKIP_INSTALL"] == "true"
    assert captured["environment"]["STEAM_APPID"] == "1890870"  # experimental
    assert captured["environment"]["SERVER_PUBLIC_PORT"] == "2005"
    assert captured["environment"]["SERVER_PUBLIC_ADDRESS"] == "203.0.113.5"
    # server binds the same port it is published on (host == container)
    assert captured["environment"]["SERVER_BIND_PORT"] == "2005"
    # ports published 1:1 so A2S/RCON queries reach the right internal port
    assert captured["ports"]["2005/udp"] == 2005
    assert captured["ports"]["17780/udp"] == 17780
    assert captured["ports"]["20002/udp"] == 20002
    # volumes: shared serverfiles at /reforger + per-instance dirs
    binds = {v["bind"] for v in captured["volumes"].values()}
    assert binds == {"/reforger", "/reforger/Configs", "/home/profile", "/reforger/workshop"}
    # labels let us rediscover the container after a restart
    assert captured["labels"][docker_service.LABEL_INSTANCE_ID] == "1"
    assert captured["labels"][docker_service.LABEL_ROLE] == docker_service.ROLE_INSTANCE
    # auto_restart default True -> container survives Docker/host restart (#17)
    assert captured["restart_policy"] == {"Name": "unless-stopped"}


# --------------------------------------------------------------------------- #
# Scheduled restarts
# --------------------------------------------------------------------------- #

def test_normalise_times_sorts_dedupes_and_pads():
    assert instance_service._normalise_times(["4:00", "16:30", "04:00"]) == ["04:00", "16:30"]


@pytest.mark.parametrize("bad", ["24:00", "12:60", "noon", "1200", "", "12:5"])
def test_normalise_times_rejects_malformed(bad):
    with pytest.raises(instance_service.InstanceError):
        instance_service._normalise_times([bad])


def test_schedule_times_reads_and_survives_garbage():
    assert instance_service.schedule_times(_inst()) == []  # default: no schedule
    good = _inst(restart_schedule_json=json.dumps({"times": ["04:00", "16:00"]}))
    assert instance_service.schedule_times(good) == ["04:00", "16:00"]
    assert instance_service.schedule_times(_inst(restart_schedule_json="not json")) == []


def test_due_scheduled_restart_fires_once_per_window():
    times = ["04:00", "16:00"]
    grace = instance_service.SCHEDULE_CATCHUP_GRACE_SECONDS

    # Just before 04:00 — nothing due.
    assert instance_service._due_scheduled_restart(
        times, datetime(2026, 7, 8, 3, 59), None, grace
    ) is None

    # At 04:00 with no prior service — the 04:00 occurrence is due.
    now = datetime(2026, 7, 8, 4, 0, 10)
    due = instance_service._due_scheduled_restart(times, now, None, grace)
    assert due == datetime(2026, 7, 8, 4, 0)

    # Already serviced this window — not due again.
    assert instance_service._due_scheduled_restart(times, now, due, grace) is None


def test_due_scheduled_restart_skips_stale_window():
    # 04:00 schedule, but it is now 06:00 (2h late) — beyond the 1h grace, so
    # a manager that was down does not restart a recovered server on boot.
    assert instance_service._due_scheduled_restart(
        ["04:00"], datetime(2026, 7, 8, 6, 0), None,
        instance_service.SCHEDULE_CATCHUP_GRACE_SECONDS,
    ) is None


def test_next_scheduled_restart_today_then_tomorrow():
    times = ["04:00", "16:00"]
    # before both -> next is today 04:00
    assert instance_service._next_scheduled_restart(
        times, datetime(2026, 7, 8, 2, 0)
    ) == datetime(2026, 7, 8, 4, 0)
    # between them -> next is today 16:00
    assert instance_service._next_scheduled_restart(
        times, datetime(2026, 7, 8, 10, 0)
    ) == datetime(2026, 7, 8, 16, 0)
    # after both -> wraps to tomorrow's earliest (04:00)
    assert instance_service._next_scheduled_restart(
        times, datetime(2026, 7, 8, 18, 0)
    ) == datetime(2026, 7, 9, 4, 0)
    # no schedule -> None
    assert instance_service._next_scheduled_restart([], datetime(2026, 7, 8, 18, 0)) is None


def test_next_restart_label_formats_server_local():
    inst = _inst(restart_schedule_json=json.dumps({"times": ["16:00"]}))
    label = instance_service.next_restart_label(inst, now=datetime(2026, 7, 8, 10, 0))
    assert label == "2026-07-08 16:00"
    assert instance_service.next_restart_label(_inst()) is None  # no schedule


def test_due_scheduled_restart_picks_latest_of_several():
    # Both 04:00 and 05:00 are past and within grace at 05:10 — pick the latest.
    due = instance_service._due_scheduled_restart(
        ["04:00", "05:00"], datetime(2026, 7, 8, 5, 10), None, 3600
    )
    assert due == datetime(2026, 7, 8, 5, 0)


def test_container_uptime_seconds_parses_docker_timestamp():
    from datetime import datetime, timedelta, timezone

    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%S.123456789Z"
    )
    c = type("C", (), {"attrs": {"State": {"StartedAt": started}}})()
    secs = instance_service._container_uptime_seconds(c)
    assert secs is not None and 290 <= secs <= 310  # ~5 minutes


def test_container_uptime_none_when_never_started():
    c = type("C", (), {"attrs": {"State": {"StartedAt": "0001-01-01T00:00:00Z"}}})()
    assert instance_service._container_uptime_seconds(c) is None


def test_container_ports_match_detects_stale_mapping():
    inst = _inst()  # game 2005, a2s 17780, rcon 20002

    class FakeContainer:
        def __init__(self, bindings):
            self.attrs = {"HostConfig": {"PortBindings": bindings}}

        def reload(self):
            pass

    current = FakeContainer({
        "2005/udp": [{"HostIp": "", "HostPort": "2005"}],
        "17780/udp": [{"HostIp": "", "HostPort": "17780"}],
        "20002/udp": [{"HostIp": "", "HostPort": "20002"}],
    })
    assert instance_service._container_ports_match(current, inst) is True

    # Pre-v0.16 mapping: A2S/RCON forwarded from fixed internal 17777/19999
    stale = FakeContainer({
        "2001/udp": [{"HostIp": "", "HostPort": "2005"}],
        "17777/udp": [{"HostIp": "", "HostPort": "17780"}],
        "19999/udp": [{"HostIp": "", "HostPort": "20002"}],
    })
    assert instance_service._container_ports_match(stale, inst) is False


def test_container_ports_match_true_when_unreadable():
    # A container we can't inspect must never be force-recreated.
    class Broken:
        def reload(self):
            raise instance_service.DockerException("boom")
        attrs = {}

    assert instance_service._container_ports_match(Broken(), _inst()) is True


def test_create_container_applies_launch_params(tmp_path, monkeypatch):
    import config
    from services import docker_service
    from services.template_service import LaunchParams

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "serverfiles_dir", str(tmp_path / "sf"))
    monkeypatch.setattr(docker_service, "host_path_for", lambda p: p)

    captured = {}

    class FakeContainers:
        def create(self, image, **kw):
            captured.update(kw)
            return type("C", (), {"id": "x" * 16})()

    monkeypatch.setattr(docker_service, "get_client",
                        lambda: type("Cl", (), {"containers": FakeContainers()})())

    launch = LaunchParams(max_fps=90, no_backend=True, auto_reload_scenario=300)
    instance_service._create_container(_inst(), tmp_path / "server.json", launch)
    env = captured["environment"]
    assert env["ARMA_MAX_FPS"] == "90"
    assert "-noBackend" in env["ARMA_PARAMS"]
    assert "-autoreload 300" in env["ARMA_PARAMS"]
