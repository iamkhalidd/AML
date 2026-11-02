"""Operator command line: aml migrate | status | tenant create/list/drop.

Run from backend/ so .env is found. Every change is logged in the control plane with the
operator's name, taken from AML_OPERATOR or the login name.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from aml.config import Settings
from aml.control.secrets import SecretStore
from aml.control.tenants import (
    DatabaseStatus,
    Fleet,
    TenantError,
    create_tenant,
    drop_tenant,
    fleet_status,
    list_tenants,
    migrate_fleet,
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
    return parser


def _print_status(rows: list[DatabaseStatus]) -> None:
    for row in rows:
        marker = "" if row.current == row.head else "  <- behind"
        print(f"{row.database:<32} {row.current or 'base':<8} head {row.head}{marker}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        fleet = build_fleet(Settings())
        if args.command == "migrate":
            _print_status(migrate_fleet(fleet))
        elif args.command == "status":
            _print_status(fleet_status(fleet))
        elif args.tenant_command == "create":
            tenant = create_tenant(fleet, args.slug, args.name)
            print(f"{tenant.slug}: {tenant.status} at {tenant.schema_version}")
        elif args.tenant_command == "list":
            for tenant in list_tenants(fleet):
                print(f"{tenant.slug:<24} {tenant.status:<12} {tenant.schema_version or '-'}")
        elif args.tenant_command == "drop":
            if not args.yes:
                raise TenantError("Pass --yes to delete the tenant database")
            drop_tenant(fleet, args.slug)
            print(f"{args.slug}: removed")
    except TenantError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
