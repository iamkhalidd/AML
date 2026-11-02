"""Tenant provisioning and fleet migration (ACC-1, ADM-5; ADR-001, ADR-011).

Each tenant gets its own database and its own login role, which owns the database and is
the only login the application uses for it. A second, non-login role owns the tenant's
audit schema (ADR-006), so the application can add audit entries but never change them;
only the provisioner assumes that role, to migrate the schema and run the daily check.
The control plane records every tenant, the schema version of its database and an
append-only log of what the operator did.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.errors import UniqueViolation
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from aml.control import migrations
from aml.control.secrets import SecretStore

SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
DROPPABLE_ENVIRONMENTS = frozenset({"local", "test"})


class TenantError(Exception):
    """An operator mistake (bad slug, duplicate, unknown tenant), not a system failure."""


@dataclass(frozen=True)
class Tenant:
    id: Any
    slug: str
    name: str
    database_name: str
    database_role: str
    status: str
    schema_version: str | None
    migrated_at: datetime | None


@dataclass(frozen=True)
class Fleet:
    """Everything needed to act on the control plane and the tenant databases."""

    control_url: str
    admin_url: str
    secrets: SecretStore
    actor: str
    environment: str


@dataclass(frozen=True)
class DatabaseStatus:
    database: str
    current: str | None
    head: str


def validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.fullmatch(slug):
        raise TenantError(
            f"Invalid slug {slug!r}: 3 to 31 characters, lowercase letters, digits and "
            "underscores, starting with a letter"
        )
    return slug


def database_name(slug: str) -> str:
    return f"aml_t_{slug}"


def audit_role(database_role: str) -> str:
    """The role that owns a tenant's audit schema."""
    return f"{database_role}_audit"


def admin_url_for(fleet: Fleet, database: str) -> str:
    """The provisioner's URL for one tenant database (used with SET ROLE to the audit role)."""
    return make_url(fleet.admin_url).set(database=database).render_as_string(hide_password=False)


def _tenant_url(admin_url: str, role: str, password: str, database: str) -> str:
    url = make_url(admin_url).set(username=role, password=password, database=database)
    return url.render_as_string(hide_password=False)


def _event(
    control: psycopg.Connection, actor: str, action: str, tenant_id: Any, **detail: object
) -> None:
    control.execute(
        "INSERT INTO control_event (actor, tenant_id, action, detail) VALUES (%s, %s, %s, %s)",
        (actor, tenant_id, action, Jsonb(detail)),
    )


def list_tenants(fleet: Fleet) -> list[Tenant]:
    with (
        psycopg.connect(fleet.control_url) as control,
        control.cursor(row_factory=class_row(Tenant)) as cursor,
    ):
        cursor.execute(
            "SELECT id, slug, name, database_name, database_role, status, schema_version,"
            " migrated_at FROM tenant ORDER BY slug"
        )
        return cursor.fetchall()


def get_tenant(fleet: Fleet, slug: str) -> Tenant:
    for tenant in list_tenants(fleet):
        if tenant.slug == slug:
            return tenant
    raise TenantError(f"No tenant {slug!r}")


def create_tenant(fleet: Fleet, slug: str, name: str) -> Tenant:
    """Create the tenant's role and database, migrate it to head and mark it active."""
    validate_slug(slug)
    name_in_db = database_name(slug)
    with psycopg.connect(fleet.control_url, autocommit=True) as control:
        try:
            tenant_id = control.execute(
                "INSERT INTO tenant (slug, name, database_name, database_role)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (slug, name, name_in_db, name_in_db),
            ).fetchone()[0]  # type: ignore[index]
        except UniqueViolation:
            raise TenantError(f"Tenant {slug!r} already exists") from None
        _event(control, fleet.actor, "tenant.create.started", tenant_id, slug=slug)
        try:
            url = _create_database(fleet, slug, name_in_db)
            fleet.secrets.put(slug, url)
            _migrate_audit(fleet, name_in_db, name_in_db)
            version = migrations.upgrade("tenant", url)
            with psycopg.connect(url) as tenant_db:
                tenant_db.execute("INSERT INTO tenant_identity (slug) VALUES (%s)", (slug,))
            control.execute(
                "UPDATE tenant SET status = 'active', schema_version = %s, migrated_at = now(),"
                " updated_at = now() WHERE id = %s",
                (version, tenant_id),
            )
            _event(control, fleet.actor, "tenant.create.completed", tenant_id, version=version)
        except Exception as exc:
            control.execute(
                "UPDATE tenant SET status = 'failed', updated_at = now() WHERE id = %s",
                (tenant_id,),
            )
            # The error type only: messages can carry connection details.
            _event(
                control, fleet.actor, "tenant.create.failed", tenant_id, error=type(exc).__name__
            )
            raise
    return get_tenant(fleet, slug)


def _create_database(fleet: Fleet, slug: str, name_in_db: str) -> str:
    """Create the login role and the database it owns; return the role's connection URL."""
    password = secrets.token_urlsafe(32)
    role = sql.Identifier(name_in_db)
    with psycopg.connect(fleet.admin_url, autocommit=True) as admin:
        # PostgreSQL 16+: let the (non-superuser) provisioner SET ROLE to roles it creates,
        # which CREATE DATABASE ... OWNER needs. It does not inherit their privileges.
        admin.execute("SET createrole_self_grant = 'set'")
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}"
            ).format(role, sql.Literal(password))
        )
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                role, role
            )
        )
        admin.execute(sql.SQL("SET ROLE {}").format(role))
        admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(role))
        admin.execute("RESET ROLE")
        _provision_audit_role(admin, name_in_db, name_in_db)
    return _tenant_url(fleet.admin_url, name_in_db, password, name_in_db)


