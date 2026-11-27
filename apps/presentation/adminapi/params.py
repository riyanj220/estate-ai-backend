"""Query parameters and the base view shared by the Story 10.4 to 10.8 administrator routes."""

from datetime import date, datetime, time, timedelta

from rest_framework.views import APIView

from apps.identity import selectors as identity
from apps.listings.selectors import KARACHI
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin

REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)


class AdminView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


def id_param(request, name: str):
    raw = request.query_params.get(name) or None
    if raw is None:
        return None
    key = identity.parse_id(raw)
    if key is None:
        raise field_errors({name: "invalid"})
    return key


def choice_param(request, name: str, choices) -> str | None:
    value = request.query_params.get(name) or None
    if value is not None and value not in choices:
        raise field_errors({name: "invalid"})
    return value


def day_start(request, name: str, *, days_later: int = 0) -> datetime | None:
    """The start of a Karachi calendar day (`days_later=1` makes an end date inclusive)."""
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        raise field_errors({name: "invalid"}) from None
    if len(raw) != 10 or not 2000 <= value.year <= 2999:
        raise field_errors({name: "invalid"})
    return datetime.combine(value + timedelta(days=days_later), time.min, KARACHI)
