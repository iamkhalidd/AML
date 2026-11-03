"""The evaluation engine: runs approved rules on job lanes (RUL-6, ADR-004, ADR-008).

Event rules look at one transaction. Each accepted or newly settled transaction queues an
``evaluate_transaction`` job on the ``evaluate`` lane, which runs every enabled event rule
whose scope covers the customer. Scheduled rules look at a time window: the
``scheduled_rule`` lane's worker queues one ``evaluate_window`` job per rule and due slot
(hourly, or daily at a Lagos time over the previous Lagos day), deduplicated so a slot runs
once. Only the latest due slot is queued; missed slots are not backfilled.

Rules always run with the approved version in effect when the job runs. What a rule finds
goes to the registered finding sinks; alerts (story 3.1) are one.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from aml import jobs
from aml.modules.rules import service
from aml.modules.rules.service import Rule, RuleVersion
from aml.modules.rules.types import RULE_TYPES, Mode

LAGOS = ZoneInfo("Africa/Lagos")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    """What one rule found about one customer, with the values that triggered it (RUL-4)."""

    customer_id: Any
    trigger_values: dict[str, Any]
    txn_ids: tuple[Any, ...] = ()
    account_id: Any = None
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass(frozen=True)
class Finding:
    rule: Rule
    version: RuleVersion
    hit: Hit
    evaluated_at: datetime


@dataclass(frozen=True)
class EventContext:
    conn: psycopg.Connection
    rule: Rule
    version: RuleVersion
    txn: dict[str, Any]
    now: datetime

    @property
    def parameters(self) -> dict[str, Any]:
        return self.version.parameters


@dataclass(frozen=True)
class WindowContext:
    conn: psycopg.Connection
    rule: Rule
    version: RuleVersion
    window_start: datetime
    window_end: datetime
    now: datetime
    skipped: list[tuple[Any, str]] = field(default_factory=list)

    @property
    def parameters(self) -> dict[str, Any]:
        return self.version.parameters

    def skip(self, customer_id: Any, reason: str) -> None:
        """Record that a customer in scope could not be evaluated for this window, and why."""
        self.skipped.append((customer_id, reason))
        log.info(
            "rule %s skipped customer %s for %s to %s: %s", self.rule.id, customer_id,
            self.window_start.isoformat(), self.window_end.isoformat(), reason,
        )  # fmt: skip

    def in_scope(self, customer: str = "c") -> sql.Composable:
        """A condition on the customer table alias that keeps customers in the rule's scope."""
        return scope_condition(self.rule.scope, customer)


EventEvaluator = Callable[[EventContext], list[Hit]]
WindowEvaluator = Callable[[WindowContext], list[Hit]]
Sink = Callable[[psycopg.Connection, Finding], None]

_event_rules: dict[str, EventEvaluator] = {}
_window_rules: dict[str, WindowEvaluator] = {}
_sinks: list[Sink] = []


def _check_mode(code: str, mode: Mode) -> None:
    kind = RULE_TYPES.get(code)
    if kind is None or kind.mode is not mode:
        raise ValueError(f"{code} is not a known {mode.value} rule type")


def event_rule(code: str) -> Callable[[EventEvaluator], EventEvaluator]:
    _check_mode(code, Mode.EVENT)

    def register(fn: EventEvaluator) -> EventEvaluator:
        _event_rules[code] = fn
        return fn

    return register


def scheduled_rule(code: str) -> Callable[[WindowEvaluator], WindowEvaluator]:
    _check_mode(code, Mode.SCHEDULED)

    def register(fn: WindowEvaluator) -> WindowEvaluator:
        _window_rules[code] = fn
        return fn

    return register


def on_finding(fn: Sink) -> Sink:
    """Register a sink for findings; it runs in the evaluating job's transaction."""
    _sinks.append(fn)
    return fn


def scope_condition(scope: dict[str, Any], customer: str = "c") -> sql.Composable:
    conditions = [
        sql.SQL("{}::text = {}").format(sql.Identifier(customer, field), sql.Literal(scope[field]))
        for field in ("customer_type", "segment")
        if scope.get(field) is not None
    ]
    return sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("TRUE")


