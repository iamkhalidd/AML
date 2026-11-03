"""Store records sent through the integration API (CUS-1, CUS-6, KYC-1, ING-1, ING-3, ING-4).

A call is one batch. Every record is counted once: accepted (new or changed), duplicate
(same ID, same content, nothing changes) or rejected (kept in the dead-letter store with
the contract's error code). The counts always add up. A customer change raises its
version and keeps a snapshot of what was received, so earlier states can be shown later.
A transaction never changes once accepted, except that a PENDING one may settle once.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from aml.modules.audit import store
from aml.modules.ingestion.contract import (
    Account,
    Customer,
    RecordRejectedError,
    Transaction,
    check,
)

# The contract allows a little clock drift between the bank's systems and ours.
FUTURE_TOLERANCE = timedelta(minutes=5)


@dataclass
class BatchResult:
    batch_id: UUID
    submitted: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Caller:
    """Who sent the batch, for the audit trail."""

    label: str
    ip: str | None = None


@dataclass(frozen=True)
class Batch:
    """What a record handler needs to know about the batch it belongs to."""

    id: UUID
    source_system: str
    received_at: datetime


def _lower(value: Any) -> Any:
    return None if value is None else str(value).lower()


def _jsonb(value: Any) -> Jsonb:
    return Jsonb(value, dumps=lambda v: json.dumps(v, sort_keys=True, default=str))


def ingest_customers(
    conn: psycopg.Connection, caller: Caller, source_system: str, records: Sequence[Any]
) -> BatchResult:
    return _ingest(conn, caller, source_system, "customer", records, "customer_id", _customer)


def ingest_accounts(
    conn: psycopg.Connection, caller: Caller, source_system: str, records: Sequence[Any]
) -> BatchResult:
    return _ingest(conn, caller, source_system, "account", records, "account_id", _account)


def ingest_transactions(
    conn: psycopg.Connection, caller: Caller, source_system: str, records: Sequence[Any]
) -> BatchResult:
    return _ingest(
        conn, caller, source_system, "transaction", records, "transaction_id", _transaction
    )


Handler = Callable[[psycopg.Connection, Batch, Any], bool]


def _ingest(
    conn: psycopg.Connection,
    caller: Caller,
    source_system: str,
    entity: str,
    records: Sequence[Any],
    id_field: str,
    handle: Handler,
) -> BatchResult:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO ingestion_batch (source_system, entity, submitted, status)"
            " VALUES (%s, %s, %s, 'processing') RETURNING id, received_at",
            (source_system, entity, len(records)),
        ).fetchone()
        assert row is not None  # noqa: S101 - INSERT ... RETURNING always returns a row
        batch = Batch(row[0], source_system, row[1])
        result = BatchResult(batch_id=batch.id, submitted=len(records))
        for index, raw in enumerate(records):
            try:
                with conn.transaction():  # a savepoint: one record's failure stays its own
                    changed = handle(conn, batch, raw)
            except RecordRejectedError as rejection:
                result.rejected += 1
                result.errors.append(
                    {
                        "index": index,
                        id_field: raw.get(id_field) if isinstance(raw, dict) else None,
                        "code": rejection.code,
                        "detail": rejection.detail,
                    }
                )
                conn.execute(
                    "INSERT INTO dead_letter (batch_id, entity, raw, reason_code, reason_detail)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (result.batch_id, entity, _jsonb(raw), rejection.code, rejection.detail),
                )
                continue
            if changed:
                result.accepted += 1
            else:
                result.duplicates += 1
        counts = {
            "submitted": result.submitted, "accepted": result.accepted,
            "duplicates": result.duplicates, "rejected": result.rejected,
        }  # fmt: skip
        conn.execute(
            "UPDATE ingestion_batch SET accepted = %s, duplicates = %s, rejected = %s,"
            " status = 'completed', report = %s WHERE id = %s",
            (
                result.accepted,
                result.duplicates,
                result.rejected,
                _jsonb({"errors": result.errors}),
                result.batch_id,
            ),
        )
        store.record(
            conn,
            f"ingestion.{entity}_batch",
            object_type="ingestion_batch",
            object_id=result.batch_id,
            actor_type="system",
            actor_label=caller.label,
            after={"source_system": source_system, **counts},
            ip=caller.ip,
        )
    return result


_CUSTOMER_COLUMNS = (
    "customer_type", "full_name", "date_of_birth", "nationality", "occupation", "industry",
    "declared_income_min", "declared_income_max", "expected_monthly_credit",
    "expected_monthly_credit_count", "state", "country", "onboarding_date", "kyc_status",
    "kyc_verified_at", "kyc_reference", "risk_level", "is_pep", "segment", "status",
    "registration_number", "registration_date",
)  # fmt: skip


def _customer_values(record: Customer) -> tuple[Any, ...]:
    return (
        _lower(record.customer_type), record.full_name, record.date_of_birth,
        record.nationality, record.occupation, record.industry,
        record.declared_income_min_minor, record.declared_income_max_minor,
        record.expected_monthly_credit_minor, record.expected_monthly_credit_count,
        record.state, record.country, record.onboarding_date, _lower(record.kyc_status),
        record.kyc_verified_at, record.kyc_reference, _lower(record.risk_rating),
        record.is_pep, record.segment, _lower(record.status), record.registration_number,
        record.registration_date,
    )  # fmt: skip


def _customer(conn: psycopg.Connection, batch: Batch, raw: Any) -> bool:
    source_system = batch.source_system
    record = check(Customer, raw)
    snapshot = record.model_dump(mode="json")
    found = conn.execute(
        "SELECT c.id, c.version, v.snapshot FROM customer c"
        " JOIN customer_version v ON v.customer_id = c.id AND v.version = c.version"
        " WHERE c.source_system = %s AND c.external_id = %s FOR UPDATE OF c",
        (source_system, record.customer_id),
    ).fetchone()
    if found is None:
        insert = sql.SQL(
            "INSERT INTO customer (source_system, external_id, {}) VALUES (%s, %s, {})"
            " ON CONFLICT (source_system, external_id) DO NOTHING RETURNING id"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, _CUSTOMER_COLUMNS)),
            sql.SQL(", ").join(sql.Placeholder() for _ in _CUSTOMER_COLUMNS),
        )
        row = conn.execute(
            insert, (source_system, record.customer_id, *_customer_values(record))
        ).fetchone()
        if row is None:  # another batch created it a moment ago
            raise RecordRejectedError(
                "DUPLICATE_CONFLICT", "customer_id was created by a concurrent batch"
            )
        customer_id, version = row[0], 1
    else:
        customer_id, version, previous = found
        if previous == snapshot:
            return False
        version += 1
        update = sql.SQL("UPDATE customer SET {}, version = %s, updated_at = now() WHERE id = %s")
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(column)) for column in _CUSTOMER_COLUMNS
        )
        conn.execute(update.format(assignments), (*_customer_values(record), version, customer_id))
    conn.execute(
        "INSERT INTO customer_version (customer_id, version, snapshot) VALUES (%s, %s, %s)",
        (customer_id, version, _jsonb(snapshot)),
    )
    for owner in record.beneficial_owners:
        conn.execute(
            "INSERT INTO beneficial_owner (customer_id, owner_ref, full_name, date_of_birth,"
            " nationality, ownership_percent, control_type, is_pep)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (customer_id, owner_ref) DO UPDATE SET full_name = excluded.full_name,"
            " date_of_birth = excluded.date_of_birth, nationality = excluded.nationality,"
            " ownership_percent = excluded.ownership_percent,"
            " control_type = excluded.control_type, is_pep = excluded.is_pep",
            (
                customer_id, owner.owner_id, owner.full_name, owner.date_of_birth,
                owner.nationality, owner.ownership_percent, _lower(owner.control_type),
                owner.is_pep,
            ),
        )  # fmt: skip
    return True


def _account(conn: psycopg.Connection, batch: Batch, raw: Any) -> bool:
    source_system = batch.source_system
    record = check(Account, raw)
    owner = conn.execute(
        "SELECT id FROM customer WHERE source_system = %s AND external_id = %s",
        (source_system, record.customer_id),
    ).fetchone()
    if owner is None:
        raise RecordRejectedError("REFERENCE_NOT_FOUND", "customer_id is not known yet")
    values = (
        record.product_type, record.currency, record.opened_date, _lower(record.status),
        record.branch_id,
    )  # fmt: skip
    found = conn.execute(
        "SELECT id, customer_id, product_type, currency, opened_date, status::text, branch_id"
        " FROM account WHERE source_system = %s AND external_id = %s FOR UPDATE",
        (source_system, record.account_id),
    ).fetchone()
    if found is None:
        row = conn.execute(
            "INSERT INTO account (customer_id, source_system, external_id, product_type,"
            " currency, opened_date, status, branch_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (source_system, external_id) DO NOTHING RETURNING id",
            (owner[0], source_system, record.account_id, *values),
        ).fetchone()
        if row is None:
            raise RecordRejectedError(
                "DUPLICATE_CONFLICT", "account_id was created by a concurrent batch"
            )
        return True
    if found[1] != owner[0]:
        raise RecordRejectedError(
            "DUPLICATE_CONFLICT", "customer_id of an existing account cannot change"
        )
    if tuple(found[2:]) == values:
        return False
    conn.execute(
        "UPDATE account SET product_type = %s, currency = %s, opened_date = %s, status = %s,"
        " branch_id = %s, updated_at = now() WHERE id = %s",
        (*values, found[0]),
    )
    return True


# Contract field -> txn column, for everything but status, which may settle once.
_TXN_FIXED = {
    "account_id": "account_id", "customer_id": "customer_id", "timestamp": "occurred_at",
    "value_date": "value_date", "amount_minor": "amount", "currency": "currency",
    "direction": "direction", "channel": "channel", "transaction_type": "txn_type",
    "counterparty_name": "counterparty_name", "counterparty_account": "counterparty_account",
    "counterparty_bank_code": "counterparty_bank_code", "narration": "narration",
    "reference": "reference", "reversal_of": "reversal_of", "location_id": "location_id",
}  # fmt: skip
_TXN_ENUMS = ("direction", "channel", "txn_type")


def _lookup(conn: psycopg.Connection, table: str, source_system: str, external_id: str) -> Any:
    query = sql.SQL("SELECT id FROM {} WHERE source_system = %s AND external_id = %s")
    found = conn.execute(
        query.format(sql.Identifier(table)), (source_system, external_id)
    ).fetchone()
    return None if found is None else found[0]


def _transaction(conn: psycopg.Connection, batch: Batch, raw: Any) -> bool:
    record = check(Transaction, raw)
    if record.timestamp > batch.received_at + FUTURE_TOLERANCE:
        raise RecordRejectedError(
            "TIMESTAMP_INVALID", "timestamp is more than 5 minutes in the future"
        )
    references = {
        "account_id": ("account", record.account_id),
        "customer_id": ("customer", record.customer_id),
        "reversal_of": ("txn", record.reversal_of),
    }
    ids: dict[str, Any] = {}
    for name, (table, external_id) in references.items():
        ids[name] = external_id and _lookup(conn, table, batch.source_system, external_id)
        if external_id and ids[name] is None:
            # Held until the reference arrives (ING-3); story 1.5 reprocesses it.
            raise RecordRejectedError("REFERENCE_NOT_FOUND", f"{name} is not known yet")
    values = {
        "account_id": ids["account_id"], "customer_id": ids["customer_id"],
        "timestamp": record.timestamp, "value_date": record.value_date,
        "amount_minor": record.amount_minor, "currency": record.currency,
        "direction": _lower(record.direction), "channel": _lower(record.channel),
        "transaction_type": _lower(record.transaction_type),
        "counterparty_name": record.counterparty_name,
        "counterparty_account": record.counterparty_account,
        "counterparty_bank_code": record.counterparty_bank_code,
        "narration": record.narration, "reference": record.reference,
        "reversal_of": ids["reversal_of"], "location_id": record.location_id,
    }  # fmt: skip
    status = _lower(record.status)
    stored_columns = sql.SQL(", ").join(
        sql.SQL("{}::text" if column in _TXN_ENUMS else "{}").format(sql.Identifier(column))
        for column in _TXN_FIXED.values()
    )
    found = conn.execute(
        sql.SQL(
            "SELECT id, status::text, {} FROM txn"
            " WHERE source_system = %s AND external_id = %s FOR UPDATE"
        ).format(stored_columns),
        (batch.source_system, record.transaction_id),
    ).fetchone()
    if found is None:
        insert = sql.SQL(
            "INSERT INTO txn (source_system, external_id, batch_id, status, {})"
            " VALUES (%s, %s, %s, %s, {})"
            " ON CONFLICT (source_system, external_id) DO NOTHING RETURNING id"
        ).format(
            sql.SQL(", ").join(sql.Identifier(c) for c in _TXN_FIXED.values()),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        )
        params = (batch.source_system, record.transaction_id, batch.id, status, *values.values())
        if conn.execute(insert, params).fetchone() is None:
            raise RecordRejectedError(
                "DUPLICATE_CONFLICT", "transaction_id was created by a concurrent batch"
            )
        return True
    txn_id, stored_status, *stored = found
    changed = [
        name for name, before in zip(_TXN_FIXED, stored, strict=True) if before != values[name]
    ]
    if changed:
        raise RecordRejectedError(
            "DUPLICATE_CONFLICT", f"an accepted transaction cannot change {', '.join(changed)}"
        )
    if stored_status == status:
        return False
    if stored_status != "pending":
        raise RecordRejectedError(
            "DUPLICATE_CONFLICT",
            f"status cannot change from {stored_status.upper()}; only PENDING may settle",
        )
    conn.execute("UPDATE txn SET status = %s WHERE id = %s", (status, txn_id))
    return True


def get_batch(conn: psycopg.Connection, batch_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, source_system, entity, received_at, status, submitted, accepted,"
        " duplicates, rejected, report FROM ingestion_batch WHERE id = %s",
        (batch_id,),
    ).fetchone()
    if row is None:
        return None
    keys = (
        "batch_id", "source_system", "entity", "received_at", "status", "submitted",
        "accepted", "duplicates", "rejected",
    )  # fmt: skip
    batch = dict(zip(keys, row[:-1], strict=True))
    batch["errors"] = (row[-1] or {}).get("errors", [])
    return batch
