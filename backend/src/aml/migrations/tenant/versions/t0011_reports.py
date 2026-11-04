"""Suspicious and currency transaction report drafts (REP-1, REP-2).

Revision ID: t0011
Revises: t0010
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0011"
down_revision: str | None = "t0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE report_type AS ENUM ('str', 'ctr');
        CREATE TYPE report_status AS ENUM ('draft', 'approved', 'submitted');
        CREATE SEQUENCE report_reference_seq;

        -- Content is in an internal format until the NFIU format is confirmed (REP-5).
        -- A suspicious report comes from a case; a currency report from one transaction
        -- at or above the threshold. Approval and submission come with REP-3.
        CREATE TABLE report (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          reference text NOT NULL UNIQUE,
          report_type report_type NOT NULL,
          status report_status NOT NULL DEFAULT 'draft',
          case_id uuid REFERENCES aml_case(id),
          customer_id uuid NOT NULL REFERENCES customer(id),
          content jsonb NOT NULL,
          narrative text NOT NULL DEFAULT '',
          created_by uuid NOT NULL REFERENCES app_user(id),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_by uuid NOT NULL REFERENCES app_user(id),
          updated_at timestamptz NOT NULL DEFAULT now(),
          approved_by uuid REFERENCES app_user(id),
          approved_at timestamptz,
          submitted_by uuid REFERENCES app_user(id),
          submitted_at timestamptz,
          submission_reference text,
          CHECK (report_type <> 'str' OR case_id IS NOT NULL),
          CHECK ((approved_at IS NULL) = (approved_by IS NULL)),
          CHECK ((submitted_at IS NULL) = (submitted_by IS NULL)),
          CHECK (status = 'draft' OR approved_at IS NOT NULL),
          CHECK ((status = 'submitted') = (submitted_at IS NOT NULL))
        );
        -- One suspicious report per case.
        CREATE UNIQUE INDEX report_str_case ON report (case_id) WHERE report_type = 'str';
        CREATE INDEX report_list ON report (report_type, status, created_at);
        CREATE INDEX report_customer ON report (customer_id, created_at);

        CREATE TABLE report_txn (
          report_id uuid NOT NULL REFERENCES report(id),
          txn_id uuid NOT NULL REFERENCES txn(id),
          report_type report_type NOT NULL,
          PRIMARY KEY (report_id, txn_id)
        );
        -- A transaction goes on one currency report only.
        CREATE UNIQUE INDEX report_txn_ctr ON report_txn (txn_id) WHERE report_type = 'ctr';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE report_txn;
        DROP TABLE report;
        DROP SEQUENCE report_reference_seq;
        DROP TYPE report_status;
        DROP TYPE report_type;
        """
    )
