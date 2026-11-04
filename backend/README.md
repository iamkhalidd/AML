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

### Transactions (ING-1, ING-3, ING-4, ING-5)

`POST /v1/transactions` takes the same envelope with up to 1000 records. Amounts are whole
minor units above zero (`AMOUNT_INVALID` otherwise). A `timestamp` needs a UTC offset and may
be at most 5 minutes ahead of when the batch arrived (`TIMESTAMP_INVALID`); `reversal_of`
names the original `transaction_id` and only applies to type `REVERSAL`. A transaction whose
account, customer or original is not known yet is held as `REFERENCE_NOT_FOUND`.

Sending a transaction again is a duplicate and changes nothing, even with the timestamp
written in another offset. Once accepted, a transaction never changes, except that a
`PENDING` one may settle to `SUCCESS`, `FAILED` or `REVERSED`; any other difference is
`DUPLICATE_CONFLICT`, naming the fields.

### Dead letters (ING-3)

Every rejected record stays in `dead_letter` as `held`. `GET /v1/dead-letters` lists them
newest first, filtered by `status` (`held`, `reprocessed`, `discarded`), `entity`, `batch_id`
and `limit` (at most 500). A record held as `REFERENCE_NOT_FOUND` is retried automatically
after each batch from the same source that accepts something, until nothing more resolves,
so a transaction waits for its account and a reversal for its original.
`POST /v1/dead-letters/{id}/reprocess` retries any held record by hand: it becomes
`reprocessed`, or stays held with the new reason. Either way it keeps its original batch,
and the 5-minute future check uses that batch's arrival time. Every resolution, and every
hand retry, is audited. A staff review screen comes with the web interface.

### CSV files (ING-2)

For the MVP, batch files arrive through the API rather than SFTP; at the pilot an SFTP
folder watcher can feed the same importer. `POST /v1/files/{customers|accounts|transactions}`
takes one CSV file as the body (`Content-Type: text/csv`, at most 5 MB) with
`?source_system=core&schema_version=0.1` and the file's SHA-256 in `X-Checksum-SHA256`:

```bash
curl -X POST "https://{tenant}/v1/files/transactions?source_system=core&schema_version=0.1" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: text/csv" \
  -H "X-Checksum-SHA256: $(sha256sum txns.csv | cut -d' ' -f1)" --data-binary @txns.csv
```

The header row must match the template from `GET /v1/files/templates/{kind}` exactly. The
templates follow the contract's field tables, with three differences: money is a decimal
string such as `1500.50` (no sign, no thousand separators, at most two decimals), so its
column drops the `_minor` suffix (`amount`, `declared_income_min`, ...); `source_system`
and `schema_version` come with the upload instead of every row; and beneficial owners are
sent through the API. Files are UTF-8, with or without a byte order mark. An empty cell
leaves the field out.

A checksum mismatch (`BATCH_CHECKSUM_FAILED`) or a header, encoding or CSV error
(`BATCH_TEMPLATE_INVALID`) refuses the whole file with 422. The refusal is kept as a
`failed` batch, which `GET /v1/batches/{id}` shows, and is audited as
`ingestion.file_rejected`. Otherwise each row is checked like an API record, with `index`
counting data rows from 0: a bad amount is `AMOUNT_INVALID` and a row with the wrong
number of cells is `SCHEMA_INVALID`, held with its cells as received. A file batch keeps
its checksum, and its audit entry records it. Sending the same file again only counts
duplicates.

## Rules (RUL-2, RUL-3, RUL-5)

`GET /v1/rule-types` lists the rule types the engine knows (R01â€“R05, R13) with their
mode, schedule, default severity, default parameters and parameter JSON schema. Amounts
are in kobo; the defaults are illustrative starting values, not regulatory figures.

A rule is never edited in place. `POST /v1/rules` (type, name, rationale, and optionally
scope, parameters, severity, preemptive, effective_from) adds a disabled rule and its
version 1; `POST /v1/rules/{id}/versions` proposes the next version, changing only the
fields sent and starting from the latest approved version. Each proposal is validated
against the type's schema and goes to the approval queue as a `rule_change`; one change
per rule can be pending. Approving it through `/v1/approvals/{id}/approve`, by someone
other than the maker, makes the version count from its `effective_from`, which is never
earlier than the approval. Earlier versions are kept unchanged, so alerts can always be
explained by the parameters that raised them. `GET /v1/rules` and `GET /v1/rules/{id}`
show the version in effect now, any pending approval, and the full version history.
Analysts propose, compliance officers propose and approve, auditors only view.

