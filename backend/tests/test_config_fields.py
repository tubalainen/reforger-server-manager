from services import config_fields
from services.config_validator import FIELD_PATHS
from services.template_service import TemplateSpec

# Settings that reach config.json but are edited somewhere other than the field
# catalog: their own wizard steps or cards, or derived from another setting.
_ELSEWHERE = {
    "scenario_id",          # Scenario step
    "mods",                 # Mods step
    "game_name",            # Server card, with its own Randomize button
    "password",
    "admin_password",
    "admins",               # Player access card
    "player_whitelist",
    "player_ban_list",
    "mission_header",       # Mission header card
    "supported_platforms",  # follows cross_platform (model validator)
}


def _fields():
    return {f["name"]: f for f in config_fields.catalog()["fields"]}


def test_every_config_setting_is_in_the_catalog_or_known_to_live_elsewhere():
    # A setting added to the model and to FIELD_PATHS without a place in the
    # wizard is exactly how a key ends up in config.json with no way to set it.
    missing = set(FIELD_PATHS) - set(_fields()) - _ELSEWHERE
    assert missing == set()


def test_catalog_fields_are_spec_fields_in_known_groups():
    groups = {g["id"] for g in config_fields.GROUPS}
    for name, field in _fields().items():
        assert name in TemplateSpec.model_fields
        assert field["group"] in groups
        assert field["card"] in {"server", "advanced"}
        requires = field.get("requires")
        assert requires is None or requires in TemplateSpec.model_fields


def test_limits_and_defaults_come_from_the_model():
    fields = _fields()
    view = fields["server_max_view_distance"]
    assert (view["min"], view["max"]) == (500, 12000)
    assert (fields["player_save_time"]["min"], fields["player_save_time"]["default"]) == (1, 120)
    assert fields["ai_limit"]["min"] == -1
    assert fields["von_can_transmit_cross_faction"]["default"] is False
    assert fields["rcon_permission"]["kind"] == "select"
    assert fields["rcon_permission"]["options"] == ["admin", "monitor"]


def test_key_is_the_last_part_of_the_config_path():
    fields = _fields()
    assert fields["von_can_transmit_cross_faction"]["path"] == (
        "game.gameProperties.VONCanTransmitCrossFaction"
    )
    assert fields["von_can_transmit_cross_faction"]["key"] == "VONCanTransmitCrossFaction"
    assert fields["join_queue_max_size"]["key"] == "maxSize"


def test_fields_endpoint_requires_auth(client):
    assert client.get("/api/templates/fields").status_code == 401


def test_fields_endpoint(logged_in):
    body = logged_in.get("/api/templates/fields").json()
    assert [g["id"] for g in body["groups"]][0] == "game_properties"
    assert any(f["key"] == "VONCanTransmitCrossFaction" for f in body["fields"])
