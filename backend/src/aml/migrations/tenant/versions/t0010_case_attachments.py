"""Case attachments, limited by type and size (CAS-2).

Revision ID: t0010
Revises: t0009
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0010"
down_revision: str | None = "t0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- Kept in the database so the backend needs no disk of its own. Attachments are
        -- evidence: like the timeline, they are never changed or removed.
        CREATE TABLE case_attachment (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL REFERENCES aml_case(id),
          filename text NOT NULL CHECK (btrim(filename) <> ''),
          content_type text NOT NULL,
          size_bytes int NOT NULL CHECK (size_bytes > 0),
          sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
          content bytea NOT NULL,
          uploaded_by uuid NOT NULL REFERENCES app_user(id),
          uploaded_at timestamptz NOT NULL DEFAULT now(),
          CHECK (octet_length(content) = size_bytes)
        );
        CREATE INDEX case_attachment_case ON case_attachment (case_id, uploaded_at);

        CREATE TRIGGER case_attachment_immutable BEFORE UPDATE OR DELETE ON case_attachment
          FOR EACH ROW EXECUTE FUNCTION case_entry_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE case_attachment;")
