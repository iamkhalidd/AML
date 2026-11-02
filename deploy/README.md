# Environments

Every environment is created by script (NFR-13). Local, CI and staging hold synthetic data only.

| Environment | How it is created |
| --- | --- |
| Local | `backend/scripts/setup_local.py` against a local PostgreSQL 18 |
| CI | `.github/workflows/ci.yml`, with a PostgreSQL 18 service container |
| Staging | `deploy/staging/provision.sh` once on the host, then `deploy/staging/deploy.sh` for each release |

## Local

Requires PostgreSQL 18 running on `localhost:5432` and its superuser password.

```bash
cd backend
uv run python scripts/setup_local.py
uv run uvicorn aml.main:app --reload
```

The script creates the `aml_app` role and the `aml_control` and `aml_control_test` databases, then writes `backend/.env`. Running it again resets the `aml_app` password and rewrites the two database lines. Check it worked at <http://127.0.0.1:8000/v1/health/ready>.

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
5. **Deploy.** `deploy/staging/deploy.sh deploy@<host> [image]` decrypts the settings, copies the stack, starts it and fails unless `/v1/health/ready` answers.

Not yet covered, and owned by later stories: database migrations (0.3), WAL archiving and the quarterly restore test, monitoring, the bastion host, and the container image scan in CI.
