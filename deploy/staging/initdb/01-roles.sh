#!/usr/bin/env bash
# Runs once, when the database volume is first created (postgres image entrypoint).
# Creates the two non-superuser roles and the control-plane database (ADR-011).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v app_password="$AML_APP_DB_PASSWORD" \
  -v provisioner_password="$AML_PROVISIONER_DB_PASSWORD" <<'SQL'
CREATE ROLE aml_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE ROLE aml_provisioner LOGIN NOSUPERUSER CREATEDB CREATEROLE PASSWORD :'provisioner_password';
CREATE DATABASE aml_control OWNER aml_app ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE aml_control FROM PUBLIC;
SQL
