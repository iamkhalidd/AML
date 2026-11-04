# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

"""Cases: escalation and the workspace (4.1), closure (4.2), attachments (4.3)."""

import hashlib
import uuid
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from aml.modules.access import approvals
from aml.modules.access.approvals import ApprovalError
from aml.modules.alerts import closure as alert_closure
from aml.modules.alerts.queue import AlertError
from aml.modules.cases import attachments, closure, service
from aml.modules.cases.service import CaseError, Filters
from tests.test_alerts import NOW, alerts, code, db, r01, raise_alerts, second_customer  # noqa: F401
from tests.test_approvals import Staff, staff  # noqa: F401
from tests.test_engine import rule
from tests.test_ingest_customers import audit

pytestmark = pytest.mark.requirement("CAS-1")

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


def open_case(db: psycopg.Connection, staff: Staff, *refs: str, note: str | None = None) -> Any:
    ids = raise_alerts(db, *(refs or ("T-1",)))
    case = service.escalate(db, staff.users["analyst"], ids, title=None, note=note, now=NOW)
    return case["id"]


def entry_types(db: psycopg.Connection, case_id: Any) -> list[str]:
    return [e["entry_type"] for e in service.workspace(db, case_id, now=NOW)["timeline"]]


# --- Pure logic -----------------------------------------------------------------------------


@pytest.mark.requirement("CAS-2")
@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("statement.pdf", PDF, "application/pdf"),
        ("ID.PNG", b"\x89PNG\r\n\x1a\nrest", "image/png"),
        ("photo.jpg", b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        ("notes.txt", "Ọjọ́ ìbí".encode(), "text/plain"),
        ("ledger.csv", b"date,amount\n", "text/csv"),
    ],
)
def test_attachment_types_are_checked_by_content(name: str, content: bytes, expected: str) -> None:
    assert attachments._content_type(name, content) == expected


@pytest.mark.requirement("CAS-2")
@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("tool.exe", b"MZ", "FILE_TYPE_REFUSED"),
        ("no_extension", b"abc", "FILE_TYPE_REFUSED"),
        ("fake.pdf", b"<html>", "FILE_TYPE_MISMATCH"),
        ("fake.docx", PDF, "FILE_TYPE_MISMATCH"),
        ("binary.txt", b"\xff\xfe\x00bad", "FILE_TYPE_MISMATCH"),
    ],
)
def test_attachments_of_the_wrong_type_are_refused(
    name: str, content: bytes, expected: str
) -> None:
    with pytest.raises(CaseError) as exc:
        attachments._content_type(name, content)
    assert code(exc) == expected


@pytest.mark.requirement("CAS-2")
def test_attachment_names_lose_unsafe_characters() -> None:
    assert attachments._clean_name(' ../evil\\"name\x00.pdf ') == ".._evil__name_.pdf"
    for bad in ("", "  ", ".", ".."):
        with pytest.raises(CaseError) as exc:
            attachments._clean_name(bad)
        assert code(exc) == "FILENAME_INVALID"


@pytest.mark.requirement("CAS-2")
def test_empty_and_oversized_files_are_refused_before_storage() -> None:
    user: Any = SimpleNamespace(id=uuid.uuid4(), full_name="x")
    for content, expected in ((b"", "FILE_EMPTY"), (b"x" * (attachments.MAX_BYTES + 1),
                                                   "FILE_TOO_LARGE")):  # fmt: skip
        with pytest.raises(CaseError) as exc:
            attachments.add(None, user, uuid.uuid4(), "a.txt", content, now=NOW)  # type: ignore[arg-type]
        assert code(exc) == expected


@pytest.mark.requirement("CAS-3")
def test_closure_needs_a_known_outcome_and_a_reason() -> None:
    user: Any = SimpleNamespace(id=uuid.uuid4())
    for outcome, reason, expected in (
        ("dismissed", "x", "OUTCOME_INVALID"),
        ("no_suspicion", "  ", "REASON_REQUIRED"),
    ):
        with pytest.raises(CaseError) as exc:
            closure.request_closure(None, user, uuid.uuid4(), outcome=outcome, reason=reason,  # type: ignore[arg-type]
                                    now=NOW)  # fmt: skip
        assert code(exc) == expected


@pytest.mark.requirement("CAS-5")
def test_a_note_needs_text() -> None:
    user: Any = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(CaseError) as exc:
        service.add_note(None, user, uuid.uuid4(), "   ", now=NOW)  # type: ignore[arg-type]
    assert code(exc) == "TEXT_REQUIRED"


