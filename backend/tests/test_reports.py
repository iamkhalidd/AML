# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

"""Reports: STR and CTR drafts (5.1), approval and submission (5.2), the dashboard (5.3)."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from aml.modules.access import approvals
from aml.modules.access.approvals import ApprovalError
from aml.modules.cases import closure as case_closure
from aml.modules.cases import service as cases
from aml.modules.reports import dashboard, service, workflow
from aml.modules.reports.service import ReportError
from tests.test_alerts import NOW, code, db, r01, raise_alerts  # noqa: F401
from tests.test_approvals import Staff, staff  # noqa: F401
from tests.test_ingest_customers import COMPANY, audit
from tests.test_library import DAY, load, tx

pytestmark = pytest.mark.requirement("REP-1")

GROUNDS = "Cash deposits far above declared income, with no business to explain them."
ANYONE: Any = SimpleNamespace(id=uuid.uuid4(), role="compliance_officer", full_name="x")


def str_for_case(db: psycopg.Connection, staff: Staff) -> tuple[Any, Any]:
    """An open case from one alert and its drafted STR, by the analyst."""
    ids = raise_alerts(db, "T-1")
    case = cases.escalate(db, staff.users["analyst"], ids, title=None, note=None, now=NOW)
    report_id = service.draft_str(db, staff.users["analyst"], case["id"], now=NOW)
    return case["id"], report_id


def ready(db: psycopg.Connection, staff: Staff, report_id: Any) -> None:
    narrative = service.get(db, report_id)["narrative"]
    start = narrative.index(service.STR_PLACEHOLDER)
    end = narrative.index("]", start) + 1
    service.update_draft(db, staff.users["analyst"], report_id,
                         narrative=narrative[:start] + GROUNDS + narrative[end:], content=None,
                         now=NOW)  # fmt: skip


# --- Pure logic -----------------------------------------------------------------------------


@pytest.mark.requirement("REP-3")
def test_the_fingerprint_changes_with_the_narrative_or_content() -> None:
    base = workflow.fingerprint("text", {"a": 1, "b": [1, 2]})
    assert base == workflow.fingerprint("text", {"b": [1, 2], "a": 1})
    assert base != workflow.fingerprint("text ", {"a": 1, "b": [1, 2]})
    assert base != workflow.fingerprint("text", {"a": 2, "b": [1, 2]})
    assert len(base) == 64


@pytest.mark.parametrize(
    ("narrative", "content", "expected"),
    [
        (None, None, "NOTHING_TO_CHANGE"),
        ("x" * (service.MAX_NARRATIVE + 1), None, "NARRATIVE_TOO_LONG"),
        (None, {"format": "goaml-5"}, "FORMAT_INVALID"),
    ],
)
def test_draft_edits_are_checked_before_anything_is_read(
    narrative: str | None, content: dict[str, Any] | None, expected: str
) -> None:
    with pytest.raises(ReportError) as exc:
        service.update_draft(None, ANYONE, uuid.uuid4(), narrative=narrative, content=content,  # type: ignore[arg-type]
                             now=NOW)  # fmt: skip
    assert code(exc) == expected


@pytest.mark.requirement("REP-3")
@pytest.mark.parametrize(
    ("reference", "expected"),
    [("  ", "SUBMISSION_REFERENCE_REQUIRED"), ("N" * 201, "SUBMISSION_REFERENCE_INVALID")],
)
def test_submission_needs_a_sensible_filing_reference(reference: str, expected: str) -> None:
    with pytest.raises(ReportError) as exc:
        workflow.mark_submitted(None, ANYONE, uuid.uuid4(), submission_reference=reference,  # type: ignore[arg-type]
                                now=NOW)  # fmt: skip
    assert code(exc) == expected


@pytest.mark.requirement("REP-3")
def test_asking_for_approval_needs_a_note() -> None:
    with pytest.raises(ReportError) as exc:
        workflow.request_approval(None, ANYONE, uuid.uuid4(), note=" ", now=NOW)  # type: ignore[arg-type]
    assert code(exc) == "NOTE_REQUIRED"


def test_the_ctr_period_must_be_in_the_past_and_forwards() -> None:
    for start, end in ((NOW, NOW), (NOW - timedelta(days=1), NOW + timedelta(days=1))):
        with pytest.raises(ReportError) as exc:
            service.draft_ctrs(None, ANYONE, date_from=start, date_to=end, now=NOW)  # type: ignore[arg-type]
        assert code(exc) == "PERIOD_INVALID"


# --- STR and CTR drafts (story 5.1) ---------------------------------------------------------


def test_an_str_is_drafted_from_the_case_record(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id, report_id = str_for_case(db, staff)
    report = service.get(db, report_id)
    assert (report["report_type"], report["status"]) == ("str", "draft")
    assert report["case_id"] == case_id
    assert report["content"]["format"] == service.FORMAT
    assert report["content"]["subject"]["external_id"] == "C-1"
    assert [t["external_id"] for t in report["transactions"]] == ["T-1"]
    assert service.STR_PLACEHOLDER in report["narrative"]
    assert "Ada Okafor" in report["narrative"]
    entries = cases.workspace(db, case_id, now=NOW)["timeline"]
    assert entries[-1]["entry_type"] == "report_drafted"
    with pytest.raises(ReportError) as exc:
        service.draft_str(db, staff.users["analyst"], case_id, now=NOW)
    assert code(exc) == "REPORT_EXISTS"
    with pytest.raises(ReportError) as exc:
        service.draft_str(db, staff.users["analyst"], uuid.uuid4(), now=NOW)
    assert code(exc) == "CASE_NOT_FOUND"


def test_ctrs_cover_each_settled_cash_transaction_at_the_threshold_once(
    db: psycopg.Connection, staff: Staff
) -> None:
    load(
        db,
        tx("T-1", DAY, 500_000_000),
        tx("T-2", DAY + timedelta(minutes=1), 499_999_999),
        tx("T-3", DAY + timedelta(minutes=2), 600_000_000, channel="TRANSFER",
           transaction_type="TRANSFER"),
        tx("T-4", DAY + timedelta(minutes=3), 700_000_000, status="FAILED"),
    )  # fmt: skip
    load(db, tx("T-5", DAY, 999_999_999, customer_id="B-1"), customer=COMPANY)
    officer = staff.users["mlro"]
    period = {"date_from": DAY - timedelta(days=1), "date_to": NOW, "now": NOW}
    ids, more = service.draft_ctrs(db, officer, **period)
    assert more is False
    assert len(ids) == 1
    report = service.get(db, ids[0])
    assert [t["external_id"] for t in report["transactions"]] == ["T-1"]
    assert report["content"]["threshold"]["customer_type"] == "individual"
    assert service.draft_ctrs(db, officer, **period) == ([], False)
    assert len(audit(db, "report.ctr_run")) == 2


def test_the_ctr_threshold_is_a_versioned_setting(db: psycopg.Connection, staff: Staff) -> None:
    assert service.ctr_threshold(db, NOW) == service.DEFAULT_CTR | {"version": 0}
    admin = staff.users["admin"]
    for kwargs, expected in (
        ({"individual_minor": 0, "business_minor": 1, "rationale": "x"}, "THRESHOLD_INVALID"),
        ({"individual_minor": 1, "business_minor": 1, "rationale": " "}, "RATIONALE_REQUIRED"),
    ):
        with pytest.raises(ReportError) as exc:
            service.set_ctr_threshold(db, admin, now=NOW, **kwargs)
        assert code(exc) == expected
    with pytest.raises(ReportError) as exc:
        service.set_ctr_threshold(db, staff.users["mlro"], individual_minor=1, business_minor=1,
                                  rationale="x", now=NOW)  # fmt: skip
    assert code(exc) == "PERMISSION_DENIED"
    service.set_ctr_threshold(
        db, admin, individual_minor=100_000_000, business_minor=200_000_000,
        rationale="New CBN circular", now=NOW,
    )  # fmt: skip
    current = service.ctr_threshold(db, NOW)
    assert (current["individual_minor"], current["version"]) == (100_000_000, 1)


# --- Approval and submission (story 5.2) ----------------------------------------------------


@pytest.mark.requirement("REP-3")
def test_a_report_needs_grounds_and_a_second_person_before_submission(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id, report_id = str_for_case(db, staff)
    analyst, mlro = staff.users["analyst"], staff.users["mlro"]
    with pytest.raises(ReportError) as exc:
        workflow.request_approval(db, analyst, report_id, note="Please check", now=NOW)
    assert code(exc) == "REPORT_INCOMPLETE"
    with pytest.raises(ReportError) as exc:
        workflow.mark_submitted(db, mlro, report_id, submission_reference="NFIU-1", now=NOW)
    assert code(exc) == "REPORT_NOT_APPROVED"

    ready(db, staff, report_id)
    approval = workflow.request_approval(db, analyst, report_id, note="Please check", now=NOW)
    assert service.get(db, report_id)["approval_pending"] is True
    with pytest.raises(ReportError) as exc:
        service.update_draft(db, analyst, report_id, narrative="changed", content=None, now=NOW)
    assert code(exc) == "APPROVAL_PENDING"

    approvals.approve(db, mlro, approval.id, note="Grounds are clear", now=NOW)
    assert service.get(db, report_id)["status"] == "approved"
    with pytest.raises(ReportError) as exc:
        service.update_draft(db, analyst, report_id, narrative="changed", content=None, now=NOW)
    assert code(exc) == "REPORT_NOT_DRAFT"

    workflow.mark_submitted(db, mlro, report_id, submission_reference=" NFIU-2026-0042 ",
                            now=NOW)  # fmt: skip
    report = service.get(db, report_id)
    assert (report["status"], report["submission_reference"]) == ("submitted", "NFIU-2026-0042")
    assert len(audit(db, "report.approve")) == 1
    assert len(audit(db, "report.submit")) == 1
    entries = [e["entry_type"] for e in cases.workspace(db, case_id, now=NOW)["timeline"]]
    assert entries[-3:] == ["report_approval_requested", "report_approved", "report_submitted"]
    assert [r["status"] for r in workflow.requests_for(db, report_id)] == ["approved"]


@pytest.mark.requirement("REP-3")
def test_the_drafter_cannot_approve_their_own_report(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    ids = raise_alerts(db, "T-1")
    mlro, deputy = staff.users["mlro"], staff.users["deputy"]
    case = cases.escalate(db, mlro, ids, title=None, note=None, now=NOW)
    report_id = service.draft_str(db, mlro, case["id"], now=NOW)
    ready(db, staff, report_id)  # the analyst edits, but the officer drafted it
    approval = workflow.request_approval(db, deputy, report_id, note="Check", now=NOW)
    with pytest.raises(ApprovalError) as exc:
        approvals.approve(db, mlro, approval.id, note=None, now=NOW)
    assert code(exc) == "SELF_APPROVAL"
    assert service.get(db, report_id)["status"] == "draft"


# --- The dashboard (story 5.3) --------------------------------------------------------------


@pytest.mark.requirement("REP-4")
@pytest.mark.requirement("REP-10")
def test_dashboard_figures_reconcile_to_the_records(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    empty = dashboard.build(db, date_from=NOW - timedelta(days=30), date_to=NOW, now=NOW)
    assert empty["alerts"]["created"] == 0
    assert empty["alerts"]["false_positive_share"] is None
    assert [r["report_type"] for r in empty["reports"]] == ["str", "ctr"]

    case_id, report_id = str_for_case(db, staff)
    approval = case_closure.request_closure(
        db, staff.users["analyst"], case_id, outcome="reported", reason="STR drafted", now=NOW
    )
    approvals.approve(db, staff.users["mlro"], approval.id, note=None, now=NOW)

    later = NOW + timedelta(minutes=1)
    figures = dashboard.build(db, date_from=NOW - timedelta(days=30), date_to=later, now=later)
    alerts = figures["alerts"]
    assert alerts["created"] == 1
    assert alerts["closed"] == 1
    assert alerts["closed_no_suspicion"] == 0  # closed as reported, so not a false positive
    assert alerts["false_positive_share"] == 0
    assert [(r["rule_id"], r["created"]) for r in alerts["by_rule"]] == [(r01, 1)]
    assert figures["cases"]["opened"] == 1
    assert figures["cases"]["outcomes"] == {"no_suspicion": 0, "reported": 1, "referred": 0}
    strs = figures["reports"][0]
    assert (strs["drafted"], strs["submitted"], strs["outstanding"]) == (1, 0, 1)
    assert figures["str_conversion"] == {"strs_submitted": 0, "cases_closed": 1, "rate": 0}
    buckets = [b["bucket"] for b in figures["ageing"]["alerts"]]
    assert buckets == [label for label, _, _ in dashboard.AGE_BUCKETS]
    assert sum(b["count"] for b in figures["ageing"]["alerts"]) == 0
    assert report_id
