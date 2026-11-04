"""Reports: STR and CTR drafts, approval and submission status (REP-1 to REP-3).

Writes need the CSRF header. Report content is in an internal format until the NFIU
format is confirmed. A second person approves a report through
``/v1/approvals/{id}/approve``; then it can be marked submitted.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from aml.modules.access.api import ClientIp, TenantDb, now, require, require_csrf
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.modules.alerts.api import UserRef
from aml.modules.cases.api import CustomerRef
from aml.modules.reports import dashboard, service, workflow
from aml.modules.reports.service import Filters, ReportError
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["reports"])

_STATUS_FOR_CODE = {
    "REPORT_NOT_FOUND": 404,
    "CASE_NOT_FOUND": 404,
    "CUSTOMER_NOT_FOUND": 404,
    "PERMISSION_DENIED": 403,
    "REPORT_EXISTS": 409,
    "REPORT_NOT_DRAFT": 409,
    "REPORT_NOT_APPROVED": 409,
    "REPORT_INCOMPLETE": 409,
    "APPROVAL_PENDING": 409,
    "APPROVAL_ALREADY_PENDING": 409,
}
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404, 409)
}
_writes = [Depends(require_csrf)]

Viewer = Annotated[User, Depends(require(Permission.REPORTS_VIEW))]
Drafter = Annotated[User, Depends(require(Permission.REPORTS_DRAFT))]
Admin = Annotated[User, Depends(require(Permission.SETTINGS_MANAGE))]
Submitter = Annotated[User, Depends(require(Permission.REPORTS_SUBMIT))]
ReportType = Literal["str", "ctr"]
ReportStatus = Literal["draft", "approved", "submitted"]


class ReportOut(BaseModel):
    id: UUID
    reference: str = Field(examples=["STR-000007"])
    report_type: ReportType
    status: ReportStatus
    case_id: UUID | None
    case_reference: str | None
    customer: CustomerRef
    created_by: UserRef
    created_at: datetime
    updated_by: UserRef
    updated_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    submitted_by: UUID | None
    submitted_at: datetime | None
    submission_reference: str | None
    approval_pending: bool = Field(description="An approval request is waiting")


class ReportPage(BaseModel):
    items: list[ReportOut]
    next_cursor: str | None


class ReportTxn(BaseModel):
    id: UUID
    external_id: str
    account_external_id: str
    occurred_at: datetime
    amount: int = Field(description="Minor units (kobo)")
    currency: str
    direction: str
    channel: str
    txn_type: str
    status: str
    counterparty_name: str | None
    counterparty_account: str | None
    narration: str | None


class ApprovalRequestOut(BaseModel):
    approval_id: UUID
    status: str
    note: str
    requested_by: UserRef
    requested_at: datetime
    decided_by: UserRef | None
    decided_at: datetime | None
    decision_note: str | None


class ApprovalIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000, description="What the approver should check")


class ApprovalRequested(BaseModel):
    approval_id: UUID
    status: str
    requested_at: datetime


class SubmittedIn(BaseModel):
    submission_reference: str = Field(
        min_length=1,
        max_length=workflow.MAX_SUBMISSION_REFERENCE,
        description="The reference the NFIU returned for the filing",
    )


class ReportDetail(ReportOut):
    narrative: str
    content: dict[str, Any] = Field(description=f"Prefilled fields, format {service.FORMAT}")
    transactions: list[ReportTxn]
    approval_requests: list[ApprovalRequestOut]


class ReportEdit(BaseModel):
    narrative: str | None = Field(None, max_length=service.MAX_NARRATIVE)
    content: dict[str, Any] | None = Field(None, description="Replaces the whole content")


class CtrRunIn(BaseModel):
    date_from: datetime
    date_to: datetime


class CtrRunOut(BaseModel):
    drafted: list[ReportOut]
    more: bool = Field(description="More transactions qualify; run again for the rest")


class CtrThreshold(BaseModel):
    individual_minor: int = Field(gt=0, le=10**15, description="Kobo")
    business_minor: int = Field(gt=0, le=10**15, description="Kobo")
    currency: Literal["NGN"] = "NGN"
    version: int = Field(0, description="Setting version; 0 is the starter default")


class CtrThresholdIn(BaseModel):
    individual_minor: int = Field(gt=0, le=10**15)
    business_minor: int = Field(gt=0, le=10**15)
    rationale: str = Field(min_length=1, max_length=2000)


def report_out(row: dict[str, Any]) -> ReportOut:
    return ReportOut(
        id=row["id"],
        reference=row["reference"],
        report_type=row["report_type"],
        status=row["status"],
        case_id=row["case_id"],
        case_reference=row["case_reference"],
        customer=CustomerRef(
            id=row["customer_id"],
            external_id=row["customer_external_id"],
            name=row["customer_name"],
            customer_type=row["customer_type"],
            risk_level=row["customer_risk"],
        ),
        created_by=UserRef(id=row["created_by"], name=row["created_by_name"]),
        created_at=row["created_at"],
        updated_by=UserRef(id=row["updated_by"], name=row["updated_by_name"]),
        updated_at=row["updated_at"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        submitted_by=row["submitted_by"],
        submitted_at=row["submitted_at"],
        submission_reference=row["submission_reference"],
        approval_pending=row["approval_pending"],
    )


def _detail(conn: Any, report_id: Any) -> ReportDetail:
    row = service.get(conn, report_id)
    return ReportDetail(
        **report_out(row).model_dump(),
        narrative=row["narrative"],
        content=row["content"],
        transactions=[ReportTxn.model_validate(t) for t in row["transactions"]],
        approval_requests=[_request_out(r) for r in workflow.requests_for(conn, report_id)],
    )


def _request_out(row: dict[str, Any]) -> ApprovalRequestOut:
    decided_by = None
    if row["checker_id"] is not None:
        decided_by = UserRef(id=row["checker_id"], name=row["checker_name"])
    return ApprovalRequestOut(
        approval_id=row["id"],
        status=row["status"],
        note=row["rationale"],
        requested_by=UserRef(id=row["maker_id"], name=row["maker_name"]),
        requested_at=row["created_at"],
        decided_by=decided_by,
        decided_at=row["decided_at"],
        decision_note=row["decision_note"],
    )


def problem(exc: ReportError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, exc.detail)


@router.get("/reports", responses=_problems)
def list_reports(
    conn: TenantDb,
    user: Viewer,
    report_type: Annotated[list[ReportType] | None, Query(alias="type")] = None,
    status: Annotated[list[ReportStatus] | None, Query()] = None,
    case_id: UUID | None = None,
    customer_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = service.PAGE_SIZE,
    cursor: str | None = None,
) -> ReportPage:
    filters = Filters(
        report_type=tuple(report_type or ()),
        status=tuple(status or ()),
        case_id=case_id,
        customer_id=customer_id,
    )
    try:
        rows, next_cursor = service.list_reports(conn, filters, limit=limit, cursor=cursor)
    except ReportError as exc:
        raise problem(exc) from None
    return ReportPage(items=[report_out(row) for row in rows], next_cursor=next_cursor)


@router.get("/reports/{report_id}", responses=_problems)
def get_report(report_id: UUID, conn: TenantDb, user: Viewer) -> ReportDetail:
    try:
        return _detail(conn, report_id)
    except ReportError as exc:
        raise problem(exc) from None


@router.post(
    "/cases/{case_id}/str",
    status_code=status.HTTP_201_CREATED,
    dependencies=_writes,
    responses=_problems,
    tags=["cases"],
)
def draft_str(case_id: UUID, conn: TenantDb, user: Drafter, ip: ClientIp) -> ReportDetail:
    """Draft the case's suspicious transaction report, prefilled from the case."""
    try:
        report_id = service.draft_str(conn, user, case_id, now=now(), ip=ip)
        return _detail(conn, report_id)
    except ReportError as exc:
        raise problem(exc) from None


