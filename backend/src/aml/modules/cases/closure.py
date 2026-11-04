"""Case outcomes and maker-checker closure (CAS-3, CAS-4).

A case closes with one of three outcomes (no suspicion, closed with a report, referred)
and a written reason. Closing is a request: it waits in the approval queue until a second
person with the right to approve closes it. Neither the requester nor the investigator who
holds the case can approve it. Approval closes the case and its escalated alerts in one
transaction; a rejected or withdrawn request leaves the case open.
"""

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from aml.modules.access import approvals
from aml.modules.access.approvals import Approval, ApprovalError
from aml.modules.access.service import User
from aml.modules.audit import store
from aml.modules.cases import service
from aml.modules.cases.service import CaseError

OUTCOMES = ("no_suspicion", "reported", "referred")


def request_closure(
    conn: psycopg.Connection,
    user: User,
    case_id: Any,
    *,
    outcome: str,
    reason: str,
    now: datetime,
    ip: str | None = None,
) -> Approval:
    reason = reason.strip()
    if outcome not in OUTCOMES:
        raise CaseError("OUTCOME_INVALID", f"The outcome must be one of {', '.join(OUTCOMES)}")
    if not reason:
        raise CaseError("REASON_REQUIRED", "A case cannot close without a written reason")
    with conn.transaction():
        service.lock_open(conn, case_id)
        try:
            approval = approvals.submit(
                conn,
                user,
                kind="case_closure",
                subject_id=str(case_id),
                payload={"outcome": outcome, "reason": reason},
                rationale=reason,
                now=now,
                ip=ip,
            )
        except ApprovalError as exc:
            raise CaseError(exc.code, str(exc)) from None
        service.add_entry(
            conn,
            case_id,
            "closure_requested",
            now,
            user.id,
            body=reason,
            detail={"outcome": outcome, "approval_id": str(approval.id)},
        )
    return approval


@approvals.on_approved("case_closure")
def _close(conn: psycopg.Connection, approval: Approval) -> None:
    case_id, payload = approval.subject_id, approval.payload
    at, checker = approval.decided_at, approval.checker_id
    row = conn.execute(
        "SELECT status::text, assigned_to FROM aml_case WHERE id = %s FOR UPDATE", (case_id,)
    ).fetchone()
    if row is None or row[0] == "closed":
        raise ApprovalError("APPROVAL_NOT_PENDING", "The case is already closed")
    if row[1] is not None and str(row[1]) == str(checker):
        raise ApprovalError(
            "SELF_APPROVAL", "The investigator holding the case cannot approve its closure"
        )
    conn.execute(
        "UPDATE aml_case SET status = 'closed', outcome = %s, outcome_reason = %s,"
        " closure_requested_by = %s, closure_approved_by = %s, closure_approval_id = %s,"
        " closed_at = %s, updated_at = %s WHERE id = %s",
        (payload["outcome"], payload["reason"], approval.maker_id, checker, approval.id, at,
         at, case_id),
    )  # fmt: skip
    alerts = conn.execute(
        "UPDATE alert a SET status = 'closed', closed_at = %s, closed_by = %s,"
        " closure_reason = %s, closure_note = %s, closure_mode = 'manual', updated_at = %s"
        " FROM case_alert x WHERE x.alert_id = a.id AND x.case_id = %s"
        " AND a.status <> 'closed' RETURNING a.id",
        (at, checker, f"case_{payload['outcome']}", payload["reason"], at, case_id),
    ).fetchall()
    alert_ids = sorted(str(r[0]) for r in alerts)
    for alert_id in alert_ids:
        conn.execute(
            "INSERT INTO alert_event (alert_id, at, user_id, event_type, detail)"
            " VALUES (%s, %s, %s, 'closed', %s)",
            (alert_id, at, checker,
             Jsonb({"case_id": case_id, "outcome": payload["outcome"], "mode": "case"})),
        )  # fmt: skip
    after = {
        "status": "closed",
        "outcome": payload["outcome"],
        "reason": payload["reason"],
        "requested_by": str(approval.maker_id),
        "approved_by": str(checker),
        "approval_id": str(approval.id),
        "alert_ids": alert_ids,
    }
    assert at is not None  # noqa: S101 - a decided approval has a time
    service.add_entry(conn, case_id, "closed", at, checker, body=payload["reason"], detail=after)
    store.record(
        conn,
        "case.close",
        object_type="case",
        object_id=case_id,
        user_id=checker,
        before={"status": "open"},
        after=after,
    )
