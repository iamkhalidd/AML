"""The alert queue, assignment and the alert detail (ALT-1, ALT-2, ALT-3, CUS-8).

The list is ordered most severe first, then oldest first, and any column can be re-sorted
with ``sort`` (prefix ``-`` for descending). Pages hold 50 alerts by default; pass the
returned ``next_cursor`` to get the next page. Writes need the CSRF header.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from aml.modules.access.api import ClientIp, TenantDb, now, require, require_csrf
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.modules.alerts import detail, queue
from aml.modules.alerts.queue import AlertError, Filters
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["alerts"])

_STATUS_FOR_CODE = {"ALERT_NOT_FOUND": 404, "CUSTOMER_NOT_FOUND": 404, "ALERT_CLOSED": 409}
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


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class EventOut(BaseModel):
    id: int
    at: datetime
    event_type: str = Field(examples=["created", "evidence_added", "assigned", "note"])
    detail: dict[str, Any]
    user: UserRef | None


class RuleDetail(BaseModel):
    id: UUID
    name: str
    rule_type: str
    version_id: UUID
    version: int
    severity: Severity
    effective_from: datetime
    rationale: str
    parameters: dict[str, Any]
    href: str = Field(description="The rule, with its versions and parameters")


class TxnOut(BaseModel):
    id: UUID
    external_id: str
    account_external_id: str
    occurred_at: datetime
    amount: int
    currency: str
    direction: str
    channel: str
    txn_type: str
    status: str
    counterparty_name: str | None
    counterparty_account: str | None
    counterparty_bank_code: str | None
    narration: str | None
    added_at: datetime = Field(description="When the transaction was linked to the alert")


class Closure(BaseModel):
    closed_at: datetime
    closed_by: UserRef | None
    reason: str
    note: str | None
    mode: Literal["manual", "auto"]


class OwnerOut(BaseModel):
    owner_ref: str
    full_name: str
    date_of_birth: date | None
    nationality: str | None
    ownership_percent: Decimal | None
    control_type: str
    is_pep: bool


class AccountOut(BaseModel):
    id: UUID
    source_system: str
    external_id: str
    product_type: str
    currency: str
    opened_date: date
    status: str
    branch_id: str | None


class Activity(BaseModel):
    currency: str
    credit_count: int
    credit_total: int


class EarlierAlert(BaseModel):
    id: UUID
    severity: Severity
    status: Status
    rule_name: str
    rule_type: str
    created_at: datetime
    closed_at: datetime | None
    closure_reason: str | None


class CustomerProfile(BaseModel):
    id: UUID
    source_system: str
    external_id: str
    customer_type: str
    full_name: str
    date_of_birth: date | None
    nationality: str | None
    occupation: str | None
    industry: str | None
    state: str | None
    country: str | None
    onboarding_date: date
    kyc_status: str
    kyc_verified_at: datetime | None
    kyc_reference: str | None
    risk_level: Severity | None
    is_pep: bool
    segment: str | None
    status: str
    registration_number: str | None
    registration_date: date | None
    version: int
    updated_at: datetime


class ExpectedActivity(BaseModel):
    declared_income_min: int | None
    declared_income_max: int | None
    monthly_credit: int | None
    monthly_credit_count: int | None


class CustomerView(BaseModel):
    customer: CustomerProfile
    expected_activity: ExpectedActivity
    actual_activity: list[Activity] = Field(description="Successful credits, by currency")
    activity_window_days: int
    beneficial_owners: list[OwnerOut]
    accounts: list[AccountOut]
    earlier_alerts: list[EarlierAlert]


class AlertDetail(AlertOut):
    rule_detail: RuleDetail
    trigger_values: dict[str, Any]
    customer_version: int = Field(description="The customer record version it was raised on")
    transactions: list[TxnOut]
    notes: list[EventOut]
    timeline: list[EventOut]
    closure: Closure | None
    customer_view: CustomerView


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


def _event_out(row: dict[str, Any]) -> EventOut:
    user = None if row["user_id"] is None else UserRef(id=row["user_id"], name=row["user_name"])
    return EventOut(
        id=row["id"], at=row["at"], event_type=row["event_type"], detail=row["detail"], user=user
    )


def customer_view_out(view: dict[str, Any]) -> CustomerView:
    c = view["customer"]
    return CustomerView(
        customer=CustomerProfile.model_validate(c),
        expected_activity=ExpectedActivity(
            declared_income_min=c["declared_income_min"],
            declared_income_max=c["declared_income_max"],
            monthly_credit=c["expected_monthly_credit"],
            monthly_credit_count=c["expected_monthly_credit_count"],
        ),
        actual_activity=[Activity.model_validate(r) for r in view["actual_activity"]],
        activity_window_days=view["activity_window_days"],
        beneficial_owners=[OwnerOut.model_validate(r) for r in view["beneficial_owners"]],
        accounts=[AccountOut.model_validate(r) for r in view["accounts"]],
        earlier_alerts=[EarlierAlert.model_validate(r) for r in view["earlier_alerts"]],
    )


def detail_out(found: dict[str, Any]) -> AlertDetail:
    alert, rule = found["alert"], found["rule"]
    events = [_event_out(row) for row in found["events"]]
    closure = None
    if rule["closed_at"] is not None:
        closed_by = None
        if rule["closed_by"] is not None:
            closed_by = UserRef(id=rule["closed_by"], name=rule["closed_by_name"])
        closure = Closure(
            closed_at=rule["closed_at"],
            closed_by=closed_by,
            reason=rule["closure_reason"],
            note=rule["closure_note"],
            mode=rule["closure_mode"],
        )
    return AlertDetail(
        **alert_out(alert).model_dump(),
        rule_detail=RuleDetail(
            id=alert["rule_id"],
            name=alert["rule_name"],
            rule_type=alert["rule_type"],
            version_id=alert["rule_version_id"],
            version=alert["rule_version"],
            severity=rule["severity"],
            effective_from=rule["effective_from"],
            rationale=rule["rationale"],
            parameters=rule["parameters"],
            href=f"/v1/rules/{alert['rule_id']}",
        ),
        trigger_values=rule["trigger_values"],
        customer_version=rule["customer_version"],
        transactions=[TxnOut.model_validate(row) for row in found["transactions"]],
        notes=[e for e in events if e.event_type == "note"],
        timeline=events,
        closure=closure,
        customer_view=customer_view_out(found["customer"]),
    )


@router.get("/alerts/{alert_id}", responses=_problems)
def get_alert(alert_id: UUID, conn: TenantDb, user: Viewer) -> AlertDetail:
    try:
        return detail_out(detail.get_detail(conn, alert_id, now=now()))
    except AlertError as exc:
        raise problem(exc) from None


@router.post("/alerts/{alert_id}/notes", status_code=201, dependencies=_writes, responses=_problems)
def add_note(alert_id: UUID, body: NoteIn, conn: TenantDb, user: Worker, ip: ClientIp) -> EventOut:
    try:
        return _event_out(detail.add_note(conn, user, alert_id, body.text, now=now(), ip=ip))
    except AlertError as exc:
        raise problem(exc) from None


@router.get("/customers/{customer_id}/view", responses=_problems)
def get_customer_view(customer_id: UUID, conn: TenantDb, user: Viewer) -> CustomerView:
    """The unified customer view on its own, as the alert detail shows it."""
    try:
        return customer_view_out(detail.customer_view(conn, customer_id, now=now()))
    except AlertError as exc:
        raise problem(exc) from None