def _provision_audit_role(admin: psycopg.Connection, database_role: str, database: str) -> None:
    """Create the tenant's audit role if it is missing and let it and the provisioner in.

    Idempotent, so the fleet migration can bring tenants made before the audit store up.
    """
    audit = audit_role(database_role)
    if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (audit,)).fetchone():
        admin.execute("SET createrole_self_grant = 'set'")
        admin.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
                sql.Identifier(audit)
            )
        )
    provisioner = admin.execute("SELECT session_user").fetchone()[0]  # type: ignore[index]
    db = sql.Identifier(database)
    admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(database_role)))
    admin.execute(
        sql.SQL("GRANT CONNECT, CREATE ON DATABASE {} TO {}").format(db, sql.Identifier(audit))
    )
    admin.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, sql.Identifier(provisioner))
    )
    admin.execute("RESET ROLE")


def _migrate_audit(fleet: Fleet, database_role: str, database: str) -> str:
    return migrations.upgrade(
        "audit", admin_url_for(fleet, database), role=audit_role(database_role)
    )


def drop_tenant(fleet: Fleet, slug: str) -> None:
    """Delete a tenant's database and role. Local and test environments only."""
    if fleet.environment not in DROPPABLE_ENVIRONMENTS:
        raise TenantError(f"Dropping tenants is not allowed in {fleet.environment}")
    tenant = get_tenant(fleet, slug)
    role = sql.Identifier(tenant.database_role)
    with psycopg.connect(fleet.admin_url, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (tenant.database_role,)
        ).fetchone()
        if exists:
            # Not DROP ... WITH (FORCE): it refuses when an autovacuum worker is in the
            # database, and runs as one role, so it cannot end the other's sessions. End the
            # provisioner's own sessions (audit migrations and checks), then the tenant's, and
            # wait for each; a plain DROP asks autovacuum to stop and waits for it.
            terminate = (
                "SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity"
                " WHERE datname = %s AND usename = current_user AND pid <> pg_backend_pid()"
            )
            admin.execute(terminate, (tenant.database_name,))
            admin.execute(sql.SQL("SET ROLE {}").format(role))
            admin.execute(terminate, (tenant.database_name,))
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(tenant.database_name))
            )
            admin.execute("RESET ROLE")
            admin.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(
                    sql.Identifier(audit_role(tenant.database_role))
                )
            )
            admin.execute(sql.SQL("DROP ROLE {}").format(role))
    fleet.secrets.delete(slug)
    with psycopg.connect(fleet.control_url, autocommit=True) as control:
        control.execute(
            "UPDATE tenant SET status = 'removed', updated_at = now() WHERE id = %s",
            (tenant.id,),
        )
        _event(control, fleet.actor, "tenant.dropped", tenant.id)


def migrate_fleet(fleet: Fleet) -> list[DatabaseStatus]:
    """Migrate the control plane, then every active tenant; stop at the first failure.

    Each tenant's audit store is migrated before its main schema.
    """
    head = migrations.head_revision("control")
    results = [DatabaseStatus("control", migrations.upgrade("control", fleet.control_url), head)]
    tenant_head = migrations.head_revision("tenant")
    audit_head = migrations.head_revision("audit")
    with psycopg.connect(fleet.control_url, autocommit=True) as control:
        for tenant in list_tenants(fleet):
            if tenant.status != "active":
                continue
            try:
                with psycopg.connect(fleet.admin_url, autocommit=True) as admin:
                    _provision_audit_role(admin, tenant.database_role, tenant.database_name)
                audit_version = _migrate_audit(fleet, tenant.database_role, tenant.database_name)
                version = migrations.upgrade("tenant", fleet.secrets.get(tenant.slug))
            except Exception as exc:
                _event(
                    control,
                    fleet.actor,
                    "tenant.migrate.failed",
                    tenant.id,
                    error=type(exc).__name__,
                )
                raise
            control.execute(
                "UPDATE tenant SET schema_version = %s, migrated_at = now(), updated_at = now()"
                " WHERE id = %s",
                (version, tenant.id),
            )
            if version != tenant.schema_version:
                _event(
                    control,
                    fleet.actor,
                    "tenant.migrated",
                    tenant.id,
                    previous=tenant.schema_version,
                    version=version,
                )
            results.append(DatabaseStatus(tenant.database_name, version, tenant_head))
            results.append(
                DatabaseStatus(f"{tenant.database_name}/audit", audit_version, audit_head)
            )
    return results


def fleet_status(fleet: Fleet) -> list[DatabaseStatus]:
    results = [
        DatabaseStatus(
            "control",
            migrations.current_revision(fleet.control_url),
            migrations.head_revision("control"),
        )
    ]
    tenant_head = migrations.head_revision("tenant")
    audit_head = migrations.head_revision("audit")
    for tenant in list_tenants(fleet):
        if tenant.status == "active":
            current = migrations.current_revision(fleet.secrets.get(tenant.slug))
            results.append(DatabaseStatus(tenant.database_name, current, tenant_head))
            try:
                audit = migrations.current_revision(
                    admin_url_for(fleet, tenant.database_name),
                    "audit",
                    role=audit_role(tenant.database_role),
                )
            except DBAPIError:  # a tenant made before the audit store has no audit role yet
                audit = None
            results.append(DatabaseStatus(f"{tenant.database_name}/audit", audit, audit_head))
    return results
