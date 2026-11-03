"""The rule types the engine knows, with their parameter schemas and defaults (RUL-1, RUL-2).

Defaults are the illustrative starting values from the rule specifications, not
regulatory figures: each institution calibrates and approves its own. Amounts are in
kobo, so 5,000,000 naira is 500_000_000.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class Mode(StrEnum):
    EVENT = "event"
    SCHEDULED = "scheduled"


class Preemptive(StrEnum):
    """Whether a type may run inside the synchronous check API (ING-9)."""

    YES = "yes"
    OPTIONAL = "optional"
    NO = "no"


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HighValueCash(Parameters):
    individual_threshold_minor: int = Field(500_000_000, gt=0, le=10**15)
    business_threshold_minor: int = Field(1_000_000_000, gt=0, le=10**15)


class Structuring(Parameters):
    window_days: int = Field(3, ge=1, le=31)
    min_count: int = Field(3, ge=2, le=100)
    individual_threshold_minor: int = Field(500_000_000, gt=0, le=10**15)
    business_threshold_minor: int = Field(1_000_000_000, gt=0, le=10**15)
    lower_percent: float = Field(80, gt=0, le=100)
    upper_percent: float = Field(99.99, gt=0, le=100)
    total_percent: float = Field(100, gt=0, le=1000)

    @model_validator(mode="after")
    def _band(self) -> Structuring:
        if self.lower_percent >= self.upper_percent:
            raise ValueError("lower_percent must be below upper_percent")
        return self


class PassThrough(Parameters):
    min_credit_minor: int = Field(100_000_000, gt=0, le=10**15)
    window_hours: int = Field(24, ge=1, le=24 * 31)
    min_debit_percent: float = Field(90, gt=0, le=100)


class DormantSurge(Parameters):
    dormant_days: int = Field(180, ge=1, le=3650)
    min_daily_value_minor: int = Field(50_000_000, gt=0, le=10**15)


class VelocitySpike(Parameters):
    period: Literal["hour", "day"] = "hour"
    lookback_days: int = Field(90, ge=7, le=365)
    multiple: float = Field(5, gt=1, le=1000)
    min_value_minor: int = Field(20_000_000, gt=0, le=10**15)
    min_history_days: int = Field(30, ge=1, le=365)

    @model_validator(mode="after")
    def _history(self) -> VelocitySpike:
        if self.min_history_days > self.lookback_days:
            raise ValueError("min_history_days cannot exceed lookback_days")
        return self


class NewAccountActivity(Parameters):
    new_account_days: int = Field(30, ge=1, le=365)
    min_total_value_minor: int = Field(100_000_000, gt=0, le=10**15)


@dataclass(frozen=True)
class RuleType:
    code: str
    name: str
    mode: Mode
    schedule: str | None  # for scheduled rules, in Africa/Lagos time
    preemptive: Preemptive
    default_severity: str
    parameters: type[Parameters]
    explanation: str  # the plain-language template, filled in from a finding's values (RUL-4)

    def defaults(self) -> dict[str, Any]:
        return self.parameters().model_dump()


RULE_TYPES: dict[str, RuleType] = {
    t.code: t
    for t in (
        RuleType("R01", "High-value cash", Mode.EVENT, None, Preemptive.YES, "medium",
                 HighValueCash,
                 "A cash {direction} of {amount} on {date} met or exceeded the {customer_type}"
                 " threshold of {threshold}."),
        RuleType("R02", "Structuring below a reporting threshold", Mode.SCHEDULED,
                 "daily 02:00", Preemptive.NO, "high", Structuring,
                 "{count} cash deposits between {low} and {high} over {days} days totalled"
                 " {total}, each below the threshold of {threshold}."),
        RuleType("R03", "Rapid pass-through", Mode.EVENT, None, Preemptive.OPTIONAL, "high",
                 PassThrough,
                 "{credit} credited on {date} and {debit} ({percent}) left the account within"
                 " {hours} hours."),
        RuleType("R04", "Dormant account surge", Mode.EVENT, None, Preemptive.OPTIONAL,
                 "medium", DormantSurge,
                 "After {days} days with no activity, {amount} moved through the account on"
                 " {date}."),
        RuleType("R05", "Velocity spike", Mode.SCHEDULED, "hourly", Preemptive.NO, "medium",
                 VelocitySpike,
                 "{count} transactions worth {amount} in {period}, which is {multiple} times"
                 " the {days}-day average."),
        RuleType("R13", "New account high activity", Mode.SCHEDULED, "daily 02:00",
                 Preemptive.NO, "medium", NewAccountActivity,
                 "{amount} moved through an account opened on {opened}, within {days} days of"
                 " opening, reaching the new-account threshold of {threshold}."),
    )
}  # fmt: skip

# The rules a new institution starts from: every type, with the illustrative defaults above.
STARTER_RULES = tuple(RULE_TYPES)


class ParametersInvalidError(ValueError):
    """The parameters do not fit the rule type's schema; the message says where."""


def validate_parameters(rule_type: RuleType, values: dict[str, Any]) -> dict[str, Any]:
    """Check ``values`` against the type's schema and return them with defaults filled in."""
    try:
        return rule_type.parameters.model_validate(values).model_dump()
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"])
            problems.append(f"{where}: {error['msg']}" if where else error["msg"])
        raise ParametersInvalidError("; ".join(problems)) from None
