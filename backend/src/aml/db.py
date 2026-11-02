"""Database connectivity helpers."""

import psycopg

CONNECT_TIMEOUT_SECONDS = 3


def database_reachable(url: str) -> bool:
    """Return True when a connection can be opened and a trivial query answered."""
    try:
        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        return False
    return True
