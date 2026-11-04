# Environments

Every environment is created by script (NFR-13). Local, CI and staging hold synthetic data only.

| Environment | How it is created |
| --- | --- |
| Local | `backend/scripts/setup_local.py` against a local PostgreSQL 18 |
| CI | `.github/workflows/ci.yml`, with a PostgreSQL 18 service container set up by the same `setup_local.py` |
| Staging | `deploy/staging/provision.sh` once on the host, then `deploy/staging/deploy.sh` for each release |
| Free-tier demo | Neon (database), Render (API and workers), Vercel (frontend), all deploying from `main` |

## Local

Requires PostgreSQL 18 running on `localhost:5432` and its superuser password.

```bash
cd backend
uv run python scripts/setup_local.py
uv run uvicorn aml.main:app --reload
```

The script creates two roles, neither a superuser: `aml_app`, which owns the `aml_control` and `aml_control_test` databases, and `aml_provisioner` (CREATEDB, CREATEROLE), which the `aml` command uses to create tenant databases. It then writes `backend/.env`. Running it again resets both passwords and rewrites the database lines. On first run it also generates `AML_MFA_KEY` and keeps it on later runs. Run `uv run aml migrate` afterwards. Check it worked at <http://127.0.0.1:8000/v1/health/ready>.

## Free-tier demo

A public demonstration on free plans, with the synthetic `aml_demo` tenant only. No real customer data goes here: the hosts are outside Nigeria and none of them meets the production controls.

| Part | Where | Notes |
| --- | --- | --- |
| Frontend | Vercel project `aml-demo`, root `frontend/` | <https://aml-demo-flax.vercel.app>. `frontend/vercel.json` forwards `/v1/*` to Render, so the session cookie stays first-party. |
| API and workers | Render web service `aml-demo`, Docker, Frankfurt | <https://aml-demo.onrender.com>. `deploy/render/start.sh` migrates, starts one worker for every lane (`aml worker --lane all`), the keep-alive, then the API. |
| Database | Neon project `aml-platform`, eu-central-1 | Holds `aml_control`, `aml_control_test` and `aml_t_aml_demo`. |

The tenant comes from the first label of the Host header, so the Render hostname `aml-demo.onrender.com` serves the `aml_demo` tenant. Vercel forwards requests with that host.

**Render settings.** Set these on the service, never in the repository:

| Variable | Value |
| --- | --- |
| `AML_ENVIRONMENT` | `staging` |
| `AML_DATABASE_URL` | `aml_app` on `aml_control`, `?sslmode=require` |
| `AML_ADMIN_DATABASE_URL` | `aml_provisioner` on `neondb`, `?sslmode=require` |
| `AML_MFA_KEY` | the Fernet key from `backend/.env` |
| `AML_TENANT_DB_AML_DEMO` | the tenant's URL; overrides the secrets file, since Render's disk is wiped on each deploy |
| `AML_APP_URL` | the Vercel address, for links in emails |
| `AML_KEEPALIVE_URL` | `https://aml-demo.onrender.com/v1/health` |

**Sleeping.** A free Render instance sleeps after 15 minutes without a request, and the first request after that waits about a minute. `python -m aml.keepalive` pings the service every 10 minutes on weekdays from 07:00 to 20:00 Lagos time, and a Vercel cron (`frontend/vercel.json`) wakes it at about 07:00 on weekdays. Outside those hours it sleeps. Keeping it awake all the time would use about 744 of the 750 instance hours a month that the workspace's free services share. It would also keep the Neon compute from scaling down, because the worker queries the database every few seconds, and that would go past Neon's free compute allowance.

**Neon setup.** `backend/scripts/setup_local.py` assumes a local superuser, so the first setup on Neon needed changes. There is no `postgres` database (use `neondb`). Roles cannot be created with `NOSUPERUSER`. The owner role needs `GRANT aml_app TO neondb_owner WITH SET TRUE` before it can create a database owned by `aml_app`. The `public` schema already belongs to `pg_database_owner`. And `aml_provisioner` needs `ALTER ROLE aml_provisioner SET createrole_self_grant = 'set, inherit'` so that it can use the tenant roles it creates.

**Demo tenant.** Created and loaded from a workstation with the Neon URLs in `backend/.env`:

```bash
cd backend
uv run aml demo load aml_demo --name "AML Demo MFB" > .secrets/demo-users.txt
```

The sign-ins it prints are kept in the gitignored `.secrets/demo-users.txt`. Copy `.secrets/tenants/aml_demo.secret` into `AML_TENANT_DB_AML_DEMO` on Render.

Not covered here: the daily audit chain check (NFR-10), which needs a scheduler that Render's free plan lacks; email (no SMTP host is set, so emails go to the log).

## Staging

Hosting is not yet chosen (ADR-002). The scripts assume a plain Ubuntu 24.04 VM at a Nigerian provider, the same layout as one production institution: Caddy edge proxy, the app container and PostgreSQL on a private network.

1. **Image.** CI builds the image on every push and publishes `ghcr.io/iamkhalidd/aml:main` and `:sha-<commit>` from `main`. If the package is private, run `docker login ghcr.io` on the host as the deploy user with a read-only token.
2. **Host.** Copy `deploy/staging/provision.sh` to the new VM and run it once as root:
   `sudo bash provision.sh deploy "ssh-ed25519 AAAA... you@laptop"`.
   It installs Docker, creates a key-only deploy user, disables root and password SSH, enables a default-deny firewall (SSH, 80, 443) and turns on automatic security updates.
3. **DNS.** Point the staging domain at the host. Caddy gets the TLS certificate itself.
4. **Secrets.** Secrets live in an age-encrypted file (infrastructure plan). Once:
   ```bash
   age-keygen -o ~/.config/aml/staging-age-key.txt
   cp deploy/staging/.env.example /tmp/staging.env   # fill in real values
   age -r <public key printed above> -o deploy/staging/staging.env.age /tmp/staging.env
   rm /tmp/staging.env
   ```
   Commit `staging.env.age`. Keep the key file offline with a second copy held by a named trusted person.
5. **Deploy.** `deploy/staging/deploy.sh deploy@<host> [image]` decrypts the settings, copies the stack, starts it, runs `aml migrate` and fails unless `/v1/health/ready` answers.

On first start, `initdb/01-roles.sh` creates the non-superuser `aml_app` and `aml_provisioner` roles and the `aml_control` database. The postgres superuser never leaves the database container. Operator commands run in the on-demand `ops` service, which shares the tenant secrets volume with the app (read-only for the app):

```bash
docker compose run --rm ops tenant create demo_mfb --name "Demo Microfinance Bank"
docker compose run --rm ops status
docker compose run --rm ops user create demo_mfb --email admin@demo.example --name "Demo Admin"
```

The audit chain check must run once a day (NFR-10). Add it to the deploy user's crontab on the host (`crontab -e`); a failed run exits non-zero, so wire cron mail or monitoring to it:

```bash
15 2 * * * cd /opt/aml && docker compose run --rm ops audit verify >> /var/log/aml-audit-verify.log 2>&1
```

Each institution is served at its own subdomain (`demo-mfb.<AML_DOMAIN>`). The Caddyfile does not route those yet; that needs a wildcard DNS record and either a DNS-challenge wildcard certificate or Caddy on-demand TLS.

Not yet covered, and owned by later stories: per-tenant encryption keys (ADR-001), a secrets manager in place of the secrets volume, WAL archiving and the quarterly restore test, monitoring, the bastion host, and the container image scan in CI.
