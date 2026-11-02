"""Staff sign-in endpoints (ACC-2; ADR-005, ADR-012; threat T02).

The session token travels only in an HttpOnly, Secure, SameSite=Strict cookie. Requests
that change state while signed in must also send the CSRF token, readable from its own
cookie, in the X-CSRF-Token header.
"""

import hmac
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from aml.config import Settings
from aml.control.secrets import SecretStore
from aml.modules.access import permissions, security, service
from aml.modules.access.permissions import Permission
from aml.modules.access.security import SecretCipher
from aml.modules.access.service import AuthPolicy, SignInError, User
from aml.problems import ProblemBody, ProblemError
from aml.tenancy import TenantDirectory

SESSION_COOKIE = "aml_session"
CSRF_COOKIE = "aml_csrf"
CSRF_HEADER = "X-CSRF-Token"

router = APIRouter(prefix="/v1/auth", tags=["auth"])
problems: dict[int | str, dict[str, object]] = {
    status.HTTP_401_UNAUTHORIZED: {"model": ProblemBody},
    status.HTTP_404_NOT_FOUND: {"model": ProblemBody},
}


@dataclass(frozen=True)
class AccessContext:
    tenants: TenantDirectory
    cipher: SecretCipher
    policy: AuthPolicy
    cookie_secure: bool


def build_context(settings: Settings) -> AccessContext | None:
    """None when the control database or the MFA key is not configured."""
    if settings.database_url is None or settings.mfa_key is None:
        return None
    return AccessContext(
        tenants=TenantDirectory(
            settings.database_url.get_secret_value(), SecretStore(settings.secrets_dir / "tenants")
        ),
        cipher=SecretCipher(settings.mfa_key.get_secret_value()),
        policy=policy_from(settings),
        cookie_secure=settings.cookie_secure,
    )


def policy_from(settings: Settings) -> AuthPolicy:
    return AuthPolicy(
        idle=timedelta(minutes=settings.session_idle_minutes),
        absolute=timedelta(hours=settings.session_absolute_hours),
        max_failed_logins=settings.max_failed_logins,
        lockout=timedelta(minutes=settings.lockout_minutes),
    )


def context(request: Request) -> AccessContext:
    ctx: AccessContext | None = request.app.state.access
    if ctx is None:
        raise ProblemError(503, "AUTH_NOT_CONFIGURED", "Sign-in is not configured on this server")
    return ctx


Context = Annotated[AccessContext, Depends(context)]


def tenant_db(request: Request, ctx: Context) -> Iterator[psycopg.Connection]:
    url = ctx.tenants.database_url(request.headers.get("host", ""))
    if url is None:
        raise ProblemError(404, "TENANT_UNKNOWN", "No institution is served at this address")
    with psycopg.connect(url) as conn:
        yield conn


TenantDb = Annotated[psycopg.Connection, Depends(tenant_db)]


def now() -> datetime:
    return datetime.now(UTC)


def client_ip(request: Request) -> str | None:
    """The caller's address for the audit trail. Behind a proxy, run uvicorn with
    --proxy-headers and --forwarded-allow-ips so this is the client, not the proxy."""
    return request.client.host if request.client else None


ClientIp = Annotated[str | None, Depends(client_ip)]


def current_user(request: Request, ctx: Context, conn: TenantDb) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user = service.authenticate(conn, ctx.policy, token, now()) if token else None
    if user is None:
        raise ProblemError(401, "NOT_SIGNED_IN", "Sign in to continue")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require(permission: Permission) -> Callable[[User], User]:
    """Dependency that answers 403 unless the signed-in user's role has ``permission``."""

    def check(user: CurrentUser) -> User:
        if not permissions.allowed(user.role, permission):
            raise ProblemError(403, "PERMISSION_DENIED", "Your role cannot do this")
        return user

    return check


def require_csrf(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE, "")
    sent = request.headers.get(CSRF_HEADER, "")
    if not token or not hmac.compare_digest(sent, security.csrf_token(token)):
        raise ProblemError(403, "CSRF_FAILED", "Missing or wrong CSRF token")


class SignInRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)
    code: str = Field(pattern=r"^\d{6}$", description="Current code from the authenticator app")


class UserOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    permissions: list[str] = Field(description="What the role may do, so screens can adapt")


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        permissions=sorted(permissions.ROLE_PERMISSIONS.get(user.role, ())),
    )


@router.post("/sign-in", responses=problems)
def sign_in(
    body: SignInRequest, response: Response, ctx: Context, conn: TenantDb, ip: ClientIp
) -> UserOut:
    try:
        user, token = service.sign_in(
            conn,
            ctx.cipher,
            ctx.policy,
            email=body.email,
            password=body.password,
            code=body.code,
            now=now(),
            ip=ip,
        )
    except SignInError:
        raise ProblemError(
            401,
            "SIGN_IN_FAILED",
            "Sign-in failed",
            "The email, password or code is wrong, or the account is locked for a while.",
        ) from None
    max_age = int(ctx.policy.absolute.total_seconds())
    for name, value, http_only in (
        (SESSION_COOKIE, token, True),
        (CSRF_COOKIE, security.csrf_token(token), False),
    ):
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path="/",
            secure=ctx.cookie_secure,
            httponly=http_only,
            samesite="strict",
        )
    return _user_out(user)


@router.post(
    "/sign-out",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
    responses={**problems, status.HTTP_403_FORBIDDEN: {"model": ProblemBody}},
)
def sign_out(request: Request, response: Response, conn: TenantDb, ip: ClientIp) -> None:
    service.sign_out(conn, request.cookies[SESSION_COOKIE], now(), ip)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", responses=problems)
def me(user: CurrentUser) -> UserOut:
    return _user_out(user)
