"""Alerts raised by rules, their transactions and their history (AUD-7, ALT-1 to ALT-4).

Revision ID: t0008
Revises: t0007
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0008"
down_revision: str | None = "t0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE alert_status AS ENUM ('open', 'in_review', 'escalated', 'closed');

        -- One alert names the rule version and the customer record version it was raised
        -- on, so a trace can show exactly what fired and on what data (AUD-7).
        CREATE TABLE alert (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          rule_instance_id uuid NOT NULL REFERENCES rule_instance(id),
          rule_version_id uuid NOT NULL REFERENCES rule_version(id),
          customer_id uuid NOT NULL REFERENCES customer(id),
          customer_version int NOT NULL,
          account_id uuid REFERENCES account(id),
          severity risk_level NOT NULL,
          status alert_status NOT NULL DEFAULT 'open',
          assigned_to uuid REFERENCES app_user(id),
          explanation text NOT NULL,
          trigger_values jsonb NOT NULL,
          window_start timestamptz,
          window_end timestamptz,
          -- The rule, customer and window; while an alert with this key is not closed, a
          -- new finding adds its evidence to that alert instead of raising another.
          dedupe_key text NOT NULL,
          hits int NOT NULL DEFAULT 1 CHECK (hits > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          closed_at timestamptz,
          closed_by uuid REFERENCES app_user(id),
          closure_reason text,
          closure_note text,
          closure_mode text NOT NULL DEFAULT 'manual' CHECK (closure_mode IN ('manual', 'auto')),
          FOREIGN KEY (customer_id, customer_version)
            REFERENCES customer_version (customer_id, version),
          CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
          CHECK (status <> 'closed' OR closure_reason IS NOT NULL),
          CHECK (window_start IS NULL OR window_end IS NULL OR window_start <= window_end)
        );
        CREATE UNIQUE INDEX alert_open_dedupe ON alert (dedupe_key) WHERE status <> 'closed';
        CREATE INDEX alert_queue ON alert (status, severity, created_at);
        CREATE INDEX alert_customer ON alert (customer_id, created_at);
        CREATE INDEX alert_assignee ON alert (assigned_to, status) WHERE assigned_to IS NOT NULL;

        CREATE TABLE alert_txn (
          alert_id uuid NOT NULL REFERENCES alert(id),
          txn_id uuid NOT NULL REFERENCES txn(id),
          added_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (alert_id, txn_id)
        );
        CREATE INDEX alert_txn_txn ON alert_txn (txn_id);

        -- The alert's own timeline: created, evidence added, assigned, closed. Entries are
        -- never edited; the audit store holds the same changes in its hash chain.
        CREATE TABLE alert_event (
          id bigserial PRIMARY KEY,
          alert_id uuid NOT NULL REFERENCES alert(id),
          at timestamptz NOT NULL DEFAULT now(),
          user_id uuid REFERENCES app_user(id),
          event_type text NOT NULL,
          detail jsonb NOT NULL DEFAULT '{}'
        );
        CREATE INDEX alert_event_alert ON alert_event (alert_id, at);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE alert_event;
        DROP TABLE alert_txn;
        DROP TABLE alert;
        DROP TYPE alert_status;
        """
    )
