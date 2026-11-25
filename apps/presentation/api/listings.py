"""Listing endpoints: start a Draft, read an owned Listing, list own Listings (Story 2.1) and
edit one with a revision check (Story 2.2).

Rules live in `listings`; enums come from the Field registry, never hand-copied.
"""

import logging

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
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
from apps.listings import publication, quota, selectors, services
from apps.listings import status as lifecycle_status
from apps.listings.fields import EDITABLE_FIELDS, WIRE_FIELDS
from apps.listings.lifecycle import ListingStatus, Origin, Visibility
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import (
    ConflictWithCurrent,
    ConflictWithDetails,
    exact_fields,
    field_errors,
)
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn
from apps.presentation.api.serializers import ErrorBodySerializer

logger = logging.getLogger("estate.listings")

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
        sign = ", a leading minus sign" if definition.minimum < 0 else ""
        return (
            f"Decimal string with up to {definition.scale} decimals (`{low}` to `{high}`), "
            f"digits and a point only{sign}, never a JSON number. Returned with "
            f"{definition.scale} decimals.{note}"
        )
    if definition.type == "integer":
        low, high = int(definition.minimum), int(definition.maximum)
        return f"Whole number from {low} to {high}, a JSON integer (never text). Null clears it."
    if definition.type == "boolean":
        return "`true` or `false`; null when not answered (never defaulted to either)."
    if definition.type == "reference":
        return (
            f"The uuid of an active {definition.reference} from the reference lists "
            "(`/api/v1/reference/cities`), as text; never a name. Null clears it."
        )
    if definition.type == "amenities":
        return (
            "An object `{code: state}` with catalogue codes (`/api/v1/reference/amenities`) and "
            'the state `"present"` or `"none"`, at most 40 entries. It REPLACES the whole map; '
            "`null` or `{}` clears it. A code left out is unanswered, never `none`. `nearby` "
            "entries are the seller's own declaration: show them as seller-declared; there is no "
            "distance and no verification. Returned as the answered codes only, or `null` when "
            "none is answered."
        )
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
    if definition.type == "amenities":  # codes and states are plain strings, never enums
        return serializers.DictField(child=serializers.CharField(), **options)
    if definition.type == "reference":
        return serializers.UUIDField(**options)  # documented as `format: uuid`; the service judges
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


CONTENT_TYPES = ["image/jpeg", "image/png", "image/webp"]


class ImageSerializer(serializers.Serializer):
    """An attached image (AD-24). `url` is the relative path of the media endpoint; the asset id,
    the storage key and the owner never appear."""

    id = serializers.UUIDField()
    position = serializers.IntegerField(
        min_value=0, help_text="0-based and contiguous: 0 is the first image."
    )
    is_cover = serializers.BooleanField(help_text="Exactly one image of a Listing is the cover.")
    content_type = serializers.ChoiceField(choices=CONTENT_TYPES)
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    bytes = serializers.IntegerField(help_text="Size of the stored (re-encoded) file.")
    url = serializers.CharField(
        help_text=(
            "Relative path of the media endpoint, `/api/v1/listings/{id}/images/{image_id}/file`."
        )
    )


class ListingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=_choices(s.value for s in ListingStatus))
    # Labelled (value, NAME) pairs on purpose: `private` and `public` are also the registry's field
    # classes, and drf-spectacular would merge two enums with the same (value, label) pairs into
    # one component. The labels keep `VisibilityEnum` separate from `FieldClassEnum`.
    visibility = serializers.ChoiceField(choices=[(v.value, v.name) for v in Visibility])
    revision = serializers.IntegerField(min_value=1)
    registry_version = serializers.IntegerField(min_value=1)
    fields = ListingFieldsSerializer()
    provenance = serializers.DictField(
        child=ProvenanceEntrySerializer(),
        help_text="Registry field id -> where the value came from; set fields only.",
    )
    media = ImageSerializer(
        many=True, help_text="The images in `position` order; empty when there are none."
    )
    restricted = serializers.BooleanField(
        help_text=(
            "Set by moderation: a restricted Listing is never public and the owner cannot edit "
            "it (it may still be archived). Owner resource only, never in a public shape."
        )
    )
    restriction_reason = serializers.CharField(
        allow_null=True,
        max_length=200,
        help_text=(
            "A short plain-text reason for a restricted or removed Listing, shown to the owner "
            "only; null otherwise."
        ),
    )


