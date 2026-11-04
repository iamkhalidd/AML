"""Case attachments, limited by type and size (CAS-2).

Only document and image types an investigation needs are taken, and the file's first
bytes must match its type, so a renamed executable is refused. Files are stored with their
SHA-256 and are never changed or removed. Malware scanning (CAS-6) comes with the pilot.
"""

import hashlib
import re
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from aml.modules.access.service import User
from aml.modules.audit import store
from aml.modules.cases import service
from aml.modules.cases.service import CaseError

MAX_BYTES = 10 * 1024 * 1024
MAX_FILENAME = 200

_ZIP = (b"PK\x03\x04",)
# extension -> (content type, accepted leading bytes; empty means text)
TYPES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "pdf": ("application/pdf", (b"%PDF-",)),
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", _ZIP),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _ZIP),
    "csv": ("text/csv", ()),
    "txt": ("text/plain", ()),
}
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\\"]")


def _clean_name(filename: str) -> str:
    name = _UNSAFE.sub("_", filename.strip())[:MAX_FILENAME]
    if not name or name in {".", ".."}:
        raise CaseError("FILENAME_INVALID", "The file needs a name")
    return name


def _content_type(name: str, content: bytes) -> str:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in TYPES:
        raise CaseError("FILE_TYPE_REFUSED", f"Attach one of: {', '.join(sorted(TYPES))}")
    content_type, magic = TYPES[extension]
    if magic and not content.startswith(magic):
        raise CaseError("FILE_TYPE_MISMATCH", f"The file is not a real .{extension} file")
    if not magic:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise CaseError("FILE_TYPE_MISMATCH", "A text file must be UTF-8") from None
    return content_type


def add(
    conn: psycopg.Connection,
    user: User,
    case_id: Any,
    filename: str,
    content: bytes,
    *,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    if not content:
        raise CaseError("FILE_EMPTY", "The file is empty")
    if len(content) > MAX_BYTES:
        raise CaseError("FILE_TOO_LARGE", f"Attach at most {MAX_BYTES // (1024 * 1024)} MB")
    name = _clean_name(filename)
    content_type = _content_type(name, content)
    digest = hashlib.sha256(content).hexdigest()
    with conn.transaction():
        service.lock_open(conn, case_id)
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                "INSERT INTO case_attachment (case_id, filename, content_type, size_bytes,"
                " sha256, content, uploaded_by, uploaded_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id, filename, content_type, size_bytes, sha256, uploaded_by,"
                " uploaded_at",
                (case_id, name, content_type, len(content), digest, content, user.id, now),
            ).fetchone()
        assert row is not None  # noqa: S101 - INSERT ... RETURNING
        detail = {
            "attachment_id": str(row["id"]),
            "filename": name,
            "content_type": content_type,
            "size_bytes": len(content),
            "sha256": digest,
        }
        service.add_entry(conn, case_id, "attachment_added", now, user.id, detail=detail)
        store.record(
            conn,
            "case.attachment_added",
            object_type="case",
            object_id=case_id,
            user_id=user.id,
            after=detail,
            ip=ip,
        )
    return row | {"uploaded_by_name": user.full_name}


def list_for(conn: psycopg.Connection, case_id: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT t.id, t.filename, t.content_type, t.size_bytes, t.sha256, t.uploaded_by,"
            " u.full_name AS uploaded_by_name, t.uploaded_at FROM case_attachment t"
            " JOIN app_user u ON u.id = t.uploaded_by WHERE t.case_id = %s"
            " ORDER BY t.uploaded_at, t.id",
            (case_id,),
        ).fetchall()


def fetch(conn: psycopg.Connection, case_id: Any, attachment_id: Any) -> tuple[str, str, bytes]:
    row = conn.execute(
        "SELECT filename, content_type, content FROM case_attachment"
        " WHERE id = %s AND case_id = %s",
        (attachment_id, case_id),
    ).fetchone()
    if row is None:
        raise CaseError("ATTACHMENT_NOT_FOUND", "This case has no attachment with that ID")
    return row[0], row[1], bytes(row[2])
