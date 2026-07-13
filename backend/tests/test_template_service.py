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


def test_mod_version_written_only_when_locked():
    # No lock -> config.json omits "version" so the server follows the latest
    # Workshop release; a locked version is written verbatim (#60). The picker
    # metadata (versions/explicit/dependencies) never reaches config.json.
    spec = _spec(mods=[
        {"modId": "AAAAAAAAAAAAAAAA", "name": "Dynamic", "versions": ["1.2.0", "1.1.0"]},
        {"modId": "BBBBBBBBBBBBBBBB", "name": "Pinned", "version": "1.1.0",
         "versions": ["1.2.0", "1.1.0"]},
    ])
    mods = spec.to_config()["game"]["mods"]
    assert mods == [
        {"modId": "AAAAAAAAAAAAAAAA", "name": "Dynamic"},
        {"modId": "BBBBBBBBBBBBBBBB", "name": "Pinned", "version": "1.1.0"},
    ]


def test_scenario_name_never_rendered_to_config():
    # scenario_name is wizard display metadata (#59); the server's config.json
    # only understands the raw game.scenarioId.
    cfg_json = render_config_json(_spec(scenario_name="Conflict Everon"))
    assert "scenario_name" not in cfg_json
    assert "Conflict Everon" not in cfg_json


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


def test_grass_distance_valid_by_default():
    # regression for #28: server schema requires serverMinGrassDistance >= 50
    cfg = _spec().to_config()
    assert cfg["game"]["gameProperties"]["serverMinGrassDistance"] >= 50
    with pytest.raises(ValidationError):
        _spec(server_min_grass_distance=0)


def test_navmesh_streaming_is_array_or_omitted():
    # regression for #28: disableNavmeshStreaming must be an array, not a bool
    off = _spec(disable_navmesh_streaming=False).to_config()
    assert "disableNavmeshStreaming" not in off["operating"]
    on = _spec(disable_navmesh_streaming=True).to_config()
    assert on["operating"]["disableNavmeshStreaming"] == []
    # round-trips back to the toggle
    assert spec_from_config(json.dumps(on))["disable_navmesh_streaming"] is True
    assert spec_from_config(json.dumps(off))["disable_navmesh_streaming"] is False


def test_rcon_includes_blacklist_whitelist_arrays():
    cfg = _spec(rcon_password="x").to_config()
    assert cfg["rcon"]["blacklist"] == [] and cfg["rcon"]["whitelist"] == []


def test_advanced_options_render_and_roundtrip():
    cfg = _spec(
        disable_third_person=True, fast_validation=False, ai_limit=64,
        von_can_transmit_cross_faction=True, disable_server_shutdown=True,
        player_save_time=300, server_min_grass_distance=75,
    ).to_config()
    props = cfg["game"]["gameProperties"]
    op = cfg["operating"]
    assert props["disableThirdPerson"] is True
    assert props["fastValidation"] is False
    assert props["VONCanTransmitCrossFaction"] is True
    assert props["serverMinGrassDistance"] == 75
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


def test_longbow_extra_settings_render_and_roundtrip():
    cfg = _spec(
        mods_required_by_default=True, disable_ai=True, join_queue_max_size=20,
        persistence_enabled=True, auto_save_interval=15, hive_id=42,
        rcon_password="pw", rcon_permission="monitor", rcon_max_clients=8,
    ).to_config()
    assert cfg["game"]["modsRequiredByDefault"] is True
    assert cfg["operating"]["disableAI"] is True
    assert cfg["operating"]["joinQueue"] == {"maxSize": 20}
    assert cfg["game"]["gameProperties"]["persistence"]["autoSaveInterval"] == 15
    assert cfg["game"]["gameProperties"]["persistence"]["hiveId"] == 42
    assert cfg["rcon"]["permission"] == "monitor"
    assert cfg["rcon"]["maxClients"] == 8

    restored = spec_from_config(json.dumps(cfg))
    assert restored["mods_required_by_default"] is True
    assert restored["disable_ai"] is True
    assert restored["join_queue_max_size"] == 20
    assert restored["persistence_enabled"] is True
    assert restored["auto_save_interval"] == 15
    assert restored["hive_id"] == 42
    assert restored["rcon_permission"] == "monitor"
    assert restored["rcon_max_clients"] == 8


def test_persistence_omitted_when_disabled():
    cfg = _spec().to_config()
    assert "persistence" not in cfg["game"]["gameProperties"]
    assert spec_from_config(json.dumps(cfg))["persistence_enabled"] is False


def test_rcon_permission_pattern_validated():
    with pytest.raises(ValidationError):
        _spec(rcon_permission="superuser")


def test_launch_params_render():
    from services.template_service import LaunchParams

    lp = LaunchParams(
        max_fps=60, auto_reload_scenario=600, spatial_map_resolution=500,
        no_backend=True, log_voting=True, freeze_check_mode="crash",
        short_worker_count=12, extra_args="-someCustom 1",
    )
    params, max_fps = lp.render()
    assert max_fps == 60  # goes to ARMA_MAX_FPS, not ARMA_PARAMS
    assert "-maxFPS" not in params
    assert "-autoreload 600" in params
    assert "-nwkResolution 500" in params
    assert "-jobsysShortWorkerCount 12" in params
    assert "-noBackend" in params and "-logVoting" in params
    assert "-freezeCheckMode crash" in params
    assert "-someCustom 1" in params


def test_launch_params_empty_render():
    from services.template_service import LaunchParams

    params, max_fps = LaunchParams().render()
    assert params == "" and max_fps is None


def test_launch_params_bounds():
    from services.template_service import LaunchParams

    with pytest.raises(ValidationError):
        LaunchParams(spatial_map_resolution=50)   # below 100
    with pytest.raises(ValidationError):
        LaunchParams(max_fps=5)
