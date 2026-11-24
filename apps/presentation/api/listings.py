"""Listing endpoints: start a Draft, read an owned Listing, list own Listings (Story 2.1) and
edit one with a revision check (Story 2.2).

Rules live in `listings`; enums come from the Field registry, never hand-copied.
"""

from django.core.exceptions import ImproperlyConfigured
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param
from rest_framework.views import APIView

from apps.catalog import registry
from apps.listings import selectors, services
from apps.listings.fields import EDITABLE_FIELDS, WIRE_FIELDS
from apps.listings.lifecycle import ListingStatus, Origin, Visibility
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import ConflictWithCurrent, exact_fields, field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn
from apps.presentation.api.serializers import ErrorBodySerializer

CREATE_FIELDS = ("purpose", "category", "subtype")
UPDATE_BODY = ("base_revision", "fields")
PAGE_SIZE = selectors.DEFAULT_LIMIT
MAX_PAGE_SIZE = selectors.MAX_LIMIT


def _choices(values):
    return [(v, v) for v in values]


PURPOSES = _choices(registry.purposes())
CATEGORIES = _choices(c.id for c in registry.categories())
SUBTYPES = _choices(registry.all_subtypes())


ORIGINS = _choices(o.value for o in Origin)


def _help(definition) -> str:
    text = _type_help(definition)
    if definition.seller_declared:
        text += " Seller-declared: the owner's own claim, never verified by EstateAI."
    if definition.allowed_values_by_subtype is not None:
        text += " The choices depend on the Subtype (see the registry)."
    return text.strip()


def _type_help(definition) -> str:
    if definition.type == "money":
        return f"Decimal string in {definition.currency} with two decimals, e.g. `1500000.00`."
    if definition.type == "text":
        low = f"{definition.min_length} to " if definition.min_length else "Up to "
        return f"{low}{definition.max_length} characters."
    if definition.type == "decimal":
        note = " Computed by the server; read-only." if definition.derived else ""
        low, high = format(definition.minimum, "f"), format(definition.maximum, "f")
        return (
            f"Decimal string with up to {definition.scale} decimals (`{low}` to `{high}`), "
            f"digits and a point only, never a JSON number. Returned with {definition.scale} "
            f"decimals.{note}"
        )
    if definition.type == "integer":
        low, high = int(definition.minimum), int(definition.maximum)
        return f"Whole number from {low} to {high}, a JSON integer (never text). Null clears it."
    if definition.type == "boolean":
        return "`true` or `false`; null when not answered (never defaulted to either)."
    if definition.type == "date":
        low, high = definition.minimum.isoformat(), definition.maximum.isoformat()
        return (
            f"A calendar date as text `YYYY-MM-DD` (`{low}` to `{high}`), never a timestamp or "
            "a JSON number. Returned the same way."
        )
    return ""


def _serializer_field(definition, update: bool):
    """The documented field for a registry definition (the service judges every value)."""
    options = {"allow_null": True, "help_text": _help(definition)}
    if update:
        options["required"] = False
    if definition.type == "enum":
        return serializers.ChoiceField(choices=_choices(definition.allowed_values), **options)
    if definition.type == "integer":
        return serializers.IntegerField(
            min_value=int(definition.minimum), max_value=int(definition.maximum), **options
        )
    if definition.type == "boolean":
        return serializers.BooleanField(**options)
    if definition.type == "date":
        return serializers.DateField(**options)  # documented as `format: date`; the service judges
    return serializers.CharField(**options)  # text, and money as a decimal string


def _field_serializer(name: str, names, update: bool, doc: str):
    declared = {}
    for field_id in names:
        definition = registry.field(field_id)
        if definition is None:  # a mapped field must be defined (tests/listings/test_fields.py)
            raise ImproperlyConfigured(f"registry field {field_id} is missing")
        declared[field_id] = _serializer_field(definition, update)
    return type(name, (serializers.Serializer,), {**declared, "__doc__": doc})


