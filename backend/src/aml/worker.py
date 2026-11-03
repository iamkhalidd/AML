"""Job workers: ``aml worker --lane evaluate`` works one lane for every active tenant (ADR-004).

Run one or more workers per lane. Each pass visits every active tenant, queues due
scheduled-rule windows when it works the ``scheduled_rule`` lane, and runs up to
``limit`` ready jobs per tenant.
"""

import logging
import socket
import time
from datetime import UTC, datetime

import psycopg

from aml import jobs
from aml.control.tenants import Fleet, list_tenants
from aml.modules.rules import engine, library  # noqa: F401 - registers the starter rules

log = logging.getLogger(__name__)


def work_tenant(url: str, lane: str, worker: str, now: datetime, limit: int = 100) -> int:
    with psycopg.connect(url, autocommit=True) as conn:
        if lane == "scheduled_rule":
            with conn.transaction():
                engine.schedule_due(conn, now)
        return jobs.run_all(conn, lane, worker, now, limit)


def work_fleet(fleet: Fleet, lane: str, worker: str, now: datetime | None = None) -> int:
    """One pass over every active tenant; returns how many jobs ran.

    A tenant that cannot be reached is logged and skipped so the others still get worked.
    """
    ran = 0
    for tenant in list_tenants(fleet):
        if tenant.status != "active":
            continue
        try:
            url = fleet.secrets.get(tenant.slug)
            ran += work_tenant(url, lane, worker, now or datetime.now(UTC))
        except Exception:
            log.exception("worker %s could not work tenant %s", worker, tenant.slug)
    return ran


def run(fleet: Fleet, lane: str, *, once: bool = False, idle_seconds: float = 2.0) -> None:
    if lane not in jobs.LANES:
        raise ValueError(f"Unknown lane {lane!r}")
    worker = f"{socket.gethostname()}:{lane}"
    while True:
        ran = work_fleet(fleet, lane, worker)
        if once:
            return
        if not ran:
            time.sleep(idle_seconds)
