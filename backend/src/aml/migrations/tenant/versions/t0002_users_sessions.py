"""Staff users and server-side sessions (ACC-2; ADR-005).

Revision ID: t0002
Revises: t0001
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t0002"
down_revision: str | None = "t0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE user_role AS ENUM
          ('compliance_officer', 'analyst', 'administrator', 'auditor');

        CREATE TABLE app_user (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          email text NOT NULL UNIQUE CHECK (email = lower(email)),
          full_name text NOT NULL,
          role user_role NOT NULL,
          password_hash text NOT NULL,
          -- Encrypted with the application MFA key; never stored in clear.
          mfa_secret_encrypted bytea,
          mfa_enabled boolean NOT NULL DEFAULT false,
          -- Last accepted TOTP time step, so a code cannot be replayed.
          mfa_last_step bigint,
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
          failed_logins int NOT NULL DEFAULT 0,
          locked_until timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        -- Only the SHA-256 of the session token is stored.
        CREATE TABLE app_session (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES app_user(id),
          token_hash bytea NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_seen_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz
        );
        CREATE INDEX app_session_user_id ON app_session (user_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE app_session;
        DROP TABLE app_user;
        DROP TYPE user_role;
        """
    )