# Built from the registry and the single field map (`listings.fields`), never hand-listed.
ListingFieldsSerializer = _field_serializer(
    "ListingFieldsSerializer", WIRE_FIELDS, False, "Flat registry ids. Null means unknown."
)
ListingFieldsUpdateSerializer = _field_serializer(
    "ListingFieldsUpdateSerializer",
    EDITABLE_FIELDS,
    True,
    "The editable fields; the service judges every value (null clears a field).",
)


class ProvenanceEntrySerializer(serializers.Serializer):
    origin = serializers.ChoiceField(choices=ORIGINS)
    confirmed = serializers.BooleanField()


class ListingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=_choices(s.value for s in ListingStatus))
    visibility = serializers.ChoiceField(choices=_choices(v.value for v in Visibility))
    revision = serializers.IntegerField(min_value=1)
    registry_version = serializers.IntegerField(min_value=1)
    fields = ListingFieldsSerializer()
    provenance = serializers.DictField(
        child=ProvenanceEntrySerializer(),
        help_text="Registry field id -> where the value came from; set fields only.",
    )
    media = serializers.ListField(
        child=serializers.DictField(), help_text="Empty until Story 2.10."
    )
    amenities = serializers.ListField(
        child=serializers.CharField(), help_text="Empty until Story 2.8."
    )


class DraftCreateSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=PURPOSES, help_text="From the registry.")
    category = serializers.ChoiceField(choices=CATEGORIES, help_text="From the registry.")
    subtype = serializers.ChoiceField(
        choices=SUBTYPES, help_text="A Subtype of the chosen Category."
    )

    def to_internal_value(self, data):
        # Unknown fields are refused here; values and rules are judged by the service, all at once.
        return exact_fields(data, CREATE_FIELDS)


class ListingUpdateSerializer(serializers.Serializer):
    base_revision = serializers.IntegerField(
        min_value=1, help_text="The `revision` of the Listing the client last read."
    )
    fields = ListingFieldsUpdateSerializer()

    # Both keys are always required, also on PATCH: keep the schema generator from publishing
    # a partial "Patched" body with optional keys.
    @property
    def partial(self):
        return False

    @partial.setter
    def partial(self, value):
        pass

    def to_internal_value(self, data):
        # Only the two keys are accepted (`origin` and `confirmed` are never client input);
        # shapes and values are judged by the service, in its order, all failures at once.
        values = exact_fields(data, UPDATE_BODY)
        return {name: values.get(name) for name in UPDATE_BODY}


class ListingConflictSerializer(serializers.Serializer):
    """`409 stale_revision`: the error envelope plus the Listing as it is now."""

    error = ErrorBodySerializer()
    current = ListingSerializer(help_text="The current Listing, to reload and reapply the edit.")


class ListingPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ListingSerializer(many=True)


def _data(view: selectors.ListingView) -> dict:
    return ListingSerializer(
        {
            "id": view.id,
            "status": view.status,
            "visibility": view.visibility,
            "revision": view.revision,
            "registry_version": view.registry_version,
            "fields": dict(view.fields),
            "provenance": {name: dict(entry) for name, entry in view.provenance.items()},
            "media": list(view.media),
            "amenities": list(view.amenities),
        }
    ).data