def in_scope(scope: dict[str, Any], customer: dict[str, Any]) -> bool:
    return all(
        scope.get(field) is None or customer.get(field) == scope[field]
        for field in ("customer_type", "segment")
    )


def _emit(conn: psycopg.Connection, rule: Rule, version: RuleVersion, hits: list[Hit],
          now: datetime) -> None:  # fmt: skip
    for hit in hits:
        finding = Finding(rule, version, hit, now)
        for sink in _sinks:
            sink(conn, finding)


# --- Event rules ----------------------------------------------------------------------------


@jobs.handler("evaluate_transaction")
def evaluate_transaction(conn: psycopg.Connection, job: jobs.Job, now: datetime) -> None:
    with conn.cursor(row_factory=dict_row) as cursor:
        txn = cursor.execute(
            "SELECT t.*, t.direction::text AS direction, t.channel::text AS channel,"
            " t.txn_type::text AS txn_type, t.status::text AS status,"
            " c.customer_type::text AS customer_type, c.segment"
            " FROM txn t JOIN customer c ON c.id = t.customer_id WHERE t.id = %s",
            (job.payload["txn_id"],),
        ).fetchone()
    if txn is None:
        return
    for rule in service.list_rules(conn, now):
        evaluate = _event_rules.get(rule.rule_type)
        if evaluate is None or not rule.enabled or not in_scope(rule.scope, txn):
            continue
        version = service.version_in_effect(conn, rule.id, now)
        if version is None:
            continue
        _emit(conn, rule, version, evaluate(EventContext(conn, rule, version, txn, now)), now)


# --- Scheduled rules ------------------------------------------------------------------------


def due_window(schedule: str, now: datetime) -> tuple[datetime, datetime]:
    """The window of the latest slot due at ``now``: the past hour, or the past Lagos day."""
    if schedule == "hourly":
        end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return end - timedelta(hours=1), end
    kind, _, at = schedule.partition(" ")
    if kind != "daily":
        raise ValueError(f"Unknown schedule {schedule!r}")
    local = now.astimezone(LAGOS)
    day = local.date() if local.time() >= time.fromisoformat(at) else local.date() - timedelta(1)
    end = datetime.combine(day, time(), LAGOS)
    return (end - timedelta(days=1)).astimezone(UTC), end.astimezone(UTC)


def schedule_due(conn: psycopg.Connection, now: datetime) -> int:
    """Queue each enabled scheduled rule's latest due window, unless already queued."""
    queued = 0
    for rule in service.list_rules(conn, now):
        kind = RULE_TYPES.get(rule.rule_type)
        if (
            kind is None or kind.schedule is None or rule.rule_type not in _window_rules
            or not rule.enabled or rule.current_version is None
        ):  # fmt: skip
            continue
        start, end = due_window(kind.schedule, now)
        job_id = jobs.enqueue(
            conn, "scheduled_rule", "evaluate_window",
            {"rule_id": str(rule.id), "window_start": start.isoformat(),
             "window_end": end.isoformat()},
            now=now, dedupe_key=f"window:{rule.id}:{end.isoformat()}",
        )  # fmt: skip
        queued += job_id is not None
    return queued


@jobs.handler("evaluate_window")
def evaluate_window(conn: psycopg.Connection, job: jobs.Job, now: datetime) -> None:
    rule = service.get_rule(conn, job.payload["rule_id"], now)
    if rule is None or not rule.enabled:
        return
    evaluate = _window_rules.get(rule.rule_type)
    version = service.version_in_effect(conn, rule.id, now)
    if evaluate is None or version is None:
        return
    context = WindowContext(
        conn, rule, version,
        datetime.fromisoformat(job.payload["window_start"]),
        datetime.fromisoformat(job.payload["window_end"]),
        now,
    )  # fmt: skip
    _emit(conn, rule, version, evaluate(context), now)
