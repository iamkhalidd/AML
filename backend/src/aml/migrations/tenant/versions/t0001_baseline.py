"""Tenant baseline: extensions and the identity row that names the institution.

Revision ID: t0001
Revises:
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE EXTENSION IF NOT EXISTS pgcrypto;

        -- Exactly one row: which institution this database belongs to. Every tenant
        -- connection can check it before touching data (tenant isolation, ACC-1).
        CREATE TABLE tenant_identity (
          singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
          slug text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE tenant_identity;")
