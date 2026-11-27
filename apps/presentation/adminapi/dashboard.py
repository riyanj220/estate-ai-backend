"""The administrator dashboard (Story 10.8): counts taken when it loads, nothing stored."""

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from apps.governance import reports, selectors
from apps.identity import selectors as identity
from apps.listings import selectors as listings
from apps.messaging import selectors as messaging
from apps.presentation.adminapi.params import REFUSALS, AdminView
from apps.presentation.api.actors import get_actor

ACTIVITY_DAYS = 7


class ListingsByStatusSerializer(serializers.Serializer):
    draft = serializers.IntegerField()
    published = serializers.IntegerField()
    archived = serializers.IntegerField()
    sold = serializers.IntegerField()
    rented = serializers.IntegerField()
    removed = serializers.IntegerField()


class ActivitySerializer(serializers.Serializer):
    days = serializers.IntegerField()
    new_accounts = serializers.IntegerField()
    new_listings = serializers.IntegerField()
    messages_sent = serializers.IntegerField()


class AdminDashboardSerializer(serializers.Serializer):
    individual_accounts = serializers.IntegerField()
    agency_accounts = serializers.IntegerField()
    published_listings = serializers.IntegerField()
    listings_by_status = ListingsByStatusSerializer()
    pending_verifications = serializers.IntegerField()
    unresolved_reports = serializers.IntegerField()
    activity = ActivitySerializer()


class AdminDashboardView(AdminView):
    @extend_schema(
        operation_id="get_admin_dashboard",
        summary="Platform counts",
        description=(
            f"Accounts in every state; activity covers the last {ACTIVITY_DAYS} days. " + REFUSALS
        ),
        responses={200: OpenApiResponse(AdminDashboardSerializer, description="Counts")},
    )
    def get(self, request):
        actor = get_actor(request)
        since = timezone.now() - timedelta(days=ACTIVITY_DAYS)
        accounts = identity.admin_account_totals(actor, since)
        by_status, new_listings, live = listings.admin_listing_totals(actor, since)
        return Response(
            AdminDashboardSerializer(
                {
                    "individual_accounts": accounts["individual"],
                    "agency_accounts": accounts["agency"],
                    "published_listings": live,
                    "listings_by_status": by_status,
                    "pending_verifications": selectors.pending_verification_count(actor),
                    "unresolved_reports": reports.unresolved_report_count(actor),
                    "activity": {
                        "days": ACTIVITY_DAYS,
                        "new_accounts": accounts["new"],
                        "new_listings": new_listings,
                        "messages_sent": messaging.messages_sent_since(actor, since),
                    },
                }
            ).data
        )
