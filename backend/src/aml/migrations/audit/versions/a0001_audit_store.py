"""Append-only, hash-chained audit store, partitioned by month (AUD-1, AUD-2, NFR-10).

Revision ID: a0001
Revises:
Create Date: 2026-11-02

Runs as the tenant's audit role. The tenant role (the database owner, which the
application signs in as) may insert the descriptive columns and read; it cannot set the
time, sequence or hashes, and cannot update, delete or truncate.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Months of partitions kept ready ahead of the current one; the daily job tops them up.
MONTHS_AHEAD = 3


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit.entry (
          seq bigint NOT NULL,
          at timestamptz NOT NULL DEFAULT now(),
          user_id uuid,
          actor_type text NOT NULL CHECK (actor_type IN ('user', 'operator', 'system')),
          -- Who acted when there is no user: the operator's login name or the job's name.
          actor_label text,
          action text NOT NULL CHECK (action ~ '^[a-z_]+([.][a-z_]+)+$'),
          object_type text NOT NULL,
          object_id text,
          before jsonb,
          after jsonb,
          ip inet,
          prev_hash bytea,
          hash bytea NOT NULL,
          PRIMARY KEY (seq, at)
        ) PARTITION BY RANGE (at);
        CREATE INDEX entry_object ON audit.entry (object_type, object_id, at);
        CREATE INDEX entry_user ON audit.entry (user_id, at);

        -- The last link of the chain. Its row lock orders concurrent writers, so the
        -- sequence and the chain always agree.
        CREATE TABLE audit.chain_head (
          id boolean PRIMARY KEY DEFAULT true CHECK (id),
          seq bigint NOT NULL,
          hash bytea
        );
        INSERT INTO audit.chain_head (seq, hash) VALUES (0, NULL);

        -- Everything is serialised as one JSON array, so no two entries share an input
        -- and no session setting (time zone, date style) changes the result.
        CREATE FUNCTION audit.entry_hash(
          prev_hash bytea, seq bigint, at timestamptz, user_id uuid, actor_type text,
          actor_label text, action text, object_type text, object_id text, before jsonb,
          after jsonb, ip inet
        ) RETURNS bytea LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp AS $$
          SELECT sha256(coalesce(prev_hash, ''::bytea) || convert_to(jsonb_build_array(
            seq, extract(epoch FROM at), user_id, actor_type, actor_label, action,
            object_type, object_id, before, after, ip)::text, 'UTF8'))
        $$;

        CREATE FUNCTION audit.chain() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
          head audit.chain_head;
        BEGIN
          SELECT * INTO head FROM audit.chain_head FOR UPDATE;
          NEW.seq := head.seq + 1;
          NEW.prev_hash := head.hash;
          NEW.hash := audit.entry_hash(
            NEW.prev_hash, NEW.seq, NEW.at, NEW.user_id, NEW.actor_type, NEW.actor_label,
            NEW.action, NEW.object_type, NEW.object_id, NEW.before, NEW.after, NEW.ip);
          UPDATE audit.chain_head SET seq = NEW.seq, hash = NEW.hash;
          RETURN NEW;
        END $$;
        CREATE TRIGGER entry_chain BEFORE INSERT ON audit.entry
          FOR EACH ROW EXECUTE FUNCTION audit.chain();

        CREATE FUNCTION audit.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit.entry is append-only';
        END $$;
        CREATE TRIGGER entry_append_only BEFORE UPDATE OR DELETE ON audit.entry
          FOR EACH ROW EXECUTE FUNCTION audit.refuse_change();
        CREATE TRIGGER entry_no_truncate BEFORE TRUNCATE ON audit.entry
          FOR EACH STATEMENT EXECUTE FUNCTION audit.refuse_change();

        -- Create this month's partition and the next ones; return how many were new.
        -- Only the audit role can run it: partitions belong to the table's owner.
        CREATE FUNCTION audit.ensure_partitions(months_ahead int) RETURNS int
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
          month timestamp := date_trunc('month', now() AT TIME ZONE 'UTC');
          name text;
          created int := 0;
        BEGIN
          FOR i IN 0..months_ahead LOOP
            name := 'entry_' || to_char(month, 'YYYY_MM');
            IF to_regclass('audit.' || name) IS NULL THEN
              EXECUTE format(
                'CREATE TABLE audit.%I PARTITION OF audit.entry FOR VALUES FROM (%L) TO (%L)',
                name, month AT TIME ZONE 'UTC', (month + interval '1 month') AT TIME ZONE 'UTC');
              created := created + 1;
            END IF;
            month := month + interval '1 month';
          END LOOP;
          RETURN created;
        END $$;
        REVOKE EXECUTE ON FUNCTION audit.ensure_partitions(int) FROM PUBLIC;
        """
    )
    op.execute(f"SELECT audit.ensure_partitions({MONTHS_AHEAD})")
    op.execute(
        """
        DO $$
        DECLARE
          app text := (SELECT pg_get_userbyid(datdba) FROM pg_database
                       WHERE datname = current_database());
        BEGIN
          EXECUTE format('GRANT USAGE ON SCHEMA audit TO %I', app);
          EXECUTE format('GRANT SELECT ON audit.entry, audit.chain_head TO %I', app);
          EXECUTE format(
            'GRANT INSERT (user_id, actor_type, actor_label, action, object_type, object_id,'
            ' before, after, ip) ON audit.entry TO %I', app);
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE audit.entry;
        DROP TABLE audit.chain_head;
        DROP FUNCTION audit.chain();
        DROP FUNCTION audit.refuse_change();
        DROP FUNCTION audit.ensure_partitions(int);
        DROP FUNCTION audit.entry_hash(bytea, bigint, timestamptz, uuid, text, text, text,
          text, text, jsonb, jsonb, inet);
        """
    )
