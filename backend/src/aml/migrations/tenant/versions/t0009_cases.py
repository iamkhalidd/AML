"""Cases built from escalated alerts, with an append-only timeline (CAS-1 to CAS-5).

Revision ID: t0009
Revises: t0008
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0009"
down_revision: str | None = "t0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE case_status AS ENUM ('open', 'closed');
        CREATE TYPE case_outcome AS ENUM ('no_suspicion', 'reported', 'referred');
        CREATE SEQUENCE case_reference_seq;

        -- One investigation into one customer. It closes only with an outcome, a written
        -- reason and a second person's approval (CAS-3, CAS-4).
        CREATE TABLE aml_case (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          reference text NOT NULL UNIQUE
            DEFAULT ('CASE-' || lpad(nextval('case_reference_seq')::text, 6, '0')),
          customer_id uuid NOT NULL REFERENCES customer(id),
          title text NOT NULL CHECK (btrim(title) <> ''),
          status case_status NOT NULL DEFAULT 'open',
          assigned_to uuid REFERENCES app_user(id),
          opened_by uuid NOT NULL REFERENCES app_user(id),
          opened_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          outcome case_outcome,
          outcome_reason text,
          closure_requested_by uuid REFERENCES app_user(id),
          closure_approved_by uuid REFERENCES app_user(id),
          closure_approval_id uuid REFERENCES approval_request(id),
          closed_at timestamptz,
          CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
          CHECK (status <> 'closed' OR (outcome IS NOT NULL AND btrim(outcome_reason) <> ''
                 AND closure_approved_by IS NOT NULL)),
          CHECK (closure_approved_by IS NULL OR closure_approved_by <> closure_requested_by)
        );
        CREATE INDEX aml_case_queue ON aml_case (status, opened_at);
        CREATE INDEX aml_case_customer ON aml_case (customer_id, opened_at);
        CREATE INDEX aml_case_assignee ON aml_case (assigned_to, status)
          WHERE assigned_to IS NOT NULL;

        -- An alert belongs to at most one case.
        CREATE TABLE case_alert (
          case_id uuid NOT NULL REFERENCES aml_case(id),
          alert_id uuid NOT NULL UNIQUE REFERENCES alert(id),
          linked_at timestamptz NOT NULL DEFAULT now(),
          linked_by uuid NOT NULL REFERENCES app_user(id),
          PRIMARY KEY (case_id, alert_id)
        );

        -- The case timeline. Entries are never changed or removed; a correction is a new
        -- entry that names the one it corrects (CAS-5). The trigger enforces it.
        CREATE TABLE case_entry (
          id bigserial PRIMARY KEY,
          case_id uuid NOT NULL REFERENCES aml_case(id),
          at timestamptz NOT NULL DEFAULT now(),
          user_id uuid REFERENCES app_user(id),
          entry_type text NOT NULL,
          body text,
          detail jsonb NOT NULL DEFAULT '{}',
          corrects bigint REFERENCES case_entry(id),
          CHECK (corrects IS NULL OR entry_type = 'correction')
        );
        CREATE INDEX case_entry_case ON case_entry (case_id, at, id);

        CREATE FUNCTION case_entry_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'case timeline entries cannot be changed or removed'
            USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER case_entry_immutable BEFORE UPDATE OR DELETE ON case_entry
          FOR EACH ROW EXECUTE FUNCTION case_entry_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER case_entry_immutable ON case_entry;
        DROP FUNCTION case_entry_immutable();
        DROP TABLE case_entry;
        DROP TABLE case_alert;
        DROP TABLE aml_case;
        DROP SEQUENCE case_reference_seq;
        DROP TYPE case_outcome;
        DROP TYPE case_status;
        """
    )