# The registry fields of class `public` other than those shown at the top of the projection: the
# same rule `selectors.public_fields` applies, derived from the registry (never hand-listed).
PUBLIC_FIELD_IDS = tuple(
    name
    for name in WIRE_FIELDS
    if name not in selectors.PROJECTED_APART
    and (found := registry.field(name)) is not None
    and found.field_class == "public"
)
PublicFieldsSerializer = _field_serializer(
    "PublicFieldsSerializer",
    PUBLIC_FIELD_IDS,
    False,
    "The registry fields of class `public`, flat by registry id. Null means unknown.",
)


class PublicImageSerializer(serializers.Serializer):
    """An image as the public sees it: no asset id, storage key, owner or stored size."""

    id = serializers.UUIDField()
    position = serializers.IntegerField(min_value=0)
    is_cover = serializers.BooleanField()
    content_type = serializers.ChoiceField(choices=CONTENT_TYPES)
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    url = serializers.CharField(
        help_text=(
            "Relative path of the media endpoint, `/api/v1/listings/{id}/images/{image_id}/file`."
        )
    )


class PublicListingSerializer(serializers.Serializer):
    """The public projection (`selectors.PublicListingView`), field by field. It has no owner,
    status, revision, provenance, private-class field or phone."""

    id = serializers.UUIDField()
    purpose = serializers.ChoiceField(choices=PURPOSES)
    category = serializers.ChoiceField(choices=CATEGORIES)
    subtype = serializers.ChoiceField(choices=SUBTYPES)
    title = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_null=True)
    fields = PublicFieldsSerializer()
    media = PublicImageSerializer(many=True, help_text="The images in `position` order.")
    registry_version = serializers.IntegerField(min_value=1)


class ListingPreviewSerializer(PublicListingSerializer):
    """What the public will see of the owner's own Listing, plus an `estimate` that is always
    null until the valuation feature exists."""

    estimate = serializers.DictField(
        allow_null=True,
        read_only=True,
        help_text="Always null for now; reserved for the valuation estimate.",
    )


def public_image_data(view: selectors.PublicImageView) -> dict:
    return PublicImageSerializer(
        {
            "id": view.id,
            "position": view.position,
            "is_cover": view.is_cover,
            "content_type": view.content_type,
            "width": view.width,
            "height": view.height,
            "url": view.url,
        }
    ).data


def preview_data(view: selectors.PublicListingView) -> dict:
    return ListingPreviewSerializer(
        {
            "id": view.id,
            "purpose": view.purpose,
            "category": view.category,
            "subtype": view.subtype,
            "title": view.title,
            "description": view.description,
            "fields": dict(view.fields),
            "media": [public_image_data(item) for item in view.media],
            "registry_version": view.registry_version,
            "estimate": None,
        }
    ).data


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


class _OffsetDateTimeField(serializers.DateTimeField):
    """An ISO 8601 string in the value's own offset (DRF would convert it to UTC)."""

    def to_representation(self, value):
        return value.isoformat()


class PublishBodySerializer(serializers.Serializer):
    base_revision = serializers.IntegerField(
        min_value=1, help_text="The `revision` of the Draft the client last read."
    )

    def to_internal_value(self, data):
        # Only `base_revision` is accepted; its type is judged by the service with the header.
        return {"base_revision": exact_fields(data, ("base_revision",)).get("base_revision")}


class QuotaDetailsSerializer(serializers.Serializer):
    limit = serializers.IntegerField(help_text="The caller's monthly limit.")
    used = serializers.IntegerField(help_text="The caller's counted publications this month.")
    resets_at = _OffsetDateTimeField(help_text="Start of the next Karachi month, +05:00.")


