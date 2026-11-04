"""The searchable audit view for auditors and compliance officers (AUD-3). Read-only."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from aml.modules.access.api import TenantDb, require
from aml.modules.access.permissions import Permission
from aml.modules.access.service import User
from aml.modules.audit import search
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["audit"])

_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404)
}

Auditor = Annotated[User, Depends(require(Permission.AUDIT_VIEW))]


class Actor(BaseModel):
    type: Literal["user", "operator", "system"]
    user_id: UUID | None
    name: str | None = Field(description="The user's name, or the operator or job label")
    email: str | None


class AuditEntry(BaseModel):
    seq: int
    at: datetime
    actor: Actor
    action: str
    object_type: str
    object_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None


class AuditPage(BaseModel):
    entries: list[AuditEntry]
    next_before_seq: int | None = Field(description="Pass as before_seq for the next page")


class AuditFacets(BaseModel):
    object_types: list[str]
    actions: list[str]


def _out(row: dict[str, Any]) -> AuditEntry:
    return AuditEntry(
        seq=row["seq"],
        at=row["at"],
        actor=Actor(
            type=row["actor_type"],
            user_id=row["user_id"],
            name=row["user_name"] or row["actor_label"],
            email=row["user_email"],
        ),
        action=row["action"],
        object_type=row["object_type"],
        object_id=row["object_id"],
        before=row["before"],
        after=row["after"],
        ip=row["ip"],
    )


@router.get("/audit", responses=_problems)
def search_audit(
    conn: TenantDb,
    user: Auditor,
    user_id: UUID | None = None,
    actor_type: Literal["user", "operator", "system"] | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    object_type: Annotated[str | None, Query(max_length=100)] = None,
    object_id: Annotated[str | None, Query(max_length=200)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=search.MAX_PAGE)] = 50,
) -> AuditPage:
    """Audit entries newest first. ``action`` matches exactly or by prefix (``case``)."""
    if date_from is not None and date_to is not None and date_from >= date_to:
        raise ProblemError(400, "DATE_RANGE_INVALID", "date_from must be before date_to")
    filters = search.Filters(
        user_id=user_id, actor_type=actor_type, action=action, object_type=object_type,
        object_id=object_id, date_from=date_from, date_to=date_to,
    )  # fmt: skip
    rows, next_seq = search.search(conn, filters, before_seq=before_seq, limit=limit)
    return AuditPage(entries=[_out(r) for r in rows], next_before_seq=next_seq)


@router.get("/audit/facets", responses=_problems)
def audit_facets(conn: TenantDb, user: Auditor) -> AuditFacets:
    """The object types and actions recorded so far, to fill the search form."""
    return AuditFacets(**search.facets(conn))


@router.get("/audit/{seq}", responses=_problems)
def get_audit_entry(seq: int, conn: TenantDb, user: Auditor) -> AuditEntry:
    row = search.get(conn, seq)
    if row is None:
        raise ProblemError(404, "AUDIT_ENTRY_NOT_FOUND", "No audit entry has this number")
    return _out(row)
