"""Tenant registry and append-only control event log.

Revision ID: c0001
Revises:
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenant (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9_]{2,30}$'),
          name text NOT NULL,
          database_name text NOT NULL UNIQUE,
          database_role text NOT NULL UNIQUE,
          status text NOT NULL DEFAULT 'provisioning'
            CHECK (status IN ('provisioning', 'active', 'suspended', 'failed', 'removed')),
          schema_version text,
          migrated_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE control_event (
          seq bigserial PRIMARY KEY,
          at timestamptz NOT NULL DEFAULT now(),
          actor text NOT NULL,
          tenant_id uuid REFERENCES tenant(id),
          action text NOT NULL,
          detail jsonb NOT NULL DEFAULT '{}'
        );
        CREATE INDEX control_event_tenant ON control_event (tenant_id, at);

        CREATE FUNCTION control_event_block_change() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'control_event is append-only';
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER control_event_append_only
          BEFORE UPDATE OR DELETE ON control_event
          FOR EACH STATEMENT EXECUTE FUNCTION control_event_block_change();
        CREATE TRIGGER control_event_no_truncate
          BEFORE TRUNCATE ON control_event
          FOR EACH STATEMENT EXECUTE FUNCTION control_event_block_change();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE control_event;
        DROP FUNCTION control_event_block_change();
        DROP TABLE tenant;
        """
    )