class PublishConflictSerializer(serializers.Serializer):
    """`409`: the error envelope plus, for `stale_revision`, the Listing as it is now under
    `current` and, for `quota_exhausted`, the caller's own numbers under `details`."""

    error = ErrorBodySerializer()
    current = ListingSerializer(required=False, help_text="With `stale_revision` only.")
    details = QuotaDetailsSerializer(required=False, help_text="With `quota_exhausted` only.")


class ListingPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ListingSerializer(many=True)


def image_data(view: selectors.ImageView) -> dict:
    return ImageSerializer(
        {
            "id": view.id,
            "position": view.position,
            "is_cover": view.is_cover,
            "content_type": view.content_type,
            "width": view.width,
            "height": view.height,
            "bytes": view.bytes,
            "url": view.url,
        }
    ).data


def listing_data(view: selectors.ListingView) -> dict:
    return ListingSerializer(
        {
            "id": view.id,
            "status": view.status,
            "visibility": view.visibility,
            "revision": view.revision,
            "registry_version": view.registry_version,
            "fields": dict(view.fields),
            "provenance": {name: dict(entry) for name, entry in view.provenance.items()},
            "media": [image_data(item) for item in view.media],
            "restricted": view.restricted,
            "restriction_reason": view.restriction_reason,
        }
    ).data


