"""Keep a free-tier host awake during working hours: ``python -m aml.keepalive``.

Render's free instances sleep after 15 minutes without a request. This loop requests
``AML_KEEPALIVE_URL`` every ``AML_KEEPALIVE_MINUTES`` (default 10) on weekdays between
``AML_KEEPALIVE_HOURS`` (default ``7-20``, display time zone), and lets the host sleep
otherwise. Pinging around the clock would use nearly all of a free month's instance hours
and keep the database compute awake too. Without ``AML_KEEPALIVE_URL`` it exits at once.
"""

import logging
import os
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from aml.config import get_settings

log = logging.getLogger(__name__)


def parse_hours(text: str) -> tuple[int, int]:
    start, end = (int(part) for part in text.split("-", 1))
    if not 0 <= start < end <= 24:
        raise ValueError(f"Keep-alive hours must look like 7-20, not {text!r}")
    return start, end


def in_window(now: datetime, hours: tuple[int, int]) -> bool:
    """Weekdays, from the start hour up to (not including) the end hour, in ``now``'s zone."""
    return now.weekday() < 5 and hours[0] <= now.hour < hours[1]


def ping(url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - our own URL
            log.debug("keep-alive %s -> %s", url, response.status)
    except OSError as exc:
        log.warning("keep-alive request to %s failed: %s", url, exc)


def main() -> None:
    url = os.environ.get("AML_KEEPALIVE_URL")
    if not url:
        return
    hours = parse_hours(os.environ.get("AML_KEEPALIVE_HOURS", "7-20"))
    every = 60 * float(os.environ.get("AML_KEEPALIVE_MINUTES", "10"))
    zone = ZoneInfo(get_settings().display_timezone)
    while True:
        if in_window(datetime.now(zone), hours):
            ping(url)
        time.sleep(every)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
