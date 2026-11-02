"""Find the tenant database for a request (ADR-001, ADR-011).

The API is served at https://{institution}.{platform-domain}/v1. The first label of the
host names the tenant; hostnames cannot hold underscores, so ``lapo-mfb`` means the slug
``lapo_mfb``.
"""

import threading

import psycopg

from aml.control.secrets import SecretStore
from aml.control.tenants import SLUG_PATTERN


def slug_from_host(host: str) -> str | None:
    label = host.split(":", 1)[0].split(".", 1)[0].lower().replace("-", "_")
    return label if SLUG_PATTERN.fullmatch(label) else None


class TenantDirectory:
    """Maps active tenants to their database URLs, remembering each one it has found."""

    def __init__(self, control_url: str, secrets: SecretStore) -> None:
        self._control_url = control_url
        self._secrets = secrets
        self._urls: dict[str, str] = {}
        self._lock = threading.Lock()

    def database_url(self, host: str) -> str | None:
        slug = slug_from_host(host)
        if slug is None:
            return None
        with self._lock:
            if slug in self._urls:
                return self._urls[slug]
        with psycopg.connect(self._control_url) as control:
            row = control.execute("SELECT status FROM tenant WHERE slug = %s", (slug,)).fetchone()
        if row is None or row[0] != "active":
            return None
        url = self._secrets.get(slug)
        with self._lock:
            self._urls[slug] = url
        return url
