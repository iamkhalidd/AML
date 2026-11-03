"""Rule types, configured rules and rule-change proposals (RUL-2, RUL-3, RUL-5).

Proposals land in the approval queue; a second person approves them through
``/v1/approvals/{id}/approve``. Writes need the CSRF header like every signed-in write.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from aml.modules.access.api import ClientIp, TenantDb, now, require, require_csrf
from aml.modules.access.approvals import Approval, ApprovalError
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.modules.rules import service
from aml.modules.rules.service import Change, Rule, RuleError, RuleVersion
from aml.modules.rules.types import RULE_TYPES, RuleType
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["rules"])

_STATUS_FOR_CODE = {
    "RULE_NOT_FOUND": 404,
    "PERMISSION_DENIED": 403,
    "APPROVAL_ALREADY_PENDING": 409,
}
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404, 409)
}
_writes = [Depends(require_csrf)]

Viewer = Annotated[User, Depends(require(Permission.RULES_VIEW))]
Proposer = Annotated[User, Depends(require(Permission.RULES_PROPOSE))]
Severity = Literal["low", "medium", "high"]


class RuleTypeOut(BaseModel):
    code: str
    name: str
    mode: str
    schedule: str | None
    preemptive: str
    default_severity: str
    defaults: dict[str, Any]
    parameters_schema: dict[str, Any]


class Scope(BaseModel):
    """Which customers the rule looks at. Empty means all customers."""

    customer_type: Literal["individual", "business"] | None = None
    segment: str | None = Field(default=None, min_length=1, max_length=100)


class ChangeIn(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000, description="Why the change is needed")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    scope: Scope | None = None
    enabled: bool | None = None
    parameters: dict[str, Any] | None = Field(
        default=None, description="Only the parameters to change; the rest are kept"
    )
    severity: Severity | None = None
    preemptive: bool | None = None
    effective_from: datetime | None = Field(
        default=None, description="Never earlier than the approval; defaults to it"
    )


class NewRuleIn(ChangeIn):
    rule_type: str = Field(examples=["R01"])
    name: str = Field(min_length=1, max_length=200)


class RuleOut(BaseModel):
    id: UUID
    rule_type: str
    name: str
    scope: dict[str, Any]
    enabled: bool
    created_at: datetime
    current_version: int | None = Field(description="The approved version in effect now")
    pending_approval_id: UUID | None


class RuleVersionOut(BaseModel):
    version: int
    parameters: dict[str, Any]
    severity: str
    preemptive: bool
    effective_from: datetime
    rationale: str
    created_by: UUID
    created_at: datetime
    approval_request_id: UUID | None
    approval_status: str | None
    approved_by: UUID | None
    approved_at: datetime | None


class RuleDetail(RuleOut):
    versions: list[RuleVersionOut]


class ProposalOut(BaseModel):
    rule_id: UUID
    version: int
    approval_id: UUID
    status: str


def _type_out(kind: RuleType) -> RuleTypeOut:
    return RuleTypeOut(
        code=kind.code,
        name=kind.name,
        mode=kind.mode.value,
        schedule=kind.schedule,
        preemptive=kind.preemptive.value,
        default_severity=kind.default_severity,
        defaults=kind.defaults(),
        parameters_schema=kind.parameters.model_json_schema(),
    )


def _rule_out(rule: Rule) -> RuleOut:
    return RuleOut.model_validate(rule, from_attributes=True)


def _proposal_out(result: tuple[RuleVersion, Approval]) -> ProposalOut:
    version, approval = result
    return ProposalOut(
        rule_id=version.rule_instance_id,
        version=version.version,
        approval_id=approval.id,
        status=approval.status,
    )


def _change(body: ChangeIn) -> Change:
    return Change(
        rationale=body.rationale,
        name=body.name,
        scope=None if body.scope is None else body.scope.model_dump(exclude_none=True),
        enabled=body.enabled,
        parameters=body.parameters,
        severity=body.severity,
        preemptive=body.preemptive,
        effective_from=body.effective_from,
    )


def _problem(exc: RuleError | ApprovalError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, str(exc))


@router.get("/rule-types", responses=_problems)
def list_rule_types(user: Viewer) -> list[RuleTypeOut]:
    return [_type_out(kind) for kind in RULE_TYPES.values()]


@router.get("/rules", responses=_problems)
def list_rules(conn: TenantDb, user: Viewer) -> list[RuleOut]:
    return [_rule_out(rule) for rule in service.list_rules(conn, now())]


@router.get("/rules/{rule_id}", responses=_problems)
def get_rule(rule_id: UUID, conn: TenantDb, user: Viewer) -> RuleDetail:
    rule = service.get_rule(conn, rule_id, now())
    if rule is None:
        raise ProblemError(404, "RULE_NOT_FOUND", "No such rule")
    versions = [
        RuleVersionOut.model_validate(v, from_attributes=True)
        for v in service.list_versions(conn, rule_id)
    ]
    return RuleDetail(**_rule_out(rule).model_dump(), versions=versions)


@router.post(
    "/rules", status_code=status.HTTP_201_CREATED, dependencies=_writes, responses=_problems
)
def propose_rule(body: NewRuleIn, conn: TenantDb, user: Proposer, ip: ClientIp) -> ProposalOut:
    try:
        return _proposal_out(
            service.propose_new(conn, user, body.rule_type, _change(body), now=now(), ip=ip)
        )
    except (RuleError, ApprovalError) as exc:
        raise _problem(exc) from None


@router.post(
    "/rules/{rule_id}/versions",
    status_code=status.HTTP_201_CREATED,
    dependencies=_writes,
    responses=_problems,
)
def propose_version(
    rule_id: UUID, body: ChangeIn, conn: TenantDb, user: Proposer, ip: ClientIp
) -> ProposalOut:
    try:
        return _proposal_out(
            service.propose_change(conn, user, rule_id, _change(body), now=now(), ip=ip)
        )
    except (RuleError, ApprovalError) as exc:
        raise _problem(exc) from None