class DraftCreateView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="create_listing_draft",
        summary="Start a Draft",
        description=(
            "Needs `X-CSRFToken`. Body: `purpose`, `category` and `subtype`; any other field "
            "(`owner_account_id`, `status`, `revision`) is `validation_error` (`unknown_field`). "
            "Failing fields are reported at once (`required`, `invalid`, `invalid_choice`, "
            "`subtype_not_in_category`). Only Active and Email-unverified Individual and Agency "
            "accounts may start one (`forbidden` otherwise, administrators included). The Draft "
            "belongs to the account. At 20 open Drafts the answer is `429 throttled` without "
            "`Retry-After` (a cap clears when a Draft is removed). Never cached."
        ),
        request=DraftCreateSerializer,
        responses={201: OpenApiResponse(ListingSerializer, description="The new Draft")},
    )
    def post(self, request):
        serializer = DraftCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            view = services.create_draft(
                get_actor(request),
                values.get("purpose"),
                values.get("category"),
                values.get("subtype"),
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(_data(view), status=status.HTTP_201_CREATED)


class ListingDetailView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_listing",
        summary="One of the signed-in account's own Listings",
        description=(
            "Owner only. Another account's, an unknown and a malformed id are all "
            "`404 not_found`, so existence is never confirmed. Never cached."
        ),
        responses={200: OpenApiResponse(ListingSerializer, description="The Listing")},
    )
    def get(self, request, listing_id):
        return Response(_data(selectors.get_owned_listing(get_actor(request), listing_id)))

    @extend_schema(
        operation_id="update_listing",
        summary="Edit one of the signed-in account's own Listings",
        description=(
            "Needs `X-CSRFToken`. Body: `{base_revision, fields}` and nothing else (any other "
            "key, `origin` and `confirmed` included, is `validation_error` `unknown_field`). "
            "`fields` holds registry ids: `title`, `description`, `asking_price` (a decimal "
            "string in PKR, at most two decimals), `price_basis`, `rental_period`, `area_value` "
            "(a decimal string, at most four decimals, above zero; never a JSON number), "
            '`area_unit` and `marla_basis` (`"225"`, `"250"` or `"272.25"` square feet '
            "per marla, only while the unit is marla or kanal; changing the unit away clears "
            "it) and the House and Apartment details (`bedrooms`, `bathrooms`, "
            "`construction_year`, "
            "`condition`, `furnishing`, `parking_spaces`, `occupancy_status`, "
            "`covered_area_sqft` (a decimal string in square feet), the House set `total_floors`, "
            "`kitchens`, "
            "`construction_status`, room counts and `basement`, and the Apartment set "
            "`floor_number`, `building_floors`, `lift`, `backup_electricity`, "
            "`building_security`, `balcony_or_terrace`, `building_lobby`, `intercom` and "
            "`maintenance_charges` (PKR per month)), the details Shops and Offices share with them "
            "(`condition`, `furnishing`, `parking_spaces`, `occupancy_status`, floors, "
            "`lift`, `building_security`, `building_lobby`, `maintenance_charges`), the "
            "utilities `electricity`, `water` and `gas`, the Plot and Plot File details "
            "(`plot_width`, `plot_depth` and `road_width_ft` as decimal strings in feet with "
            "at most two decimals, `possession_status`, `corner_plot`, `balloting_status`, "
            "`sewerage`, `development_status`, `approval_authority`, `park_facing`, "
            "`main_boulevard`, `boundary_wall`, `orientation`, `plot_file_status`) and the "
            "Shop and Office details (`business_use`, `frontage_ft`, `road_access`, "
            "`ceiling_height_ft`, `power_load_kw`, `three_phase_electricity`, "
            "`conference_rooms`, `business_centre_facilities`) and the sale and rent terms "
            "(`price_negotiable` for both; sale only `possession_availability`; rent only "
            "`security_deposit` and `advance_rent` as decimal strings in PKR with at most two "
            "decimals, `availability_date` as text `YYYY-MM-DD` between 2000-01-01 and "
            "2100-12-31, `minimum_lease_months`, `notice_period_days`, the four "
            "`utility_*_included` answers, `pets_allowed` and `occupant_restrictions`; the rent "
            "terms are `not_applicable` on a sale Draft and the other way round; a rental period "
            "is never assumed). Installment, payment type, down payment and title or approval "
            "document terms are not supported yet (`unknown_field`). Possession, balloting, "
            "development, approval and plot file status are seller-declared (registry flag "
            "`seller_declared`): the owner's claim, never verified by EstateAI. A Plot File "
            "allows only `file_only` for `possession_status` and the other Subtypes refuse it "
            "(`invalid_choice`). Ids the registry does not define (for example installment, "
            "down payment, title document or mortgage attributes) are `unknown_field`. "
            "Counts are JSON integers and yes/no "
            "answers are JSON `true`/`false`, never text; unanswered is `null`, never 0 or "
            "false. The registry says which apply to which Subtype (`not_applicable` "
            "otherwise); `null` clears a field. `area_sqft` is computed by the server and is "
            "`not_editable` as input. Saving never needs complete data. Every saved field is "
            "recorded as `manual` and confirmed; the client never chooses provenance. A "
            "`base_revision` that is not the current `revision` is `409 stale_revision` with "
            "the current Listing under `current` and nothing written (a retried PATCH after it "
            "succeeded is also a 409: reload). An edit that changes nothing is `200` and keeps the "
            "revision. `400` carries codes keyed by registry field id (`invalid`, `too_short`, "
            "`too_long`, `invalid_price`, `invalid_area`, `invalid_range`, `invalid_floor`, "
            "`invalid_date`, "
            "`invalid_choice`, `not_applicable`, `invalid_measure`, "
            "`contains_contact_details`, `not_editable`; unknown ids are "
            "`unknown_field` under `non_field_errors`) and never echoes a value. Another "
            "account's, an unknown or a malformed id is `404 not_found`; an account that may "
            "not edit (Suspended, Deactivated) is `403 forbidden`. Never cached."
        ),
        request=ListingUpdateSerializer,
        responses={
            200: OpenApiResponse(ListingSerializer, description="The updated Listing"),
            409: OpenApiResponse(
                ListingConflictSerializer,
                description="Stale revision: the Listing changed; `current` holds it",
            ),
        },
    )
    def patch(self, request, listing_id):
        serializer = ListingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data
        try:
            view = services.apply_updates(
                get_actor(request),
                listing_id,
                body["base_revision"],
                body["fields"],
                origin=Origin.MANUAL,  # fixed here, never read from the request
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        except services.StaleRevision as exc:
            raise ConflictWithCurrent(_data(exc.current)) from None
        return Response(_data(view))


def _page_size(request) -> int:
    raw = request.query_params.get("page_size")
    if raw and len(raw) <= 3 and raw.isascii() and raw.isdigit() and 1 <= int(raw) <= MAX_PAGE_SIZE:
        return int(raw)
    return PAGE_SIZE


def _link(request, cursor: str | None) -> str | None:
    if cursor is None:
        return None
    return replace_query_param(request.build_absolute_uri(), "cursor", cursor)


class MyListingsView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="list_my_listings",
        summary="The signed-in account's own Listings, newest first",
        description=(
            "Own Listings only, newest first, with cursor pagination (`next` link, `previous` is "
            "always null; 20 per page, `page_size` up to 50, an invalid `page_size` falls back to "
            "the default). `status` filters by a known status. A bad `cursor` or `status` is "
            "`validation_error` (`fields.cursor`, `fields.status`). Never cached."
        ),
        parameters=[
            OpenApiParameter("status", str, enum=[s.value for s in ListingStatus], required=False),
            OpenApiParameter("page_size", int, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(ListingPageSerializer, description="One page")},
    )
    def get(self, request):
        state = request.query_params.get("status")
        if state is not None and state not in {s.value for s in ListingStatus}:
            raise field_errors({"status": "invalid_choice"})
        try:
            page = selectors.list_owned(
                get_actor(request),
                state,
                request.query_params.get("cursor") or None,  # an empty cursor is page one
                _page_size(request),
            )
        except selectors.InvalidCursor:  # a stable code, never the value
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            {
                "next": _link(request, page.next_cursor),
                "previous": None,
                "results": [_data(v) for v in page.items],
            }
        )
