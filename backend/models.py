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


class TemplateChange(SQLModel, table=True):
    """One immutable audit line in a template's change log (#112).

    Append-only: written by the template create/update handlers, read by the
    change-log view, and removed only when the template itself is deleted.
    Nothing in the API mutates or deletes an individual row, so the log can't
    be altered or trimmed by the user. Rows from one save event share a
    changed_at so the UI can group them.
    """

    id: int | None = Field(default=None, primary_key=True)
    template_id: int = Field(index=True, foreign_key="template.id")
    changed_at: datetime = Field(default_factory=_utcnow, index=True)
    category: str = "setting"  # meta | scenario | mod | setting
    summary: str = ""


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

        # container_id was removed from the Instance model in #88 (containers are
        # located by Docker label now, not a stored id). create_all() never alters
        # an existing table, so databases predating #88 keep the physical column
        # with its original NOT NULL and no default. New inserts no longer supply
        # it, so SQLite substitutes NULL and the insert dies with
        # "NOT NULL constraint failed: instance.container_id" (#127). Drop the
        # orphan so the table matches the model. This runs LAST, after the additive
        # steps above, so the rebuild fallback copies a complete column set.
        # DROP COLUMN needs SQLite >= 3.35 (Python 3.12 bundles far newer); if it is
        # unavailable or blocked, rebuild the table without the column instead so
        # startup still self-heals.
        if "container_id" in icols:
            try:
                conn.execute(text("ALTER TABLE instance DROP COLUMN container_id"))
                conn.commit()
            except Exception:
                conn.rollback()
                _rebuild_instance_without_container_id(conn)
                conn.commit()


def _rebuild_instance_without_container_id(conn) -> None:
    """Fallback for SQLite too old for ALTER TABLE ... DROP COLUMN (< 3.35).

    Rebuilds `instance` from the current model definition and copies over every
    column the old and new tables share, dropping the orphaned `container_id`.
    """
    from sqlalchemy import text

    old_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(instance)"))}
    new_cols = [c.name for c in Instance.__table__.columns]
    shared = [c for c in new_cols if c in old_cols and c != "container_id"]
    col_list = ", ".join(f'"{c}"' for c in shared)

    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(text("ALTER TABLE instance RENAME TO instance_legacy_pre88"))
    Instance.__table__.create(bind=conn)
    conn.execute(text(
        f"INSERT INTO instance ({col_list}) "
        f"SELECT {col_list} FROM instance_legacy_pre88"
    ))
    conn.execute(text("DROP TABLE instance_legacy_pre88"))
    conn.execute(text("PRAGMA foreign_keys=ON"))
