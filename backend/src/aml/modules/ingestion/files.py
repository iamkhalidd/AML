"""CSV files sent through the API (data contract: CSV batch conventions; ING-2).

A file is one batch of one entity. Its SHA-256 must match the checksum sent with it and
its header row must match the entity's template exactly, or the whole file is rejected and
kept on record as a failed batch. Each row then goes through the same checks as an API
record, so a bad row is rejected on its own. Money is a decimal string such as 1500.50,
converted with exact decimal arithmetic, so its column drops the contract's ``_minor``
suffix. The source system and schema version come with the upload rather than in every
row, and a business's beneficial owners are sent through the API.
"""

import csv
import hashlib
import io
import re
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from aml.modules.audit import store
from aml.modules.ingestion import service
from aml.modules.ingestion.contract import Account, Customer, Record, Transaction, Unreadable

MAX_FILE_BYTES = 5 * 1024 * 1024

_MODELS: dict[str, type[Record]] = {
    "customer": Customer,
    "account": Account,
    "transaction": Transaction,
}
_API_ONLY = {"beneficial_owners"}
_MINOR = "_minor"
_DECIMAL = re.compile(r"\d+(\.\d{1,2})?")  # no sign, no thousand separators
_WHOLE = re.compile(r"\d+")

# The header row each entity's file must start with, in the order of the contract's tables.
TEMPLATES: dict[str, tuple[str, ...]] = {
    entity: tuple(f.removesuffix(_MINOR) for f in model.model_fields if f not in _API_ONLY)
    for entity, model in _MODELS.items()
}


class FileRejectedError(Exception):
    """The whole file is refused; ``batch_id`` is the failed batch that records it."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail
        self.batch_id: UUID | None = None


def template(entity: str) -> str:
    return ",".join(TEMPLATES[entity]) + "\r\n"


def import_file(
    conn: psycopg.Connection,
    caller: service.Caller,
    source_system: str,
    entity: str,
    content: bytes,
    checksum: str,
) -> service.BatchResult:
    """Check and store one file. Raises FileRejectedError after recording a refused file."""
    digest = hashlib.sha256(content).hexdigest()
    try:
        if checksum.strip().lower() != digest:
            raise FileRejectedError(
                "BATCH_CHECKSUM_FAILED", "The file does not match its SHA-256 checksum"
            )
        records = read(entity, content)
    except FileRejectedError as rejection:
        rejection.batch_id = _refused(conn, caller, source_system, entity, digest, rejection)
        raise
    return service.ingest(conn, caller, source_system, entity, records, checksum=digest)


def read(entity: str, content: bytes) -> list[Any]:
    """The file's rows as contract records; a row that cannot be read becomes Unreadable."""
    try:
        text = content.decode("utf-8-sig")  # spreadsheet exports often start with a BOM
    except UnicodeDecodeError:
        raise FileRejectedError("BATCH_TEMPLATE_INVALID", "The file must be UTF-8 text") from None
    if "\x00" in text:  # PostgreSQL text cannot hold it
        raise FileRejectedError("BATCH_TEMPLATE_INVALID", "The file contains a NUL character")
    rows = csv.reader(io.StringIO(text, newline=""))
    expected = TEMPLATES[entity]
    try:
        header = tuple(cell.strip() for cell in next(rows, []))
        if header != expected:
            raise FileRejectedError(
                "BATCH_TEMPLATE_INVALID",
                f"The header row must be the {entity} template: {','.join(expected)}",
            )
        return [_record(entity, cells) for cells in rows if cells]
    except csv.Error as error:
        raise FileRejectedError(
            "BATCH_TEMPLATE_INVALID", f"The file is not valid CSV at line {rows.line_num}"
        ) from error


def _record(entity: str, cells: list[str]) -> Any:
    columns = TEMPLATES[entity]
    named: dict[str, Any] = dict(zip(columns, cells, strict=False))
    if len(cells) != len(columns):
        if len(cells) > len(columns):
            named["extra_cells"] = cells[len(columns) :]
        detail = f"The row has {len(cells)} cells but the template has {len(columns)}"
        return Unreadable(named, "SCHEMA_INVALID", detail)
    fields = _MODELS[entity].model_fields
    record: dict[str, Any] = {}
    for column, cell in named.items():
        value = cell.strip()
        if not value:
            continue  # an empty cell is a field left out
        field = column if column in fields else column + _MINOR
        if field.endswith(_MINOR):
            if not _DECIMAL.fullmatch(value):
                detail = f"{column} must be a decimal amount such as 1500.50"
                return Unreadable(named, "AMOUNT_INVALID", detail)
            record[field] = int(Decimal(value) * 100)
        elif field.endswith("_count") and _WHOLE.fullmatch(value):
            record[field] = int(value)
        else:
            record[field] = value
    return record


def _refused(
    conn: psycopg.Connection,
    caller: service.Caller,
    source_system: str,
    entity: str,
    digest: str,
    rejection: FileRejectedError,
) -> UUID:
    error = {"code": rejection.code, "detail": rejection.detail}
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO ingestion_batch (source_system, entity, checksum, status, report)"
            " VALUES (%s, %s, %s, 'failed', %s) RETURNING id",
            (source_system, entity, digest, Jsonb({"errors": [error]})),
        ).fetchone()
        assert row is not None  # noqa: S101 - INSERT ... RETURNING always returns a row
        store.record(
            conn,
            "ingestion.file_rejected",
            object_type="ingestion_batch",
            object_id=row[0],
            actor_type="system",
            actor_label=caller.label,
            after={"source_system": source_system, "entity": entity, "checksum": digest, **error},
            ip=caller.ip,
        )
    batch_id: UUID = row[0]
    return batch_id
