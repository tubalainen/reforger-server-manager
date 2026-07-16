"""Validate a hand-edited Reforger config.json (issue #29).

Two levels, and the split is the whole point:

* errors   — the config is wrong and we refuse to apply it. Only ever raised for
             things we actually know: bad JSON shape, a missing scenarioId, or a
             modelled key carrying a value the server would reject.
* warnings — keys the manager doesn't model. NOT an error: gameProperties is
             scenario-specific and mod authors add their own keys, so an unknown
             key is a normal thing a user does on purpose. We flag it only so the
             user knows the GUI won't manage it, and preserve it verbatim.

Neither list restates the config schema. Errors come from running the existing
TemplateSpec pydantic model (so every Field(ge=/le=/pattern=) constraint is
enforced once, where it already lives); known keys are derived by rendering a
maximal spec through to_config(). Both stay in sync with the model by
construction.
"""
import json

from pydantic import ValidationError

from services.template_service import TemplateSpec, spec_from_config

# Spec field -> the config.json path it renders to, so an error reads in the
# user's terms ("game.gameProperties.serverMinGrassDistance") and not the
# model's ("server_min_grass_distance"). Only fields that reach config.json.
_FIELD_PATHS = {
    "scenario_id": "game.scenarioId",
    "game_name": "game.name",
    "password": "game.password",
    "admin_password": "game.passwordAdmin",
    "admins": "game.admins",
    "max_players": "game.maxPlayers",
    "visible": "game.visible",
    "cross_platform": "game.crossPlatform",
    "supported_platforms": "game.supportedPlatforms",
    "mods": "game.mods",
    "mods_required_by_default": "game.modsRequiredByDefault",
    "server_max_view_distance": "game.gameProperties.serverMaxViewDistance",
    "server_min_grass_distance": "game.gameProperties.serverMinGrassDistance",
    "network_view_distance": "game.gameProperties.networkViewDistance",
    "battleye": "game.gameProperties.battlEye",
    "disable_third_person": "game.gameProperties.disableThirdPerson",
    "fast_validation": "game.gameProperties.fastValidation",
    "von_disable_ui": "game.gameProperties.VONDisableUI",
    "von_disable_direct_speech_ui": "game.gameProperties.VONDisableDirectSpeechUI",
    "von_can_transmit_cross_faction": "game.gameProperties.VONCanTransmitCrossFaction",
    "auto_save_interval": "game.gameProperties.persistence.autoSaveInterval",
    "hive_id": "game.gameProperties.persistence.hiveId",
    "lobby_player_synchronise": "operating.lobbyPlayerSynchronise",
    "disable_server_shutdown": "operating.disableServerShutdown",
    "disable_crash_reporter": "operating.disableCrashReporter",
    "disable_ai": "operating.disableAI",
    "player_save_time": "operating.playerSaveTime",
    "ai_limit": "operating.aiLimit",
    "slot_reservation_timeout": "operating.slotReservationTimeout",
    "join_queue_max_size": "operating.joinQueue.maxSize",
    "rcon_password": "rcon.password",
    "rcon_permission": "rcon.permission",
    "rcon_max_clients": "rcon.maxClients",
}

_UNKNOWN_KEY_MESSAGE = (
    "Not managed by the GUI. It will be saved to config.json exactly as written "
    "and preserved when you edit this template."
)


def _paths(node: dict, prefix: str = "") -> set[str]:
    """Every dotted key path in `node`, recursing through nested objects only.

    Lists are leaves: config.json arrays (mods, admins, supportedPlatforms) are
    validated by the model, not by path.
    """
    found = set()
    for key, value in node.items():
        path = f"{prefix}{key}"
        found.add(path)
        if isinstance(value, dict):
            found |= _paths(value, f"{path}.")
    return found


def known_paths() -> set[str]:
    """Every path to_config() can emit, with all optional blocks turned on."""
    maximal = TemplateSpec(
        name="_",
        scenario_id="_",
        # The blocks to_config writes conditionally; on = their keys are known.
        persistence_enabled=True,
        rcon_password="_",
        disable_navmesh_streaming=True,
    ).to_config()
    return _paths(maximal)


def unknown_paths(cfg: dict) -> list[str]:
    """Top-most paths in `cfg` that to_config() never emits.

    Stops descending at the first unknown key: reporting `gameProperties.myBlock`
    is useful, also reporting each of its children is noise. This is the one
    definition of "a custom key" — the editor's warnings and the wizard's badge
    both count it, so they can never disagree about how many there are.
    """
    known = known_paths()
    unknown: list[str] = []

    def walk(node: dict, prefix: str = "") -> None:
        for key, value in node.items():
            path = f"{prefix}{key}"
            if path not in known:
                unknown.append(path)
                continue  # its children are unknown too; one report is enough
            if isinstance(value, dict):
                walk(value, f"{path}.")

    walk(cfg)
    return unknown


def _model_errors(cfg: dict) -> list[dict]:
    """Run the config through TemplateSpec and translate pydantic's complaints."""
    try:
        # clamp=False: validation must judge what the user typed. With clamping
        # on, an out-of-range value would be quietly corrected here and never
        # reported — then reconcile would diff it back into `extras` and override
        # the correction anyway. See spec_from_config's docstring.
        known = spec_from_config(json.dumps(cfg), clamp=False)
    except (ValueError, TypeError, AttributeError) as exc:
        return [{"path": "", "message": f"Could not read this as a Reforger config: {exc}"}]
    # `name` is the template's, not the config's — spec_from_config never returns
    # one. A placeholder keeps min_length off the error list; the real name is
    # validated by the normal save path.
    known["name"] = "_"
    try:
        TemplateSpec(**known)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            field = err["loc"][0] if err["loc"] else ""
            path = _FIELD_PATHS.get(field, str(field))
            # Index/attribute tail for list fields, e.g. mods -> game.mods[0].modId
            for part in err["loc"][1:]:
                path += f"[{part}]" if isinstance(part, int) else f".{part}"
            errors.append({"path": path, "message": err["msg"]})
        return errors
    return []


def validate_config(cfg: dict) -> dict:
    """{"errors": [{path, message}], "warnings": [{path, message}]}."""
    if not isinstance(cfg, dict):
        return {
            "errors": [{"path": "", "message": "The config must be a JSON object."}],
            "warnings": [],
        }

    game = cfg.get("game")
    if not isinstance(game, dict):
        return {
            "errors": [{"path": "game", "message": 'Missing the required "game" object.'}],
            "warnings": [],
        }
    if not str(game.get("scenarioId") or "").strip():
        # Called out by hand: without it the server has nothing to run, and the
        # message beats pydantic's "String should have at least 1 character".
        return {
            "errors": [{
                "path": "game.scenarioId",
                "message": "A scenarioId is required — the server has no mission to load without it.",
            }],
            "warnings": [],
        }

    return {
        "errors": _model_errors(cfg),
        "warnings": [
            {"path": p, "message": _UNKNOWN_KEY_MESSAGE} for p in unknown_paths(cfg)
        ],
    }
