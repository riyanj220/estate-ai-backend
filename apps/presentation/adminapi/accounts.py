"""Administrator account lookup, suspension and reactivation (Story 2.21).

Rules live in `identity.services.suspension`; the lookup is a reasoned private read.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import selectors, services
from apps.identity.services.suspension import REASON_MAX, REASON_MIN
from apps.identity.state import AccountState
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import exact_fields, field_errors, single_field
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin
from apps.presentation.api.private_reads import ReasonedReadMixin, ReasonedReadSerializer

_TYPES = ["individual", "agency"]
_STATES = [s.value for s in AccountState]


class AccountLookupSerializer(ReasonedReadSerializer):
    email = serializers.CharField(max_length=254, help_text="Matched ignoring case.")


class AccountReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(
        min_length=REASON_MIN,
        max_length=REASON_MAX,
        help_text="One line, without contact details.",
    )


class AccountStatusSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    account_type = serializers.ChoiceField(choices=_TYPES)
    state = serializers.ChoiceField(choices=_STATES)
    suspension_reason = serializers.CharField(allow_blank=True, help_text="Empty when none.")
    suspended_at = serializers.DateTimeField(allow_null=True)


class AdminAccountSerializer(AccountStatusSerializer):
    email = serializers.CharField(help_text="Private.")
    display_name = serializers.CharField(
        allow_blank=True, help_text="Full name or agency name; empty when there is no profile."
    )
    email_verified = serializers.BooleanField()


class _AdminAccountView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Needs `X-CSRFToken`. Visitors get `401 unauthenticated`; signed-in non-administrators get "
    "`404 not_found`. Never cached."
)


class AccountLookupView(ReasonedReadMixin, _AdminAccountView):
    @extend_schema(
        operation_id="lookup_admin_account",
        summary="Look up an account by email, with a reason",
        description=(
            "A reasoned private read: `reason` (10 to 300 characters, one line, no contact "
            "details) is recorded in the audit trail. Only Individual and Agency accounts; an "
            "unknown or administrator address is `404 not_found`. " + _REFUSALS
        ),
        request=AccountLookupSerializer,
        responses={200: OpenApiResponse(AdminAccountSerializer, description="The account")},
    )
    def post(self, request):
        serializer = AccountLookupSerializer(data=exact_fields(request.data, ("email", "reason")))
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "\x00" in data["email"]:
            raise field_errors({"email": "invalid"})
        actor = get_actor(request)
        account_id = selectors.account_id_for_admin_lookup(actor, data["email"])
        view = selectors.admin_read_account(actor, data["reason"], account_id)
        return Response(
            AdminAccountSerializer(
                {
                    "id": view.id,
                    "email": view.email,
                    "account_type": view.account_type,
                    "display_name": view.display_name,
                    "state": view.state.value,
                    "email_verified": view.email_verified,
                    "suspension_reason": view.suspension_reason,
                    "suspended_at": view.suspended_at,
                }
            ).data
        )


def _change(request, account_id, service) -> Response:
    reason = single_field(request.data, "reason")
    try:
        view = service(get_actor(request), account_id, reason)
    except services.FieldsInvalid as exc:
        raise field_errors(exc.fields) from None
    return Response(
        AccountStatusSerializer(
            {
                "id": view.id,
                "account_type": view.type,
                "state": view.state.value,
                "suspension_reason": view.suspension_reason,
                "suspended_at": view.suspended_at,
            }
        ).data
    )


_CHANGE_REFUSALS = (
    "`reason` is 5 to 500 characters after trimming, one line, without contact details, else "
    "`400 validation_error`. An unknown id or an administrator account is `404 not_found`. "
)


class AccountSuspendView(_AdminAccountView):
    @extend_schema(
        operation_id="suspend_admin_account",
        summary="Suspend an account",
        description=(
            _CHANGE_REFUSALS + "A Suspended or Deactivated account is `409 conflict`. The "
            "account sees the reason; its Listings leave public view. " + _REFUSALS
        ),
        request=AccountReasonSerializer,
        responses={200: OpenApiResponse(AccountStatusSerializer, description="Suspended")},
    )
    def post(self, request, account_id):
        return _change(request, account_id, services.suspend_account)


class AccountReactivateView(_AdminAccountView):
    @extend_schema(
        operation_id="reactivate_admin_account",
        summary="Reactivate a suspended account",
        description=(
            _CHANGE_REFUSALS + "An account that is not Suspended is `409 conflict`. The state "
            "becomes `active`, or `email_unverified` if the email was never verified. " + _REFUSALS
        ),
        request=AccountReasonSerializer,
        responses={200: OpenApiResponse(AccountStatusSerializer, description="Reactivated")},
    )
    def post(self, request, account_id):
        return _change(request, account_id, services.reactivate_account)
