"""SQLite persistence (SQLModel): server templates, instances, port leases."""
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Field, SQLModel, create_engine

import config


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Template(SQLModel, table=True):
    """A saved Arma server template: scenario + mods + settings.

    config_json holds the full Reforger server config.json this template
    renders to; scenario/mod picks are edited through it.
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    config_json: str
    # Display name of the selected scenario (config.json only keeps the raw
    # scenarioId) so the edit wizard can show what's currently picked (#59)
    scenario_name: str = ""
    # Player count the scenario declares on the Workshop (#65). Kept next to the
    # name for the same reason: config.json has no place for it. None = unknown
    # (base-game scenario, or a template saved before this).
    scenario_player_count: int | None = None
    launch_params_json: str = "{}"  # engine launch params (issue #20)
    # Enriched mod list with dependency metadata the flat config.json can't hold
    # (explicit vs dependency, edges) — the editing source of truth for mods (#55)
    mods_json: str = ""
    # Hand-edited config keys the wizard doesn't model, as an RFC 7386 merge patch
    # re-applied over every render so they survive a wizard save (#29)
    extras_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Instance(SQLModel, table=True):
    """A concrete server: a template bound to a branch, ports and a container."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    template_id: int = Field(foreign_key="template.id")
    branch: str = "stable"  # stable | experimental
    game_port: int
    a2s_port: int
    rcon_port: int
    # NB: no container_id here on purpose. Containers are found by LABEL
    # (reforger-manager.instance_id), which survives a manager restart AND a
    # container recreation; a stored id goes stale the moment we recreate one.
    # The column used to exist, was written once and never read (#88). Old
    # databases keep the orphaned column; nothing selects it.
    desired_state: str = "stopped"  # running | stopped
    auto_restart: bool = True   # restart the server if it crashes
    auto_start: bool = True     # start the server after a Docker/host restart
    # Scheduled daily restarts (issue: roadmap). JSON {"times": ["04:00", ...]}
    # in the container's local time; "" disables. last_scheduled_restart tracks
    # the most recently serviced occurrence so a restart fires once per window.
    restart_schedule_json: str = ""
    last_scheduled_restart: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        data_dir = Path(config.settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{data_dir / 'manager.db'}",
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _migrate(engine)


def _migrate(engine) -> None:
    """Lightweight additive migrations for existing SQLite databases."""
    from sqlalchemy import text

    with engine.connect() as conn:
        icols = {row[1] for row in conn.execute(text("PRAGMA table_info(instance)"))}
        # auto_start split out from auto_restart (issue #26); default from
        # auto_restart so existing "keep running" servers keep both behaviours
        if "auto_start" not in icols:
            conn.execute(text(
                "ALTER TABLE instance ADD COLUMN auto_start BOOLEAN NOT NULL DEFAULT 1"
            ))
            conn.execute(text("UPDATE instance SET auto_start = auto_restart"))
            conn.commit()
        # scheduled daily restarts: schedule + last-serviced marker
        if "restart_schedule_json" not in icols:
            conn.execute(text(
                "ALTER TABLE instance ADD COLUMN restart_schedule_json VARCHAR NOT NULL DEFAULT ''"
            ))
            conn.commit()
        if "last_scheduled_restart" not in icols:
            conn.execute(text(
                "ALTER TABLE instance ADD COLUMN last_scheduled_restart DATETIME"
            ))
            conn.commit()
        # engine launch parameters per template (issue #20)
        tcols = {row[1] for row in conn.execute(text("PRAGMA table_info(template)"))}
        if "launch_params_json" not in tcols:
            conn.execute(text(
                "ALTER TABLE template ADD COLUMN launch_params_json VARCHAR NOT NULL DEFAULT '{}'"
            ))
            conn.commit()
        # enriched mod list w/ dependency metadata (issue #55); empty = fall back
        # to the flat mods[] in config_json for templates predating this
        if "mods_json" not in tcols:
            conn.execute(text(
                "ALTER TABLE template ADD COLUMN mods_json VARCHAR NOT NULL DEFAULT ''"
            ))
            conn.commit()
        # scenario display name for the edit wizard (issue #59); empty = show
        # the raw scenarioId for templates saved before this
        if "scenario_name" not in tcols:
            conn.execute(text(
                "ALTER TABLE template ADD COLUMN scenario_name VARCHAR NOT NULL DEFAULT ''"
            ))
            conn.commit()
        # the scenario's declared player count (issue #65); NULL = unknown, which
        # just means the wizard shows no "recommended" hint for that template
        if "scenario_player_count" not in tcols:
            conn.execute(text(
                "ALTER TABLE template ADD COLUMN scenario_player_count INTEGER"
            ))
            conn.commit()
        # hand-edited config overlay (issue #29); '{}' = no custom keys, which is
        # what every template predating the JSON editor has
        if "extras_json" not in tcols:
            conn.execute(text(
                "ALTER TABLE template ADD COLUMN extras_json VARCHAR NOT NULL DEFAULT '{}'"
            ))
            conn.commit()
