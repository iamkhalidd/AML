"""Customers, accounts, transactions and the ingestion stores (CUS-1, CUS-6, ING-1, ING-3).

Revision ID: t0004
Revises: t0003
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0004"
down_revision: str | None = "t0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- Values are the data contract's, in lower case; the API maps them.
        CREATE TYPE customer_type AS ENUM ('individual', 'business');
        CREATE TYPE kyc_status AS ENUM
          ('not_started', 'pending', 'verified', 'rejected', 'expired');
        CREATE TYPE risk_level AS ENUM ('low', 'medium', 'high');
        CREATE TYPE customer_status AS ENUM ('active', 'dormant', 'closed', 'restricted');
        CREATE TYPE account_status AS ENUM ('active', 'dormant', 'closed', 'frozen');
        CREATE TYPE control_type AS ENUM ('ownership', 'director', 'signatory', 'other');
        CREATE TYPE relationship_type AS ENUM
          ('family', 'employer', 'signatory', 'guarantor', 'other');
        CREATE TYPE txn_direction AS ENUM ('credit', 'debit');
        CREATE TYPE txn_status AS ENUM ('success', 'failed', 'reversed', 'pending');
        CREATE TYPE txn_channel AS ENUM
          ('cash', 'branch', 'atm', 'pos', 'ussd', 'mobile_app', 'web', 'agent', 'transfer',
           'card', 'other');
        CREATE TYPE txn_type AS ENUM
          ('deposit', 'withdrawal', 'transfer', 'bill_payment', 'loan_disbursement',
           'loan_repayment', 'fee', 'reversal', 'interest', 'other');

        -- Business records are never deleted; a change raises version and keeps a snapshot.
        -- Money is BIGINT minor units (kobo); timestamps are timestamptz in UTC.
        CREATE TABLE customer (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_system text NOT NULL,
          external_id text NOT NULL,
          customer_type customer_type NOT NULL,
          full_name text NOT NULL CHECK (btrim(full_name) <> ''),
          date_of_birth date,
          nationality char(2),
          occupation text,
          industry text,
          declared_income_min bigint CHECK (declared_income_min >= 0),
          declared_income_max bigint,
          expected_monthly_credit bigint CHECK (expected_monthly_credit >= 0),
          expected_monthly_credit_count int CHECK (expected_monthly_credit_count >= 0),
          state text,
          country char(2),
          onboarding_date date NOT NULL,
          kyc_status kyc_status NOT NULL,
          kyc_verified_at timestamptz,
          kyc_reference text,
          risk_level risk_level,
          is_pep boolean NOT NULL DEFAULT false,
          segment text,
          status customer_status NOT NULL,
          registration_number text,
          registration_date date,
          version int NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_system, external_id),
          CHECK (declared_income_max IS NULL OR declared_income_min IS NULL
                 OR declared_income_max >= declared_income_min)
        );

        CREATE TABLE customer_version (
          customer_id uuid NOT NULL REFERENCES customer(id),
          version int NOT NULL CHECK (version > 0),
          snapshot jsonb NOT NULL,
          changed_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (customer_id, version)
        );

        CREATE TABLE beneficial_owner (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          customer_id uuid NOT NULL REFERENCES customer(id),
          owner_ref text NOT NULL,
          full_name text NOT NULL,
          date_of_birth date,
          nationality char(2),
          ownership_percent numeric(5, 2) CHECK (ownership_percent BETWEEN 0 AND 100),
          control_type control_type NOT NULL,
          is_pep boolean NOT NULL DEFAULT false,
          UNIQUE (customer_id, owner_ref)
        );

        CREATE TABLE related_party (
          customer_id uuid NOT NULL REFERENCES customer(id),
          related_customer_id uuid NOT NULL REFERENCES customer(id),
          relationship_type relationship_type NOT NULL,
          PRIMARY KEY (customer_id, related_customer_id, relationship_type),
          CHECK (customer_id <> related_customer_id)
        );

        CREATE TABLE account (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          customer_id uuid NOT NULL REFERENCES customer(id),
          source_system text NOT NULL,
          external_id text NOT NULL,
          product_type text NOT NULL,
          currency char(3) NOT NULL,
          opened_date date NOT NULL,
          status account_status NOT NULL,
          branch_id text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_system, external_id)
        );
        CREATE INDEX account_customer ON account (customer_id);

        -- One API call or file. The counts must add up once the batch is done.
        CREATE TABLE ingestion_batch (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_system text NOT NULL,
          entity text NOT NULL CHECK (entity IN ('customer', 'account', 'transaction')),
          received_at timestamptz NOT NULL DEFAULT now(),
          submitted int NOT NULL DEFAULT 0 CHECK (submitted >= 0),
          accepted int NOT NULL DEFAULT 0 CHECK (accepted >= 0),
          duplicates int NOT NULL DEFAULT 0 CHECK (duplicates >= 0),
          rejected int NOT NULL DEFAULT 0 CHECK (rejected >= 0),
          checksum text,
          status text NOT NULL DEFAULT 'received'
            CHECK (status IN ('received', 'processing', 'completed', 'failed')),
          report jsonb,
          CHECK (status <> 'completed' OR submitted = accepted + duplicates + rejected)
        );

        -- Records that could not be accepted yet, kept as received for review (ING-3).
        CREATE TABLE dead_letter (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          batch_id uuid NOT NULL REFERENCES ingestion_batch(id),
          entity text NOT NULL CHECK (entity IN ('customer', 'account', 'transaction')),
          raw jsonb NOT NULL,
          reason_code text NOT NULL,
          reason_detail text,
          status text NOT NULL DEFAULT 'held'
            CHECK (status IN ('held', 'reprocessed', 'discarded')),
          created_at timestamptz NOT NULL DEFAULT now(),
          resolved_at timestamptz,
          CHECK ((status = 'held') = (resolved_at IS NULL))
        );
        CREATE INDEX dead_letter_held ON dead_letter (status, created_at);

        CREATE TABLE txn (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_system text NOT NULL,
          external_id text NOT NULL,
          account_id uuid NOT NULL REFERENCES account(id),
          customer_id uuid NOT NULL REFERENCES customer(id),
          batch_id uuid REFERENCES ingestion_batch(id),
          occurred_at timestamptz NOT NULL,
          value_date date,
          amount bigint NOT NULL CHECK (amount > 0),
          currency char(3) NOT NULL,
          direction txn_direction NOT NULL,
          channel txn_channel NOT NULL,
          txn_type txn_type NOT NULL,
          status txn_status NOT NULL,
          counterparty_name text,
          counterparty_account text,
          counterparty_bank_code text,
          narration text,
          reference text,
          reversal_of uuid REFERENCES txn(id),
          location_id text,
          received_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_system, external_id)
        );
        CREATE INDEX txn_customer_time ON txn (customer_id, occurred_at);
        CREATE INDEX txn_account_time ON txn (account_id, occurred_at);
        CREATE INDEX txn_counterparty_time ON txn (counterparty_account, occurred_at)
          WHERE counterparty_account IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE txn;
        DROP TABLE dead_letter;
        DROP TABLE ingestion_batch;
        DROP TABLE account;
        DROP TABLE related_party;
        DROP TABLE beneficial_owner;
        DROP TABLE customer_version;
        DROP TABLE customer;
        DROP TYPE txn_type;
        DROP TYPE txn_channel;
        DROP TYPE txn_status;
        DROP TYPE txn_direction;
        DROP TYPE relationship_type;
        DROP TYPE control_type;
        DROP TYPE account_status;
        DROP TYPE customer_status;
        DROP TYPE risk_level;
        DROP TYPE kyc_status;
        DROP TYPE customer_type;
        """
    )
