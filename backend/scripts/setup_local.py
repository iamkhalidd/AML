"""Create the local development databases and write backend/.env.

Run from backend/:  uv run python scripts/setup_local.py

Safe to run again: it creates what is missing, gives the application role a fresh
password and rewrites only the AML_* database lines in .env. It needs a PostgreSQL
superuser once; the password is read from PGPASSWORD or asked for, never stored.
Local data is synthetic only.
"""

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg import sql

APP_ROLE = "aml_app"
DATABASES = {"AML_DATABASE_URL": "aml_control", "AML_TEST_DATABASE_URL": "aml_control_test"}
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("PGHOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PGPORT", "5432")))
    parser.add_argument("--admin-user", default=os.environ.get("PGUSER", "postgres"))
    return parser.parse_args()


def admin_connection(args: argparse.Namespace, dbname: str) -> psycopg.Connection:
    password = os.environ.get("PGPASSWORD") or getpass.getpass(
        f"Password for PostgreSQL user {args.admin_user} on {args.host}:{args.port}: "
    )
    os.environ["PGPASSWORD"] = password  # reused for this run only
    return psycopg.connect(
        host=args.host,
        port=args.port,
        user=args.admin_user,
        password=password,
        dbname=dbname,
        autocommit=True,
    )


def ensure_role(conn: psycopg.Connection, password: str) -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
    conn.execute(
        sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB PASSWORD {}").format(
            verb, sql.Identifier(APP_ROLE), sql.Literal(password)
        )
    )
    print(f"role {APP_ROLE}: {'password reset' if exists else 'created'}")


def ensure_database(conn: psycopg.Connection, name: str) -> None:
    exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
    if exists:
        print(f"database {name}: already exists")
        return
    conn.execute(
        sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
            sql.Identifier(name), sql.Identifier(APP_ROLE)
        )
    )
    print(f"database {name}: created")


def lock_down_public_schema(args: argparse.Namespace, name: str) -> None:
    """Only the owning role may create objects; no other login can connect."""
    with admin_connection(args, name) as conn:
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(APP_ROLE)))


def write_env(values: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in values]
    if not any(line.startswith("AML_ENVIRONMENT=") for line in kept):
        kept.insert(0, "AML_ENVIRONMENT=local")
    kept += [f"{key}={value}" for key, value in values.items()]
    ENV_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"wrote {ENV_FILE}")


def main() -> int:
    args = parse_args()
    password = secrets.token_urlsafe(24)
    try:
        with admin_connection(args, "postgres") as conn:
            ensure_role(conn, password)
            for name in DATABASES.values():
                ensure_database(conn, name)
        for name in DATABASES.values():
            lock_down_public_schema(args, name)
    except psycopg.OperationalError as exc:
        print(f"Could not connect to PostgreSQL: {exc}", file=sys.stderr)
        return 1
    write_env(
        {
            key: f"postgresql://{APP_ROLE}:{password}@{args.host}:{args.port}/{name}"
            for key, name in DATABASES.items()
        }
    )
    print("Done. Start the API with: uv run uvicorn aml.main:app --reload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
