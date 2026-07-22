"""Regression tests for schema migrations in models._migrate (issue #127).

A database created before #88 physically keeps an orphaned `container_id`
column on `instance` with its original NOT NULL constraint and no default.
Because create_all() never alters an existing table, new inserts — which no
longer supply that column — used to fail with
"NOT NULL constraint failed: instance.container_id". The migration drops the
orphan so instance creation works again.
"""
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

import models

# The instance table exactly as a pre-#88 database carries it: container_id is
# present, NOT NULL and without a default. Deliberately missing the columns the
# additive migrations add (auto_start, restart_schedule_json, ...), so the test
# also proves those run before the rebuild path copies rows.
_PRE88_SCHEMA = """
CREATE TABLE template (
    id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
    description VARCHAR NOT NULL DEFAULT '', config_json VARCHAR NOT NULL DEFAULT '{}',
    created_at DATETIME, updated_at DATETIME
);
CREATE TABLE instance (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE,
    template_id INTEGER,
    branch VARCHAR,
    game_port INTEGER,
    a2s_port INTEGER,
    rcon_port INTEGER,
    container_id VARCHAR NOT NULL,
    desired_state VARCHAR,
    auto_restart BOOLEAN,
    created_at DATETIME
);
INSERT INTO template (id, name, config_json, created_at, updated_at)
VALUES (9, 'Tuba', '{}', '2026-07-19 10:00:00', '2026-07-19 10:00:00');
INSERT INTO instance
    (name, template_id, branch, game_port, a2s_port, rcon_port,
     container_id, desired_state, auto_restart, created_at)
VALUES ('Tuba Server Instance 1', 9, 'stable', 2001, 17777, 19999,
        'abc123', 'stopped', 1, '2026-07-19 10:00:00');
"""


def _make_pre88_engine():
    """A fresh SQLite engine on a pre-#88 database file, isolated from the suite."""
    db = Path(tempfile.mkdtemp(prefix="rsm-mig-")) / "manager.db"
    con = sqlite3.connect(db)
    con.executescript(_PRE88_SCHEMA)
    con.commit()
    con.close()
    return create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})


def _init_like_startup(engine):
    """Replicate models.init_db() against an arbitrary engine (create_all + migrate)."""
    SQLModel.metadata.create_all(engine)  # existing tables are left untouched
    models._migrate(engine)


def test_orphaned_container_id_is_dropped_and_insert_works():
    engine = _make_pre88_engine()
    _init_like_startup(engine)

    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(instance)"))}
    assert "container_id" not in cols, "legacy container_id column should be gone"

    # The exact operation that failed in #127: create a second instance.
    with Session(engine) as s:
        s.add(models.Instance(
            name="Tuba Server Instance 2", template_id=9, branch="stable",
            game_port=2003, a2s_port=17779, rcon_port=20000,
        ))
        s.commit()
        names = [r.name for r in s.exec(select(models.Instance)).all()]

    assert names == ["Tuba Server Instance 1", "Tuba Server Instance 2"]


def test_existing_rows_survive_migration():
    engine = _make_pre88_engine()
    _init_like_startup(engine)

    with Session(engine) as s:
        keep = s.exec(
            select(models.Instance).where(models.Instance.name == "Tuba Server Instance 1")
        ).one()
    # Original data preserved, and the added auto_start defaults sensibly.
    assert keep.game_port == 2001
    assert keep.a2s_port == 17777
    assert keep.rcon_port == 19999
    assert keep.auto_restart is True


def test_migration_is_idempotent():
    engine = _make_pre88_engine()
    _init_like_startup(engine)
    # A second startup must be a harmless no-op (container_id already gone).
    _init_like_startup(engine)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(instance)"))}
    assert "container_id" not in cols


def test_fallback_rebuild_when_drop_column_unavailable(monkeypatch):
    """Older SQLite (< 3.35) has no DROP COLUMN; the rebuild path must self-heal."""
    engine = _make_pre88_engine()

    real_connect = engine.connect

    class _NoDropColumn:
        """Wraps a connection so any DROP COLUMN raises, as ancient SQLite would."""
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, statement, *args, **kwargs):
            if "DROP COLUMN" in str(statement):
                raise RuntimeError("simulated: SQLite < 3.35 has no DROP COLUMN")
            return self._conn.execute(statement, *args, **kwargs)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

    monkeypatch.setattr(engine, "connect", lambda: _NoDropColumn(real_connect()))
    models.SQLModel.metadata.create_all(engine)
    models._migrate(engine)
    monkeypatch.setattr(engine, "connect", real_connect)

    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(instance)"))}
        leftovers = [
            r[0] for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE name LIKE '%legacy%'")
            )
        ]
    assert "container_id" not in cols
    assert leftovers == [], "the temporary rebuild table must be cleaned up"

    with Session(engine) as s:
        s.add(models.Instance(
            name="Rebuilt", template_id=9, branch="stable",
            game_port=2003, a2s_port=17779, rcon_port=20000,
        ))
        s.commit()
        assert s.exec(select(models.Instance)).all()  # insert succeeded
