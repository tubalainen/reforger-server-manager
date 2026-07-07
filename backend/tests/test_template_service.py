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


def test_advanced_options_render_and_roundtrip():
    cfg = _spec(
        disable_third_person=True, fast_validation=False, ai_limit=64,
        von_can_transmit_cross_faction=True, disable_server_shutdown=True,
        player_save_time=300, server_min_grass_distance=50,
    ).to_config()
    props = cfg["game"]["gameProperties"]
    op = cfg["operating"]
    assert props["disableThirdPerson"] is True
    assert props["fastValidation"] is False
    assert props["VONCanTransmitCrossFaction"] is True
    assert props["serverMinGrassDistance"] == 50
    assert op["aiLimit"] == 64
    assert op["disableServerShutdown"] is True
    assert op["playerSaveTime"] == 300

    restored = spec_from_config(json.dumps(cfg))
    assert restored["disable_third_person"] is True
    assert restored["fast_validation"] is False
    assert restored["ai_limit"] == 64
    assert restored["player_save_time"] == 300


def test_advanced_bounds_validated():
    with pytest.raises(ValidationError):
        _spec(server_min_grass_distance=999)
    with pytest.raises(ValidationError):
        _spec(ai_limit=-5)
