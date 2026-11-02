"""Staff users, sign-in with mandatory second factor, lockout and sessions (ACC-2; ADR-005).

Every function takes an open connection to one tenant's database.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import class_row, dict_row

from aml.modules.access import security
from aml.modules.access.security import SecretCipher

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
) -> tuple[User, str]:
    """Create an active user with a second factor; return the user and its otpauth URI."""
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
            user = cursor.fetchone()
    except UniqueViolation:
        raise AccessError("A user with that email already exists") from None
    return user, security.totp_uri(secret, email)  # type: ignore[return-value]


def sign_in(
    conn: psycopg.Connection,
    cipher: SecretCipher,
    policy: AuthPolicy,
    *,
    email: str,
    password: str,
    code: str,
    now: datetime,
) -> tuple[User, str]:
    """Check password and TOTP code together; return the user and a new session token.

    Each failure counts towards the lockout. Reaching ``policy.max_failed_logins`` locks
    the account for ``policy.lockout``, during which even correct credentials fail.
    """
    with conn.transaction():
        user = _check_credentials(conn, cipher, policy, email.strip().lower(), password, code, now)
        if user is None:
            outcome = None
        else:
            token = security.new_session_token()
            conn.execute(
                "INSERT INTO app_session (user_id, token_hash, created_at, last_seen_at,"
                " expires_at) VALUES (%s, %s, %s, %s, %s)",
                (user.id, security.token_hash(token), now, now, now + policy.absolute),
            )
            outcome = (user, token)
    # Raised after the transaction commits, so the failure count is saved.
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
) -> User | None:
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT id, email, full_name, role::text AS role, password_hash,"
            " mfa_secret_encrypted, mfa_enabled, mfa_last_step, status, failed_logins,"
            " locked_until FROM app_user WHERE email = %s FOR UPDATE",
            (email,),
        ).fetchone()
    if row is None:
        security.verify_password(None, password)  # same work as for a real account
        return None
    password_ok = security.verify_password(row["password_hash"], password)
    if row["locked_until"] is not None and row["locked_until"] > now:
        return None
    step = None
    if password_ok and row["mfa_enabled"] and row["mfa_secret_encrypted"] is not None:
        secret = cipher.decrypt(bytes(row["mfa_secret_encrypted"]))
        step = security.verify_totp(secret, code, now, row["mfa_last_step"])
    if step is None or row["status"] != "active":
        failures = row["failed_logins"] + 1
        if failures >= policy.max_failed_logins:
            conn.execute(
                "UPDATE app_user SET failed_logins = 0, locked_until = %s WHERE id = %s",
                (now + policy.lockout, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE app_user SET failed_logins = %s WHERE id = %s", (failures, row["id"])
            )
        return None
    new_hash = (
        security.hash_password(password) if security.needs_rehash(row["password_hash"]) else None
    )
    conn.execute(
        "UPDATE app_user SET failed_logins = 0, locked_until = NULL, mfa_last_step = %s,"
        " password_hash = coalesce(%s, password_hash) WHERE id = %s",
        (step, new_hash, row["id"]),
    )
    return User(row["id"], row["email"], row["full_name"], row["role"])


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


def sign_out(conn: psycopg.Connection, token: str, now: datetime) -> None:
    with conn.transaction():
        conn.execute(
            "UPDATE app_session SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL",
            (now, security.token_hash(token)),
        )


def revoke_sessions(conn: psycopg.Connection, user_id: Any, now: datetime) -> int:
    """End every open session of a user at once (threat T02); return how many."""
    with conn.transaction():
        cursor = conn.execute(
            "UPDATE app_session SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
            (now, user_id),
        )
    return cursor.rowcount
