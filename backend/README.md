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

## Roles and approvals (ACC-3, ACC-4, RUL-5)

Code asks for a permission, never a role. The matrix in
`src/aml/modules/access/permissions.py` is the one place that decides who may do what, and
`GET /v1/auth/me` returns the signed-in user's permissions so screens can adapt. Protect an
endpoint with `Depends(require(Permission.X))`; a refusal is 403 `PERMISSION_DENIED`.

| Role | May |
| --- | --- |
| compliance_officer | view everything, work alerts and cases, approve rule changes, case closures and reports, submit reports, manage users |
| analyst | work alerts and cases, propose rule changes, ask to close cases, draft reports |
| administrator | manage users, settings and the fallback approver; no customer data |
| auditor | view everything, change nothing |

Rule changes, case closures and report submissions go through maker-checker
(`src/aml/modules/access/approvals.py`). The owning module calls `approvals.submit` with a
rationale and registers what approval does with `@approvals.on_approved(kind)`; the handler
runs in the same transaction as the approval. The maker can never decide on their own
change, which a database CHECK enforces too. An administrator can name one fallback
approver (an active compliance officer or analyst) who may approve any kind.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/approvals?status=pending` | the queue, newest first |
| `GET /v1/approvals/{id}` | one request |
| `POST /v1/approvals/{id}/approve` | `{note?}`; checker only |
| `POST /v1/approvals/{id}/reject` | `{note}`; checker only |
| `POST /v1/approvals/{id}/withdraw` | maker only |
| `GET`/`PUT /v1/approvals/fallback-approver` | `{user_id, rationale}`; administrators |

## Audit trail (AUD-1, AUD-2, NFR-10, ADR-006)

Every change writes one entry to `audit.entry` in the tenant database, in the same
transaction as the change: who, when, what (`action` such as `approval.approve`), which
object, the values before and after, and the client IP. Call `audit.record(conn, ...)` from
`aml.modules.audit.store` as the last statement of the transaction. Never put passwords,
codes or session tokens in `before` or `after`.

The database, not the code, keeps the trail honest:

- The `audit` schema belongs to a separate role, `aml_t_<slug>_audit`, which nobody logs in
  as. The application's role may only insert the descriptive columns and read; it cannot
  update, delete, truncate, set the time or sequence, or disable triggers.
- A trigger sets each entry's time, sequence number and SHA-256 hash, which covers the entry
  and the previous entry's hash, so the entries form one chain. Further triggers refuse
  UPDATE, DELETE and TRUNCATE even from the owner.
- `aml audit verify` recomputes the chain for each tenant, keeps monthly partitions three
  months ahead and stores a digest (last sequence number and hash) in the control plane,
  where the tenant cannot reach. Each run checks the chain against earlier digests, so even
  a privileged insider who rewrites the chain and every later hash is caught.

```bash
uv run aml audit verify             # every active tenant; exit code 1 if any chain is broken
uv run aml audit verify lapo_mfb    # one tenant
```

Run it daily (see `deploy/README.md`). A broken chain needs an incident: compare against
backups and the control-plane digests to find what changed. Searching and viewing the trail
come in story 7.1.

Behind a proxy, run uvicorn with `--proxy-headers` (the Docker image does) so the recorded IP
is the client's, not the proxy's.

## Customers and transactions (CUS-1, CUS-6, ING-1)

Tenant migration `t0004` holds the bank's data as the data model describes it: `customer`
(with `customer_version` snapshots, `beneficial_owner` and `related_party`), `account`, `txn`,
and the ingestion stores `ingestion_batch` and `dead_letter`. Every record is identified by
`(source_system, external_id)`, unique per table. Business records are never deleted. Money is
BIGINT minor units (kobo) and must be positive; direction says credit or debit. Timestamps
are `timestamptz` in UTC. Enum values are the data contract's in lower case; the API maps the
upper-case wire values. A completed batch's counts must add up (submitted = accepted +
duplicates + rejected), and a dead letter has a resolution time exactly when it is no longer
held.

## Integration API: customers and accounts (CUS-1, CUS-6, KYC-1, ING-3)

The bank's systems call `/v1` with a bearer credential, never a staff session. An operator
creates one per system; the token is printed once and only its SHA-256 is stored:

```bash
uv run aml credential create <slug> --name core-banking --scope ingest:write --days 90 --allow-ip 10.0.0.0/24
uv run aml credential list <slug>
uv run aml credential revoke <slug> --name core-banking
```

Credentials always expire (at most 365 days), can be revoked at once, and with `--allow-ip`
work only from those ranges. Creating and revoking one is audited.

`POST /v1/customers` and `POST /v1/accounts` take `{schema_version: "0.1", source_system,
records: [...]}`, up to 500 records. A well-formed call answers 200 with `batch_id`,
`submitted`, `accepted`, `duplicates`, `rejected` and `errors` (index, ID, code, detail); a
bad record never sinks the batch. Codes follow the data contract: `SCHEMA_INVALID`,
`ENUM_INVALID`, `AMOUNT_INVALID`, `REFERENCE_NOT_FOUND` (an account whose customer is not
known yet) and `DUPLICATE_CONFLICT`. Rejected records are kept in `dead_letter` as held.
Resending a record unchanged counts as a duplicate; a changed customer gets a new version
with a snapshot of what was received. Beneficial owners are updated but never deleted.
`GET /v1/batches/{id}` reads a batch back. Each batch writes one audit entry with its counts.
The whole request is refused with 400 for a wrong `schema_version` or malformed body, and with
413 above the record limit.

Checks, as CI runs them:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pip-audit
```
