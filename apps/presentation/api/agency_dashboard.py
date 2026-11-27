"""The Agency dashboard (Story 8.1) and Listing view statistics (Story 8.2). Rules live in
`listings`; inquiry counts come from `messaging`. The view beacon is the one public write: it
adds to a total and records nothing about the caller."""

import re
from datetime import date

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import selectors as catalog
from apps.kernel import ratelimit
from apps.listings import portfolio, selectors, services
from apps.listings.lifecycle import ListingStatus
from apps.messaging import selectors as messaging
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.listings import (
    CATEGORIES,
    PURPOSES,
    SUBTYPES,
    ListingCountsSerializer,
    _link,
    _page_size,
)
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import AllowPublic, RequireSignedIn
from apps.presentation.api.public_listings import PlaceSerializer
from apps.presentation.api.serializers import ErrorBodySerializer

# Crawlers, link previewers, monitors and scripts that name themselves. Not a defence against a
# client that pretends to be a browser; the rate limit bounds that.
_BOT = re.compile(
    r"bot|crawl|spider|slurp|preview|headless|lighthouse|facebookexternalhit|monitor|pingdom"
    r"|curl|wget|python|httpx|node-fetch|undici|go-http|java/",
    re.IGNORECASE,
)
STATUSES = [s.value for s in ListingStatus]
FILTERS = ("status", "category", "city_id", "locality_id", "created_from", "created_to")


class DashboardSerializer(serializers.Serializer):
    total_listings = serializers.IntegerField(min_value=0)
    by_status = ListingCountsSerializer()
    inquiries_total = serializers.IntegerField(
        min_value=0, help_text="Conversations others started about the Agency's Listings."
    )
    views_total = serializers.IntegerField(min_value=0)


class DashboardListingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField(allow_null=True)
    status = serializers.ChoiceField(choices=STATUSES)
    purpose = serializers.ChoiceField(choices=PURPOSES)
    category = serializers.ChoiceField(choices=CATEGORIES)
    subtype = serializers.ChoiceField(choices=SUBTYPES)
    city = PlaceSerializer(allow_null=True)
    locality = PlaceSerializer(allow_null=True)
    created_on = serializers.DateField(help_text="The Asia/Karachi date of creation.")
    views = serializers.IntegerField(min_value=0)
    inquiries = serializers.IntegerField(min_value=0)


class DashboardListingPageSerializer(serializers.Serializer):
    results = DashboardListingSerializer(many=True)
    next = serializers.URLField(allow_null=True, help_text="The next page; null on the last.")


_REFUSALS = {
    401: OpenApiResponse(ErrorBodySerializer, description="`unauthenticated`: a Visitor"),
    403: OpenApiResponse(ErrorBodySerializer, description="`forbidden`: not an Agency account"),
}


class AgencyDashboardView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_agency_dashboard",
        summary="The signed-in Agency's totals",
        description=(
            "Agency accounts only (a Suspended one may read). Listings by status and in total, "
            "inquiries received and detail views. The publication allowance is "
            "`/me/publication-allowance` and the inbox `/conversations`. Never cached."
        ),
        responses={200: OpenApiResponse(DashboardSerializer, "The totals"), **_REFUSALS},
    )
    def get(self, request):
        actor = get_actor(request)
        found = portfolio.summary(actor)
        return Response(
            DashboardSerializer(
                {
                    "total_listings": found.total_listings,
                    "by_status": found.by_status,
                    "inquiries_total": messaging.inquiries_received(actor),
                    "views_total": found.views_total,
                }
            ).data
        )


def _date(raw: str) -> date | None:
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":  # only YYYY-MM-DD
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if 2000 <= parsed.year <= 9000 else None


