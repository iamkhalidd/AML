"""Daily audit chain digests, kept outside the tenant databases (ADR-006, NFR-10).

Revision ID: c0002
Revises: c0001
Create Date: 2026-11-02

Each day the verification job records how far each tenant's chain reached and the hash
there. Rewriting a tenant's audit chain, even consistently, then no longer matches what
the control plane saw, and these digests also anchor the chain when old months are
removed under the retention rule (AUD-5).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c0002"
down_revision: str | None = "c0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_digest (
          seq bigserial PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenant(id),
          taken_at timestamptz NOT NULL DEFAULT now(),
          -- Last entry seen and its hash; 0 and NULL for an empty chain.
          last_seq bigint NOT NULL,
          last_hash bytea,
          ok boolean NOT NULL,
          problem text,
          CHECK (ok = (problem IS NULL))
        );
        CREATE INDEX audit_digest_tenant ON audit_digest (tenant_id, taken_at);

        CREATE FUNCTION audit_digest_block_change() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_digest is append-only';
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER audit_digest_append_only
          BEFORE UPDATE OR DELETE ON audit_digest
          FOR EACH STATEMENT EXECUTE FUNCTION audit_digest_block_change();
        CREATE TRIGGER audit_digest_no_truncate
          BEFORE TRUNCATE ON audit_digest
          FOR EACH STATEMENT EXECUTE FUNCTION audit_digest_block_change();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE audit_digest;
        DROP FUNCTION audit_digest_block_change();
        """
    )
