import json
from pathlib import Path

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
