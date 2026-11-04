"""Tenant database credentials: files, or environment variables on hosts without a disk."""

from pathlib import Path

import pytest

from aml.control.secrets import SecretStore


def test_secrets_are_kept_in_files(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "tenants")
    store.put("demo_mfb", "postgresql://file")
    assert store.get("demo_mfb") == "postgresql://file"
    store.delete("demo_mfb")
    with pytest.raises(KeyError):
        store.get("demo_mfb")


def test_an_environment_variable_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SecretStore(tmp_path / "tenants")
    store.put("demo_mfb", "postgresql://file")
    monkeypatch.setenv("AML_TENANT_DB_DEMO_MFB", "postgresql://env")
    assert SecretStore.env_name("demo_mfb") == "AML_TENANT_DB_DEMO_MFB"
    assert store.get("demo_mfb") == "postgresql://env"
    store.delete("demo_mfb")
    assert store.get("demo_mfb") == "postgresql://env"