def _criteria(request) -> portfolio.Criteria:
    """The filters of the query string; an empty value is no filter."""
    given = {name: request.query_params.get(name, "") for name in FILTERS}
    parsers = {
        "status": lambda v: v if v in STATUSES else None,
        "category": lambda v: v if v in {c for c, _ in CATEGORIES} else None,
        "city_id": catalog.parse_uuid,
        "locality_id": catalog.parse_uuid,
        "created_from": _date,
        "created_to": _date,
    }
    values, errors = {}, {}
    for name, raw in given.items():
        values[name] = parsers[name](raw) if raw else None
        if raw and values[name] is None:
            errors[name] = "invalid_choice" if name in ("status", "category") else "invalid"
    start, end = values["created_from"], values["created_to"]
    if start and end and start > end:
        errors["created_to"] = "inverted_range"
    if errors:
        raise field_errors(errors)
    return portfolio.Criteria(
        status=values["status"],
        category=values["category"],
        city=values["city_id"],
        locality=values["locality_id"],
        created_from=start,
        created_to=end,
    )


class AgencyDashboardListingsView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="list_agency_dashboard_listings",
        summary="The signed-in Agency's Listings with views and inquiries",
        description=(
            "The Agency's own Listings only, newest first, 20 per page (`page_size` up to 50). "
            "The filters combine; an empty one is ignored. The dates are Asia/Karachi dates of "
            "creation, both inclusive. A bad value is `validation_error`; so is a `cursor` "
            "issued for other filters. Never cached."
        ),
        parameters=[
            OpenApiParameter("status", str, enum=STATUSES, required=False),
            OpenApiParameter("category", str, enum=[c for c, _ in CATEGORIES], required=False),
            OpenApiParameter("city_id", str, required=False),
            OpenApiParameter("locality_id", str, required=False),
            OpenApiParameter("created_from", date, required=False),
            OpenApiParameter("created_to", date, required=False),
            OpenApiParameter("page_size", int, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={
            200: OpenApiResponse(DashboardListingPageSerializer, "One page"),
            400: OpenApiResponse(ErrorBodySerializer, description="`validation_error`"),
            **_REFUSALS,
        },
    )
    def get(self, request):
        actor = get_actor(request)
        portfolio.require_agency(actor)  # refused before the query string is judged
        try:
            page = portfolio.listings(
                actor,
                _criteria(request),
                request.query_params.get("cursor") or None,
                _page_size(request),
            )
        except selectors.InvalidCursor:  # a stable code, never the value
            raise field_errors({"cursor": "invalid"}) from None
        inquiries = messaging.inquiries_by_listing(actor, [row.id for row in page.items])
        results = [{**vars(row), "inquiries": inquiries[row.id]} for row in page.items]
        return Response(
            DashboardListingPageSerializer(
                {"results": results, "next": _link(request, page.next_cursor)}
            ).data
        )


class ListingViewBeaconView(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]

    @extend_schema(
        operation_id="record_listing_view",
        summary="Count one view of a published Listing",
        description=(
            "Sent by the page after it has shown the Listing. No body. A signed-in caller needs "
            "`X-CSRFToken`. `204` whether or not it counted: the owner, administrators, known "
            "bots, a repeat from the same browser within 30 minutes and an over-eager network "
            "are not counted. Only a total per Listing is kept; no cookie is set. A Listing "
            "that is not publicly visible is `not_found`."
        ),
        request=None,
        responses={
            204: OpenApiResponse(description="Received"),
            403: OpenApiResponse(ErrorBodySerializer, description="`forbidden`: CSRF"),
            404: OpenApiResponse(ErrorBodySerializer, description="`not_found`"),
        },
    )
    def post(self, request, listing_id):
        actor = get_actor(request)
        agent = request.META.get("HTTP_USER_AGENT", "")
        if (
            agent
            and not _BOT.search(agent)
            and ratelimit.hit_client("listing_view", actor, request).allowed
        ):
            browser = f"{ratelimit.client_network(request)}\x00{agent[:400]}"
            services.record_view(actor, listing_id, browser)
        return Response(status=status.HTTP_204_NO_CONTENT)
