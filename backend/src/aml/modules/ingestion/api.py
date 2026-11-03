"""Integration API for the institution's systems (API specification; ING-1, CUS-1, CUS-6).

Callers authenticate with a bearer credential, not a staff session, so these endpoints
need no CSRF token. A well-formed request answers 200 even when some records fail; the
response lists each rejected record by index. 400 means the whole request is malformed.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints

from aml.modules.access.api import ClientIp, TenantDb, now
from aml.modules.ingestion import credentials, service
from aml.modules.ingestion.contract import SCHEMA_VERSION
from aml.modules.ingestion.credentials import Credential
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["integration"])
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404, 413)
}

MAX_CUSTOMERS = 500
MAX_ACCOUNTS = 500


def require_scope(scope: str) -> Callable[..., Credential]:
    """Dependency: 401 without a live credential, 403 if it lacks ``scope``."""

    def check(request: Request, conn: TenantDb, ip: ClientIp) -> Credential:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        credential = None
        if scheme.lower() == "bearer" and token:
            credential = credentials.authenticate(conn, token.strip(), ip, now())
        if credential is None:
            raise ProblemError(401, "CREDENTIAL_INVALID", "Missing, invalid or expired credential")
        if scope not in credential.scopes:
            raise ProblemError(403, "SCOPE_MISSING", f"This credential lacks the {scope} scope")
        return credential

    return check


Ingest = Annotated[Credential, Depends(require_scope("ingest:write"))]
SourceSystem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class Submission(BaseModel):
    schema_version: str
    source_system: SourceSystem
    # Records are checked one by one, so a bad record is reported, not a 400.
    records: list[Any] = Field(min_length=1)


class BatchOut(BaseModel):
    batch_id: UUID
    submitted: int
    accepted: int
    duplicates: int
    rejected: int
    errors: list[dict[str, Any]]


class BatchDetail(BatchOut):
    source_system: str
    entity: str
    received_at: datetime
    status: str


def _checked(submission: Submission, limit: int) -> None:
    if submission.schema_version != SCHEMA_VERSION:
        raise ProblemError(
            400,
            "SCHEMA_VERSION_UNSUPPORTED",
            "Unsupported schema_version",
            f"schema_version must be {SCHEMA_VERSION}",
        )
    if len(submission.records) > limit:
        raise ProblemError(
            413, "BATCH_TOO_LARGE", "Batch too large", f"Send at most {limit} records per call"
        )


def _out(result: service.BatchResult) -> BatchOut:
    return BatchOut.model_validate(result, from_attributes=True)


@router.post("/customers", responses=_problems)
def submit_customers(
    submission: Submission, conn: TenantDb, credential: Ingest, ip: ClientIp
) -> BatchOut:
    """Create or update customers, with their beneficial owners, up to 500 per call."""
    _checked(submission, MAX_CUSTOMERS)
    caller = service.Caller(credential.label, ip)
    return _out(
        service.ingest_customers(conn, caller, submission.source_system, submission.records)
    )


@router.post("/accounts", responses=_problems)
def submit_accounts(
    submission: Submission, conn: TenantDb, credential: Ingest, ip: ClientIp
) -> BatchOut:
    """Create or update accounts, up to 500 per call. The customer must already exist."""
    _checked(submission, MAX_ACCOUNTS)
    caller = service.Caller(credential.label, ip)
    return _out(service.ingest_accounts(conn, caller, submission.source_system, submission.records))


@router.get("/batches/{batch_id}", responses=_problems)
def get_batch(batch_id: UUID, conn: TenantDb, credential: Ingest) -> BatchDetail:
    """The result of a batch, including its rejected records."""
    batch = service.get_batch(conn, batch_id)
    if batch is None:
        raise ProblemError(404, "BATCH_NOT_FOUND", "No batch with this ID")
    return BatchDetail.model_validate(batch)
