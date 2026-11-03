"""Background jobs in each tenant's ``job`` table, worked by lane (ADR-004).

A worker claims the next ready job in its lane with ``FOR UPDATE SKIP LOCKED``, so any
number of workers can share a lane. Each lane has its own workers, so a heavy report never
starves evaluation. Handlers run in their own transaction and must be idempotent: a job
that fails is retried with exponential backoff, then left ``failed`` where operators see it.
A worker that dies mid-job leaves it ``running``; it is claimed again once its lock is stale.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

LANES = ("ingest", "evaluate", "scheduled_rule", "report", "notification")
BACKOFF = timedelta(seconds=30)
STALE_LOCK = timedelta(minutes=15)


@dataclass(frozen=True)
class Job:
    id: Any
    lane: str
    job_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    created_at: datetime


Handler = Callable[[psycopg.Connection, Job, datetime], None]
_handlers: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    """Register the function that runs jobs of ``job_type``."""

    def register(fn: Handler) -> Handler:
        _handlers[job_type] = fn
        return fn

    return register


def enqueue(
    conn: psycopg.Connection,
    lane: str,
    job_type: str,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    priority: int = 5,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
) -> Any:
    """Queue a job; returns its id, or ``None`` when ``dedupe_key`` is already queued."""
    if lane not in LANES:
        raise ValueError(f"Unknown lane {lane!r}")
    row = conn.execute(
        "INSERT INTO job (lane, job_type, payload, priority, run_after, dedupe_key, created_at)"
        " VALUES (%s, %s, %s, %s, coalesce(%s, %s, now()), %s, coalesce(%s, now()))"
        " ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
        (lane, job_type, Jsonb(payload), priority, run_after, now, dedupe_key, now),
    ).fetchone()
    return None if row is None else row[0]


def run_next(conn: psycopg.Connection, lane: str, worker: str, now: datetime) -> Job | None:
    """Claim and run the next ready job in ``lane``; ``None`` when there is nothing to do.

    ``conn`` must not be inside a transaction: the claim, the work and the outcome are
    committed separately so that a failure is recorded even though the work rolled back.
    """
    job = _claim(conn, lane, worker, now)
    if job is None:
        return None
    try:
        run = _handlers.get(job.job_type)
        if run is None:
            raise LookupError(f"No handler for job type {job.job_type!r}")
        with conn.transaction():
            run(conn, job, now)
            conn.execute(
                "UPDATE job SET status = 'done', finished_at = %s, locked_by = NULL,"
                " last_error = NULL WHERE id = %s",
                (now, job.id),
            )
    except Exception as exc:  # any handler failure is recorded and retried
        _failed(conn, job, f"{type(exc).__name__}: {exc}"[:2000], now)
    return job


def run_all(conn: psycopg.Connection, lane: str, worker: str, now: datetime, limit: int) -> int:
    """Run up to ``limit`` ready jobs; returns how many ran."""
    ran = 0
    while ran < limit and run_next(conn, lane, worker, now) is not None:
        ran += 1
    return ran


def _claim(conn: psycopg.Connection, lane: str, worker: str, now: datetime) -> Job | None:
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "UPDATE job SET status = 'running', attempts = attempts + 1, locked_by = %s,"
            " locked_at = %s WHERE id = ("
            "   SELECT id FROM job WHERE lane = %s AND ("
            "     (status = 'queued' AND run_after <= %s)"
            "     OR (status = 'running' AND locked_at < %s))"
            "   ORDER BY priority, run_after, created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
            " RETURNING id, lane, job_type, payload, attempts, max_attempts, created_at",
            (worker, now, lane, now, now - STALE_LOCK),
        ).fetchone()
    return None if row is None else Job(**row)


def _failed(conn: psycopg.Connection, job: Job, error: str, now: datetime) -> None:
    final = job.attempts >= job.max_attempts
    conn.execute(
        "UPDATE job SET status = %s, run_after = %s, last_error = %s, locked_by = NULL,"
        " finished_at = %s WHERE id = %s",
        (
            "failed" if final else "queued",
            now + BACKOFF * 2 ** (job.attempts - 1),
            error,
            now if final else None,
            job.id,
        ),
    )
    conn.commit()


def retry(conn: psycopg.Connection, job_id: Any, now: datetime) -> bool:
    """Put a failed job back in its queue with a fresh set of attempts."""
    row = conn.execute(
        "UPDATE job SET status = 'queued', attempts = 0, run_after = %s, finished_at = NULL"
        " WHERE id = %s AND status = 'failed' RETURNING id",
        (now, job_id),
    ).fetchone()
    return row is not None


def get(conn: psycopg.Connection, job_id: Any) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute("SELECT * FROM job WHERE id = %s", (job_id,)).fetchone()


@dataclass(frozen=True)
class LaneStatus:
    lane: str
    queued: int
    running: int
    failed: int
    done: int
    p50_seconds: float | None
    p95_seconds: float | None
    max_seconds: float | None


def lane_status(conn: psycopg.Connection, since: datetime) -> list[LaneStatus]:
    """Backlog per lane, and how long jobs finished since ``since`` took from queue to done."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = {
            row["lane"]: row
            for row in cursor.execute(
                "SELECT lane,"
                " count(*) FILTER (WHERE status = 'queued') AS queued,"
                " count(*) FILTER (WHERE status = 'running') AS running,"
                " count(*) FILTER (WHERE status = 'failed') AS failed,"
                " count(*) FILTER (WHERE status = 'done' AND finished_at >= %(since)s) AS done,"
                " percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM"
                "   finished_at - created_at)) FILTER (WHERE status = 'done'"
                "   AND finished_at >= %(since)s) AS p50_seconds,"
                " percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM"
                "   finished_at - created_at)) FILTER (WHERE status = 'done'"
                "   AND finished_at >= %(since)s) AS p95_seconds,"
                " max(extract(epoch FROM finished_at - created_at)) FILTER (WHERE"
                "   status = 'done' AND finished_at >= %(since)s) AS max_seconds"
                " FROM job GROUP BY lane",
                {"since": since},
            ).fetchall()
        }
    statuses = []
    for lane in LANES:
        row = rows.get(lane, {})

        def seconds(key: str, row: dict[str, Any] = row) -> float | None:
            return None if row.get(key) is None else float(row[key])

        statuses.append(
            LaneStatus(
                lane=lane,
                queued=int(row.get("queued", 0)),
                running=int(row.get("running", 0)),
                failed=int(row.get("failed", 0)),
                done=int(row.get("done", 0)),
                p50_seconds=seconds("p50_seconds"),
                p95_seconds=seconds("p95_seconds"),
                max_seconds=seconds("max_seconds"),
            )
        )
    return statuses