def _note_open(actor, listing_id) -> None:
    """Record the open, but never let a failing bookkeeping write fail the read it follows (the
    class is logged, never the id or the message)."""
    try:
        services.record_open(actor, listing_id)
    except Exception as exc:
        logger.error(
            "listing_open_not_recorded",
            extra={"event": "listing_open_not_recorded", "exception_class": type(exc).__name__},
        )


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
        return Response(listing_data(view), status=status.HTTP_201_CREATED)


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
        actor = get_actor(request)
        view = selectors.get_owned_listing(actor, listing_id)
        _note_open(actor, listing_id)  # opening a Draft counts as activity (at most hourly)
        return Response(listing_data(view))

    @extend_schema(
        operation_id="delete_listing",
        summary="Delete one of the signed-in account's own Drafts",
        description=(
            "Needs `X-CSRFToken`. Deletes the Draft and everything of it: its images (the stored "
            "files go and the bytes are given back to the account's storage), its fields, private "
            "address and revision history. This cannot be undone. `204` has no body. Only a "
            "`draft` can be deleted here: any other status is `409 conflict` and nothing changes. "
            "Another account's, an administrator's, an unknown, a malformed and an already deleted "
            "id are all `404 not_found`; an account that may not edit (Suspended, Deactivated) is "
            "`403 forbidden`. Never cached."
        ),
        responses={
            204: OpenApiResponse(description="Deleted"),
            401: OpenApiResponse(ErrorBodySerializer, description="`unauthenticated`: a Visitor"),
            403: OpenApiResponse(
                ErrorBodySerializer, description="`forbidden`: Suspended, or a missing CSRF token"
            ),
            404: OpenApiResponse(ErrorBodySerializer, description="`not_found`"),
            409: OpenApiResponse(
                ErrorBodySerializer, description="`conflict`: the Listing is not a Draft"
            ),
        },
    )
    def delete(self, request, listing_id):
        services.delete_draft(get_actor(request), listing_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

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
            "is never assumed) and the location: `city` and `locality` as uuid text of an active "
            "city and one of its localities (`/api/v1/reference/cities` and "
            "`/api/v1/reference/cities/{city_id}/localities`; a locality of another city is "
            "`locality_not_in_city`, a locality without a city `invalid`; changing or clearing "
            "`city` clears the stored `locality` in the same write), the public location text "
            "`society`, `phase`, `sector` and `block` (up to 60 characters, no phone numbers or "
            "email addresses) and the PRIVATE address `street` (up to 120 characters), "
            "`house_number` (up to 30) and `latitude` and `longitude` (decimal strings with at "
            "most six decimals, -90 to 90 and -180 to 180, both or neither; `out_of_range` "
            "otherwise): the private fields are returned only to the owner and never appear in "
            'any public response. `amenities` is the map `{code: "present" | "none"}` of '
            "catalogue codes (`/api/v1/reference/amenities`): the sent map replaces the whole "
            "stored one, `null` or `{}` clears it, a code that is left out is unanswered (never "
            "`none`), an unknown or inactive code, another state, a non-object or more than 40 "
            "entries is `invalid`; nearby facilities are seller-declared (no distance, no "
            "verification). Installment, payment type, down payment and title or approval "
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
            "`invalid_choice`, `not_applicable`, `invalid_measure`, `out_of_range`, "
            "`locality_not_in_city`, "
            "`contains_contact_details`, `not_editable` (also an identity field of a `published` "
            "Listing: `city`, `locality`, `area_value`, `area_unit` and `marla_basis` are locked "
            "while it is published, editable as a Draft or Archived); unknown ids are "
            "`unknown_field` under `non_field_errors`) and never echoes a value. Another "
            "account's, an unknown or a malformed id is `404 not_found`; an account that may "
            "not edit (Suspended, Deactivated) is `403 forbidden`, and so is a `sold`, `rented` or "
            "`removed` Listing or a restricted one (view only). A `draft`, `published` and "
            "`archived` Listing may be edited. An edit of a `published` Listing must leave it "
            "complete (the same rules as publication): otherwise `400 validation_error` names "
            "every missing or invalid item at once (clearing the title or a price, a locality "
            "outside the city) and nothing is written; an edit of an `archived` one is judged "
            "like a Draft edit. These edits use no publication and write no event. Never cached."
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
            raise ConflictWithCurrent(listing_data(exc.current)) from None
        return Response(listing_data(view))


class ListingPreviewView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="preview_listing",
        summary="Preview what the public will see of one of the signed-in account's own Listings",
        description=(
            "Owner only, for the account's own Listing in any status (draft, published, archived, "
            "sold, rented, removed). The body is the "
            "public projection (the same fields a visitor would get, built by the same function): "
            "never a private address, coordinates, a phone number, the revision, the provenance or "
            "the status. `media` lists the images in the public shape; their `url` is the media "
            "endpoint, which serves a Draft's image to its owner only. `estimate` is always "
            "`null` until the valuation feature exists. Another account's, an administrator's, an "
            "unknown and a malformed id are all `404 not_found`. A Visitor gets `401`. Opening the "
            "preview counts as opening the Draft (it postpones the idle-Draft notice). Never "
            "cached."
        ),
        responses={200: OpenApiResponse(ListingPreviewSerializer, description="The preview")},
    )
    def get(self, request, listing_id):
        actor = get_actor(request)
        view = selectors.preview_of(actor, listing_id)
        _note_open(actor, listing_id)
        return Response(preview_data(view))


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
            "the default). `status` filters by one of the six statuses. A bad `cursor` or `status` "
            "is "
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
                "results": [listing_data(v) for v in page.items],
            }
        )


class ListingCountsSerializer(serializers.Serializer):
    draft = serializers.IntegerField(min_value=0)
    published = serializers.IntegerField(min_value=0)
    archived = serializers.IntegerField(min_value=0)
    sold = serializers.IntegerField(min_value=0)
    rented = serializers.IntegerField(min_value=0)
    removed = serializers.IntegerField(min_value=0)


