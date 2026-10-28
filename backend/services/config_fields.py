"""The wizard's catalog of config.json settings (Advanced settings).

The Settings step used to hand-write every control, with labels too short to
find a key by ("Cross-faction VON" for VONCanTransmitCrossFaction) and input
limits that had drifted from the model's. This module says, once, where each
setting is shown, what it is called and what it does; everything that is a
*rule* — type, default, min/max — is read from TemplateSpec's own schema, so
the wizard's limits and defaults can never disagree with what a save accepts.

The JSON path of each setting comes from config_validator.FIELD_PATHS, the same
map that names a bad value in the JSON editor.
"""
from services.config_validator import FIELD_PATHS
from services.template_service import TemplateSpec

# Shown in this order. `path` is the config.json object the group's keys live in.
GROUPS = [
    {"id": "game_properties", "title": "Game properties", "path": "game.gameProperties"},
    {"id": "von", "title": "Voice (VON)", "path": "game.gameProperties"},
    {"id": "game", "title": "Game", "path": "game"},
    {"id": "operating", "title": "Operating", "path": "operating"},
    {"id": "join_queue", "title": "Join queue", "path": "operating.joinQueue"},
    {"id": "persistence", "title": "Persistence (save games)",
     "path": "game.gameProperties.persistence"},
    {"id": "rcon", "title": "RCON", "path": "rcon"},
]

# Settings with no FIELD_PATHS entry: they don't map one-to-one onto a key.
_PATHS = FIELD_PATHS | {
    # A switch for the whole block: ticked = the persistence object is written.
    "persistence_enabled": "game.gameProperties.persistence",
}

# (field, group, label, help, extra). `card: "server"` = shown on the Server
# card above Advanced settings; listed here so a search finds it and says where.
# `requires` = the field only takes effect while that other field is set.
_FIELDS = [
    # ---- on the Server card ------------------------------------------------
    ("max_players", "game", "Max players", "Most players on the server at once.",
     {"card": "server"}),
    ("visible", "game", "Public (server browser)",
     "Lists the server in the in-game server browser.", {"card": "server"}),
    ("cross_platform", "game", "Cross-platform",
     "Lets console players join as well as PC players.", {"card": "server"}),
    ("server_max_view_distance", "game_properties", "Server view distance",
     "The furthest a player can set their view distance, in metres.", {"card": "server"}),
    ("network_view_distance", "game_properties", "Network view distance",
     "How far away the server sends players what is happening, in metres.",
     {"card": "server"}),
    ("battleye", "game_properties", "BattlEye",
     "The BattlEye anti-cheat. Keep it on for a public server.", {"card": "server"}),
    ("disable_third_person", "game_properties", "First-person only",
     "Turns third-person view off for every player.", {"card": "server"}),
    ("rcon_password", "rcon", "RCON password",
     "Turns RCON (remote admin tools) on. Leave it empty to keep RCON off.",
     {"card": "server"}),

    # ---- Advanced settings ---------------------------------------------------
    ("server_min_grass_distance", "game_properties", "Min grass distance",
     "Players see grass at least this far away, in metres, whatever their own "
     "settings say.", {"unit": "m"}),
    ("fast_validation", "game_properties", "Fast validation",
     "Checks the data players send in a quicker way. Keep it on for a public server.", {}),

    ("von_disable_ui", "von", "Disable VON UI",
     "Hides the on-screen voice (VON) indicators.", {}),
    ("von_disable_direct_speech_ui", "von", "Disable direct-speech UI",
     "Hides the indicator for direct speech, the voice nearby players hear.", {}),
    ("von_can_transmit_cross_faction", "von", "Cross-faction VON",
     "Lets players talk on, and listen to, other factions' radio channels.", {}),

    ("mods_required_by_default", "game", "Mods required by default",
     "Every mod in the list is required to join, unless the mod's own entry says "
     "otherwise.", {}),

    ("player_save_time", "operating", "Player save interval",
     "How often each player's character is saved.", {"unit": "s"}),
    ("ai_limit", "operating", "AI limit",
     "The most AI characters alive at once. -1 means no limit: the scenario decides.", {}),
    ("slot_reservation_timeout", "operating", "Slot reservation timeout",
     "How long a disconnected player's slot is kept free for them to rejoin.",
     {"unit": "s"}),
    ("lobby_player_synchronise", "operating", "Lobby player sync",
     "Sends the player list to players who are still in the lobby.", {}),
    ("disable_navmesh_streaming", "operating", "Disable navmesh streaming",
     "Loads the AI navigation meshes whole at start instead of streaming them in. "
     "Uses more memory.", {}),
    ("disable_server_shutdown", "operating", "Disable auto-shutdown",
     "Stops the server shutting itself down on its own.", {}),
    ("disable_crash_reporter", "operating", "Disable crash reporter",
     "Doesn't send crash reports to Bohemia.", {}),
    ("disable_ai", "operating", "Disable AI", "Turns AI off completely.", {}),

    ("join_queue_max_size", "join_queue", "Join queue max size",
     "How many players can wait in line while the server is full. 0 turns the "
     "queue off.", {}),

    ("persistence_enabled", "persistence", "Configure persistence",
     "The server saves the mission on its own by default. Tick this to write a "
     "persistence block and set the values below yourself; leave it clear to let "
     "the engine decide. Clearing it does not switch saving off: to stop a "
     "scenario saving at all, add \"m_eSaveTypes\": 0 to the mission header.", {}),
    ("auto_save_interval", "persistence", "Auto-save interval",
     "Minutes between automatic saves. 0 turns automatic saves off.",
     {"unit": "min", "requires": "persistence_enabled"}),
    ("save_retention", "persistence", "Save points kept",
     "How many save points are kept per mission.", {"requires": "persistence_enabled"}),
    ("hive_id", "persistence", "Hive ID",
     "Keeps saves apart when servers share a database.",
     {"requires": "persistence_enabled"}),
    ("load_session_save", "persistence", "Load the latest save on startup",
     "Carries on from the last save point when the server starts.",
     {"requires": "persistence_enabled"}),
    ("keep_session_save", "persistence", "Keep save points after the mission ends",
     "Leaves the save points in place once the mission is over.",
     {"requires": "persistence_enabled"}),

    ("rcon_permission", "rcon", "Permission",
     "admin may run every RCON command; monitor may only read.",
     {"requires": "rcon_password", "options": ["admin", "monitor"]}),
    ("rcon_max_clients", "rcon", "Max clients",
     "How many RCON tools can be connected at once.", {"requires": "rcon_password"}),
]

_KINDS = {"boolean": "bool", "integer": "int", "string": "text"}


def catalog() -> dict:
    """{groups, fields} for the wizard. Rules come from TemplateSpec's schema."""
    props = TemplateSpec.model_json_schema()["properties"]
    fields = []
    for name, group, label, help_text, extra in _FIELDS:
        schema = props[name]
        path = _PATHS[name]
        field = {
            "name": name,
            "group": group,
            "label": label,
            "help": help_text,
            "path": path,
            "key": path.rsplit(".", 1)[-1],
            "kind": "select" if "options" in extra else _KINDS[schema["type"]],
            "default": schema.get("default"),
            "card": extra.get("card", "advanced"),
        }
        for rule in ("minimum", "maximum"):
            if rule in schema:
                field[rule[:3]] = schema[rule]
        for opt in ("unit", "requires", "options"):
            if opt in extra:
                field[opt] = extra[opt]
        fields.append(field)
    return {"groups": GROUPS, "fields": fields}