@router.post("/reports/ctr-runs", dependencies=_writes, responses=_problems)
def draft_ctrs(body: CtrRunIn, conn: TenantDb, user: Drafter, ip: ClientIp) -> CtrRunOut:
    """Draft a CTR for each settled cash transaction in the period at or above the threshold.

    Transactions already on a CTR are skipped, so a run can repeat safely. At most
    500 drafts are made per run.
    """
    try:
        ids, more = service.draft_ctrs(
            conn, user, date_from=body.date_from, date_to=body.date_to, now=now(), ip=ip
        )
        drafted = [report_out(service.get(conn, report_id)) for report_id in ids]
    except ReportError as exc:
        raise problem(exc) from None
    return CtrRunOut(drafted=drafted, more=more)


@router.patch("/reports/{report_id}", dependencies=_writes, responses=_problems)
def edit_report(
    report_id: UUID, body: ReportEdit, conn: TenantDb, user: Drafter, ip: ClientIp
) -> ReportDetail:
    """Edit a draft's narrative or content. Approved and submitted reports are fixed."""
    try:
        service.update_draft(
            conn, user, report_id, narrative=body.narrative, content=body.content, now=now(), ip=ip
        )
        return _detail(conn, report_id)
    except ReportError as exc:
        raise problem(exc) from None


