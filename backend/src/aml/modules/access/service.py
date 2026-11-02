"""Staff users, sign-in with mandatory second factor, lockout and sessions (ACC-2; ADR-005).

Every function takes an open connection to one tenant's database. Each change writes its
audit entry in the same transaction (AUD-1); refreshing a session's idle timer does not.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import class_row, dict_row

from aml.modules.access import security
from aml.modules.access.security import SecretCipher
from aml.modules.audit import store as audit

ROLES = ("compliance_officer", "analyst", "administrator", "auditor")


class AccessError(Exception):
    """A request that cannot be met as asked (duplicate email, weak password, bad role)."""


class SignInError(Exception):
    """Wrong email, password or code, a locked or disabled account, or no second factor.

    Deliberately one error: the caller must not learn which part was wrong.
    """


@dataclass(frozen=True)
class User:
    id: Any
    email: str
    full_name: str
    role: str


@dataclass(frozen=True)
class AuthPolicy:
    idle: timedelta = timedelta(minutes=30)
    absolute: timedelta = timedelta(hours=12)
    max_failed_logins: int = 5
    lockout: timedelta = timedelta(minutes=15)


def create_user(
    conn: psycopg.Connection,
    cipher: SecretCipher,
    *,
    email: str,
    full_name: str,
    role: str,
    password: str,
    created_by: User | None = None,
    operator: str | None = None,
    ip: str | None = None,
) -> tuple[User, str]:
    """Create an active user with a second factor; return the user and its otpauth URI.

    ``created_by`` is the signed-in administrator, or None when an operator runs the CLI,
    in which case ``operator`` names them for the audit trail.
    """
    if role not in ROLES:
        raise AccessError(f"Unknown role {role!r}; choose one of {', '.join(ROLES)}")
    try:
        password_hash = security.hash_password(password)
    except ValueError as exc:
        raise AccessError(str(exc)) from None
    secret = security.new_totp_secret()
    email = email.strip().lower()
    try:
        with conn.transaction(), conn.cursor(row_factory=class_row(User)) as cursor:
            cursor.execute(
                "INSERT INTO app_user (email, full_name, role, password_hash,"
                " mfa_secret_encrypted, mfa_enabled) VALUES (%s, %s, %s, %s, %s, true)"
                " RETURNING id, email, full_name, role::text AS role",
                (email, full_name, role, password_hash, cipher.encrypt(secret)),
            )
            user: User = cursor.fetchone()  # type: ignore[assignment]
            audit.record(
                conn,
                "user.create",
                object_type="user",
                object_id=user.id,
                user_id=None if created_by is None else created_by.id,
                actor_type="operator" if created_by is None else "user",
                actor_label=operator if created_by is None else None,
                after={"email": user.email, "full_name": user.full_name, "role": user.role},
                ip=ip,
            )
    except UniqueViolation:
        raise AccessError("A user with that email already exists") from None
    return user, security.totp_uri(secret, email)


def sign_in(
    conn: psycopg.Connection,
    cipher: SecretCipher,
    policy: AuthPolicy,
    *,
    email: str,
    password: str,
    code: str,
    now: datetime,
    ip: str | None = None,
) -> tuple[User, str]:
    """Check password and TOTP code together; return the user and a new session token.

    Each failure counts towards the lockout. Reaching ``policy.max_failed_logins`` locks
    the account for ``policy.lockout``, during which even correct credentials fail. The
    audit entry of a failure records why; the caller still learns nothing.
    """
    email = email.strip().lower()
    with conn.transaction():
        account_id, user, reason = _check_credentials(
            conn, cipher, policy, email, password, code, now
        )
        if user is None:
            outcome = None
            audit.record(
                conn,
                "auth.sign_in_failed",
                object_type="user",
                object_id=account_id,
                user_id=account_id,
                after={"email": email, "reason": reason},
                ip=ip,
            )
        else:
            token = security.new_session_token()
            session_id = conn.execute(
                "INSERT INTO app_session (user_id, token_hash, created_at, last_seen_at,"
                " expires_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (user.id, security.token_hash(token), now, now, now + policy.absolute),
            ).fetchone()[0]  # type: ignore[index]
            audit.record(
                conn,
                "auth.sign_in",
                object_type="session",
                object_id=session_id,
                user_id=user.id,
                after={"expires_at": now + policy.absolute},
                ip=ip,
            )
            outcome = (user, token)
    # Raised after the transaction commits, so the failure count and its entry are saved.
    if outcome is None:
        raise SignInError
    return outcome


def _check_credentials(
    conn: psycopg.Connection,
    cipher: SecretCipher,
    policy: AuthPolicy,
    email: str,
    password: str,
    code: str,
    now: datetime,
) -> tuple[Any, User | None, str]:
    """Return the matching account's ID (None if none), the user if sign-in may go ahead,
    and the reason it may not, for the audit trail only."""
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT id, email, full_name, role::text AS role, password_hash,"
            " mfa_secret_encrypted, mfa_enabled, mfa_last_step, status, failed_logins,"
            " locked_until FROM app_user WHERE email = %s FOR UPDATE",
            (email,),
        ).fetchone()
    if row is None:
        security.verify_password(None, password)  # same work as for a real account
        return None, None, "unknown_email"
    password_ok = security.verify_password(row["password_hash"], password)
    if row["locked_until"] is not None and row["locked_until"] > now:
        return row["id"], None, "locked"
    step = None
    if password_ok and row["mfa_enabled"] and row["mfa_secret_encrypted"] is not None:
        secret = cipher.decrypt(bytes(row["mfa_secret_encrypted"]))
        step = security.verify_totp(secret, code, now, row["mfa_last_step"])
    if step is None or row["status"] != "active":
        if row["status"] != "active":
            reason = "disabled"
        elif not password_ok:
            reason = "wrong_password"
        else:
            reason = "wrong_code"
        failures = row["failed_logins"] + 1
        if failures >= policy.max_failed_logins:
            reason += "_now_locked"
            conn.execute(
                "UPDATE app_user SET failed_logins = 0, locked_until = %s WHERE id = %s",
                (now + policy.lockout, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE app_user SET failed_logins = %s WHERE id = %s", (failures, row["id"])
            )
        return row["id"], None, reason
    new_hash = (
        security.hash_password(password) if security.needs_rehash(row["password_hash"]) else None
    )
    conn.execute(
        "UPDATE app_user SET failed_logins = 0, locked_until = NULL, mfa_last_step = %s,"
        " password_hash = coalesce(%s, password_hash) WHERE id = %s",
        (step, new_hash, row["id"]),
    )
    return row["id"], User(row["id"], row["email"], row["full_name"], row["role"]), "ok"


def authenticate(
    conn: psycopg.Connection, policy: AuthPolicy, token: str, now: datetime
) -> User | None:
    """Return the session's user and extend its idle timer, or None if it is not valid."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT s.id AS session_id, u.id, u.email, u.full_name, u.role::text AS role"
            " FROM app_session s JOIN app_user u ON u.id = s.user_id"
            " WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s"
            " AND s.last_seen_at > %s AND u.status = 'active'",
            (security.token_hash(token), now, now - policy.idle),
        ).fetchone()
        if row is None:
            return None
        cursor.execute(
            "UPDATE app_session SET last_seen_at = %s WHERE id = %s", (now, row["session_id"])
        )
    return User(row["id"], row["email"], row["full_name"], row["role"])


