import json

import pytest
from pydantic import ValidationError

from services.template_service import TemplateSpec, render_config_json, spec_from_config


def _spec(**over) -> TemplateSpec:
    base = dict(
        name="My Server",
        scenario_id="{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        mods=[{"modId": "591AF5BDA9F7CE8B", "name": "RHS", "version": "1.0"}],
    )
    base.update(over)
    return TemplateSpec(**base)


def test_render_produces_valid_config():
    cfg = json.loads(render_config_json(_spec()))
    assert cfg["game"]["scenarioId"] == "{ECC61978EDCC2B5A}Missions/23_Campaign.conf"
    assert cfg["game"]["mods"] == [{"modId": "591AF5BDA9F7CE8B", "name": "RHS", "version": "1.0"}]
    assert cfg["game"]["maxPlayers"] == 64
    assert cfg["bindPort"] == 2001
    assert cfg["a2s"]["port"] == 17777


def test_rcon_omitted_unless_password_set():
    assert "rcon" not in _spec().to_config()
    assert _spec(rcon_password="secret").to_config()["rcon"]["password"] == "secret"


def test_mod_entry_drops_null_fields():
    cfg = _spec(mods=[{"modId": "591AF5BDA9F7CE8B"}]).to_config()
    assert cfg["game"]["mods"] == [{"modId": "591AF5BDA9F7CE8B"}]


def test_scenario_id_required():
    with pytest.raises(ValidationError):
        TemplateSpec(name="x", scenario_id="")


def test_max_players_bounds():
    with pytest.raises(ValidationError):
        _spec(max_players=0)
    with pytest.raises(ValidationError):
        _spec(max_players=999)


def test_roundtrip_config_to_spec():
    cfg_json = render_config_json(_spec(game_name="Roundtrip", max_players=48))
    restored = spec_from_config(cfg_json)
    assert restored["game_name"] == "Roundtrip"
    assert restored["max_players"] == 48
    assert restored["scenario_id"] == "{ECC61978EDCC2B5A}Missions/23_Campaign.conf"
    assert restored["mods"][0]["modId"] == "591AF5BDA9F7CE8B"
