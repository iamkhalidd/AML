"""Daily audit check across the fleet (ADR-006, NFR-10).

For each active tenant: top up next months' audit partitions, recompute the whole hash
chain against the digests from earlier days, and record today's digest in the control
plane, outside the tenant's reach. Schedule ``aml audit verify`` once a day.
"""

from collections.abc import Collection
from dataclasses import dataclass

import psycopg

from aml.control.tenants import Fleet, Tenant, admin_url_for, audit_role, list_tenants
from aml.modules.audit import store
from aml.modules.audit.store import ChainReport


@dataclass(frozen=True)
class AuditCheck:
    slug: str
    report: ChainReport
    partitions_created: int


def check_fleet(fleet: Fleet, slugs: Collection[str] | None = None) -> list[AuditCheck]:
    """Check every active tenant, or only those named; one failure does not stop the rest."""
    return [
        check_tenant(fleet, tenant)
        for tenant in list_tenants(fleet)
        if tenant.status == "active" and (not slugs or tenant.slug in slugs)
    ]


def check_tenant(fleet: Fleet, tenant: Tenant) -> AuditCheck:
    with psycopg.connect(fleet.control_url, autocommit=True) as control:
        checkpoints: dict[int, bytes | None] = dict(
            control.execute(
                "SELECT last_seq, last_hash FROM audit_digest"
                " WHERE tenant_id = %s AND ok AND last_seq > 0",
                (tenant.id,),
            ).fetchall()
        )
        created = 0
        try:
            with psycopg.connect(
                admin_url_for(fleet, tenant.database_name), autocommit=True
            ) as conn:
                conn.execute(
                    "SELECT set_config('role', %s, false)", (audit_role(tenant.database_role),)
                )
                with conn.transaction():
                    created = store.ensure_partitions(conn)
                report = store.verify_chain(conn, checkpoints)
        except psycopg.Error as exc:
            # The error type only: messages can carry connection details.
            report = ChainReport(0, 0, None, f"Could not check: {type(exc).__name__}")
        control.execute(
            "INSERT INTO audit_digest (tenant_id, last_seq, last_hash, ok, problem)"
            " VALUES (%s, %s, %s, %s, %s)",
            (tenant.id, report.last_seq, report.last_hash, report.ok, report.problem),
        )
    return AuditCheck(tenant.slug, report, created)