class MyListingCountsView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_my_listing_counts",
        summary="How many Listings the signed-in account has in each status",
        description=(
            "The caller's own Listings only, one count per status (every status is present, 0 "
            "when none): `draft`, `published`, `archived`, `sold`, `rented`, `removed`. Same "
            "rules as the list: a Visitor gets `401`, an administrator or a Deactivated account "
            "`403 forbidden`, a Suspended owner may read it. Never cached."
        ),
        responses={
            200: OpenApiResponse(ListingCountsSerializer, description="The counts"),
            401: OpenApiResponse(ErrorBodySerializer, description="`unauthenticated`"),
            403: OpenApiResponse(ErrorBodySerializer, description="`forbidden`"),
        },
    )
    def get(self, request):
        return Response(ListingCountsSerializer(selectors.owner_counts(get_actor(request))).data)


class StatusBodySerializer(serializers.Serializer):
    base_revision = serializers.IntegerField(
        min_value=1, help_text="The `revision` of the Listing the client last read."
    )
    status = serializers.ChoiceField(
        choices=_choices(lifecycle_status.TARGETS),
        help_text="The status to move to: `archived`, `sold` or `rented`.",
    )

    def to_internal_value(self, data):
        # Only the two keys are accepted; types and values are judged by the service.
        values = exact_fields(data, ("base_revision", "status"))
        return {name: values.get(name) for name in ("base_revision", "status")}


class ListingStatusView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="change_listing_status",
        summary="Archive, mark sold or mark rented one of the signed-in account's own Listings",
        description=(
            "Needs `X-CSRFToken`. Body: `{base_revision, status}` and nothing else; `status` is "
            "`archived`, `sold` or `rented` (anything else is `400 validation_error`, "
            "`fields.status`). Allowed moves: `published` to `archived`, `sold` or `rented`; "
            "`sold` and `rented` to `archived`. Every other move (from a `draft`, an `archived` "
            "or the same status) is `409 conflict` and nothing is written; a Listing never "
            "returns to `published` here. `sold` is only for a Listing with purpose `sale` and "
            "`rented` only for purpose `rent` (`409 conflict` otherwise). No publication is used "
            "and no key is needed. "
            "`200` is the updated Listing; it is no longer public. A Suspended owner may only "
            "archive (`403 forbidden` for `sold` and `rented`); a restricted Listing may only be "
            "archived by its owner (`403 forbidden` otherwise) and a `removed` one cannot be "
            "changed (`403 forbidden`). A `base_revision` that is not the current `revision` is "
            "`409 stale_revision` with the Listing under `current`. Another account's, an "
            "administrator's, an unknown and a malformed id are `404 not_found`; a Visitor gets "
            "`401`. Never cached."
        ),
        request=StatusBodySerializer,
        responses={
            200: OpenApiResponse(ListingSerializer, description="The updated Listing"),
            400: OpenApiResponse(ErrorBodySerializer, description="`validation_error`"),
            401: OpenApiResponse(ErrorBodySerializer, description="`unauthenticated`"),
            403: OpenApiResponse(
                ErrorBodySerializer,
                description="`forbidden`: Suspended, restricted, removed, or a missing CSRF token",
            ),
            404: OpenApiResponse(ErrorBodySerializer, description="`not_found`"),
            409: OpenApiResponse(
                ListingConflictSerializer,
                description="`stale_revision` (with `current`) or `conflict`",
            ),
        },
    )
    def post(self, request, listing_id):
        serializer = StatusBodySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data
        try:
            view = lifecycle_status.change_status(
                get_actor(request), listing_id, body["base_revision"], body["status"]
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        except services.StaleRevision as exc:
            raise ConflictWithCurrent(listing_data(exc.current)) from None
        return Response(listing_data(view))


class PublicationAllowanceSerializer(serializers.Serializer):
    limit = serializers.IntegerField(help_text="The monthly limit in force now.")
    used = serializers.IntegerField(help_text="Counted publications this Karachi month.")
    remaining = serializers.IntegerField(help_text="`max(limit - used, 0)`.")
    period_start = _OffsetDateTimeField(help_text="Start of the month, +05:00.")
    resets_at = _OffsetDateTimeField(help_text="Start of the next month, +05:00.")
    timezone = serializers.ChoiceField(choices=[quota.TIMEZONE_NAME])


def _now():
    return timezone.now()


class PublicationAllowanceView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_my_publication_allowance",
        summary="The signed-in owner's publication allowance this month",
        description=(
            "Individual and Agency accounts (Active, Email-unverified or Suspended). The month "
            "is the calendar month in Asia/Karachi; `period_start` and `resets_at` carry the "
            "+05:00 offset. `used` counts only this account's counted publications and can "
            "exceed `limit` after a later limit decrease, `remaining` never goes below 0. "
            "A Visitor and a Deactivated account (which has no session) get `unauthenticated` "
            "(401); an administrator gets `forbidden` (403). Never cached."
        ),
        responses={
            200: OpenApiResponse(PublicationAllowanceSerializer, description="The allowance"),
            401: OpenApiResponse(ErrorBodySerializer, description="`unauthenticated`"),
            403: OpenApiResponse(ErrorBodySerializer, description="`forbidden`: administrator"),
        },
    )
    def get(self, request):
        view = quota.owner_allowance(get_actor(request), _now())
        return Response(
            PublicationAllowanceSerializer(
                {
                    "limit": view.limit,
                    "used": view.used,
                    "remaining": view.remaining,
                    "period_start": view.period_start,
                    "resets_at": view.resets_at,
                    "timezone": quota.TIMEZONE_NAME,
                }
            ).data
        )


