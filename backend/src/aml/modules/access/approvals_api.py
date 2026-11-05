"""Approval queue and fallback approver endpoints (ACC-3, ACC-4).

Changes are submitted by the module that owns them (rules, cases, reports); this router
only lists and decides them. Writes need the CSRF header like every signed-in write.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from aml.modules.access import approvals
from aml.modules.access.api import ClientIp, CurrentUser, TenantDb, now, require, require_csrf
from aml.modules.access.approvals import Approval, ApprovalError
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

_STATUS_FOR_CODE = {
    "APPROVAL_NOT_FOUND": 404,
    "PERMISSION_DENIED": 403,
    "SELF_APPROVAL": 403,
    "APPROVAL_NOT_PENDING": 409,
}
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404, 409)
}
_writes = [Depends(require_csrf)]

Viewer = Annotated[User, Depends(require(Permission.APPROVALS_VIEW))]
SettingsManager = Annotated[User, Depends(require(Permission.SETTINGS_MANAGE))]
ApprovalStatus = Literal["pending", "approved", "rejected", "withdrawn"]


class ApprovalOut(BaseModel):
    id: UUID
    kind: str
    subject_id: str
    payload: dict[str, Any]
    rationale: str
    status: str
    maker_id: UUID
    created_at: datetime
    checker_id: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    maker_name: str
    checker_name: str | None
    subject_label: str = Field(description="What the change is about, in words")
    can_decide: bool = Field(description="The signed-in user can approve or reject this now")
    blocked_reason: str | None = Field(description="Why they cannot, while it is pending")


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class Rejection(BaseModel):
    note: str = Field(min_length=1, max_length=2000, description="Why the change is rejected")


class FallbackApprover(BaseModel):
    user_id: UUID | None = Field(description="None clears the fallback approver")


class FallbackApproverChange(FallbackApprover):
    rationale: str = Field(min_length=1, max_length=2000)


# Per kind: the subject's words, and who the owning module keeps away from the decision.
_SUBJECTS = {
    "case_closure": "SELECT id::text, reference || ': ' || title, assigned_to FROM aml_case",
    "report_submission": "SELECT id::text, reference, created_by FROM report",
    "rule_change": "SELECT id::text, name, NULL::uuid FROM rule_instance",
}


def _views(conn: psycopg.Connection, user: User, items: list[Approval]) -> list[ApprovalOut]:
    """Add what a reader needs on top of the stored request, in one query per kind."""
    people = {a.maker_id for a in items} | {a.checker_id for a in items if a.checker_id}
    rows = conn.execute(
        "SELECT id, full_name FROM app_user WHERE id = ANY(%s)", (list(people),)
    ).fetchall()
    names = {row[0]: row[1] for row in rows}
    subjects: dict[tuple[str, str], tuple[str, Any]] = {}
    for kind, query in _SUBJECTS.items():
        ids = [a.subject_id for a in items if a.kind == kind]
        if ids:
            found = conn.execute(f"{query} WHERE id::text = ANY(%s)", (ids,)).fetchall()
            subjects |= {(kind, row[0]): (row[1], row[2]) for row in found}
    fallback = approvals.fallback_approver(conn, now())
    views = []
    for approval in items:
        label, holder = subjects.get((approval.kind, approval.subject_id), (None, None))
        pending = approval.status == "pending"
        blocked = approvals.why_not_decide(user, approval, fallback, holder) if pending else None
        views.append(
            ApprovalOut.model_validate(
                asdict(approval)
                | {
                    "maker_name": names[approval.maker_id],
                    "checker_name": names.get(approval.checker_id),
                    "subject_label": label or approval.subject_id,
                    "can_decide": pending and blocked is None,
                    "blocked_reason": blocked,
                }
            )
        )
    return views


def _view(conn: psycopg.Connection, user: User, approval: Approval) -> ApprovalOut:
    return _views(conn, user, [approval])[0]


def _problem(exc: ApprovalError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, str(exc))


@router.get("", responses=_problems)
def list_approvals(
    conn: TenantDb,
    user: Viewer,
    status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ApprovalOut]:
    return _views(conn, user, approvals.list_approvals(conn, status=status_, limit=limit))


@router.get("/fallback-approver", responses=_problems)
def get_fallback_approver(conn: TenantDb, user: SettingsManager) -> FallbackApprover:
    return FallbackApprover(user_id=approvals.fallback_approver(conn, now()))


@router.put("/fallback-approver", dependencies=_writes, responses=_problems)
def set_fallback_approver(
    body: FallbackApproverChange, conn: TenantDb, user: SettingsManager, ip: ClientIp
) -> FallbackApprover:
    try:
        approvals.set_fallback_approver(
            conn, user, body.user_id, rationale=body.rationale, now=now(), ip=ip
        )
    except ApprovalError as exc:
        raise _problem(exc) from None
    return FallbackApprover(user_id=body.user_id)


@router.get("/{approval_id}", responses=_problems)
def get_approval(approval_id: UUID, conn: TenantDb, user: Viewer) -> ApprovalOut:
    approval = approvals.get(conn, approval_id)
    if approval is None:
        raise ProblemError(404, "APPROVAL_NOT_FOUND", "No such approval request")
    return _view(conn, user, approval)


@router.post("/{approval_id}/approve", dependencies=_writes, responses=_problems)
def approve(
    approval_id: UUID, body: Decision, conn: TenantDb, user: CurrentUser, ip: ClientIp
) -> ApprovalOut:
    try:
        decided = approvals.approve(conn, user, approval_id, note=body.note, now=now(), ip=ip)
    except ApprovalError as exc:
        raise _problem(exc) from None
    return _view(conn, user, decided)


@router.post("/{approval_id}/reject", dependencies=_writes, responses=_problems)
def reject(
    approval_id: UUID, body: Rejection, conn: TenantDb, user: CurrentUser, ip: ClientIp
) -> ApprovalOut:
    try:
        decided = approvals.reject(conn, user, approval_id, note=body.note, now=now(), ip=ip)
    except ApprovalError as exc:
        raise _problem(exc) from None
    return _view(conn, user, decided)


@router.post("/{approval_id}/withdraw", dependencies=_writes, responses=_problems)
def withdraw(approval_id: UUID, conn: TenantDb, user: CurrentUser, ip: ClientIp) -> ApprovalOut:
    try:
        decided = approvals.withdraw(conn, user, approval_id, now=now(), ip=ip)
    except ApprovalError as exc:
        raise _problem(exc) from None
    return _view(conn, user, decided)
