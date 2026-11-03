"""Background jobs worked by lane (ADR-004, RUL-6).

Revision ID: t0007
Revises: t0006
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0007"
down_revision: str | None = "t0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- Finished jobs are kept: queue-to-done times are the measured evaluation
        -- timeframe shown to the institution (RUL-6).
        CREATE TABLE job (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          lane text NOT NULL
            CHECK (lane IN ('ingest', 'evaluate', 'scheduled_rule', 'report', 'notification')),
          job_type text NOT NULL,
          payload jsonb NOT NULL,
          priority smallint NOT NULL DEFAULT 5,
          status text NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'done', 'failed')),
          attempts int NOT NULL DEFAULT 0,
          max_attempts int NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
          run_after timestamptz NOT NULL DEFAULT now(),
          dedupe_key text UNIQUE,
          locked_by text,
          locked_at timestamptz,
          last_error text,
          created_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          CHECK ((status IN ('done', 'failed')) = (finished_at IS NOT NULL))
        );
        CREATE INDEX job_ready ON job (lane, status, priority, run_after);
        CREATE INDEX job_finished ON job (lane, finished_at) WHERE status = 'done';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE job;")
