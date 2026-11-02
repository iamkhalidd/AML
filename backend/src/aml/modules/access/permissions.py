"""What each role may do (ACC-3), from the personas table in the PRD.

Code asks for a permission, never for a role, so the matrix below is the one place that
decides access. Role changes take effect on the next request (ADM-1).
"""

from enum import StrEnum


class Permission(StrEnum):
    USERS_MANAGE = "users.manage"
    SETTINGS_MANAGE = "settings.manage"
    ALERTS_VIEW = "alerts.view"
    ALERTS_WORK = "alerts.work"
    CASES_VIEW = "cases.view"
    CASES_WORK = "cases.work"
    CASES_REQUEST_CLOSURE = "cases.request_closure"
    CASES_APPROVE_CLOSURE = "cases.approve_closure"
    RULES_VIEW = "rules.view"
    RULES_PROPOSE = "rules.propose"
    RULES_APPROVE = "rules.approve"
    REPORTS_VIEW = "reports.view"
    REPORTS_DRAFT = "reports.draft"
    REPORTS_APPROVE = "reports.approve"
    REPORTS_SUBMIT = "reports.submit"
    APPROVALS_VIEW = "approvals.view"
    AUDIT_VIEW = "audit.view"


P = Permission

_VIEW_EVERYTHING = frozenset(
    {P.ALERTS_VIEW, P.CASES_VIEW, P.RULES_VIEW, P.REPORTS_VIEW, P.APPROVALS_VIEW, P.AUDIT_VIEW}
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    # MLRO: approves rule changes and reports, submits reports, manages users, sees everything.
    "compliance_officer": _VIEW_EVERYTHING
    | {
        P.USERS_MANAGE,
        P.ALERTS_WORK,
        P.CASES_WORK,
        P.CASES_REQUEST_CLOSURE,
        P.CASES_APPROVE_CLOSURE,
        P.RULES_PROPOSE,
        P.RULES_APPROVE,
        P.REPORTS_DRAFT,
        P.REPORTS_APPROVE,
        P.REPORTS_SUBMIT,
    },
    # Works alerts and cases and drafts reports; every decision goes to a checker.
    "analyst": frozenset(
        {
            P.ALERTS_VIEW,
            P.ALERTS_WORK,
            P.CASES_VIEW,
            P.CASES_WORK,
            P.CASES_REQUEST_CLOSURE,
            P.RULES_VIEW,
            P.RULES_PROPOSE,
            P.REPORTS_VIEW,
            P.REPORTS_DRAFT,
            P.APPROVALS_VIEW,
        }
    ),
    # Manages users, roles, approvers and settings; sees no customer data.
    "administrator": frozenset({P.USERS_MANAGE, P.SETTINGS_MANAGE}),
    # Reads everything, changes nothing.
    "auditor": _VIEW_EVERYTHING,
}


def allowed(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
