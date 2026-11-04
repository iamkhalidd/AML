"""User and role administration, and the settings history (ADM-1, ADM-2).

An administrator adds users, changes roles and disables or re-enables accounts. Code asks
for permissions per request, so a role change takes effect on the user's next request;
disabling also ends every open session at once. Nobody can change their own role or
disable themselves, and the last active administrator stays. Every change is audited.

Settings are versioned rows in ``setting_version``; the modules that own each setting
write them. This module lists every setting with its history so an administrator or
examiner can see who changed what, when and why.
"""

import secrets
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from aml.modules.access import service as access
from aml.modules.access.security import SecretCipher
from aml.modules.access.service import ROLES, AccessError, User
from aml.modules.audit import store

_USER_SELECT = (
    "id, email, full_name, role::text AS role, status, mfa_enabled, failed_logins,"
    " locked_until, created_at,"
    " (SELECT max(s.created_at) FROM app_session s WHERE s.user_id = app_user.id)"
    " AS last_sign_in FROM app_user"
)


class AdminError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def list_users(
    conn: psycopg.Connection, *, status: str | None = None, role: str | None = None
) -> list[dict[str, Any]]:
    query = sql.SQL(_USER_SELECT) + sql.SQL(" WHERE true")
    params: list[Any] = []
    if status is not None:
        query += sql.SQL(" AND status = %s")
        params.append(status)
    if role is not None:
        query += sql.SQL(" AND role::text = %s")
        params.append(role)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query + sql.SQL(" ORDER BY full_name, email"), params).fetchall()


def get_user(conn: psycopg.Connection, user_id: Any) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            sql.SQL(_USER_SELECT) + sql.SQL(" WHERE id = %s"),
            (user_id,),
        ).fetchone()
    if row is None:
        raise AdminError("USER_NOT_FOUND", "No user has this ID")
    return row


def add_user(
    conn: psycopg.Connection,
    cipher: SecretCipher,
    admin: User,
    *,
    email: str,
    full_name: str,
    role: str,
    ip: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Create a user with a one-time password; return the user, the password and the
    authenticator enrolment URI. Both are shown once and never stored in clear."""
    if not full_name.strip():
        raise AdminError("NAME_REQUIRED", "The user needs a name")
    password = secrets.token_urlsafe(18)
    try:
        user, uri = access.create_user(
            conn,
            cipher,
            email=email,
            full_name=full_name.strip(),
            role=role,
            password=password,
            created_by=admin,
            ip=ip,
        )
    except AccessError as exc:
        code = "EMAIL_TAKEN" if "already exists" in str(exc) else "USER_INVALID"
        raise AdminError(code, str(exc)) from None
    return get_user(conn, user.id), password, uri


def _lock_target(conn: psycopg.Connection, admin: User, user_id: Any) -> tuple[str, str]:
    if str(user_id) == str(admin.id):
        raise AdminError("SELF_CHANGE", "Ask another administrator to change your own account")
    row = conn.execute(
        "SELECT role::text, status FROM app_user WHERE id = %s FOR UPDATE", (user_id,)
    ).fetchone()
    if row is None:
        raise AdminError("USER_NOT_FOUND", "No user has this ID")
    return row[0], row[1]


def _keep_last_admin(conn: psycopg.Connection, user_id: Any) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('admin.last_administrator'))")
    row = conn.execute(
        "SELECT count(*) FROM app_user WHERE role = 'administrator' AND status = 'active'"
        " AND id <> %s",
        (user_id,),
    ).fetchone()
    if row is None or row[0] == 0:
        raise AdminError("LAST_ADMINISTRATOR", "The institution needs an active administrator")


def change_role(
    conn: psycopg.Connection,
    admin: User,
    user_id: Any,
    *,
    role: str,
    reason: str,
    ip: str | None = None,
) -> dict[str, Any]:
    if role not in ROLES:
        raise AdminError("ROLE_INVALID", f"Choose one of {', '.join(ROLES)}")
    if not reason.strip():
        raise AdminError("REASON_REQUIRED", "A role change needs a reason")
    with conn.transaction():
        current, status = _lock_target(conn, admin, user_id)
        if current == role:
            raise AdminError("NOTHING_TO_CHANGE", f"The user is already {role}")
        if current == "administrator" and status == "active":
            _keep_last_admin(conn, user_id)
        conn.execute("UPDATE app_user SET role = %s WHERE id = %s", (role, user_id))
        store.record(
            conn,
            "user.role_change",
            object_type="user",
            object_id=user_id,
            user_id=admin.id,
            before={"role": current},
            after={"role": role, "reason": reason.strip()},
            ip=ip,
        )
    return get_user(conn, user_id)


def set_status(
    conn: psycopg.Connection,
    admin: User,
    user_id: Any,
    *,
    active: bool,
    reason: str,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    """Disable (ending every session) or re-enable a user."""
    if not reason.strip():
        raise AdminError("REASON_REQUIRED", "Give a reason")
    new = "active" if active else "disabled"
    with conn.transaction():
        role, status = _lock_target(conn, admin, user_id)
        if status == new:
            raise AdminError("NOTHING_TO_CHANGE", f"The user is already {new}")
        if not active and role == "administrator":
            _keep_last_admin(conn, user_id)
        conn.execute(
            "UPDATE app_user SET status = %s, failed_logins = 0, locked_until = NULL WHERE id = %s",
            (new, user_id),
        )
        store.record(
            conn,
            "user.enable" if active else "user.disable",
            object_type="user",
            object_id=user_id,
            user_id=admin.id,
            before={"status": status},
            after={"status": new, "reason": reason.strip()},
            ip=ip,
        )
        if not active:
            access.revoke_sessions(conn, user_id, now, revoked_by=admin, ip=ip)
    return get_user(conn, user_id)


def settings(conn: psycopg.Connection, now: datetime) -> list[dict[str, Any]]:
    """Every setting's version in effect at ``now``, with who set it and why."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT DISTINCT ON (s.key) s.key, s.version, s.value, s.effective_from,"
            " s.rationale, s.created_by, u.full_name AS created_by_name, s.approved_by,"
            " (SELECT count(*) FROM setting_version h WHERE h.key = s.key) AS versions"
            " FROM setting_version s LEFT JOIN app_user u ON u.id = s.created_by"
            " WHERE s.effective_from <= %s ORDER BY s.key, s.version DESC",
            (now,),
        ).fetchall()


def setting_history(conn: psycopg.Connection, key: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT s.key, s.version, s.value, s.effective_from, s.rationale, s.created_by,"
            " u.full_name AS created_by_name, s.approved_by, 0 AS versions"
            " FROM setting_version s LEFT JOIN app_user u ON u.id = s.created_by"
            " WHERE s.key = %s ORDER BY s.version DESC",
            (key,),
        ).fetchall()
    if not rows:
        raise AdminError("SETTING_NOT_FOUND", "No setting has this key")
    return rows
