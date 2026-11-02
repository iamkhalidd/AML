"""Write and check a tenant's audit trail (AUD-1, AUD-2, NFR-10; ADR-006).

``record`` adds one entry inside the caller's transaction, so a change and its audit
entry commit together or not at all. Call it as the last statement of the transaction:
from that point the chain's head row stays locked until commit, which is what keeps
concurrent writers in one order.

The database sets the time, sequence number and hashes; the application cannot.
"""

import ipaddress
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

ACTOR_TYPES = ("user", "operator", "system")
MONTHS_AHEAD = 3


@dataclass(frozen=True)
class ChainReport:
    """The outcome of recomputing a tenant's chain; ``problem`` is None when intact."""

    entries: int
    last_seq: int
    last_hash: bytes | None
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)  # UUIDs and times as text


def _jsonb(value: Mapping[str, Any] | None) -> Jsonb | None:
    return None if value is None else Jsonb(dict(value), dumps=_dumps)


def _ip(value: str | None) -> str | None:
    """Keep only a real address; test clients and some proxies send names."""
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def record(
    conn: psycopg.Connection,
    action: str,
    *,
    object_type: str,
    object_id: Any = None,
    user_id: Any = None,
    actor_type: str = "user",
    actor_label: str | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    ip: str | None = None,
) -> int:
    """Add an entry such as ``approval.approve`` and return its sequence number.

    ``before`` and ``after`` hold the values that changed, never secrets: no passwords,
    tokens, codes or second-factor material.
    """
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"Unknown actor type {actor_type!r}")
    row = conn.execute(
        "INSERT INTO audit.entry (user_id, actor_type, actor_label, action, object_type,"
        " object_id, before, after, ip) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING seq",
        (
            user_id,
            actor_type,
            actor_label,
            action,
            object_type,
            None if object_id is None else str(object_id),
            _jsonb(before),
            _jsonb(after),
            _ip(ip),
        ),
    ).fetchone()
    return int(row[0])  # type: ignore[index]


def verify_chain(
    conn: psycopg.Connection, checkpoints: Mapping[int, bytes | None] | None = None
) -> ChainReport:
    """Recompute every entry's hash and link, oldest first, and stop at the first break.

    ``checkpoints`` maps sequence numbers to hashes recorded earlier outside this database
    (the daily digests). They catch a chain rewritten consistently from some point on, and
    vouch for the first remaining entry once older months are removed.
    """
    checkpoints = checkpoints or {}
    with conn.transaction():
        # Read the head first: anything committed after it has a higher sequence number.
        head_seq, head_hash = conn.execute("SELECT seq, hash FROM audit.chain_head").fetchone()  # type: ignore[misc]
        count, previous, expected = 0, None, None
        problem = None
        with conn.cursor(name="audit_verify") as cursor:
            cursor.itersize = 5000
            cursor.execute(
                "SELECT seq, prev_hash, hash, hash = audit.entry_hash(prev_hash, seq, at,"
                " user_id, actor_type, actor_label, action, object_type, object_id, before,"
                " after, ip) FROM audit.entry WHERE seq <= %s ORDER BY seq",
                (head_seq,),
            )
            for seq, prev_hash, entry_hash, intact in cursor:
                problem = _link_problem(seq, prev_hash, previous, expected, checkpoints)
                if problem is None and not intact:
                    problem = f"Entry {seq} has been altered"
                if problem is None and seq in checkpoints and checkpoints[seq] != entry_hash:
                    problem = f"Entry {seq} no longer matches its daily digest"
                if problem is not None:
                    break
                count, previous, expected = count + 1, entry_hash, seq + 1
    if problem is None:
        problem = _tail_problem(head_seq, head_hash, expected, previous, checkpoints)
    return ChainReport(count, head_seq, head_hash, problem)


def _link_problem(
    seq: int,
    prev_hash: bytes | None,
    previous: bytes | None,
    expected: int | None,
    checkpoints: Mapping[int, bytes | None],
) -> str | None:
    if expected is None:  # the first entry still held
        if seq == 1:
            return None if prev_hash is None else "Entry 1 links to an entry before it"
        if checkpoints.get(seq - 1, b"") != prev_hash:
            return f"The chain starts at entry {seq} and no digest vouches for entry {seq - 1}"
        return None
    if seq != expected:
        return f"Entries {expected} to {seq - 1} are missing"
    if prev_hash != previous:
        return f"Entry {seq} does not link to entry {seq - 1}"
    return None


def _tail_problem(
    head_seq: int,
    head_hash: bytes | None,
    expected: int | None,
    previous: bytes | None,
    checkpoints: Mapping[int, bytes | None],
) -> str | None:
    if expected is None:  # no entries held
        if head_seq != 0 and checkpoints.get(head_seq, b"") != head_hash:
            return f"Entries up to {head_seq} are missing"
    elif expected - 1 != head_seq or previous != head_hash:
        return f"Entries {expected} to {head_seq} are missing"
    later = [seq for seq in checkpoints if seq > head_seq]
    if later:
        return f"A digest saw entry {max(later)} but the chain ends at entry {head_seq}"
    return None


def ensure_partitions(conn: psycopg.Connection, months_ahead: int = MONTHS_AHEAD) -> int:
    """Create missing monthly partitions; return how many. Needs the audit role."""
    row = conn.execute("SELECT audit.ensure_partitions(%s)", (months_ahead,)).fetchone()
    return int(row[0])  # type: ignore[index]
