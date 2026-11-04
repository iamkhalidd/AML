"""Email notifications with a per-user opt-out (ADM-3).

Revision ID: t0012
Revises: t0011
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0012"
down_revision: str | None = "t0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- A missing row means the user gets that kind of email.
        CREATE TABLE notification_preference (
          user_id uuid NOT NULL REFERENCES app_user(id),
          kind text NOT NULL CHECK (kind IN ('assignment', 'approval_pending', 'ageing_digest')),
          enabled boolean NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, kind)
        );

        -- One row per email sent or skipped, keyed by the job that sent it so a retried
        -- job never sends twice.
        CREATE TABLE notification_log (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          job_id uuid NOT NULL UNIQUE,
          user_id uuid NOT NULL REFERENCES app_user(id),
          kind text NOT NULL,
          subject text NOT NULL,
          outcome text NOT NULL CHECK (outcome IN ('sent', 'logged', 'opted_out', 'inactive')),
          at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX notification_log_user ON notification_log (user_id, at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE notification_log; DROP TABLE notification_preference;")
