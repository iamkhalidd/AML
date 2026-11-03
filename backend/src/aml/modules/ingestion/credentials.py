"""Bearer credentials for the integration API (API specification: authentication).

A token is ``aml_`` followed by 32 random bytes in URL-safe base64. Only its SHA-256 is
stored, so a copy of the database does not let anyone call the API. Credentials carry
scopes, always expire, can be revoked at once and can be limited to IP ranges.
"""

import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import class_row

from aml.modules.audit import store

SCOPES = ("ingest:write", "checks:invoke", "alerts:read", "webhooks:manage")
TOKEN_PREFIX = "aml_"  # noqa: S105 - a marker, not a secret
MAX_DAYS = 365


class CredentialError(Exception):
    pass


@dataclass(frozen=True)
class Credential:
    id: UUID
    name: str
    scopes: list[str]
    allowed_ips: list[Any]
    created_by: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None

    @property
    def label(self) -> str:
        """How the audit trail names the caller."""
        return f"api:{self.name}"


_COLUMNS = sql.SQL(
    "id, name, scopes, allowed_ips, created_by, created_at, expires_at, revoked_at, last_used_at"
)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def create_credential(
    conn: psycopg.Connection,
    *,
    name: str,
    scopes: list[str],
    days: int,
    operator: str,
    allowed_ips: list[str] | None = None,
) -> tuple[Credential, str]:
    """Create a credential and return it with its token, which is never shown again."""
    unknown = sorted(set(scopes) - set(SCOPES))
    if not scopes or unknown:
        raise CredentialError(f"Scopes must be some of {', '.join(SCOPES)}")
    if not 1 <= days <= MAX_DAYS:
        raise CredentialError(f"A credential lasts between 1 and {MAX_DAYS} days")
    try:
        networks = [str(ipaddress.ip_network(ip, strict=False)) for ip in allowed_ips or []]
    except ValueError as exc:
        raise CredentialError(str(exc)) from exc
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with conn.transaction():
        if conn.execute("SELECT 1 FROM api_credential WHERE name = %s", (name,)).fetchone():
            raise CredentialError(f"A credential named {name!r} already exists")
        with conn.cursor(row_factory=class_row(Credential)) as cursor:
            credential = cursor.execute(
                sql.SQL(
                    "INSERT INTO api_credential (name, token_hash, scopes, allowed_ips,"
                    " created_by, expires_at)"
                    " VALUES (%s, %s, %s, %s::cidr[], %s, now() + make_interval(days => %s))"
                    " RETURNING {}"
                ).format(_COLUMNS),
                (name, _hash(token), sorted(set(scopes)), networks, operator, days),
            ).fetchone()
        if credential is None:  # pragma: no cover - INSERT ... RETURNING always returns
            raise CredentialError("Credential was not created")
        store.record(
            conn,
            "api_credential.create",
            object_type="api_credential",
            object_id=credential.id,
            actor_type="operator",
            actor_label=operator,
            after={
                "name": name, "scopes": credential.scopes, "allowed_ips": networks,
                "expires_at": credential.expires_at,
            },
        )  # fmt: skip
    return credential, token


def revoke_credential(conn: psycopg.Connection, name: str, *, operator: str) -> None:
    with conn.transaction():
        row = conn.execute(
            "UPDATE api_credential SET revoked_at = now()"
            " WHERE name = %s AND revoked_at IS NULL RETURNING id",
            (name,),
        ).fetchone()
        if row is None:
            raise CredentialError(f"No active credential named {name!r}")
        store.record(
            conn,
            "api_credential.revoke",
            object_type="api_credential",
            object_id=row[0],
            actor_type="operator",
            actor_label=operator,
        )


def list_credentials(conn: psycopg.Connection) -> list[Credential]:
    with conn.cursor(row_factory=class_row(Credential)) as cursor:
        return cursor.execute(
            sql.SQL("SELECT {} FROM api_credential ORDER BY name").format(_COLUMNS)
        ).fetchall()


def authenticate(
    conn: psycopg.Connection, token: str, ip: str | None, now: datetime
) -> Credential | None:
    """The credential for ``token`` if it is live and the caller's address is allowed."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    with conn.cursor(row_factory=class_row(Credential)) as cursor:
        credential = cursor.execute(
            sql.SQL("SELECT {} FROM api_credential WHERE token_hash = %s").format(_COLUMNS),
            (_hash(token),),
        ).fetchone()
    if credential is None or credential.revoked_at is not None or credential.expires_at <= now:
        return None
    if credential.allowed_ips:
        try:
            address = ipaddress.ip_address(ip or "")
        except ValueError:
            return None
        if not any(address in ipaddress.ip_network(str(net)) for net in credential.allowed_ips):
            return None
    # Once a minute is enough to show which credentials are in use.
    if credential.last_used_at is None or now - credential.last_used_at > timedelta(minutes=1):
        conn.execute(
            "UPDATE api_credential SET last_used_at = %s WHERE id = %s", (now, credential.id)
        )
        conn.commit()
    return credential
