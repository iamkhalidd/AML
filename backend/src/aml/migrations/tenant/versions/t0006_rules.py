"""Rule instances and their approved, versioned parameters (RUL-2, RUL-3, RUL-5).

Revision ID: t0006
Revises: t0005
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0006"
down_revision: str | None = "t0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- One configured rule, such as "R01 for individuals". It runs only when enabled
        -- and an approved version is in effect.
        CREATE TABLE rule_instance (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          rule_type text NOT NULL,
          name text NOT NULL CHECK (btrim(name) <> ''),
          scope jsonb NOT NULL DEFAULT '{}',
          enabled boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        -- Every change is a new version, never an update. A version counts only once a
        -- second person approves it, and never before its effective_from.
        CREATE TABLE rule_version (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          rule_instance_id uuid NOT NULL REFERENCES rule_instance(id),
          version int NOT NULL CHECK (version > 0),
          parameters jsonb NOT NULL,
          severity risk_level NOT NULL,
          preemptive boolean NOT NULL DEFAULT false,
          effective_from timestamptz NOT NULL,
          rationale text NOT NULL CHECK (btrim(rationale) <> ''),
          created_by uuid NOT NULL REFERENCES app_user(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          approval_request_id uuid REFERENCES approval_request(id),
          approved_by uuid REFERENCES app_user(id),
          approved_at timestamptz,
          UNIQUE (rule_instance_id, version),
          CHECK (approved_by IS NULL OR approved_by <> created_by),
          CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
          CHECK (approved_at IS NULL OR effective_from >= approved_at)
        );
        CREATE INDEX rule_version_in_effect
          ON rule_version (rule_instance_id, effective_from) WHERE approved_at IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE rule_version;
        DROP TABLE rule_instance;
        """
    )
