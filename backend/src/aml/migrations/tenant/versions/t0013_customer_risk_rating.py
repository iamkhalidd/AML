"""Manual customer risk rating with a rationale and date (CUS-2).

Revision ID: t0013
Revises: t0012
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0013"
down_revision: str | None = "t0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- The feed keeps writing risk_level; a manual rating sits beside it and wins, so a
        -- later feed update never undoes a compliance decision.
        ALTER TABLE customer
          ADD COLUMN manual_risk_level risk_level,
          ADD COLUMN manual_risk_rated_at timestamptz,
          ADD CHECK ((manual_risk_level IS NULL) = (manual_risk_rated_at IS NULL));

        -- Every rating ever given, newest last. Like the case timeline, never changed.
        CREATE TABLE customer_risk_rating (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          customer_id uuid NOT NULL REFERENCES customer(id),
          risk_level risk_level NOT NULL,
          previous_level risk_level,
          rationale text NOT NULL CHECK (btrim(rationale) <> ''),
          rated_by uuid NOT NULL REFERENCES app_user(id),
          rated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX customer_risk_rating_customer ON customer_risk_rating (customer_id, rated_at);

        CREATE TRIGGER customer_risk_rating_immutable BEFORE UPDATE OR DELETE
          ON customer_risk_rating FOR EACH ROW EXECUTE FUNCTION case_entry_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE customer_risk_rating;
        ALTER TABLE customer DROP COLUMN manual_risk_level, DROP COLUMN manual_risk_rated_at;
        """
    )
