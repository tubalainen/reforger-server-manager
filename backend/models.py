"""SQLite persistence (SQLModel): server templates, instances, port leases."""
from datetime import UTC, datetime
from pathlib import Path

from pydantic import NaiveDatetime
from sqlalchemy import UniqueConstraint
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
    # Release stage (#223): test | dev | prod. Prod is locked: its content
    # columns only ever change by a release, and then always equal its latest
    # TemplateVersion. New rows start in test; the upgrade put existing ones in dev.
    stage: str = "test"
    prod_version: int | None = None  # latest released version; None unless prod
    # The Prod template this one was copied from (no FK: the source may be
    # deleted), so a release can default to "next version of" it.
    based_on_id: int | None = None
    based_on_version: int | None = None
    # Game-mode category (#223), managed under System; None = Uncategorised.
    # Only a label: changing it never changes what a server runs.
    category_id: int | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TemplateCategory(SQLModel, table=True):
    """A game mode a server template can be filed under (#223): Conflict, HQC, …

    A few come built in and are added once; after that they are the user's to
    rename or delete like any other. Deleting one leaves its templates
    Uncategorised.
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=_utcnow)


# The categories a new install starts with (#223).
BUILTIN_CATEGORIES = (
    "Game Master", "HQC", "Conflict", "COE", "Escapist", "Freedom Fighters",
)
_CATEGORIES_SEEDED = "template_categories"


class TemplateVersion(SQLModel, table=True):
    """A frozen copy of a Prod template as it was released (#223).

    Written once per release and never changed. The content columns carry the
    same names as Template's so every reader of a template accepts a version
    row as it is.
    """

    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: int | None = Field(default=None, primary_key=True)
    template_id: int = Field(index=True, foreign_key="template.id")
    version: int
    released_at: datetime = Field(default_factory=_utcnow)
    note: str = ""
    description: str = ""
    config_json: str
    scenario_name: str = ""
    scenario_player_count: int | None = None
    launch_params_json: str = "{}"
    mods_json: str = ""
    extras_json: str = "{}"


class ModRegistryEntry(SQLModel, table=True):
    """One mod in the persistent "Mods Overview" registry (#131).

    The registry is the union of every mod that has ever been baked into a
    server template: a mod is upserted here whenever a template that lists it is
    created or saved, and it stays even after that template (or the mod's use in
    it) is gone — the "once added, always on the overview" rule. A rescan clears
    the entries no template still references; a `persist` entry is exempt and can
    never be pruned or deleted until the user clears the flag.

    This table holds only what the overview must remember on its own: the mod id,
    a best-known display name, the persist flag, and when it was first/last seen.
    Which templates and instances currently carry a mod (and at what version) is
    derived live from the templates on every read, never stored here.
    """

    id: int | None = Field(default=None, primary_key=True)
    mod_id: str = Field(index=True, unique=True)  # 16-hex Workshop id, upper-cased
    name: str = ""
    # User-pinned: excluded from clear/rescan pruning and refused by delete (#131).
    persist: bool = False
    first_added_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)


class ModTemplate(SQLModel, table=True):
    """A reusable, named mod list that can be loaded into a server template (#166).

    Deliberately *only* mods: no scenario, no settings, nothing a server needs to
    run. It is a shelf you keep a set of mods on ("our milsim pack") and load into
    the Mods step of any server template, and it carries its own change log so the
    history of that set is as auditable as a server template's.

    mods_json holds the same enriched entries as Template.mods_json (modId, name,
    version lock, published versions, …) in the same meaning: the array order IS
    the load order (#164), and it is preserved when the list is loaded into a
    template. Every entry is an explicit addon — a mod template resolves no
    dependency trees, exactly like adding one mod in the wizard (#97).
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    mods_json: str = "[]"
    # Release stage, exactly as on Template (#223).
    stage: str = "test"
    prod_version: int | None = None
    based_on_id: int | None = None
    based_on_version: int | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ModTemplateVersion(SQLModel, table=True):
    """A frozen copy of a Prod mod list as it was released (#223)."""

    __table_args__ = (UniqueConstraint("mod_template_id", "version"),)

    id: int | None = Field(default=None, primary_key=True)
    mod_template_id: int = Field(index=True, foreign_key="modtemplate.id")
    version: int
    released_at: datetime = Field(default_factory=_utcnow)
    note: str = ""
    description: str = ""
    mods_json: str = "[]"


class AppSetting(SQLModel, table=True):
    """A named JSON blob of manager-wide settings, one row per feature (#177).

    Everything else in this database belongs to a template or an instance; this
    is for the handful of settings that belong to the manager itself — the first
    of them being the automatic server-files update. Kept as opaque JSON on
    purpose: a new toggle is a key in the blob, not a migration.
    """

    key: str = Field(primary_key=True)
    value: str = ""  # JSON object; readers tolerate anything unparseable
    updated_at: datetime = Field(default_factory=_utcnow)


class Instance(SQLModel, table=True):
    """A concrete server: a template bound to a branch, ports and a container."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    # None: its template was deleted (#223). It keeps its data and settings but
    # cannot start until a template is chosen in its Settings.
    template_id: int | None = Field(default=None, foreign_key="template.id")
    # A Prod template's version this server is pinned to (#223); None = latest.
    template_version: int | None = None
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
    # Naive on purpose: local wall-clock time, compared with a naive
    # datetime.now(). sqlmodel >= 0.0.47 refuses naive values in a plain
    # `datetime` column and would store an aware one as UTC, which would shift
    # the schedule by the timezone offset.
    last_scheduled_restart: NaiveDatetime | None = None
    # Let a scheduled restart move the template's mods to their newest Workshop
    # release (#246). Every other start keeps the versions on disk.
    mod_auto_update: bool = False
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
    category: str = "setting"  # meta | scenario | mod | setting | note | release
    summary: str = ""


class ModTemplateChange(SQLModel, table=True):
    """One immutable audit line in a mod template's change log (#166).

    The same append-only contract as TemplateChange: written by the mod-template
    create/update handlers, read by the change-log view, and removed only when the
    mod template itself is deleted. Kept as its own table rather than reusing
    TemplateChange because that one's template_id is a foreign key to a server
    template — a mod template is a different thing.
    """

    id: int | None = Field(default=None, primary_key=True)
    mod_template_id: int = Field(index=True, foreign_key="modtemplate.id")
    changed_at: datetime = Field(default_factory=_utcnow, index=True)
    category: str = "mod"  # meta | mod | note | release
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
    seed_template_categories(engine)


def seed_template_categories(engine) -> int:
    """Add the built-in categories once (#223). A flag remembers it was done, so
    a built-in the user deleted is not brought back on the next start; a name
    that already exists is left alone. Returns how many were added."""
    import json

    from sqlmodel import Session, select

    with Session(engine) as session:
        if session.get(AppSetting, _CATEGORIES_SEEDED):
            return 0
        taken = {n.lower() for n in session.exec(select(TemplateCategory.name)).all()}
        added = [n for n in BUILTIN_CATEGORIES if n.lower() not in taken]
        for name in added:
            session.add(TemplateCategory(name=name))
        session.add(AppSetting(key=_CATEGORIES_SEEDED, value=json.dumps({"seeded": True})))
        session.commit()
        return len(added)


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
        # mod updates on the scheduled restart (#246). Before it, every start
        # took the newest mods, so a server that already restarts daily keeps
        # updating at that restart; the others now keep what is on disk.
        if "mod_auto_update" not in icols:
            conn.execute(text(
                "ALTER TABLE instance ADD COLUMN mod_auto_update BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.execute(text(
                "UPDATE instance SET mod_auto_update = 1 WHERE restart_schedule_json != ''"
            ))
            conn.commit()
        # pinned Prod template version (#223); NULL = run the latest
        if "template_version" not in icols:
            conn.execute(text("ALTER TABLE instance ADD COLUMN template_version INTEGER"))
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
        # release stages (#223). Templates and mod lists that exist before this
        # land in dev: editable, but nothing is called golden until it's released.
        # New rows always state their stage, so the default only reaches these.
        for table, cols in (
            ("template", tcols),
            ("modtemplate", {row[1] for row in conn.execute(text("PRAGMA table_info(modtemplate)"))}),
        ):
            if "stage" not in cols:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'dev'"
                ))
                conn.commit()
            for col in ("prod_version", "based_on_id", "based_on_version"):
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER"))
                    conn.commit()
        # game-mode category (#223); NULL = Uncategorised
        if "category_id" not in tcols:
            conn.execute(text("ALTER TABLE template ADD COLUMN category_id INTEGER"))
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
                _rebuild_instance(conn, "instance_legacy_pre88")
                conn.commit()

        # A server can be left without a template: deleting the template it
        # uses unlinks it (#223). template_id was NOT NULL until then, and
        # SQLite cannot loosen that in place, so the table is rebuilt once.
        notnull = {
            row[1]: row[3] for row in conn.execute(text("PRAGMA table_info(instance)"))
        }
        if notnull.get("template_id"):
            _rebuild_instance(conn, "instance_legacy_pre223")
            conn.commit()


def _rebuild_instance(conn, legacy: str) -> None:
    """Rebuild `instance` from the current model definition, copying every
    column the old and new tables share.

    For what SQLite's ALTER TABLE cannot do: drop a column before 3.35 (the
    orphaned `container_id`, #127), or drop a NOT NULL (`template_id`, #223).
    No other table has a foreign key to `instance`, so renaming it is safe.
    """
    from sqlalchemy import text

    old_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(instance)"))}
    new_cols = [c.name for c in Instance.__table__.columns]
    shared = [c for c in new_cols if c in old_cols]
    col_list = ", ".join(f'"{c}"' for c in shared)

    conn.execute(text(f"ALTER TABLE instance RENAME TO {legacy}"))
    # Index names are global in SQLite and move with the renamed table; the new
    # table's own (ix_instance_name, …) cannot be created while they exist.
    for (index,) in conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"
    ), {"t": legacy}).all():
        conn.execute(text(f'DROP INDEX "{index}"'))
    Instance.__table__.create(bind=conn)
    conn.execute(text(
        f"INSERT INTO instance ({col_list}) SELECT {col_list} FROM {legacy}"
    ))
    conn.execute(text(f"DROP TABLE {legacy}"))