# --- Escalation and the workspace (story 4.1) -----------------------------------------------


def test_escalating_opens_a_case_that_holds_the_alerts(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    ids = raise_alerts(db, "T-1")
    analyst = staff.users["analyst"]
    case = service.escalate(db, analyst, ids, title=None, note=" First look ", now=NOW)
    assert case["status"] == "open"
    assert case["title"] == "Investigation: Ada Okafor"
    assert case["assigned_to"] == analyst.id
    assert case["alert_count"] == 1
    assert [row["status"] for row in alerts(db)] == ["escalated"]
    assert entry_types(db, case["id"]) == ["opened", "note"]
    assert len(audit(db, "case.open")) == 1
    assert len(audit(db, "alert.escalate")) == 1

    with pytest.raises(AlertError) as exc:
        alert_closure.close(db, analyst, ids[0], reason="duplicate", note="x", now=NOW)
    assert code(exc) == "ALERT_ESCALATED"
    with pytest.raises(CaseError) as exc:
        service.escalate(db, analyst, ids, title="Again", note=None, now=NOW)
    assert code(exc) == "ALERT_ALREADY_ESCALATED"


def test_a_case_covers_one_customers_open_alerts(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (mine,) = raise_alerts(db, "T-1")
    theirs = second_customer(db, "T-9")
    analyst = staff.users["analyst"]
    with pytest.raises(CaseError) as exc:
        service.escalate(db, analyst, [mine, theirs], title=None, note=None, now=NOW)
    assert code(exc) == "ALERT_OTHER_CUSTOMER"
    with pytest.raises(CaseError) as exc:
        service.escalate(db, analyst, [uuid.uuid4()], title=None, note=None, now=NOW)
    assert code(exc) == "ALERT_NOT_FOUND"
    alert_closure.close(db, analyst, mine, reason="duplicate", note="x", now=NOW)
    with pytest.raises(CaseError) as exc:
        service.escalate(db, analyst, [mine], title=None, note=None, now=NOW)
    assert code(exc) == "ALERT_CLOSED"
    assert db.execute("SELECT count(*) FROM aml_case").fetchone() == (0,)


def test_later_alerts_can_be_linked_to_an_open_case(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id = open_case(db, staff)
    # The first rule adds evidence to its escalated alert; a second rule raises a new one.
    rule(db, staff, "R01", name="R01 second line")
    (later,) = raise_alerts(db, "T-2")
    case = service.link_alerts(db, staff.users["analyst"], case_id, [later], now=NOW)
    assert case["alert_count"] == 2
    assert entry_types(db, case_id)[-1] == "alerts_linked"


@pytest.mark.requirement("CAS-5")
def test_entries_are_append_only_and_corrected_by_new_entries(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id = open_case(db, staff)
    analyst = staff.users["analyst"]
    note = service.add_note(db, analyst, case_id, "Customer is a trader", now=NOW)
    fix = service.add_note(db, analyst, case_id, "Customer is a farmer", corrects=note["id"],
                           now=NOW)  # fmt: skip
    assert (fix["entry_type"], fix["corrects"]) == ("correction", note["id"])
    with pytest.raises(CaseError) as exc:
        service.add_note(db, analyst, case_id, "x", corrects=999_999, now=NOW)
    assert code(exc) == "ENTRY_NOT_FOUND"
    with pytest.raises(psycopg.Error):
        db.execute("UPDATE case_entry SET body = 'rewritten' WHERE id = %s", (note["id"],))
    with pytest.raises(psycopg.Error):
        db.execute("DELETE FROM case_entry WHERE id = %s", (note["id"],))
    assert len(audit(db, "case.note")) == 1
    assert len(audit(db, "case.correction")) == 1


def test_case_assignment_and_listing(db: psycopg.Connection, staff: Staff, r01: Any) -> None:
    case_id = open_case(db, staff)
    mlro, analyst2 = staff.users["mlro"], staff.users["analyst2"]
    case = service.assign(db, mlro, case_id, analyst2.id, now=NOW)
    assert case["assigned_to"] == analyst2.id
    with pytest.raises(CaseError) as exc:
        service.assign(db, mlro, case_id, staff.users["auditor"].id, now=NOW)
    assert code(exc) == "ASSIGNEE_INVALID"

    rows, cursor = service.list_cases(db, analyst2, Filters(assignee="me"))
    assert [r["id"] for r in rows] == [case_id]
    assert cursor is None
    assert service.list_cases(db, mlro, Filters(assignee="me"))[0] == []
    assert service.list_cases(db, mlro, Filters(search="Okafor"))[0][0]["id"] == case_id


def test_the_workspace_brings_alerts_timeline_and_customer_together(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id = open_case(db, staff)
    space = service.workspace(db, case_id, now=NOW)
    assert space["case"]["id"] == case_id
    assert [a["txn_count"] for a in space["alerts"]] == [1]
    assert space["customer"]["customer"]["external_id"] == "C-1"
    assert space["closure_requests"] == []
    with pytest.raises(CaseError) as exc:
        service.workspace(db, uuid.uuid4(), now=NOW)
    assert code(exc) == "CASE_NOT_FOUND"


# --- Closure (story 4.2) --------------------------------------------------------------------


@pytest.mark.requirement("CAS-3")
@pytest.mark.requirement("CAS-4")
def test_a_second_person_approves_closure_and_the_alerts_close_with_the_case(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id = open_case(db, staff)
    analyst, mlro = staff.users["analyst"], staff.users["mlro"]
    reason = "Proceeds of a land sale; deed seen"
    approval = closure.request_closure(
        db, analyst, case_id, outcome="no_suspicion", reason=reason, now=NOW
    )
    assert entry_types(db, case_id)[-1] == "closure_requested"
    assert service.get_listed(db, case_id)["closure_pending"] is True  # type: ignore[index]
    with pytest.raises(ApprovalError) as exc:
        approvals.approve(db, analyst, approval.id, note=None, now=NOW)
    assert code(exc) in {"SELF_APPROVAL", "PERMISSION_DENIED"}

    approvals.approve(db, mlro, approval.id, note="Agreed", now=NOW)
    case = service.get_listed(db, case_id)
    assert case is not None
    assert (case["status"], case["outcome"]) == ("closed", "no_suspicion")
    assert case["closure_approved_by"] == mlro.id
    assert [(r["status"], r["closure_reason"]) for r in alerts(db)] == [
        ("closed", "case_no_suspicion")
    ]
    assert len(audit(db, "case.close")) == 1
    with pytest.raises(CaseError) as err:
        service.add_note(db, analyst, case_id, "late note", now=NOW)
    assert code(err) == "CASE_CLOSED"


@pytest.mark.requirement("CAS-4")
def test_the_investigator_holding_the_case_cannot_approve_its_closure(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id = open_case(db, staff)
    mlro, deputy = staff.users["mlro"], staff.users["deputy"]
    service.assign(db, mlro, case_id, mlro.id, now=NOW)
    approval = closure.request_closure(db, deputy, case_id, outcome="reported",
                                       reason="STR filed", now=NOW)  # fmt: skip
    with pytest.raises(ApprovalError) as exc:
        approvals.approve(db, mlro, approval.id, note=None, now=NOW)
    assert code(exc) == "SELF_APPROVAL"
    assert service.get_listed(db, case_id)["status"] == "open"  # type: ignore[index]


@pytest.mark.requirement("CAS-3")
def test_analysts_cannot_approve_closures(db: psycopg.Connection, staff: Staff, r01: Any) -> None:
    case_id = open_case(db, staff)
    approval = closure.request_closure(db, staff.users["analyst"], case_id, outcome="referred",
                                       reason="Referred to the police", now=NOW)  # fmt: skip
    with pytest.raises(ApprovalError):
        approvals.approve(db, staff.users["analyst2"], approval.id, note=None, now=NOW)


# --- Attachments (story 4.3) ----------------------------------------------------------------


@pytest.mark.requirement("CAS-2")
def test_attachments_are_stored_with_their_hash_and_logged(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    case_id = open_case(db, staff)
    row = attachments.add(db, staff.users["analyst"], case_id, "deed.pdf", PDF, now=NOW)
    assert row["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert row["size_bytes"] == len(PDF)
    assert [a["id"] for a in attachments.list_for(db, case_id)] == [row["id"]]
    assert attachments.fetch(db, case_id, row["id"]) == ("deed.pdf", "application/pdf", PDF)
    assert entry_types(db, case_id)[-1] == "attachment_added"
    assert len(audit(db, "case.attachment_added")) == 1
    with pytest.raises(psycopg.Error):
        db.execute("DELETE FROM case_attachment WHERE id = %s", (row["id"],))
    with pytest.raises(CaseError) as exc:
        attachments.fetch(db, case_id, uuid.uuid4())
    assert code(exc) == "ATTACHMENT_NOT_FOUND"