@router.get("/ctr-threshold", responses=_problems)
def get_ctr_threshold(conn: TenantDb, user: Viewer) -> CtrThreshold:
    return CtrThreshold.model_validate(service.ctr_threshold(conn, now()))


@router.put("/ctr-threshold", dependencies=_writes, responses=_problems)
def set_ctr_threshold(
    body: CtrThresholdIn, conn: TenantDb, user: Admin, ip: ClientIp
) -> CtrThreshold:
    """Set the CTR threshold per customer type, in kobo; the change is versioned."""
    try:
        value = service.set_ctr_threshold(
            conn,
            user,
            individual_minor=body.individual_minor,
            business_minor=body.business_minor,
            rationale=body.rationale,
            now=now(),
            ip=ip,
        )
    except ReportError as exc:
        raise problem(exc) from None
    return CtrThreshold.model_validate(value)


@router.post(
    "/reports/{report_id}/approval-request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_writes,
    responses=_problems,
)
def request_approval(
    report_id: UUID, body: ApprovalIn, conn: TenantDb, user: Drafter, ip: ClientIp
) -> ApprovalRequested:
    """Ask a second person to approve the draft; it cannot change while the request waits."""
    try:
        approval = workflow.request_approval(
            conn, user, report_id, note=body.note, now=now(), ip=ip
        )
    except ReportError as exc:
        raise problem(exc) from None
    return ApprovalRequested(
        approval_id=approval.id, status=approval.status, requested_at=approval.created_at
    )


@router.post("/reports/{report_id}/submitted", dependencies=_writes, responses=_problems)
def mark_submitted(
    report_id: UUID, body: SubmittedIn, conn: TenantDb, user: Submitter, ip: ClientIp
) -> ReportDetail:
    """Record that the approved report was filed, with the NFIU's reference."""
    try:
        workflow.mark_submitted(
            conn,
            user,
            report_id,
            submission_reference=body.submission_reference,
            now=now(),
            ip=ip,
        )
        return _detail(conn, report_id)
    except ReportError as exc:
        raise problem(exc) from None


class RuleAlerts(BaseModel):
    rule_id: UUID
    rule_name: str
    rule_type: str
    created: int
    closed: int
    no_suspicion: int


class AlertMetrics(BaseModel):
    created: int
    transactions_received: int
    per_1000_transactions: float | None = Field(description="Alerts created per 1,000 received")
    closed: int
    closed_no_suspicion: int
    auto_closed: int
    false_positive_share: float | None = Field(description="closed_no_suspicion / closed")
    median_time_to_first_review: float | None = Field(description="Seconds")
    reviewed: int = Field(description="Alerts created in the period that a person has acted on")
    median_time_to_close: float | None = Field(description="Seconds")
    by_rule: list[RuleAlerts]


class CaseOutcomes(BaseModel):
    no_suspicion: int
    reported: int
    referred: int


class CaseMetrics(BaseModel):
    opened: int
    closed: int
    outcomes: CaseOutcomes
    median_investigation_turnaround: float | None = Field(
        description="Seconds from opening to approved closure"
    )


class ReportCounts(BaseModel):
    report_type: ReportType
    drafted: int
    approved: int
    submitted: int
    outstanding: int = Field(description="Not yet submitted, whenever drafted")


class StrConversion(BaseModel):
    strs_submitted: int
    cases_closed: int
    rate: float | None = Field(description="strs_submitted / cases_closed")


class AgeBucket(BaseModel):
    bucket: Literal["0-2", "3-7", "8-30", "31+"] = Field(description="Age in days")
    count: int


class Ageing(BaseModel):
    alerts: list[AgeBucket]
    cases: list[AgeBucket]


class Dashboard(BaseModel):
    date_from: datetime
    date_to: datetime
    generated_at: datetime
    alerts: AlertMetrics
    cases: CaseMetrics
    reports: list[ReportCounts]
    str_conversion: StrConversion
    ageing: Ageing = Field(description="Open alerts and cases now, by age")


@router.get("/dashboard", responses=_problems, tags=["dashboard"])
def get_dashboard(
    conn: TenantDb,
    user: Viewer,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Dashboard:
    """Management and effectiveness figures for a period, by default the last 30 days.

    Every rate comes with its numerator and denominator so it reconciles to the records.
    """
    current = now()
    end = date_to or current
    start = date_from or end - timedelta(days=30)
    if start >= end:
        raise ProblemError(400, "PERIOD_INVALID", "The period must end after it starts")
    if end - start > timedelta(days=366):
        raise ProblemError(400, "PERIOD_INVALID", "The period can be at most a year")
    return Dashboard.model_validate(
        dashboard.build(conn, date_from=start, date_to=end, now=current)
    )
