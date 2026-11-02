"""Run Alembic migrations against one database (building block of the ADR-001 fleet tool)."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection, make_url

Tree = Literal["control", "tenant", "audit"]

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def sqlalchemy_url(url: str) -> str:
    """Point a libpq URL at the psycopg 3 driver for SQLAlchemy."""
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _config(tree: Tree) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR / tree))
    return config


def head_revision(tree: Tree) -> str:
    head = ScriptDirectory.from_config(_config(tree)).get_current_head()
    if head is None:
        raise RuntimeError(f"The {tree} migration tree has no revisions")
    return head


def version_schema(tree: Tree) -> str | None:
    """The audit tree keeps its version table in the schema it owns (ADR-006)."""
    return "audit" if tree == "audit" else None


def current_revision(url: str, tree: Tree = "tenant", *, role: str | None = None) -> str | None:
    engine = create_engine(sqlalchemy_url(url), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            _set_role(connection, role)
            opts = {"version_table_schema": version_schema(tree)}
            return MigrationContext.configure(connection, opts=opts).get_current_revision()
    finally:
        engine.dispose()


def upgrade(tree: Tree, url: str, *, role: str | None = None) -> str:
    """Bring the database to the head of its tree and return the revision it ends on.

    ``role``, if given, is assumed with SET ROLE first: the audit tree runs as the role
    that owns the audit schema, not as the login that connects.
    """
    return _run(tree, url, role, lambda config: command.upgrade(config, "head"))


def downgrade(tree: Tree, url: str, revision: str, *, role: str | None = None) -> str:
    """Step back to an older revision. Used by tests and by rehearsed rollbacks only."""
    return _run(tree, url, role, lambda config: command.downgrade(config, revision))


def _set_role(connection: Connection, role: str | None) -> None:
    if role is not None:
        connection.exec_driver_sql("SELECT set_config('role', %s, false)", (role,))


def _run(tree: Tree, url: str, role: str | None, action: Callable[[Config], None]) -> str:
    config = _config(tree)
    engine = create_engine(sqlalchemy_url(url), poolclass=pool.NullPool)
    try:
        with engine.begin() as connection:
            _set_role(connection, role)
            config.attributes["connection"] = connection
            action(config)
            opts = {"version_table_schema": version_schema(tree)}
            revision = MigrationContext.configure(connection, opts=opts).get_current_revision()
    finally:
        engine.dispose()
    return revision or "base"
