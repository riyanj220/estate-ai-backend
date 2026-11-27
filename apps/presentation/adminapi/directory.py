"""Administrator account directory and review (Story 10.4).

The directory and the review hold no phone, address or full email: those are the reasoned POST
`.../private`, one account at a time, so there is no bulk export of private data. The account's
Listings come from the Listing review list with its `owner` filter.
"""

from dataclasses import asdict

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from apps.governance import closure, reports
from apps.identity import selectors as identity
from apps.identity.state import AccountState
from apps.presentation.adminapi.closure import AdminClosureRequestSerializer
from apps.presentation.adminapi.params import REFUSALS, AdminView, choice_param
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.listings import _link
from apps.presentation.api.private_reads import ReasonedReadMixin, ReasonedReadSerializer
from apps.presentation.api.reports import REASONS, STATUSES, TARGETS

_TYPES = list(identity.OWNER_TYPES)
_STATES = [s.value for s in AccountState]


class DirectoryRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    account_type = serializers.ChoiceField(choices=_TYPES)
    display_name = serializers.CharField(allow_blank=True)
    masked_email = serializers.CharField()
    state = serializers.ChoiceField(choices=_STATES)
    created_at = serializers.DateTimeField()


class DirectoryPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = DirectoryRowSerializer(many=True)


class AccountProfileSerializer(DirectoryRowSerializer):
    email_verified = serializers.BooleanField()
    suspension_reason = serializers.CharField(allow_blank=True)
    suspended_at = serializers.DateTimeField(allow_null=True)
    deactivated_at = serializers.DateTimeField(allow_null=True)
    description = serializers.CharField(allow_blank=True, help_text="Agencies only.")
    city = serializers.CharField(allow_null=True, help_text="Agencies only.")
    verification_status = serializers.CharField(allow_null=True, help_text="Agencies only.")


class AccountReportSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    target_type = serializers.ChoiceField(choices=TARGETS)
    listing_id = serializers.UUIDField()
    title = serializers.CharField(allow_null=True)
    reason = serializers.ChoiceField(choices=REASONS)
    status = serializers.ChoiceField(choices=STATUSES)
    created_at = serializers.DateTimeField()


class AccountReviewSerializer(serializers.Serializer):
    account = AccountProfileSerializer()
    reports = AccountReportSerializer(many=True, help_text="About this account; newest 50.")
    closure_requests = AdminClosureRequestSerializer(many=True)


class AccountContactSerializer(serializers.Serializer):
    email = serializers.CharField(help_text="Private.")
    phone = serializers.CharField(allow_blank=True, help_text="Private.")
    office_address = serializers.CharField(allow_blank=True, help_text="Private; agencies.")


class AccountDirectoryView(AdminView):
    @extend_schema(
        operation_id="list_admin_accounts",
        summary="Individual and Agency accounts, newest first",
        description=(
            "20 per page; `next` is a link, `previous` is always null. `q` is part of a name or "
            "an account id; email is not searched (use the reasoned lookup). " + REFUSALS
        ),
        parameters=[
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("type", str, required=False, enum=_TYPES),
            OpenApiParameter("state", str, required=False, enum=_STATES),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(DirectoryPageSerializer, description="One page")},
    )
    def get(self, request):
        query = request.query_params.get("q") or ""
        if len(query) > 100 or "\x00" in query:
            raise field_errors({"q": "invalid"})
        try:
            page = identity.admin_directory(
                get_actor(request),
                query=query,
                account_type=choice_param(request, "type", _TYPES),
                state=choice_param(request, "state", _STATES),
                cursor=request.query_params.get("cursor") or None,
            )
        except identity.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            DirectoryPageSerializer(
                {
                    "next": _link(request, page.next_cursor),
                    "previous": None,
                    "results": page.items,
                }
            ).data
        )


class AccountReviewView(AdminView):
    @extend_schema(
        operation_id="get_admin_account",
        summary="One account with its Reports and closure requests",
        description="An unknown id or an administrator is `404 not_found`. " + REFUSALS,
        responses={200: OpenApiResponse(AccountReviewSerializer, description="The account")},
    )
    def get(self, request, account_id):
        actor = get_actor(request)
        account = identity.admin_account_profile(actor, account_id)
        return Response(
            AccountReviewSerializer(
                {
                    "account": account,
                    "reports": reports.reports_about(actor, account.id),
                    "closure_requests": closure.requests_of(actor, account.id),
                }
            ).data
        )


class AccountContactReadView(ReasonedReadMixin, AdminView):
    @extend_schema(
        operation_id="read_admin_account_contact",
        summary="Read one account's contact details, with a reason",
        description=(
            "Needs `X-CSRFToken`. A reasoned private read recorded in the audit trail. A "
            "Listing's Private address is read on the Listing. " + REFUSALS
        ),
        request=ReasonedReadSerializer,
        responses={200: OpenApiResponse(AccountContactSerializer, description="Private")},
    )
    def post(self, request, account_id):
        actor = get_actor(request)
        reason = self.read_reason(request)
        key = identity.account_id_for_contact_read(actor, account_id)
        contact = identity.admin_read_account_contact(actor, reason, key)
        return Response(AccountContactSerializer(asdict(contact)).data)
