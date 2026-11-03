"""Plain-language explanations of findings (RUL-4).

Each rule type carries a template; the engine fills it in with the values the rule found,
so an analyst reads the actual amounts and dates rather than a rule code. Amounts are kept
in kobo in the trigger values and shown here in naira; dates are Lagos calendar dates.
"""

from datetime import date
from typing import Any

from aml.modules.rules.types import RULE_TYPES

# Trigger values that hold an amount in minor units.
MONEY = frozenset(
    {"amount", "threshold", "total", "low", "high", "credit", "debit", "average_amount"}
)
DATES = frozenset({"date", "last_activity", "opened"})
SYMBOLS = {"NGN": "₦"}


def money(minor: int, currency: str = "NGN") -> str:
    major, kobo = divmod(int(minor), 100)
    text = f"{major:,}" if kobo == 0 else f"{major:,}.{kobo:02d}"
    symbol = SYMBOLS.get(currency)
    return f"{symbol}{text}" if symbol else f"{currency} {text}"


def _day(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day.day} {day:%B %Y}"


def _number(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _show(key: str, value: Any, currency: str) -> str:
    if value is None:
        return "an unmeasured number of" if key == "multiple" else "unknown"
    if key in MONEY:
        return money(value, currency)
    if key in DATES:
        return _day(value)
    if key == "percent":
        return f"{_number(value)}%"
    if key == "period":
        return {"hour": "one hour", "day": "one day"}.get(value, str(value))
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def explain(rule_type: str, values: dict[str, Any]) -> str:
    """Fill in the rule type's template; a value the template needs but lacks shows as unknown."""
    currency = values.get("currency", "NGN")

    class Shown(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return "unknown"

    shown = Shown({key: _show(key, value, currency) for key, value in values.items()})
    return RULE_TYPES[rule_type].explanation.format_map(shown)
