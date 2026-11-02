# Backend

FastAPI modular monolith (ADR-003). Modules live under `src/aml/modules/`.

```bash
uv sync
uv run python scripts/setup_local.py   # once: creates local databases and writes .env
uv run aml migrate                     # control plane and every tenant to the latest schema
uv run uvicorn aml.main:app --reload
```

`GET /v1/health` reports the process is up; `GET /v1/health/ready` also checks the database.
Database tests are skipped until the setup script has written `backend/.env`.

## Tenants and migrations (ADR-001, ADR-011)

Each institution gets its own PostgreSQL database and login role. The control-plane database
(`aml_control`) records every tenant, its schema version and an append-only log of operator
actions. The `aml` command does the work, using the `aml_provisioner` login (CREATEDB and
CREATEROLE, not a superuser):

```bash
uv run aml tenant create lapo_mfb --name "LAPO Microfinance Bank"
uv run aml tenant list
uv run aml status                      # schema version of every database against head
uv run aml migrate                     # stops at the first tenant that fails
uv run aml tenant drop lapo_mfb --yes  # local and test environments only
```

Each tenant's connection URL is written to `.secrets/tenants/<slug>.secret` (mode 0600,
git-ignored). Migrations live in `src/aml/migrations/control` and `src/aml/migrations/tenant`;
add one by copying the latest revision file and raising the revision ID. Write the SQL by
hand, keep every migration backward compatible with the release before it, and give each a
working `downgrade()`.

Checks, as CI runs them:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pip-audit
```
