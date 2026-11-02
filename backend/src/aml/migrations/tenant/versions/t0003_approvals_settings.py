"""Maker-checker approval requests and versioned settings (ACC-4, RUL-5).

Revision ID: t0003
Revises: t0002
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0003"
down_revision: str | None = "t0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE approval_kind AS ENUM ('rule_change', 'case_closure', 'report_submission');
        CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected', 'withdrawn');

        -- One change waiting for a second person. The change takes effect only on approval.
        CREATE TABLE approval_request (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          kind approval_kind NOT NULL,
          subject_id text NOT NULL,
          payload jsonb NOT NULL DEFAULT '{}',
          rationale text NOT NULL CHECK (btrim(rationale) <> ''),
          status approval_status NOT NULL DEFAULT 'pending',
          maker_id uuid NOT NULL REFERENCES app_user(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          checker_id uuid REFERENCES app_user(id),
          decided_at timestamptz,
          decision_note text,
          -- The application checks these too; the database is the last defence.
          CHECK (checker_id IS NULL OR checker_id <> maker_id),
          CHECK ((status = 'pending') = (decided_at IS NULL)),
          CHECK (status NOT IN ('approved', 'rejected') OR checker_id IS NOT NULL)
        );
        CREATE UNIQUE INDEX approval_request_one_pending
          ON approval_request (kind, subject_id) WHERE status = 'pending';
        CREATE INDEX approval_request_queue ON approval_request (status, created_at);

        -- Institution settings. A change is a new version, never an update.
        CREATE TABLE setting_version (
          key text NOT NULL,
          version int NOT NULL CHECK (version > 0),
          value jsonb NOT NULL,
          effective_from timestamptz NOT NULL DEFAULT now(),
          rationale text NOT NULL CHECK (btrim(rationale) <> ''),
          created_by uuid NOT NULL REFERENCES app_user(id),
          approved_by uuid REFERENCES app_user(id),
          PRIMARY KEY (key, version),
          CHECK (approved_by IS NULL OR approved_by <> created_by)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE setting_version;
        DROP TABLE approval_request;
        DROP TYPE approval_status;
        DROP TYPE approval_kind;
        """
    )
