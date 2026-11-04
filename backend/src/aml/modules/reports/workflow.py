"""Report approval by a second person and submission status (REP-3).

A report moves draft, approved, submitted. The drafter asks for approval; the request names
a fingerprint of the report's content, and the draft cannot change while it waits. A
second person with the right to approve approves it through ``/v1/approvals/{id}/approve``;
neither the requester nor the person who drafted it can. Once approved, someone with the
right to submit records that it was filed with the NFIU and the reference they returned.
Every step records who and when.
"""

import hashlib
import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from aml.modules.access import approvals
from aml.modules.access.approvals import Approval, ApprovalError
from aml.modules.access.service import User
from aml.modules.audit import store
from aml.modules.cases import service as cases
from aml.modules.reports.service import STR_PLACEHOLDER, ReportError

KIND = "report_submission"
MAX_SUBMISSION_REFERENCE = 200


def fingerprint(narrative: str, content: dict[str, Any]) -> str:
    body = json.dumps({"narrative": narrative, "content": content}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _lock(conn: psycopg.Connection, report_id: Any) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT id, reference, report_type::text AS report_type, status::text AS status,"
            " case_id, narrative, content, created_by FROM report WHERE id = %s FOR UPDATE",
            (report_id,),
        ).fetchone()
    if row is None:
        raise ReportError("REPORT_NOT_FOUND", "No report has this ID")
    return row


def _case_entry(
    conn: psycopg.Connection, report: dict[str, Any], entry: str, at: datetime, user_id: Any
) -> None:
    if report["case_id"] is not None:
        detail = {"report_id": str(report["id"]), "reference": report["reference"]}
        cases.add_entry(conn, report["case_id"], entry, at, user_id, detail=detail)


def request_approval(
    conn: psycopg.Connection,
    user: User,
    report_id: Any,
    *,
    note: str,
    now: datetime,
    ip: str | None = None,
) -> Approval:
    note = note.strip()
    if not note:
        raise ReportError("NOTE_REQUIRED", "Say what the approver should check")
    with conn.transaction():
        report = _lock(conn, report_id)
        if report["status"] != "draft":
            raise ReportError("REPORT_NOT_DRAFT", f"The report is already {report['status']}")
        if not report["narrative"].strip():
            raise ReportError("REPORT_INCOMPLETE", "The report needs a narrative")
        if report["report_type"] == "str" and STR_PLACEHOLDER in report["narrative"]:
            raise ReportError(
                "REPORT_INCOMPLETE", "Replace the placeholder with the grounds for suspicion"
            )
        try:
            approval = approvals.submit(
                conn,
                user,
                kind=KIND,
                subject_id=str(report_id),
                payload={
                    "reference": report["reference"],
                    "report_type": report["report_type"],
                    "fingerprint": fingerprint(report["narrative"], report["content"]),
                },
                rationale=note,
                now=now,
                ip=ip,
            )
        except ApprovalError as exc:
            raise ReportError(exc.code, str(exc)) from None
        _case_entry(conn, report, "report_approval_requested", now, user.id)
    return approval


@approvals.on_approved(KIND)
def _approve(conn: psycopg.Connection, approval: Approval) -> None:
    report = _lock(conn, approval.subject_id)
    at, checker = approval.decided_at, approval.checker_id
    assert at is not None  # noqa: S101 - a decided approval has a time
    if report["status"] != "draft":
        raise ApprovalError("APPROVAL_NOT_PENDING", f"The report is already {report['status']}")
    if str(report["created_by"]) == str(checker):
        raise ApprovalError("SELF_APPROVAL", "The person who drafted the report cannot approve it")
    if fingerprint(report["narrative"], report["content"]) != approval.payload["fingerprint"]:
        raise ApprovalError(
            "APPROVAL_NOT_PENDING", "The report changed after approval was requested"
        )
    conn.execute(
        "UPDATE report SET status = 'approved', approved_by = %s, approved_at = %s,"
        " updated_at = %s WHERE id = %s",
        (checker, at, at, report["id"]),
    )
    _case_entry(conn, report, "report_approved", at, checker)
    store.record(
        conn,
        "report.approve",
        object_type="report",
        object_id=report["id"],
        user_id=checker,
        before={"status": "draft"},
        after={
            "status": "approved",
            "requested_by": str(approval.maker_id),
            "approved_by": str(checker),
            "approval_id": str(approval.id),
            "fingerprint": approval.payload["fingerprint"],
        },
    )


def mark_submitted(
    conn: psycopg.Connection,
    user: User,
    report_id: Any,
    *,
    submission_reference: str,
    now: datetime,
    ip: str | None = None,
) -> None:
    """Record that an approved report was filed, with the reference the NFIU returned."""
    submission_reference = submission_reference.strip()
    if not submission_reference:
        raise ReportError("SUBMISSION_REFERENCE_REQUIRED", "Give the filing reference")
    if len(submission_reference) > MAX_SUBMISSION_REFERENCE:
        raise ReportError("SUBMISSION_REFERENCE_INVALID", "The filing reference is too long")
    with conn.transaction():
        report = _lock(conn, report_id)
        if report["status"] != "approved":
            raise ReportError(
                "REPORT_NOT_APPROVED",
                "Only an approved report can be marked submitted"
                if report["status"] == "draft"
                else "The report is already submitted",
            )
        conn.execute(
            "UPDATE report SET status = 'submitted', submitted_by = %s, submitted_at = %s,"
            " submission_reference = %s, updated_at = %s WHERE id = %s",
            (user.id, now, submission_reference, now, report_id),
        )
        _case_entry(conn, report, "report_submitted", now, user.id)
        store.record(
            conn,
            "report.submit",
            object_type="report",
            object_id=report_id,
            user_id=user.id,
            before={"status": "approved"},
            after={"status": "submitted", "submission_reference": submission_reference},
            ip=ip,
        )


def requests_for(conn: psycopg.Connection, report_id: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT p.id, p.status::text AS status, p.rationale, p.created_at,"
            " p.maker_id, m.full_name AS maker_name, p.checker_id,"
            " ch.full_name AS checker_name, p.decided_at, p.decision_note"
            " FROM approval_request p JOIN app_user m ON m.id = p.maker_id"
            " LEFT JOIN app_user ch ON ch.id = p.checker_id"
            " WHERE p.kind = %s AND p.subject_id = %s ORDER BY p.created_at",
            (KIND, str(report_id)),
        ).fetchall()
