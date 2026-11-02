"""Alembic environment for the audit store (ADR-006).

The fleet tool connects as the provisioner and assumes the tenant's audit role, which
owns the audit schema. The tenant's own role, used by the application, never owns
anything here, so it cannot alter, disable or drop what this tree creates.
"""

from alembic import context
from sqlalchemy.engine import Connection

connection: Connection = context.config.attributes["connection"]
connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS audit")
owner = connection.exec_driver_sql(
    "SELECT nspowner::regrole::text = current_user FROM pg_namespace WHERE nspname = 'audit'"
).scalar()
if not owner:
    # Someone else created the schema first; refuse rather than build on their objects.
    raise RuntimeError("The audit schema must be owned by the tenant's audit role")
context.configure(
    connection=connection,
    target_metadata=None,
    transaction_per_migration=True,
    version_table_schema="audit",
)
with context.begin_transaction():
    context.run_migrations()
