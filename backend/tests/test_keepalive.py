"""The working-hours keep-alive for free-tier hosting."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aml import keepalive

LAGOS = ZoneInfo("Africa/Lagos")


def test_hours_are_parsed_and_checked() -> None:
    assert keepalive.parse_hours("7-20") == (7, 20)
    for bad in ("20-7", "7-25", "9-9"):
        with pytest.raises(ValueError, match="Keep-alive hours"):
            keepalive.parse_hours(bad)


@pytest.mark.parametrize(
    ("moment", "awake"),
    [
        (datetime(2026, 11, 4, 6, 59, tzinfo=LAGOS), False),  # Wednesday, before seven
        (datetime(2026, 11, 4, 7, 0, tzinfo=LAGOS), True),
        (datetime(2026, 11, 4, 19, 59, tzinfo=LAGOS), True),
        (datetime(2026, 11, 4, 20, 0, tzinfo=LAGOS), False),
        (datetime(2026, 11, 7, 12, 0, tzinfo=LAGOS), False),  # Saturday
    ],
)
def test_pings_only_on_weekdays_in_working_hours(moment: datetime, awake: bool) -> None:
    assert keepalive.in_window(moment, (7, 20)) is awake


def test_without_a_url_it_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AML_KEEPALIVE_URL", raising=False)
    keepalive.main()  # returns instead of looping
