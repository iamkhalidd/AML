# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

"""Alerts: raising and deduplication (3.1), the queue (3.2), detail (3.3), closure (3.4)."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from aml.control.tenants import Fleet
from aml.modules.alerts import closure, detail, queue, service
from aml.modules.alerts.queue import AlertError, Filters
from aml.modules.ingestion import service as ingestion
from aml.modules.notifications import service as notify
from tests.test_approvals import Staff, signed_in, staff  # noqa: F401
from tests.test_engine import CALLER, LATER, rule
from tests.test_ingest_customers import ACCOUNT, PERSON, audit
from tests.test_library import DAY, evaluate, load, tx

pytestmark = pytest.mark.requirement("ALT-1")

# Alerts are created when the evaluate lane runs at LATER; work on them happens after that.
NOW = LATER + timedelta(hours=1)
CASH = 500_000_000  # R01's individual threshold: exactly at it fires


@pytest.fixture
def db(staff: Staff) -> Iterator[psycopg.Connection]:
    """One autocommit connection, so every service call commits before the worker reads."""
    with psycopg.connect(staff.url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def r01(db: psycopg.Connection, staff: Staff) -> Any:
    return rule(db, staff, "R01")


def alerts(db: psycopg.Connection) -> list[dict[str, Any]]:
    with db.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT id, status::text AS status, severity::text AS severity, hits, customer_id,"
            " assigned_to, closure_reason FROM alert ORDER BY created_at, id"
        ).fetchall()


def raise_alerts(db: psycopg.Connection, *refs: str, customer: Any = PERSON) -> list[Any]:
    """Ingest a cash deposit at the threshold per reference, evaluate, return new alert IDs."""
    before = {row["id"] for row in alerts(db)}
    txns = [tx(ref, DAY + timedelta(minutes=i), CASH) for i, ref in enumerate(refs)]
    load(db, *txns, customer=customer)
    evaluate(db)
    return [row["id"] for row in alerts(db) if row["id"] not in before]


def second_customer(db: psycopg.Connection, ref: str) -> Any:
    """Another customer with their own account, and one alert for them."""
    person = PERSON | {"customer_id": "C-2", "full_name": "Bayo Adewale"}
    ingestion.ingest_customers(db, CALLER, "core", [person])
    ingestion.ingest_accounts(
        db, CALLER, "core", [ACCOUNT | {"account_id": "A-2", "customer_id": "C-2"}]
    )
    txn = tx(ref, DAY, CASH, account_id="A-2", customer_id="C-2")
    ingestion.ingest_transactions(db, CALLER, "core", [txn])
    evaluate(db)
    row = db.execute(
        "SELECT a.id FROM alert a JOIN customer c ON c.id = a.customer_id"
        " WHERE c.external_id = 'C-2'"
    ).fetchone()
    assert row is not None
    return row[0]


def code(exc: pytest.ExceptionInfo[Any]) -> str:
    return str(exc.value.code)


# --- Pure logic -----------------------------------------------------------------------------


def test_severity_is_raised_one_level_for_high_risk_customers() -> None:
    assert service.severity_for("low", "high") == "medium"
    assert service.severity_for("medium", "high") == "high"
    assert service.severity_for("high", "high") == "high"
    for risk in ("low", "medium", None):
        assert service.severity_for("low", risk) == "low"


def test_dedupe_key_names_rule_customer_and_window_in_utc() -> None:
    rule_id, customer = uuid.uuid4(), uuid.uuid4()
    start = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    hit = SimpleNamespace(customer_id=customer, window_start=start, window_end=None)
    finding: Any = SimpleNamespace(rule=SimpleNamespace(id=rule_id), hit=hit)
    assert service.dedupe_key(finding) == f"{rule_id}:{customer}:{start.isoformat()}:"
    hit.window_start = None
    assert service.dedupe_key(finding) == f"{rule_id}:{customer}::"


@pytest.mark.requirement("ALT-2")
def test_cursors_round_trip_and_refuse_tampering() -> None:
    assert queue.decode_cursor(None) == 0
    assert queue.decode_cursor(queue.encode_cursor(150)) == 150
    for bad in ("not-a-cursor", "!!!"):
        with pytest.raises(AlertError) as exc:
            queue.decode_cursor(bad)
        assert code(exc) == "CURSOR_INVALID"


@pytest.mark.requirement("ALT-4")
def test_closing_needs_a_note_before_anything_is_read() -> None:
    user: Any = SimpleNamespace(id=uuid.uuid4(), role="analyst")
    with pytest.raises(AlertError) as exc:
        closure.close(None, user, uuid.uuid4(), reason="false_positive", note="  ", now=NOW)  # type: ignore[arg-type]
    assert code(exc) == "NOTE_REQUIRED"


@pytest.mark.requirement("ADM-2")
@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        ([], "REASONS_INVALID"),
        ([{"code": "a", "label": "A"}, {"code": "a", "label": "B"}], "REASONS_INVALID"),
        ([{"code": "Bad Code", "label": "A"}], "REASONS_INVALID"),
        ([{"code": "1st", "label": "A"}], "REASONS_INVALID"),
        ([{"code": "ok", "label": "  "}], "REASONS_INVALID"),
        ([{"code": f"r{i}", "label": "x"} for i in range(51)], "REASONS_INVALID"),
    ],
)
def test_closure_reason_lists_are_validated(reasons: list[dict[str, str]], expected: str) -> None:
    admin: Any = SimpleNamespace(id=uuid.uuid4(), role="administrator")
    with pytest.raises(AlertError) as exc:
        closure.set_reasons(None, admin, reasons, rationale="Tidy list", now=NOW)  # type: ignore[arg-type]
    assert code(exc) == expected


@pytest.mark.requirement("ADM-2")
def test_only_settings_managers_with_a_rationale_change_reasons() -> None:
    reasons = [{"code": "ok", "label": "Fine"}]
    analyst: Any = SimpleNamespace(id=uuid.uuid4(), role="analyst")
    with pytest.raises(AlertError) as exc:
        closure.set_reasons(None, analyst, reasons, rationale="x", now=NOW)  # type: ignore[arg-type]
    assert code(exc) == "PERMISSION_DENIED"
    admin: Any = SimpleNamespace(id=uuid.uuid4(), role="administrator")
    with pytest.raises(AlertError) as exc:
        closure.set_reasons(None, admin, reasons, rationale=" ", now=NOW)  # type: ignore[arg-type]
    assert code(exc) == "RATIONALE_REQUIRED"


# --- Raising alerts (story 3.1) -------------------------------------------------------------


@pytest.mark.requirement("AUD-7")
def test_a_finding_raises_a_traceable_alert(db: psycopg.Connection, staff: Staff, r01: Any) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    row = queue.get_listed(db, alert_id)
    assert row is not None
    assert row["status"] == "open"
    assert row["severity"] == "medium"
    assert row["rule_id"] == r01
    assert row["customer_external_id"] == "C-1"
    assert row["amount"] == CASH
    linked = db.execute("SELECT count(*) FROM alert_txn WHERE alert_id = %s", (alert_id,))
    assert linked.fetchone() == (1,)
    created = audit(db, "alert.created")
    assert len(created) == 1
    assert created[0]["actor_type"] == "system"
    assert created[0]["after"]["rule_version"] == 1
    assert created[0]["after"]["trigger_values"]


def test_high_risk_customers_get_a_higher_severity(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1", customer=PERSON | {"risk_rating": "HIGH"})
    row = queue.get_listed(db, alert_id)
    assert row is not None
    assert row["severity"] == "high"


def test_repeat_findings_add_evidence_to_the_open_alert(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    assert raise_alerts(db, "T-2") == []
    (row,) = alerts(db)
    assert row["hits"] == 2
    assert db.execute(
        "SELECT count(*) FROM alert_txn WHERE alert_id = %s", (alert_id,)
    ).fetchone() == (2,)
    assert len(audit(db, "alert.evidence_added")) == 1
    events = db.execute(
        "SELECT event_type FROM alert_event WHERE alert_id = %s ORDER BY id", (alert_id,)
    ).fetchall()
    assert [e[0] for e in events] == ["created", "evidence_added"]


def test_a_closed_alert_does_not_absorb_new_findings(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (first,) = raise_alerts(db, "T-1")
    closure.close(db, staff.users["analyst"], first, reason="false_positive",
                  note="Salary paid in cash", now=NOW)  # fmt: skip
    (second,) = raise_alerts(db, "T-2")
    assert second != first
    assert [row["status"] for row in alerts(db)] == ["closed", "open"]


# --- The queue (story 3.2) ------------------------------------------------------------------


@pytest.mark.requirement("ALT-2")
def test_queue_sorts_filters_and_pages(db: psycopg.Connection, staff: Staff, r01: Any) -> None:
    (low_risk,) = raise_alerts(db, "T-1")
    high_risk = second_customer(db, "T-9")
    db.execute("UPDATE alert SET severity = 'high' WHERE id = %s", (high_risk,))
    analyst = staff.users["analyst"]

    page = queue.list_alerts(db, analyst, Filters())
    assert [item["id"] for item in page.items] == [high_risk, low_risk]
    assert page.next_cursor is None

    first = queue.list_alerts(db, analyst, Filters(), limit=1)
    assert [item["id"] for item in first.items] == [high_risk]
    assert first.next_cursor is not None
    rest = queue.list_alerts(db, analyst, Filters(), limit=1, cursor=first.next_cursor)
    assert [item["id"] for item in rest.items] == [low_risk]

    by_severity = queue.list_alerts(db, analyst, Filters(severity=("medium",)))
    assert [item["id"] for item in by_severity.items] == [low_risk]
    by_search = queue.list_alerts(db, analyst, Filters(search="Bayo"))
    assert [item["id"] for item in by_search.items] == [high_risk]
    oldest_first = queue.list_alerts(db, analyst, Filters(), sort=("created_at", "customer"))
    assert len(oldest_first.items) == 2

    with pytest.raises(AlertError) as exc:
        queue.list_alerts(db, analyst, Filters(), sort=("colour",))
    assert code(exc) == "SORT_INVALID"


@pytest.mark.requirement("ALT-2")
def test_assignment_moves_alerts_between_open_and_in_review(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    mlro, analyst = staff.users["mlro"], staff.users["analyst"]

    row = queue.assign(db, mlro, alert_id, analyst.id, now=NOW)
    assert (row["status"], row["assigned_to"]) == ("in_review", analyst.id)
    mine = queue.list_alerts(db, analyst, Filters(assignee="me"))
    assert [item["id"] for item in mine.items] == [alert_id]
    assert queue.list_alerts(db, analyst, Filters(assignee="none")).items == []
    assert queue.counts(db, analyst)["mine"] == 1

    row = queue.assign(db, mlro, alert_id, None, now=NOW)
    assert (row["status"], row["assigned_to"]) == ("open", None)
    assert [e["action"] for e in audit(db, "alert.assign") + audit(db, "alert.unassign")] == [
        "alert.assign",
        "alert.unassign",
    ]


@pytest.mark.requirement("ADM-3")
def test_assigning_someone_else_queues_their_email(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    analyst = staff.users["analyst"]
    queue.assign(db, analyst, alert_id, analyst.id, now=NOW)  # took it themselves: no email
    queue.assign(db, analyst, alert_id, None, now=NOW)
    queue.assign(db, staff.users["mlro"], alert_id, analyst.id, now=NOW)
    jobs = db.execute(
        "SELECT payload FROM job WHERE lane = %s AND job_type = %s", (notify.LANE, notify.EMAIL_JOB)
    ).fetchall()
    assert [(j[0]["user_id"], j[0]["kind"]) for j in jobs] == [(str(analyst.id), "assignment")]


@pytest.mark.requirement("ALT-2")
def test_alerts_go_only_to_active_users_who_work_alerts(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    mlro = staff.users["mlro"]
    for who in ("auditor", "admin"):
        with pytest.raises(AlertError) as exc:
            queue.assign(db, mlro, alert_id, staff.users[who].id, now=NOW)
        assert code(exc) == "ASSIGNEE_INVALID"
    with pytest.raises(AlertError) as exc:
        queue.assign(db, mlro, uuid.uuid4(), None, now=NOW)
    assert code(exc) == "ALERT_NOT_FOUND"
    closure.close(db, mlro, alert_id, reason="duplicate", note="Seen", now=NOW)
    with pytest.raises(AlertError) as exc:
        queue.assign(db, mlro, alert_id, mlro.id, now=NOW)
    assert code(exc) == "ALERT_CLOSED"


@pytest.mark.requirement("ALT-2")
def test_the_queue_endpoint_needs_a_role_that_sees_alerts(
    db: psycopg.Connection, staff: Staff, fleet: Fleet, tmp_path: Path, r01: Any
) -> None:
    raise_alerts(db, "T-1")
    analyst = signed_in(staff, fleet, tmp_path, "analyst")
    response = analyst.get("/v1/alerts")
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 1
    assert analyst.get("/v1/alerts", params={"sort": "colour"}).status_code == 400
    admin = signed_in(staff, fleet, tmp_path, "admin")
    assert admin.get("/v1/alerts").status_code == 403


# --- Detail (story 3.3) ---------------------------------------------------------------------


@pytest.mark.requirement("ALT-3")
@pytest.mark.requirement("CUS-8")
def test_detail_shows_the_trigger_the_customer_and_the_history(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    detail.add_note(db, staff.users["analyst"], alert_id, "Called the branch", now=NOW)
    view = detail.get_detail(db, alert_id, now=NOW)
    assert view["alert"]["id"] == alert_id
    assert view["rule"]
    assert [t["external_id"] for t in view["transactions"]] == ["T-1"]
    assert [e["event_type"] for e in view["events"]] == ["created", "note"]
    customer = view["customer"]
    assert customer["customer"]["external_id"] == "C-1"
    assert customer["activity_window_days"] == 30
    assert [a["external_id"] for a in customer["accounts"]] == ["A-1"]
    assert len(audit(db, "alert.note_added")) == 1


@pytest.mark.requirement("ALT-3")
def test_detail_lists_the_customers_earlier_alerts(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (first,) = raise_alerts(db, "T-1")
    closure.close(db, staff.users["analyst"], first, reason="duplicate", note="x", now=NOW)
    (second,) = raise_alerts(db, "T-2")
    view = detail.get_detail(db, second, now=NOW)
    assert [a["id"] for a in view["customer"]["earlier_alerts"]] == [first]


# --- Closure (story 3.4) --------------------------------------------------------------------


@pytest.mark.requirement("ALT-4")
def test_closing_records_the_reason_and_note(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    analyst = staff.users["analyst"]
    row = closure.close(db, analyst, alert_id, reason="consistent_with_profile",
                        note=" Trader; cash is normal ", now=NOW)  # fmt: skip
    assert row["status"] == "closed"
    (stored,) = alerts(db)
    assert stored["closure_reason"] == "consistent_with_profile"
    (entry,) = audit(db, "alert.close")
    assert entry["after"]["note"] == "Trader; cash is normal"
    assert entry["before"]["status"] == "open"
    with pytest.raises(AlertError) as exc:
        closure.close(db, analyst, alert_id, reason="duplicate", note="again", now=NOW)
    assert code(exc) == "ALERT_CLOSED"


@pytest.mark.requirement("ALT-4")
def test_closing_needs_a_reason_from_the_list(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    with pytest.raises(AlertError) as exc:
        closure.close(db, staff.users["analyst"], alert_id, reason="bored", note="x", now=NOW)
    assert code(exc) == "REASON_INVALID"
    with pytest.raises(AlertError) as exc:
        closure.close(db, staff.users["analyst"], uuid.uuid4(), reason="duplicate", note="x",
                      now=NOW)  # fmt: skip
    assert code(exc) == "ALERT_NOT_FOUND"


@pytest.mark.requirement("ADM-2")
def test_reason_lists_are_versioned_settings(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    assert [r["code"] for r in closure.reasons(db, NOW)] == [
        r["code"] for r in closure.DEFAULT_REASONS
    ]
    new = [{"code": "explained", "label": " Explained by the customer "}]
    version = closure.set_reasons(db, staff.users["admin"], new, rationale="Simpler list", now=NOW)
    assert version == 1
    assert closure.reasons(db, NOW) == [{"code": "explained", "label": "Explained by the customer"}]
    earlier = closure.reasons(db, NOW - timedelta(seconds=1))
    assert earlier[0]["code"] == "false_positive"
    (alert_id,) = raise_alerts(db, "T-1")
    with pytest.raises(AlertError) as exc:
        closure.close(db, staff.users["analyst"], alert_id, reason="false_positive", note="x",
                      now=NOW)  # fmt: skip
    assert code(exc) == "REASON_INVALID"
    closure.close(db, staff.users["analyst"], alert_id, reason="explained", note="x", now=NOW)
    (change,) = audit(db, "setting.change")
    assert change["after"]["version"] == 1
