"""Integration API credentials (API specification: authentication; threat T05).

Revision ID: t0005
Revises: t0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0005"
down_revision: str | None = "t0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- A bearer credential for the institution's systems. Only the SHA-256 of the
        -- token is stored; the token itself is shown once, when it is created.
        CREATE TABLE api_credential (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
          token_hash bytea NOT NULL UNIQUE,
          scopes text[] NOT NULL CHECK (cardinality(scopes) > 0 AND scopes <@ ARRAY[
            'ingest:write', 'checks:invoke', 'alerts:read', 'webhooks:manage']::text[]),
          -- Empty means any address.
          allowed_ips cidr[] NOT NULL DEFAULT '{}',
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          last_used_at timestamptz,
          CHECK (expires_at > created_at)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE api_credential;")
