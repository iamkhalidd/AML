"""Each user's email notification choices (ADM-3). Writes need the CSRF header."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from aml.modules.access.api import CurrentUser, TenantDb, now, problems, require_csrf
from aml.modules.notifications import service
from aml.modules.notifications.service import NotificationError
from aml.problems import ProblemError

router = APIRouter(prefix="/v1", tags=["notifications"])


class Preference(BaseModel):
    kind: str
    description: str
    enabled: bool


class PreferencesIn(BaseModel):
    preferences: dict[str, bool] = Field(description="Kind to on or off; others are unchanged")


def _out(chosen: dict[str, bool]) -> list[Preference]:
    return [
        Preference(kind=kind, description=service.KINDS[kind], enabled=enabled)
        for kind, enabled in chosen.items()
    ]


@router.get("/me/notifications", responses=problems)
def my_notifications(conn: TenantDb, user: CurrentUser) -> list[Preference]:
    """Which emails you get; every kind is on until you turn it off."""
    return _out(service.preferences(conn, user.id))


@router.put("/me/notifications", dependencies=[Depends(require_csrf)], responses=problems)
def set_my_notifications(
    body: PreferencesIn, conn: TenantDb, user: CurrentUser
) -> list[Preference]:
    """Turn kinds of email on or off for yourself."""
    try:
        return _out(service.set_preferences(conn, user.id, body.preferences, now()))
    except NotificationError as exc:
        raise ProblemError(400, exc.code, exc.detail) from None
