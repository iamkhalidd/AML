"""Approval queue and fallback approver endpoints (ACC-3, ACC-4).

Changes are submitted by the module that owns them (rules, cases, reports); this router
only lists and decides them. Writes need the CSRF header like every signed-in write.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from aml.modules.access import approvals
from aml.modules.access.api import CurrentUser, TenantDb, now, require, require_csrf
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


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class Rejection(BaseModel):
    note: str = Field(min_length=1, max_length=2000, description="Why the change is rejected")


class FallbackApprover(BaseModel):
    user_id: UUID | None = Field(description="None clears the fallback approver")


class FallbackApproverChange(FallbackApprover):
    rationale: str = Field(min_length=1, max_length=2000)


def _out(approval: Approval) -> ApprovalOut:
    return ApprovalOut.model_validate(approval, from_attributes=True)


def _problem(exc: ApprovalError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, str(exc))


@router.get("", responses=_problems)
def list_approvals(
    conn: TenantDb,
    user: Viewer,
    status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ApprovalOut]:
    return [_out(a) for a in approvals.list_approvals(conn, status=status_, limit=limit)]


@router.get("/fallback-approver", responses=_problems)
def get_fallback_approver(conn: TenantDb, user: SettingsManager) -> FallbackApprover:
    return FallbackApprover(user_id=approvals.fallback_approver(conn, now()))


@router.put("/fallback-approver", dependencies=_writes, responses=_problems)
def set_fallback_approver(
    body: FallbackApproverChange, conn: TenantDb, user: SettingsManager
) -> FallbackApprover:
    try:
        approvals.set_fallback_approver(
            conn, user, body.user_id, rationale=body.rationale, now=now()
        )
    except ApprovalError as exc:
        raise _problem(exc) from None
    return FallbackApprover(user_id=body.user_id)


@router.get("/{approval_id}", responses=_problems)
def get_approval(approval_id: UUID, conn: TenantDb, user: Viewer) -> ApprovalOut:
    approval = approvals.get(conn, approval_id)
    if approval is None:
        raise ProblemError(404, "APPROVAL_NOT_FOUND", "No such approval request")
    return _out(approval)


@router.post("/{approval_id}/approve", dependencies=_writes, responses=_problems)
def approve(approval_id: UUID, body: Decision, conn: TenantDb, user: CurrentUser) -> ApprovalOut:
    try:
        return _out(approvals.approve(conn, user, approval_id, note=body.note, now=now()))
    except ApprovalError as exc:
        raise _problem(exc) from None


@router.post("/{approval_id}/reject", dependencies=_writes, responses=_problems)
def reject(approval_id: UUID, body: Rejection, conn: TenantDb, user: CurrentUser) -> ApprovalOut:
    try:
        return _out(approvals.reject(conn, user, approval_id, note=body.note, now=now()))
    except ApprovalError as exc:
        raise _problem(exc) from None


@router.post("/{approval_id}/withdraw", dependencies=_writes, responses=_problems)
def withdraw(approval_id: UUID, conn: TenantDb, user: CurrentUser) -> ApprovalOut:
    try:
        return _out(approvals.withdraw(conn, user, approval_id, now=now()))
    except ApprovalError as exc:
        raise _problem(exc) from None
