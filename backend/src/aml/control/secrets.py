"""File-based secret store for tenant database credentials.

First releases keep secrets in files readable only by the service account (infrastructure
plan); the directory is encrypted at rest on the host. Replace with a secrets manager once
there are more than a few tenants.
"""

import os
from pathlib import Path


class SecretStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, name: str) -> Path:
        return self.directory / f"{name}.secret"

    def put(self, name: str, value: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(name)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)

    def get(self, name: str) -> str:
        try:
            return self._path(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise KeyError(name) from None

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)
