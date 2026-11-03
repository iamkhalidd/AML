# ruff: noqa: F811 - pytest fixtures imported from the approval tests are parameters here

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from aml import jobs, worker
from aml.control import migrations
from aml.control.tenants import Fleet
from aml.modules.access import approvals
from aml.modules.ingestion import service as ingestion
from aml.modules.rules import engine, service
from aml.modules.rules.engine import EventContext, Finding, Hit, WindowContext
from aml.modules.rules.service import Change
from tests.test_approvals import T0, Staff, conn, signed_in, staff  # noqa: F401
from tests.test_ingest_customers import ACCOUNT, PERSON
from tests.test_ingest_transactions import TXN

pytestmark = pytest.mark.requirement("RUL-6")

CALLER = ingestion.Caller("pytest")
# Ingestion queues evaluation at the database clock, so event jobs are due only after it.
LATER = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)


@pytest.fixture
def work(staff: Staff) -> Iterator[psycopg.Connection]:
    """The worker's own connection: autocommit, as the job runner expects."""
    with psycopg.connect(staff.url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def findings(monkeypatch: pytest.MonkeyPatch) -> list[Finding]:
    """Stand-in evaluators (the real rules arrive in story 2.3) and a collecting sink."""
    seen: list[Finding] = []

    def r01(ctx: EventContext) -> list[Hit]:
        if ctx.txn["amount"] < ctx.parameters["individual_threshold_minor"]:
            return []
        values = {"amount": ctx.txn["amount"]}
        return [Hit(ctx.txn["customer_id"], values, (ctx.txn["id"],), ctx.txn["account_id"])]

    def r02(ctx: WindowContext) -> list[Hit]:
        rows = ctx.conn.execute(
            sql.SQL(
                "SELECT c.id, count(t.id) FROM customer c JOIN txn t ON t.customer_id = c.id"
                " WHERE t.occurred_at >= %s AND t.occurred_at < %s AND {} GROUP BY c.id"
            ).format(ctx.in_scope("c")),
            (ctx.window_start, ctx.window_end),
        ).fetchall()
        return [
            Hit(customer, {"count": count}, window_start=ctx.window_start,
                window_end=ctx.window_end)
            for customer, count in rows
        ]  # fmt: skip

    monkeypatch.setattr(engine, "_event_rules", {"R01": r01})
    monkeypatch.setattr(engine, "_window_rules", {"R02": r02})
    monkeypatch.setattr(engine, "_sinks", [lambda _conn, finding: seen.append(finding)])
    return seen


def rule(
    conn: psycopg.Connection, staff: Staff, type_code: str = "R01", approve: bool = True, **kw: Any
) -> Any:
    change = Change(rationale="Pilot calibration", **{"name": f"{type_code} rule", **kw})
    version, approval = service.propose_new(conn, staff.users["analyst"], type_code, change, now=T0)
    if approve:
        approvals.approve(conn, staff.users["mlro"], approval.id, note=None, now=T0)
    return version.rule_instance_id


def ingest(conn: psycopg.Connection, *txns: dict[str, Any], customer: Any = PERSON) -> None:
    ingestion.ingest_customers(conn, CALLER, "core", [customer])
    account = ACCOUNT | {"customer_id": customer["customer_id"]}
    ingestion.ingest_accounts(conn, CALLER, "core", [account])
    ingestion.ingest_transactions(conn, CALLER, "core", list(txns))


def queued(conn: psycopg.Connection, lane: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT * FROM job WHERE lane = %s ORDER BY created_at", (lane,)
        ).fetchall()


# --- The job queue (ADR-004) ----------------------------------------------------------------


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(jobs, "_handlers", {})

    @jobs.handler("note")
    def note(conn: psycopg.Connection, job: jobs.Job, now: datetime) -> None:
        if job.payload.get("boom"):
            conn.execute("INSERT INTO job (lane, job_type, payload) VALUES ('report', 'x', '{}')")
            raise RuntimeError("handler failed")
        calls.append(job.payload)

    return calls


def test_jobs_run_by_lane_priority_and_time(
    work: psycopg.Connection, handled: list[dict[str, Any]]
) -> None:
    jobs.enqueue(work, "report", "note", {"n": "report"}, now=T0)
    jobs.enqueue(work, "evaluate", "note", {"n": "later"}, now=T0, run_after=T0 + timedelta(1))
    jobs.enqueue(work, "evaluate", "note", {"n": "low"}, now=T0, priority=9)
    jobs.enqueue(work, "evaluate", "note", {"n": "high"}, now=T0, priority=1)
    assert jobs.run_all(work, "evaluate", "w1", T0, limit=10) == 2
    assert handled == [{"n": "high"}, {"n": "low"}]
    assert jobs.run_all(work, "evaluate", "w1", T0 + timedelta(1), limit=10) == 1
    assert [row["status"] for row in queued(work, "report")] == ["queued"]
    with pytest.raises(ValueError, match="Unknown lane"):
        jobs.enqueue(work, "fast", "note", {})


def test_a_dedupe_key_queues_a_job_once(work: psycopg.Connection) -> None:
    first = jobs.enqueue(work, "evaluate", "note", {}, dedupe_key="k")
    assert first is not None
    assert jobs.enqueue(work, "evaluate", "note", {}, dedupe_key="k") is None


def test_failed_jobs_retry_with_backoff_then_stay_failed(
    work: psycopg.Connection, handled: list[dict[str, Any]]
) -> None:
    job_id = jobs.enqueue(work, "evaluate", "note", {"boom": True}, now=T0)
    work.execute("UPDATE job SET max_attempts = 2 WHERE id = %s", (job_id,))
    jobs.run_next(work, "evaluate", "w1", T0)
    job = jobs.get(work, job_id)
    assert job is not None
    assert (job["status"], job["attempts"]) == ("queued", 1)
    assert job["run_after"] == T0 + jobs.BACKOFF
    assert job["last_error"] == "RuntimeError: handler failed"
    # The handler's own writes rolled back with it.
    assert queued(work, "report") == []

    assert jobs.run_next(work, "evaluate", "w1", T0 + timedelta(seconds=29)) is None
    jobs.run_next(work, "evaluate", "w1", T0 + jobs.BACKOFF)
    job = jobs.get(work, job_id)
    assert job is not None
    assert (job["status"], job["attempts"], job["finished_at"]) == (
        "failed", 2, T0 + jobs.BACKOFF,
    )  # fmt: skip
    assert jobs.run_next(work, "evaluate", "w1", LATER) is None

    work.execute("UPDATE job SET payload = '{}' WHERE id = %s", (job_id,))
    assert jobs.retry(work, job_id, LATER)
    assert not jobs.retry(work, job_id, LATER)
    jobs.run_next(work, "evaluate", "w1", LATER)
    assert jobs.get(work, job_id)["status"] == "done"  # type: ignore[index]


def test_a_job_without_a_handler_fails_visibly(work: psycopg.Connection) -> None:
    job_id = jobs.enqueue(work, "evaluate", "nobody", {}, now=T0)
    jobs.run_next(work, "evaluate", "w1", T0)
    job = jobs.get(work, job_id)
    assert job is not None
    assert job["last_error"] == "LookupError: No handler for job type 'nobody'"


def test_a_job_left_running_by_a_dead_worker_is_claimed_again(
    work: psycopg.Connection, handled: list[dict[str, Any]]
) -> None:
    job_id = jobs.enqueue(work, "evaluate", "note", {"n": 1}, now=T0)
    work.execute(
        "UPDATE job SET status = 'running', attempts = 1, locked_by = 'dead', locked_at = %s"
        " WHERE id = %s",
        (T0, job_id),
    )
    assert jobs.run_next(work, "evaluate", "w2", T0 + timedelta(minutes=5)) is None
    assert jobs.run_next(work, "evaluate", "w2", T0 + jobs.STALE_LOCK + timedelta(1)) is not None
    assert handled == [{"n": 1}]


def test_two_workers_never_claim_the_same_job(
    staff: Staff, work: psycopg.Connection, handled: list[dict[str, Any]]
) -> None:
    jobs.enqueue(work, "evaluate", "note", {"n": 1}, now=T0)
    with psycopg.connect(staff.url) as other:
        other.execute("SELECT id FROM job FOR UPDATE")  # another worker holds the only job
        assert jobs.run_next(work, "evaluate", "w1", T0) is None
        other.rollback()
    assert jobs.run_next(work, "evaluate", "w1", T0) is not None


# --- Event rules ----------------------------------------------------------------------------


def test_accepted_transactions_are_evaluated_by_event_rules(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule_id = rule(conn, staff)
    small = TXN | {"transaction_id": "T-2", "amount_minor": 1_000}
    ingest(conn, TXN | {"amount_minor": 600_000_000}, small)
    assert len(queued(work, "evaluate")) == 2

    assert jobs.run_all(work, "evaluate", "w1", LATER, limit=10) == 2
    [finding] = findings
    assert (finding.rule.id, finding.version.version) == (rule_id, 1)
    assert finding.hit.trigger_values == {"amount": 600_000_000}
    assert finding.evaluated_at == LATER

    # Sending the same transaction again queues nothing new.
    ingestion.ingest_transactions(conn, CALLER, "core", [small])
    assert len(queued(work, "evaluate")) == 2


def test_a_settled_transaction_is_evaluated_again(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule(conn, staff)
    pending = TXN | {"status": "PENDING"}
    ingest(conn, pending)
    ingestion.ingest_transactions(conn, CALLER, "core", [TXN])
    assert len(queued(work, "evaluate")) == 2


def test_only_enabled_approved_rules_in_scope_run(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule(conn, staff, approve=False)
    rule(conn, staff, name="Businesses only", scope={"customer_type": "business"})
    switched_off = rule(conn, staff, name="Switched off")
    _, off = service.propose_change(
        conn, staff.users["analyst"], switched_off, Change(rationale="Pause", enabled=False),
        now=T0,
    )  # fmt: skip
    approvals.approve(conn, staff.users["mlro"], off.id, note=None, now=T0)
    large = TXN | {"amount_minor": 600_000_000}
    ingest(conn, large)
    jobs.run_all(work, "evaluate", "w1", LATER, limit=10)
    assert findings == []

    rule(conn, staff, name="Individuals", scope={"customer_type": "individual"})
    ingestion.ingest_transactions(conn, CALLER, "core", [large | {"transaction_id": "T-9"}])
    jobs.run_all(work, "evaluate", "w1", LATER, limit=10)
    assert [f.rule.name for f in findings] == ["Individuals"]


def test_rules_run_with_the_version_in_effect_when_the_job_runs(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule_id = rule(conn, staff)
    raised = {"individual_threshold_minor": 900_000_000}
    _, pending = service.propose_change(
        conn, staff.users["analyst"], rule_id, Change(rationale="Raise", parameters=raised),
        now=T0,
    )  # fmt: skip
    approvals.approve(
        conn, staff.users["mlro"], pending.id, note=None, now=LATER + timedelta(hours=1)
    )
    ingest(conn, TXN | {"amount_minor": 600_000_000})
    jobs.run_all(work, "evaluate", "w1", LATER, limit=10)
    assert [f.version.version for f in findings] == [1]

    ingestion.ingest_transactions(
        conn, CALLER, "core", [TXN | {"transaction_id": "T-3", "amount_minor": 600_000_000}]
    )
    jobs.run_all(work, "evaluate", "w1", LATER + timedelta(hours=2), limit=10)
    assert [f.version.version for f in findings] == [1]  # version 2 does not fire on it


def test_a_failing_rule_leaves_no_findings_and_is_retried(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff,
    findings: list[Finding], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    rule(conn, staff)

    def broken(ctx: EventContext) -> list[Hit]:
        raise ZeroDivisionError("bad data")

    monkeypatch.setitem(engine._event_rules, "R01", broken)
    ingest(conn, TXN)
    jobs.run_all(work, "evaluate", "w1", LATER, limit=10)
    [job] = queued(work, "evaluate")
    assert (job["status"], job["last_error"]) == ("queued", "ZeroDivisionError: bad data")


# --- Scheduled rules ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schedule", "now", "start", "end"),
    [
        ("hourly", "2026-11-03 10:15", "2026-11-03 09:00", "2026-11-03 10:00"),
        # 02:30 in Lagos: yesterday's Lagos day, which starts at 23:00 UTC.
        ("daily 02:00", "2026-11-03 01:30", "2026-11-01 23:00", "2026-11-02 23:00"),
        # 01:30 in Lagos: not yet due today, so the day before.
        ("daily 02:00", "2026-11-03 00:30", "2026-10-31 23:00", "2026-11-01 23:00"),
    ],
)
def test_due_windows(schedule: str, now: str, start: str, end: str) -> None:
    def utc(text: str) -> datetime:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)

    assert engine.due_window(schedule, utc(now)) == (utc(start), utc(end))
    with pytest.raises(ValueError, match="Unknown schedule"):
        engine.due_window("weekly", utc(now))


def test_scheduled_rules_run_once_per_due_window(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule_id = rule(conn, staff, "R02")
    rule(conn, staff, "R02", approve=False, name="Not approved")
    ingest(conn, TXN | {"timestamp": "2026-11-02T10:00:00+01:00"})
    run_at = datetime(2026, 11, 3, 1, 30, tzinfo=UTC)

    assert engine.schedule_due(work, run_at) == 1
    assert engine.schedule_due(work, run_at + timedelta(hours=1)) == 0
    jobs.run_all(work, "scheduled_rule", "w1", run_at, limit=10)
    [finding] = findings
    assert finding.rule.id == rule_id
    assert finding.hit.trigger_values == {"count": 1}
    assert finding.hit.window_end == datetime(2026, 11, 2, 23, tzinfo=UTC)


def test_the_worker_works_every_active_tenant(
    conn: psycopg.Connection, staff: Staff, fleet: Fleet, findings: list[Finding]
) -> None:
    rule(conn, staff, "R02")
    ingest(conn, TXN | {"timestamp": "2026-11-02T10:00:00+01:00"})
    run_at = datetime(2026, 11, 3, 1, 30, tzinfo=UTC)
    assert worker.work_fleet(fleet, "scheduled_rule", "w1", run_at) >= 1
    assert len(findings) == 1
    assert worker.work_fleet(fleet, "evaluate", "w1", LATER) >= 1
    with pytest.raises(ValueError, match="Unknown lane"):
        worker.run(fleet, "fast", once=True)


# --- Measured timeframe and migrations ------------------------------------------------------


def test_engine_status_shows_backlog_and_measured_time(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, fleet: Fleet,
    tmp_path: Path, findings: list[Finding],
) -> None:  # fmt: skip
    ingest(conn, TXN, TXN | {"transaction_id": "T-2"})
    now = datetime.now(UTC)
    work.execute("UPDATE job SET created_at = %s", (now - timedelta(seconds=30),))
    jobs.run_next(work, "evaluate", "w1", now)
    auditor = signed_in(staff, fleet, tmp_path, "auditor")
    lanes = {lane["lane"]: lane for lane in auditor.get("/v1/engine/status").json()}
    assert list(lanes) == list(jobs.LANES)
    evaluate = lanes["evaluate"]
    assert (evaluate["queued"], evaluate["done"]) == (1, 1)
    assert evaluate["p95_seconds"] == pytest.approx(30, abs=1)
    assert lanes["report"]["p50_seconds"] is None
    admin = signed_in(staff, fleet, tmp_path, "admin")
    assert admin.get("/v1/engine/status").status_code == 403


@pytest.mark.requirement("ADM-5")
def test_job_table_downgrades_and_upgrades(staff: Staff) -> None:
    migrations.downgrade("tenant", staff.url, "t0006")
    with psycopg.connect(staff.url) as conn:
        assert conn.execute("SELECT to_regclass('job')").fetchone() == (None,)
    assert migrations.upgrade("tenant", staff.url) == migrations.head_revision("tenant")
