"""Cases: escalation, workspace, notes, corrections and closure (CAS-1 to CAS-5).

Writes need the CSRF header. A case timeline never changes; to fix an entry, post a
correction that names it. Closing is a request that a second person approves through
``/v1/approvals/{id}/approve``.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from aml.modules.access.api import ClientIp, TenantDb, now, require, require_csrf
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.modules.alerts.api import CustomerView, UserRef, customer_view_out
from aml.modules.alerts.queue import AlertError
from aml.modules.cases import closure, service
from aml.modules.cases.service import CaseError, Filters
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["cases"])

_STATUS_FOR_CODE = {
    "CASE_NOT_FOUND": 404,
    "ALERT_NOT_FOUND": 404,
    "ENTRY_NOT_FOUND": 404,
    "CUSTOMER_NOT_FOUND": 404,
    "PERMISSION_DENIED": 403,
    "CASE_CLOSED": 409,
    "ALERT_CLOSED": 409,
    "ALERT_ALREADY_ESCALATED": 409,
    "CLOSURE_PENDING": 409,
    "APPROVAL_ALREADY_PENDING": 409,
}
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404, 409)
}
_writes = [Depends(require_csrf)]

Viewer = Annotated[User, Depends(require(Permission.CASES_VIEW))]
Worker = Annotated[User, Depends(require(Permission.CASES_WORK))]
Closer = Annotated[User, Depends(require(Permission.CASES_REQUEST_CLOSURE))]
CaseStatus = Literal["open", "closed"]
Outcome = Literal["no_suspicion", "reported", "referred"]
Severity = Literal["low", "medium", "high"]


class CustomerRef(BaseModel):
    id: UUID
    external_id: str
    name: str
    customer_type: str
    risk_level: Severity | None


class CaseOut(BaseModel):
    id: UUID
    reference: str = Field(examples=["CASE-000042"])
    title: str
    status: CaseStatus
    severity: Severity | None = Field(description="The highest severity among its alerts")
    alert_count: int
    customer: CustomerRef
    assignee: UserRef | None
    opened_by: UserRef
    opened_at: datetime
    updated_at: datetime
    closure_pending: bool = Field(description="A closure is waiting for approval")
    outcome: Outcome | None
    outcome_reason: str | None
    closed_at: datetime | None
    closure_requested_by: UUID | None
    closure_approved_by: UUID | None


class CasePage(BaseModel):
    items: list[CaseOut]
    next_cursor: str | None


class EscalateIn(BaseModel):
    alert_ids: list[UUID] = Field(min_length=1, max_length=service.MAX_ALERTS_PER_ESCALATION)
    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=5000)


class LinkIn(BaseModel):
    alert_ids: list[UUID] = Field(min_length=1, max_length=service.MAX_ALERTS_PER_ESCALATION)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    corrects: int | None = Field(
        default=None, description="The timeline entry this corrects; it stays as it was"
    )


class CloseIn(BaseModel):
    outcome: Outcome = Field(
        description="no_suspicion, reported (closed with a report) or referred"
    )
    reason: str = Field(min_length=1, max_length=5000, description="Why the case closes")


class ClosureRequested(BaseModel):
    approval_id: UUID
    status: str
    outcome: Outcome
    reason: str
    requested_at: datetime


class AssignIn(BaseModel):
    assignee_id: UUID | None = Field(description="Null takes the case off its assignee")


class EntryOut(BaseModel):
    id: int
    at: datetime
    entry_type: str = Field(examples=["opened", "note", "correction", "assigned"])
    body: str | None
    detail: dict[str, Any]
    corrects: int | None
    user: UserRef | None


class CaseAlert(BaseModel):
    id: UUID
    severity: Severity
    status: str
    explanation: str
    trigger_values: dict[str, Any]
    rule_id: UUID
    rule_name: str
    rule_type: str
    rule_version_id: UUID
    rule_version: int
    txn_count: int
    window_start: datetime | None
    window_end: datetime | None
    created_at: datetime
    linked_at: datetime


class ClosureRequest(BaseModel):
    approval_id: UUID
    status: str
    outcome: Outcome
    reason: str
    requested_by: UserRef
    requested_at: datetime
    decided_by: UserRef | None
    decided_at: datetime | None
    decision_note: str | None


class CaseWorkspace(CaseOut):
    alerts: list[CaseAlert]
    timeline: list[EntryOut]
    closure_requests: list[ClosureRequest]
    customer_view: CustomerView


def case_out(row: dict[str, Any]) -> CaseOut:
    assignee = None
    if row["assigned_to"] is not None:
        assignee = UserRef(id=row["assigned_to"], name=row["assignee_name"])
    return CaseOut(
        id=row["id"],
        reference=row["reference"],
        title=row["title"],
        status=row["status"],
        severity=row["severity"],
        alert_count=row["alert_count"],
        customer=CustomerRef(
            id=row["customer_id"],
            external_id=row["customer_external_id"],
            name=row["customer_name"],
            customer_type=row["customer_type"],
            risk_level=row["customer_risk"],
        ),
        assignee=assignee,
        opened_by=UserRef(id=row["opened_by"], name=row["opened_by_name"]),
        opened_at=row["opened_at"],
        updated_at=row["updated_at"],
        closure_pending=row["closure_pending"],
        outcome=row["outcome"],
        outcome_reason=row["outcome_reason"],
        closed_at=row["closed_at"],
        closure_requested_by=row["closure_requested_by"],
        closure_approved_by=row["closure_approved_by"],
    )


def entry_out(row: dict[str, Any]) -> EntryOut:
    user = None if row["user_id"] is None else UserRef(id=row["user_id"], name=row["user_name"])
    return EntryOut(
        id=row["id"],
        at=row["at"],
        entry_type=row["entry_type"],
        body=row["body"],
        detail=row["detail"],
        corrects=row["corrects"],
        user=user,
    )


def _closure_out(row: dict[str, Any]) -> ClosureRequest:
    decided_by = None
    if row["checker_id"] is not None:
        decided_by = UserRef(id=row["checker_id"], name=row["checker_name"])
    return ClosureRequest(
        approval_id=row["id"],
        status=row["status"],
        outcome=row["payload"]["outcome"],
        reason=row["payload"]["reason"],
        requested_by=UserRef(id=row["maker_id"], name=row["maker_name"]),
        requested_at=row["created_at"],
        decided_by=decided_by,
        decided_at=row["decided_at"],
        decision_note=row["decision_note"],
    )


def problem(exc: CaseError | AlertError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, exc.detail)


@router.get("/cases", responses=_problems)
def list_cases(
    conn: TenantDb,
    user: Viewer,
    status: Annotated[list[CaseStatus] | None, Query()] = None,
    assignee: Annotated[
        str | None, Query(description="A user ID, or 'me', or 'none' for unassigned")
    ] = None,
    customer_id: UUID | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = service.PAGE_SIZE,
    cursor: str | None = None,
) -> CasePage:
    if assignee not in (None, "me", "none"):
        try:
            assignee = str(UUID(assignee))
        except ValueError:
            raise ProblemError(
                400, "ASSIGNEE_INVALID", "assignee must be a user ID, 'me' or 'none'"
            ) from None
    filters = Filters(
        status=tuple(status or ()), assignee=assignee, customer_id=customer_id, search=q
    )
    try:
        rows, next_cursor = service.list_cases(conn, user, filters, limit=limit, cursor=cursor)
    except CaseError as exc:
        raise problem(exc) from None
    return CasePage(items=[case_out(row) for row in rows], next_cursor=next_cursor)


@router.post(
    "/cases",
    status_code=status.HTTP_201_CREATED,
    dependencies=_writes,
    responses=_problems,
)
def escalate(body: EscalateIn, conn: TenantDb, user: Worker, ip: ClientIp) -> CaseOut:
    """Open a case from one or more alerts of one customer. The caller holds the case."""
    try:
        row = service.escalate(
            conn, user, list(body.alert_ids), title=body.title, note=body.note, now=now(), ip=ip
        )
    except CaseError as exc:
        raise problem(exc) from None
    return case_out(row)


@router.get("/cases/{case_id}", responses=_problems)
def get_case(case_id: UUID, conn: TenantDb, user: Viewer) -> CaseWorkspace:
    try:
        found = service.workspace(conn, case_id, now=now())
    except (CaseError, AlertError) as exc:
        raise problem(exc) from None
    return CaseWorkspace(
        **case_out(found["case"]).model_dump(),
        alerts=[CaseAlert.model_validate(row) for row in found["alerts"]],
        timeline=[entry_out(row) for row in found["timeline"]],
        closure_requests=[_closure_out(row) for row in found["closure_requests"]],
        customer_view=customer_view_out(found["customer"]),
    )


@router.post("/cases/{case_id}/alerts", dependencies=_writes, responses=_problems)
def link_alerts(case_id: UUID, body: LinkIn, conn: TenantDb, user: Worker, ip: ClientIp) -> CaseOut:
    try:
        row = service.link_alerts(conn, user, case_id, list(body.alert_ids), now=now(), ip=ip)
    except CaseError as exc:
        raise problem(exc) from None
    return case_out(row)


@router.post(
    "/cases/{case_id}/notes",
    status_code=status.HTTP_201_CREATED,
    dependencies=_writes,
    responses=_problems,
)
def add_note(case_id: UUID, body: NoteIn, conn: TenantDb, user: Worker, ip: ClientIp) -> EntryOut:
    try:
        row = service.add_note(
            conn, user, case_id, body.text, corrects=body.corrects, now=now(), ip=ip
        )
    except CaseError as exc:
        raise problem(exc) from None
    return entry_out(row)


@router.post("/cases/{case_id}/assign", dependencies=_writes, responses=_problems)
def assign_case(
    case_id: UUID, body: AssignIn, conn: TenantDb, user: Worker, ip: ClientIp
) -> CaseOut:
    try:
        row = service.assign(conn, user, case_id, body.assignee_id, now=now(), ip=ip)
    except CaseError as exc:
        raise problem(exc) from None
    return case_out(row)


@router.post("/cases/{case_id}/claim", dependencies=_writes, responses=_problems)
def claim_case(case_id: UUID, conn: TenantDb, user: Worker, ip: ClientIp) -> CaseOut:
    """Assign the case to the caller."""
    try:
        row = service.assign(conn, user, case_id, user.id, now=now(), ip=ip)
    except CaseError as exc:
        raise problem(exc) from None
    return case_out(row)


@router.post(
    "/cases/{case_id}/closure-request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_writes,
    responses=_problems,
)
def request_closure(
    case_id: UUID, body: CloseIn, conn: TenantDb, user: Closer, ip: ClientIp
) -> ClosureRequested:
    """Ask to close the case with an outcome and reason; a second person approves it."""
    try:
        approval = closure.request_closure(
            conn, user, case_id, outcome=body.outcome, reason=body.reason, now=now(), ip=ip
        )
    except CaseError as exc:
        raise problem(exc) from None
    return ClosureRequested(
        approval_id=approval.id,
        status=approval.status,
        outcome=body.outcome,
        reason=approval.payload["reason"],
        requested_at=approval.created_at,
    )
