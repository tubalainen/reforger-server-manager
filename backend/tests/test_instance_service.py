import json
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
    # ports mapped host==container-standard
    assert captured["ports"]["2001/udp"] == 2005
    assert captured["ports"]["17777/udp"] == 17780
    assert captured["ports"]["19999/udp"] == 20002
    # volumes: shared serverfiles at /reforger + per-instance dirs
    binds = {v["bind"] for v in captured["volumes"].values()}
    assert binds == {"/reforger", "/reforger/Configs", "/home/profile", "/reforger/workshop"}
    # labels let us rediscover the container after a restart
    assert captured["labels"][docker_service.LABEL_INSTANCE_ID] == "1"
    assert captured["labels"][docker_service.LABEL_ROLE] == docker_service.ROLE_INSTANCE
    # auto_restart default True -> container survives Docker/host restart (#17)
    assert captured["restart_policy"] == {"Name": "unless-stopped"}
