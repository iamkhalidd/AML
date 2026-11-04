"""The alert queue and assignment (ALT-1, ALT-3).

The list is ordered most severe first, then oldest first, and any column can be re-sorted
with ``sort`` (prefix ``-`` for descending). Pages hold 50 alerts by default; pass the
returned ``next_cursor`` to get the next page. Writes need the CSRF header.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from aml.modules.access.api import ClientIp, TenantDb, now, require, require_csrf
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.modules.alerts import queue
from aml.modules.alerts.queue import AlertError, Filters
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["alerts"])

_STATUS_FOR_CODE = {"ALERT_NOT_FOUND": 404, "ALERT_CLOSED": 409}
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404, 409)
}
_writes = [Depends(require_csrf)]

Viewer = Annotated[User, Depends(require(Permission.ALERTS_VIEW))]
Worker = Annotated[User, Depends(require(Permission.ALERTS_WORK))]
Status = Literal["open", "in_review", "escalated", "closed"]
Severity = Literal["low", "medium", "high"]


class CustomerRef(BaseModel):
    id: UUID
    external_id: str
    name: str
    customer_type: str


class RuleRef(BaseModel):
    id: UUID
    name: str
    rule_type: str
    version_id: UUID
    version: int


class UserRef(BaseModel):
    id: UUID
    name: str


class Amount(BaseModel):
    value: int = Field(description="Sum of the linked transactions, in minor units")
    currency: str | None = Field(description="Null when the transactions mix currencies")


class AlertOut(BaseModel):
    id: UUID
    severity: Severity
    status: Status
    customer: CustomerRef
    rule: RuleRef
    explanation: str
    amount: Amount | None
    hits: int = Field(description="How many findings this alert has gathered")
    assignee: UserRef | None
    window_start: datetime | None
    window_end: datetime | None
    created_at: datetime
    updated_at: datetime


class Counts(BaseModel):
    open: int
    mine: int
    overdue: int | None = Field(description="Null until review service standards are set")


class AlertPage(BaseModel):
    items: list[AlertOut]
    next_cursor: str | None
    counts: Counts


class AssignIn(BaseModel):
    assignee_id: UUID | None = Field(description="Null takes the alert off its assignee")


def alert_out(row: dict[str, Any]) -> AlertOut:
    assignee = None
    if row["assigned_to"] is not None:
        assignee = UserRef(id=row["assigned_to"], name=row["assignee_name"])
    amount = None
    if row["amount"] is not None:
        amount = Amount(value=row["amount"], currency=row["currency"])
    return AlertOut(
        id=row["id"],
        severity=row["severity"],
        status=row["status"],
        customer=CustomerRef(
            id=row["customer_id"],
            external_id=row["customer_external_id"],
            name=row["customer_name"],
            customer_type=row["customer_type"],
        ),
        rule=RuleRef(
            id=row["rule_id"],
            name=row["rule_name"],
            rule_type=row["rule_type"],
            version_id=row["rule_version_id"],
            version=row["rule_version"],
        ),
        explanation=row["explanation"],
        amount=amount,
        hits=row["hits"],
        assignee=assignee,
        window_start=row["window_start"],
        window_end=row["window_end"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def problem(exc: AlertError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, exc.detail)


@router.get("/alerts", responses=_problems)
def list_alerts(
    conn: TenantDb,
    user: Viewer,
    status: Annotated[list[Status] | None, Query()] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    rule_id: UUID | None = None,
    rule_type: Annotated[str | None, Query(max_length=10, examples=["R01"])] = None,
    assignee: Annotated[
        str | None, Query(description="A user ID, or 'me', or 'none' for unassigned")
    ] = None,
    customer_id: UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    sort: Annotated[
        list[str] | None,
        Query(
            description="severity, created_at, status, customer, rule,"
            " amount or assignee; prefix - for descending"
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = queue.PAGE_SIZE,
    cursor: str | None = None,
) -> AlertPage:
    if assignee not in (None, "me", "none"):
        try:
            assignee = str(UUID(assignee))
        except ValueError:
            raise ProblemError(
                400, "ASSIGNEE_INVALID", "assignee must be a user ID, 'me' or 'none'"
            ) from None
    filters = Filters(
        status=tuple(status or ()),
        severity=tuple(severity or ()),
        rule_id=rule_id,
        rule_type=rule_type,
        assignee=assignee,
        customer_id=customer_id,
        created_from=created_from,
        created_to=created_to,
        search=q,
    )
    try:
        page = queue.list_alerts(
            conn,
            user,
            filters,
            sort=tuple(sort) if sort else queue.DEFAULT_SORT,
            limit=limit,
            cursor=cursor,
        )
    except AlertError as exc:
        raise problem(exc) from None
    return AlertPage(
        items=[alert_out(row) for row in page.items],
        next_cursor=page.next_cursor,
        counts=Counts.model_validate(queue.counts(conn, user)),
    )


@router.post("/alerts/{alert_id}/assign", dependencies=_writes, responses=_problems)
def assign_alert(
    alert_id: UUID, body: AssignIn, conn: TenantDb, user: Worker, ip: ClientIp
) -> AlertOut:
    try:
        row = queue.assign(conn, user, alert_id, body.assignee_id, now=now(), ip=ip)
    except AlertError as exc:
        raise problem(exc) from None
    return alert_out(row)


@router.post("/alerts/{alert_id}/claim", dependencies=_writes, responses=_problems)
def claim_alert(alert_id: UUID, conn: TenantDb, user: Worker, ip: ClientIp) -> AlertOut:
    """Assign the alert to the caller."""
    try:
        row = queue.assign(conn, user, alert_id, user.id, now=now(), ip=ip)
    except AlertError as exc:
        raise problem(exc) from None
    return alert_out(row)