def sign_out(conn: psycopg.Connection, token: str, now: datetime, ip: str | None = None) -> None:
    with conn.transaction():
        row = conn.execute(
            "UPDATE app_session SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL"
            " RETURNING id, user_id",
            (now, security.token_hash(token)),
        ).fetchone()
        if row is not None:
            audit.record(
                conn,
                "auth.sign_out",
                object_type="session",
                object_id=row[0],
                user_id=row[1],
                ip=ip,
            )


def revoke_sessions(
    conn: psycopg.Connection,
    user_id: Any,
    now: datetime,
    *,
    revoked_by: User | None = None,
    operator: str | None = None,
    ip: str | None = None,
) -> int:
    """End every open session of a user at once (threat T02); return how many.

    ``revoked_by`` is the signed-in administrator, or None for an operator (``operator``).
    """
    with conn.transaction():
        cursor = conn.execute(
            "UPDATE app_session SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
            (now, user_id),
        )
        audit.record(
            conn,
            "session.revoke_all",
            object_type="user",
            object_id=user_id,
            user_id=None if revoked_by is None else revoked_by.id,
            actor_type="operator" if revoked_by is None else "user",
            actor_label=operator if revoked_by is None else None,
            after={"sessions_revoked": cursor.rowcount},
            ip=ip,
        )
    return cursor.rowcount