## Rule engine and job lanes (RUL-6, ADR-004)

Background work lives in each tenant's `job` table, split into lanes: `ingest`,
`evaluate`, `scheduled_rule`, `report` and `notification`. Workers claim jobs with
`FOR UPDATE SKIP LOCKED`, so any number can share a lane, and each lane has its own
workers so a heavy report never delays evaluation. A failed job is retried with
exponential backoff (30 s, 60 s, ...) and is left `failed` after its last attempt. A job
whose worker died is claimed again once its lock is 15 minutes old.

Every accepted transaction, and every pending one that later settles, queues one
`evaluate_transaction` job. That job runs each enabled event rule whose scope covers the
customer, always with the approved version in effect when the job runs. Scheduled rules
run over a window: hourly rules over the previous UTC hour, and daily rules over the
previous Lagos day, once the configured Lagos time has passed. Each window is queued
once; missed windows are not backfilled.

Run one worker per lane (or more for a busy lane) against every active tenant:

```bash
uv run aml worker --lane evaluate
uv run aml worker --lane scheduled_rule
```

`--once` makes a single pass and exits. `GET /v1/engine/status` (anyone who can view rules:
compliance officers, analysts and auditors) shows each lane's backlog and how long jobs finished in the last 24
hours took from queueing to done (p50, p95 and max). This is the measured evaluation time
the institution sees.

## Starter rules (RUL-1, RUL-4)

`src/aml/modules/rules/library.py` holds the starter rules. They look only at settled
(`SUCCESS`) transactions, and each finding carries the values its explanation needs:
amounts in kobo and dates as Lagos calendar dates.

| Rule | Runs | Fires when |
| --- | --- | --- |
| R01 High-value cash | each transaction | one cash transaction is at or above the threshold for the customer's type |
| R02 Structuring | daily, 02:00 Lagos | over `window_days`, at least `min_count` cash deposits each between `lower_percent` and `upper_percent` of the threshold total at least `total_percent` of it, with one on the day evaluated |
| R03 Rapid pass-through | each transaction | debits within `window_hours` after a credit of at least `min_credit_minor` reach `min_debit_percent` of it |
| R04 Dormant account surge | each transaction | after `dormant_days` Lagos days without customer activity (interest and fees do not count), a day's value reaches `min_daily_value_minor` |
| R05 Velocity spike | hourly | the last hour's (or Lagos day's) count or value exceeds `multiple` times the trailing `lookback_days` average, above the value floor |
| R13 New account high activity | daily, 02:00 Lagos | within `new_account_days` of opening, an account's value reaches `min_total_value_minor`; reported on the day it first does |

Event rules fire once per pattern, on the transaction that completes it, whatever order
transactions arrive in. R05 skips customers with less than `min_history_days` of
history and logs each skip with the rule and window.

Each rule type carries a plain-language explanation template, listed by
`GET /v1/rule-types`. A finding's `explanation` fills it in with naira amounts and dates,
for example "A cash deposit of ₦5,000,000 on 2 November 2026 met or exceeded the individual
threshold of ₦5,000,000." The R13 wording is ours; the specifications give none.

A new institution starts from `POST /v1/rules/starter` (needs `rules.propose`). It proposes
every starter rule type the institution has no rule for yet, with the illustrative
defaults, and each proposal waits for a second person's approval like any rule change.
Calling it again proposes nothing.

## Alerts (AUD-7)

Every rule finding becomes an alert, raised inside the evaluating job's transaction. An
alert stores the rule version, the customer record version and the triggering
transactions (`alert_txn`), so a trace shows exactly what fired and on what data. Its
severity is the rule version's, raised one level for a customer rated high risk.

A rule does not raise a second alert for the same customer and trigger window while one is
open: the new transactions are added to that alert as evidence, and `hits` counts the
findings. Event rules have no window, so their findings for one customer gather on a
single open alert until it is closed. Each alert keeps a timeline in `alert_event`
(`created`, `evidence_added`, ...), and the audit store records the same changes
(`alert.created`, `alert.evidence_added`).

## Alert queue (ALT-1, ALT-3)

