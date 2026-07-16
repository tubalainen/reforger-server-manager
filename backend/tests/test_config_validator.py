"""Validation of hand-edited config.json (issue #29).

The load-bearing rule under test: unknown keys warn, they never block. Everything
in gameProperties can be scenario-specific, so a key we don't recognise is a user
doing their job, not a mistake.
"""
from services.config_validator import validate_config
from services.template_service import TemplateSpec


def _config(**over):
    cfg = TemplateSpec(name="_", scenario_id="{ECC6}Missions/23_Campaign.conf").to_config()
    cfg.update(over)
    return cfg


def test_valid_config_has_no_errors_or_warnings():
    result = validate_config(_config())
    assert result["errors"] == []
    assert result["warnings"] == []


def test_unknown_key_warns_but_never_errors():
    cfg = _config()
    cfg["game"]["gameProperties"]["someScenarioKey"] = {"tickRate": 30}
    result = validate_config(cfg)
    assert result["errors"] == []
    paths = [w["path"] for w in result["warnings"]]
    assert paths == ["game.gameProperties.someScenarioKey"]
    assert "preserved" in result["warnings"][0]["message"]


def test_unknown_subtree_reports_only_its_root():
    # Reporting every child of an unknown block would bury the useful line.
    cfg = _config()
    cfg["game"]["gameProperties"]["custom"] = {"a": 1, "nested": {"b": 2}}
    paths = [w["path"] for w in validate_config(cfg)["warnings"]]
    assert paths == ["game.gameProperties.custom"]


def test_unknown_top_level_key_warns():
    paths = [w["path"] for w in validate_config(_config(myOwnBlock={"x": 1}))["warnings"]]
    assert paths == ["myOwnBlock"]


def test_missing_scenario_id_is_an_error():
    cfg = _config()
    cfg["game"]["scenarioId"] = ""
    errors = validate_config(cfg)["errors"]
    assert len(errors) == 1
    assert errors[0]["path"] == "game.scenarioId"
    assert "required" in errors[0]["message"]


def test_missing_game_block_is_an_error():
    cfg = _config()
    del cfg["game"]
    assert validate_config(cfg)["errors"][0]["path"] == "game"


def test_non_object_config_is_an_error():
    assert validate_config([1, 2, 3])["errors"][0]["message"] == "The config must be a JSON object."


def test_out_of_range_modelled_value_errors_with_a_json_path():
    # Reuses TemplateSpec's own ge=50 constraint (#28) — reported in the user's
    # terms, not the model's field name.
    cfg = _config()
    cfg["game"]["gameProperties"]["serverMinGrassDistance"] = 10
    errors = validate_config(cfg)["errors"]
    assert len(errors) == 1
    assert errors[0]["path"] == "game.gameProperties.serverMinGrassDistance"
    assert "50" in errors[0]["message"]


def test_wrong_type_on_a_modelled_value_errors():
    cfg = _config()
    cfg["game"]["maxPlayers"] = "lots"
    errors = validate_config(cfg)["errors"]
    assert [e["path"] for e in errors] == ["game.maxPlayers"]


def test_bad_rcon_permission_errors():
    cfg = _config()
    cfg["rcon"] = {"password": "pw", "permission": "wizard", "maxClients": 4}
    assert any(e["path"] == "rcon.permission" for e in validate_config(cfg)["errors"])


def test_mod_missing_mod_id_errors_with_an_indexed_path():
    cfg = _config()
    cfg["game"]["mods"] = [{"name": "No id here"}]
    errors = validate_config(cfg)["errors"]
    assert errors[0]["path"] == "game.mods[0].modId"


def test_optional_blocks_are_known_keys():
    # persistence/rcon/disableNavmeshStreaming are rendered conditionally; they
    # must not be mistaken for custom keys just because the default spec omits them.
    cfg = TemplateSpec(
        name="_", scenario_id="{ECC6}M.conf",
        persistence_enabled=True, rcon_password="pw", disable_navmesh_streaming=True,
    ).to_config()
    result = validate_config(cfg)
    assert result["errors"] == []
    assert result["warnings"] == []
