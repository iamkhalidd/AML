"""Run Alembic migrations against one database (building block of the ADR-001 fleet tool)."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

Tree = Literal["control", "tenant"]

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


def current_revision(url: str) -> str | None:
    engine = create_engine(sqlalchemy_url(url), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def upgrade(tree: Tree, url: str) -> str:
    """Bring the database to the head of its tree and return the revision it ends on."""
    return _run(tree, url, lambda config: command.upgrade(config, "head"))


def downgrade(tree: Tree, url: str, revision: str) -> str:
    """Step back to an older revision. Used by tests and by rehearsed rollbacks only."""
    return _run(tree, url, lambda config: command.downgrade(config, revision))


def _run(tree: Tree, url: str, action: Callable[[Config], None]) -> str:
    config = _config(tree)
    engine = create_engine(sqlalchemy_url(url), poolclass=pool.NullPool)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            action(config)
            revision = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    return revision or "base"