`GET /v1/alerts` lists alerts most severe first, then oldest first. Filter by `status`,
`severity`, `rule_id`, `rule_type`, `assignee` (a user ID, `me` or `none`), `customer_id`,
`created_from`/`created_to`, and `q` (customer name or ID). `sort` takes `severity`,
`created_at`, `status`, `customer`, `rule`, `amount` or `assignee`, with `-` for
descending. Pages hold 50 alerts; pass `next_cursor` back as `cursor` for the next one. The
response carries the queue's counts: open, mine and overdue (null until review service
standards exist).

`POST /v1/alerts/{id}/claim` assigns an alert to the caller and `POST /v1/alerts/{id}/assign`
with `{"assignee_id": ...}` assigns it to someone else, or unassigns it with `null`. The
assignee must be an active user whose role works alerts, and a closed alert cannot be
reassigned. Assigning an open alert puts it in review; unassigning puts it back to open.

## Alert detail (ALT-2, CUS-8)

`GET /v1/alerts/{id}` returns the alert with what fired: the rule version, its parameters
and a link to the rule; the trigger values; the customer record version it was raised on;
the linked transactions; notes; the full timeline; and the closure once closed. Its
`customer_view` is the unified customer view: KYC status and date, risk, PEP flag,
declared against actual activity (successful credits in the last 30 days), beneficial
owners, accounts and up to 20 earlier alerts. `GET /v1/customers/{id}/view` returns the same
view on its own, with the customer's cases.

`POST /v1/alerts/{id}/notes` with `{"text": ...}` adds a note. Notes are timeline entries
and are never edited; each is also recorded in the audit store (`alert.note_added`).

## Closing alerts (ALT-4)

`POST /v1/alerts/{id}/close` with `{"reason": ..., "note": ...}` closes an alert. Both are
required, and the reason must be a code from the institution's list, which
`GET /v1/alert-closure-reasons` returns. An administrator replaces the list with
`PUT /v1/alert-closure-reasons` and a rationale; each change is a new setting version
(`alert.closure_reasons`) and is audited. Until one is set, a starter list applies. A closed
alert cannot be closed again or reassigned, and an escalated alert closes with its case.
Closing writes a `closed` timeline entry and an `alert.close` audit entry, and frees the
customer and window for a new alert.

## Cases (CAS-1, CAS-2, CAS-5)

`POST /v1/cases` with `{"alert_ids": [...], "title": ..., "note": ...}` opens a case from one
or more open alerts of one customer; the caller holds it. The alerts become `escalated`,
leave the alert queue and keep their explanations, which the case shows. An alert belongs
to one case only. `POST /v1/cases/{id}/alerts` adds more of the customer's alerts.

`GET /v1/cases` lists cases, open first and then oldest first, filtered by `status`,
`assignee` (`me`, `none` or a user ID), `customer_id` and `q` (reference, title or
customer). `GET /v1/cases/{id}` is the workspace: the case, its alerts with their
explanations, the timeline, closure requests and the unified customer view.

The timeline (`case_entry`) lists everything in time order with author and time: opened,
alerts linked, notes, corrections, assignments. Entries are never changed: a database
trigger refuses updates and deletes. `POST /v1/cases/{id}/notes` with `{"text": ...}` adds
a note, and with `"corrects": <entry id>` adds a correction that names the earlier entry.
`POST /v1/cases/{id}/claim` and `/assign` set who holds the case. A closed case cannot
change. Each change is audited (`case.open`, `case.note`, `case.correction`, ...).

## Closing cases (CAS-3, CAS-4)

`POST /v1/cases/{id}/closure-request` with `{"outcome": ..., "reason": ...}` asks to close a
case. The outcome is `no_suspicion`, `reported` (closed with a report) or `referred`, and
the written reason is required. The request goes to the approval queue (`case_closure`)
and the timeline gets a `closure_requested` entry; the case stays open meanwhile. A second
person with the right to approve closes it through `POST /v1/approvals/{id}/approve`. The
requester cannot approve their own request, and neither can the investigator holding the
case. Approval closes the case and its escalated alerts together and is audited
(`case.close`); rejection or withdrawal leaves the case open. The workspace lists every
closure request and its decision.

## Stories awaiting tests

Testing is paused while the system is built. These stories pass lint and type checks but
have no tests yet: 3.1, 3.2, 3.3, 3.4, 4.1, 4.2.

Checks, as CI runs them:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pip-audit
```
