"""Operator command line: aml migrate | status | tenant | user | credential | audit verify.

Run from backend/ so .env is found. Every change is logged in the control plane with the
operator's name, taken from AML_OPERATOR or the login name.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

import psycopg

from aml.config import Settings
from aml.control.audit_check import AuditCheck, check_fleet
from aml.control.secrets import SecretStore
from aml.control.tenants import (
    DatabaseStatus,
    Fleet,
    TenantError,
    create_tenant,
    drop_tenant,
    fleet_status,
    get_tenant,
    list_tenants,
    migrate_fleet,
)
from aml.modules.access.security import SecretCipher
from aml.modules.access.service import ROLES, AccessError, create_user
from aml.modules.ingestion.credentials import (
    SCOPES,
    CredentialError,
    create_credential,
    list_credentials,
    revoke_credential,
)


def build_fleet(settings: Settings) -> Fleet:
    if settings.database_url is None or settings.admin_database_url is None:
        raise TenantError(
            "AML_DATABASE_URL and AML_ADMIN_DATABASE_URL must be set (run scripts/setup_local.py)"
        )
    return Fleet(
        control_url=settings.database_url.get_secret_value(),
        admin_url=settings.admin_database_url.get_secret_value(),
        secrets=SecretStore(settings.secrets_dir / "tenants"),
        actor=os.environ.get("AML_OPERATOR") or getpass.getuser(),
        environment=settings.environment,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aml", description="AML platform operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="migrate the control plane and every active tenant")
    commands.add_parser("status", help="show each database's schema version")
    tenant = commands.add_parser("tenant", help="manage tenants")
    tenant_commands = tenant.add_subparsers(dest="tenant_command", required=True)
    create = tenant_commands.add_parser("create", help="provision a tenant database")
    create.add_argument("slug")
    create.add_argument("--name", required=True, help="institution's legal name")
    tenant_commands.add_parser("list", help="list tenants")
    drop = tenant_commands.add_parser("drop", help="delete a tenant (local and test only)")
    drop.add_argument("slug")
    drop.add_argument("--yes", action="store_true", help="confirm deletion")
    user = commands.add_parser("user", help="manage a tenant's staff users")
    user_commands = user.add_subparsers(dest="user_command", required=True)
    create_user_cmd = user_commands.add_parser(
        "create", help="create a user (the first administrator of a new tenant)"
    )
    create_user_cmd.add_argument("slug", help="tenant")
    create_user_cmd.add_argument("--email", required=True)
    create_user_cmd.add_argument("--name", required=True, help="full name")
    create_user_cmd.add_argument("--role", choices=ROLES, default="administrator")
    credential = commands.add_parser("credential", help="manage integration API credentials")
    credential_commands = credential.add_subparsers(dest="credential_command", required=True)
    create_credential_cmd = credential_commands.add_parser(
        "create", help="create a bearer credential; the token is printed once"
    )
    create_credential_cmd.add_argument("slug", help="tenant")
    create_credential_cmd.add_argument("--name", required=True, help="for example core-banking")
    create_credential_cmd.add_argument(
        "--scope", action="append", choices=SCOPES, required=True, dest="scopes"
    )
    create_credential_cmd.add_argument("--days", type=int, default=90, help="lifetime (max 365)")
    create_credential_cmd.add_argument(
        "--allow-ip", action="append", dest="allowed_ips", help="address or CIDR range"
    )
    list_credential_cmd = credential_commands.add_parser("list", help="list credentials")
    list_credential_cmd.add_argument("slug", help="tenant")
    revoke = credential_commands.add_parser("revoke", help="revoke a credential at once")
    revoke.add_argument("slug", help="tenant")
    revoke.add_argument("--name", required=True)
    audit = commands.add_parser("audit", help="check tenants' audit trails")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    verify = audit_commands.add_parser(
        "verify",
        help="daily job: verify each audit hash chain, record its digest, add partitions",
    )
    verify.add_argument("slugs", nargs="*", help="tenants to check (default: every active one)")
    return parser


def _create_user(fleet: Fleet, settings: Settings, args: argparse.Namespace) -> None:
    if settings.mfa_key is None:
        raise TenantError("AML_MFA_KEY must be set (run scripts/setup_local.py)")
    password = getpass.getpass("Password (at least 12 characters): ")
    if getpass.getpass("Repeat password: ") != password:
        raise TenantError("Passwords do not match")
    url = fleet.secrets.get(get_tenant(fleet, args.slug).slug)
    with psycopg.connect(url) as conn:
        user, uri = create_user(
            conn,
            SecretCipher(settings.mfa_key.get_secret_value()),
            email=args.email,
            full_name=args.name,
            role=args.role,
            password=password,
            operator=fleet.actor,
        )
    print(f"{user.email}: {user.role} created")
    print("Add this to the user's authenticator app now; it is not shown again:")
    print(uri)


def _credential(fleet: Fleet, args: argparse.Namespace) -> None:
    url = fleet.secrets.get(get_tenant(fleet, args.slug).slug)
    with psycopg.connect(url) as conn:
        if args.credential_command == "create":
            created, token = create_credential(
                conn,
                name=args.name,
                scopes=args.scopes,
                days=args.days,
                operator=fleet.actor,
                allowed_ips=args.allowed_ips,
            )
            scopes = ", ".join(created.scopes)
            print(f"{created.name}: {scopes} until {created.expires_at:%Y-%m-%d}")
            print("Give this token to the institution now; it is not shown again:")
            print(token)
        elif args.credential_command == "list":
            for item in list_credentials(conn):
                state = "revoked" if item.revoked_at else f"expires {item.expires_at:%Y-%m-%d}"
                print(f"{item.name:<24} {state:<20} {', '.join(item.scopes)}")
        else:
            revoke_credential(conn, args.name, operator=fleet.actor)
            print(f"{args.name}: revoked")


def _print_status(rows: list[DatabaseStatus]) -> None:
    for row in rows:
        marker = "" if row.current == row.head else "  <- behind"
        print(f"{row.database:<32} {row.current or 'base':<8} head {row.head}{marker}")


def _print_checks(checks: list[AuditCheck]) -> bool:
    """Print one line per tenant; return True if every chain is intact."""
    for check in checks:
        report = check.report
        if report.ok:
            print(f"{check.slug:<24} intact  {report.entries} entries, last {report.last_seq}")
        else:
            print(f"{check.slug:<24} BROKEN  {report.problem}")
    return all(check.report.ok for check in checks)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
        fleet = build_fleet(settings)
        if args.command == "migrate":
            _print_status(migrate_fleet(fleet))
        elif args.command == "status":
            _print_status(fleet_status(fleet))
        elif args.command == "tenant" and args.tenant_command == "create":
            tenant = create_tenant(fleet, args.slug, args.name)
            print(f"{tenant.slug}: {tenant.status} at {tenant.schema_version}")
        elif args.command == "tenant" and args.tenant_command == "list":
            for tenant in list_tenants(fleet):
                print(f"{tenant.slug:<24} {tenant.status:<12} {tenant.schema_version or '-'}")
        elif args.command == "tenant" and args.tenant_command == "drop":
            if not args.yes:
                raise TenantError("Pass --yes to delete the tenant database")
            drop_tenant(fleet, args.slug)
            print(f"{args.slug}: removed")
        elif args.command == "user" and args.user_command == "create":
            _create_user(fleet, settings, args)
        elif args.command == "credential":
            _credential(fleet, args)
        elif args.command == "audit" and args.audit_command == "verify":
            intact = _print_checks(check_fleet(fleet, args.slugs))
            return 0 if intact else 1
    except (TenantError, AccessError, CredentialError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
