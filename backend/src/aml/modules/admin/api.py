"""User and role administration and the settings history (ADM-1, ADM-2).

Writes need the CSRF header. A new user's one-time password and authenticator enrolment
link are returned once, in the response that creates them.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from aml.modules.access.api import (
    ClientIp,
    Context,
    TenantDb,
    now,
    require,
    require_csrf,
)
from aml.modules.access.permissions import ROLE_PERMISSIONS, Permission
from aml.modules.access.service import User
from aml.modules.admin import service
from aml.modules.admin.service import AdminError
from aml.modules.alerts.api import UserRef
from aml.problems import ProblemBody, ProblemError

router = APIRouter(prefix="/v1", tags=["administration"])

_STATUS_FOR_CODE = {
    "USER_NOT_FOUND": 404,
    "SETTING_NOT_FOUND": 404,
    "SELF_CHANGE": 403,
    "EMAIL_TAKEN": 409,
    "LAST_ADMINISTRATOR": 409,
    "NOTHING_TO_CHANGE": 409,
}
_problems: dict[int | str, dict[str, object]] = {
    code: {"model": ProblemBody} for code in (400, 401, 403, 404, 409)
}
_writes = [Depends(require_csrf)]

UserManager = Annotated[User, Depends(require(Permission.USERS_MANAGE))]
SettingsManager = Annotated[User, Depends(require(Permission.SETTINGS_MANAGE))]
Role = Literal["compliance_officer", "analyst", "administrator", "auditor"]


class StaffUser(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: Role
    status: Literal["active", "disabled"]
    permissions: list[str]
    mfa_enabled: bool
    locked: bool = Field(description="Locked after failed sign-ins")
    created_at: datetime
    last_sign_in: datetime | None


class NewUserIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str = Field(min_length=1, max_length=200)
    role: Role


class NewUserOut(StaffUser):
    one_time_password: str = Field(description="Shown once; give it to the user privately")
    mfa_enrolment_uri: str = Field(description="otpauth:// link for the user's authenticator")


class RoleIn(BaseModel):
    role: Role
    reason: str = Field(min_length=1, max_length=2000)


class StatusIn(BaseModel):
    active: bool
    reason: str = Field(min_length=1, max_length=2000)


class SettingVersion(BaseModel):
    key: str
    version: int
    value: Any
    effective_from: datetime
    rationale: str | None
    created_by: UserRef | None
    approved_by: UUID | None
    versions: int = Field(description="How many versions this setting has (0 in a history)")


def _user_out(row: dict[str, Any]) -> StaffUser:
    locked_until = row["locked_until"]
    return StaffUser(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        status=row["status"],
        permissions=sorted(ROLE_PERMISSIONS.get(row["role"], ())),
        mfa_enabled=row["mfa_enabled"],
        locked=locked_until is not None and locked_until > now(),
        created_at=row["created_at"],
        last_sign_in=row["last_sign_in"],
    )


def _setting_out(row: dict[str, Any]) -> SettingVersion:
    created_by = None
    if row["created_by"] is not None:
        created_by = UserRef(id=row["created_by"], name=row["created_by_name"])
    return SettingVersion(
        key=row["key"],
        version=row["version"],
        value=row["value"],
        effective_from=row["effective_from"],
        rationale=row["rationale"],
        created_by=created_by,
        approved_by=row["approved_by"],
        versions=row["versions"],
    )


def problem(exc: AdminError) -> ProblemError:
    return ProblemError(_STATUS_FOR_CODE.get(exc.code, 400), exc.code, exc.detail)


@router.get("/users", responses=_problems)
def list_users(
    conn: TenantDb,
    user: UserManager,
    status: Literal["active", "disabled"] | None = None,
    role: Role | None = None,
) -> list[StaffUser]:
    return [_user_out(row) for row in service.list_users(conn, status=status, role=role)]


@router.post(
    "/users", status_code=status.HTTP_201_CREATED, dependencies=_writes, responses=_problems
)
def add_user(
    body: NewUserIn, ctx: Context, conn: TenantDb, user: UserManager, ip: ClientIp
) -> NewUserOut:
    """Add a user. The response carries a one-time password and enrolment link, once."""
    try:
        row, password, uri = service.add_user(
            conn, ctx.cipher, user, email=body.email, full_name=body.full_name, role=body.role,
            ip=ip,
        )  # fmt: skip
    except AdminError as exc:
        raise problem(exc) from None
    return NewUserOut(
        **_user_out(row).model_dump(), one_time_password=password, mfa_enrolment_uri=uri
    )


@router.get("/users/{user_id}", responses=_problems)
def get_user(user_id: UUID, conn: TenantDb, user: UserManager) -> StaffUser:
    try:
        return _user_out(service.get_user(conn, user_id))
    except AdminError as exc:
        raise problem(exc) from None


@router.put("/users/{user_id}/role", dependencies=_writes, responses=_problems)
def change_role(
    user_id: UUID, body: RoleIn, conn: TenantDb, user: UserManager, ip: ClientIp
) -> StaffUser:
    """Change a user's role; it takes effect on their next request."""
    try:
        row = service.change_role(conn, user, user_id, role=body.role, reason=body.reason, ip=ip)
    except AdminError as exc:
        raise problem(exc) from None
    return _user_out(row)


@router.put("/users/{user_id}/status", dependencies=_writes, responses=_problems)
def set_status(
    user_id: UUID, body: StatusIn, conn: TenantDb, user: UserManager, ip: ClientIp
) -> StaffUser:
    """Disable a user, ending their sessions at once, or enable them again."""
    try:
        row = service.set_status(
            conn, user, user_id, active=body.active, reason=body.reason, now=now(), ip=ip
        )
    except AdminError as exc:
        raise problem(exc) from None
    return _user_out(row)


@router.get("/settings", responses=_problems)
def list_settings(conn: TenantDb, user: SettingsManager) -> list[SettingVersion]:
    """Every institution setting as now in effect, with who set it and why."""
    return [_setting_out(row) for row in service.settings(conn, now())]


@router.get("/settings/{key}/versions", responses=_problems)
def setting_history(key: str, conn: TenantDb, user: SettingsManager) -> list[SettingVersion]:
    """Every version of one setting, newest first."""
    try:
        return [_setting_out(row) for row in service.setting_history(conn, key)]
    except AdminError as exc:
        raise problem(exc) from None
