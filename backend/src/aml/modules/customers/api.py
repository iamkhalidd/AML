"""Manual customer risk rating (CUS-2). Writes need the CSRF header."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from aml.modules.access.api import ClientIp, TenantDb, now, require, require_csrf
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.modules.alerts.api import UserRef
from aml.modules.customers import rating
from aml.modules.customers.rating import RatingError
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["customers"])

_STATUS_FOR_CODE = {"CUSTOMER_NOT_FOUND": 404}
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404)
}

Viewer = Annotated[User, Depends(require(Permission.ALERTS_VIEW))]
Rater = Annotated[User, Depends(require(Permission.CUSTOMERS_RATE))]
Level = Literal["low", "medium", "high"]


class RatingIn(BaseModel):
    risk_level: Level
    rationale: str = Field(min_length=1, max_length=rating.MAX_RATIONALE)


class RatingOut(BaseModel):
    id: UUID
    customer_id: UUID
    risk_level: Level
    previous_level: Level | None = Field(description="The rating in force before this one")
    rationale: str
    rated_by: UserRef
    rated_at: datetime


def _out(row: dict[str, Any]) -> RatingOut:
    return RatingOut(
        id=row["id"],
        customer_id=row["customer_id"],
        risk_level=row["risk_level"],
        previous_level=row["previous_level"],
        rationale=row["rationale"],
        rated_by=UserRef(id=row["rated_by"], name=row["rated_by_name"]),
        rated_at=row["rated_at"],
    )


def problem(exc: RatingError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, exc.detail)


@router.post(
    "/customers/{customer_id}/risk-ratings",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    responses=_problems,
)
def rate_customer(
    customer_id: UUID, body: RatingIn, conn: TenantDb, user: Rater, ip: ClientIp
) -> RatingOut:
    """Rate the customer's risk; it overrides the feed's rating from now on."""
    try:
        row = rating.rate(
            conn, user, customer_id, level=body.risk_level, rationale=body.rationale, now=now(),
            ip=ip,
        )  # fmt: skip
    except RatingError as exc:
        raise problem(exc) from None
    return _out(row)


@router.get("/customers/{customer_id}/risk-ratings", responses=_problems)
def rating_history(customer_id: UUID, conn: TenantDb, user: Viewer) -> list[RatingOut]:
    """Every manual rating of the customer, newest first."""
    try:
        return [_out(row) for row in rating.history(conn, customer_id)]
    except RatingError as exc:
        raise problem(exc) from None
