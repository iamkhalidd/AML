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

## Staff sign-in (ACC-2, ADR-005)

Every user signs in with email, password (Argon2id, at least 12 characters) and the current
code from an authenticator app, all in one request; there is no sign-in without the second
factor. Five failures in a row lock the account for 15 minutes. Sessions live in the tenant
database (only the token's SHA-256 is stored), end after 30 minutes idle or 12 hours, and
can be revoked at once. Tune these with `AML_SESSION_IDLE_MINUTES`,
`AML_SESSION_ABSOLUTE_HOURS`, `AML_MAX_FAILED_LOGINS` and `AML_LOCKOUT_MINUTES`.

The institution comes from the host name: `https://lapo-mfb.<platform-domain>/v1/...` is
tenant `lapo_mfb`. Locally, `http://lapo-mfb.localhost:8000` works in any browser.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/auth/sign-in` | `{email, password, code}`; sets the session and CSRF cookies |
| `GET /v1/auth/me` | the signed-in user |
| `POST /v1/auth/sign-out` | needs the `aml_csrf` cookie value in the `X-CSRF-Token` header |

Create a tenant's first administrator; the command asks for the password and prints the
authenticator enrolment link once:

```bash
uv run aml user create lapo_mfb --email ada@lapo.example --name "Ada Okafor"
```

TOTP secrets are encrypted with `AML_MFA_KEY`, which `setup_local.py` generates once. Do not
change it: every enrolled authenticator would stop working. Errors use the problem-details
shape from the API specification, and every response carries `X-Request-Id`.

Checks, as CI runs them:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pip-audit
```