class ListingPublishView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="publish_listing",
        summary="Publish one of the signed-in account's own Drafts",
        description=(
            "Needs `X-CSRFToken` and an `Idempotency-Key` header (1 to 128 characters from "
            "`A-Za-z0-9._:-`; a missing or malformed one is `400 validation_error` under "
            "`fields.idempotency_key`). Body: `{base_revision}` and nothing else. Publishing is "
            "immediate (no approval) and all-or-nothing: it uses one of the owner's monthly "
            "publications and makes the Listing public if its owner is Active. The same key with "
            "the same body within 24 hours returns the Listing as it is now with no second "
            "publication; the same key with another body is `422 idempotency_key_reused`. "
            "Refusals, in order: another account's, an administrator's, an unknown and a "
            "malformed id are `404 not_found`; a Visitor is `401`; a `base_revision` that is not "
            "the current `revision` is `409 stale_revision` with the Listing under `current`; a "
            "Listing that is not a `draft` is `409 conflict`; an Email-unverified account is "
            "`403 email_not_verified` and a Suspended one `403 publishing_unavailable`; every "
            "missing or invalid required item is `400 validation_error`, all at once, keyed by "
            "registry field id (`required` and the field's own codes), `images` "
            "(`images_required`: at least one image) and a field of class `private` or of the "
            "quota identity that the Assistant filled in and the owner has not confirmed "
            "(`confirm_required`); a used-up allowance is `409 quota_exhausted` with the "
            "caller's own `limit`, `used` and `resets_at` under `details`. A refused attempt "
            "consumes nothing. Never cached."
        ),
        parameters=[
            OpenApiParameter(
                "Idempotency-Key", str, location=OpenApiParameter.HEADER, required=True
            )
        ],
        request=PublishBodySerializer,
        responses={
            200: OpenApiResponse(ListingSerializer, description="The published Listing"),
            409: OpenApiResponse(
                PublishConflictSerializer,
                description="`stale_revision`, `conflict` or `quota_exhausted`",
            ),
            422: OpenApiResponse(description="`idempotency_key_reused`"),
        },
    )
    def post(self, request, listing_id):
        serializer = PublishBodySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            view = publication.publish(
                get_actor(request),
                listing_id,
                serializer.validated_data["base_revision"],
                request.headers.get("Idempotency-Key"),
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        except services.StaleRevision as exc:
            raise ConflictWithCurrent(listing_data(exc.current)) from None
        except publication.QuotaExhausted as exc:
            raise ConflictWithDetails(
                exc.code,
                {"limit": exc.limit, "used": exc.used, "resets_at": exc.resets_at.isoformat()},
            ) from None
        return Response(listing_data(view))
