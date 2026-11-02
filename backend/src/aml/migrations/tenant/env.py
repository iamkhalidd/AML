"""Alembic environment. The fleet tool passes an open connection in config.attributes."""

from alembic import context
from sqlalchemy.engine import Connection

connection: Connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
with context.begin_transaction():
    context.run_migrations()
