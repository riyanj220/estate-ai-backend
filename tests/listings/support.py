"""Helpers shared by the listing service and API tests."""

import uuid

from django.utils import timezone

from apps.identity import services
from apps.identity.models import Account
from apps.kernel.actor import Actor
from apps.kernel.models import RateLimitPolicy

PASSWORD = "correct horse battery staple"
SYSTEM = Actor.system("r")
HOUSE = ("sale", "house", "house")


def seed_cap(limit=20):
    """The open-Draft cap policy (a transactional test may have flushed the seed rows)."""
    RateLimitPolicy.objects.update_or_create(
        scope="open_drafts",
        defaults={"limit": limit, "window_seconds": None, "key_kind": "account", "note": "t"},
    )


def make_account(email="jane.doe@example.com", account_type="individual", state="active"):
    """An account in the given state: unverified, active, suspended or deactivated."""
    view = services.create_account(SYSTEM, email, PASSWORD, account_type)
    now = timezone.now()
    fields = {
        "unverified": {},
        "active": {"email_verified_at": now},
        "suspended": {"email_verified_at": now, "suspended_at": now, "suspension_reason": "r"},
        "deactivated": {"email_verified_at": now, "deactivated_at": now},
    }[state]
    Account.objects.filter(id=view.id).update(**fields)
    return view


def user(view) -> Actor:
    return Actor.user(view.id, "req")


def random_email() -> str:
    return f"{uuid.uuid4().hex[:10]}@example.com"


APARTMENT = ("sale", "apartment", "apartment")

# The residential details (Story 2.4), in registry order; an independent list, not read from the
# registry, so a field dropped or reordered in the registry fails a test.
BOTH_FIELDS = (
    "bedrooms",
    "bathrooms",
    "construction_year",
    "condition",
    "furnishing",
    "parking_spaces",
    "occupancy_status",
    "covered_area_sqft",
)
HOUSE_FIELDS = (
    "total_floors",
    "kitchens",
    "construction_status",
    "drawing_rooms",
    "dining_rooms",
    "tv_lounges",
    "study_rooms",
    "prayer_rooms",
    "store_rooms",
    "laundry_rooms",
    "powder_rooms",
    "servant_quarters",
    "basement",
)
APARTMENT_FIELDS = (
    "floor_number",
    "building_floors",
    "lift",
    "backup_electricity",
    "building_security",
    "balcony_or_terrace",
    "building_lobby",
    "intercom",
    "maintenance_charges",
)
RESIDENTIAL_IDS = (*BOTH_FIELDS, *HOUSE_FIELDS, *APARTMENT_FIELDS)

# Story 2.5. The utilities live in the unit-details table with the residential fields; plot and
# commercial details have their own tables. Independent lists, in registry order.
UTILITY_FIELDS = ("electricity", "water", "gas")
PLOT_FIELDS = (
    "plot_width",
    "plot_depth",
    "possession_status",
    "corner_plot",
    "road_width_ft",
    "balloting_status",
    "sewerage",
    "development_status",
    "approval_authority",
    "park_facing",
    "main_boulevard",
    "boundary_wall",
    "orientation",
    "plot_file_status",
)
COMMERCIAL_FIELDS = (
    "business_use",
    "frontage_ft",
    "road_access",
    "ceiling_height_ft",
    "power_load_kw",
    "three_phase_electricity",
    "conference_rooms",
    "business_centre_facilities",
)
UNIT_IDS = (*RESIDENTIAL_IDS, *UTILITY_FIELDS)  # the columns of the unit-details table
DETAIL_IDS = (*UNIT_IDS, *PLOT_FIELDS, *COMMERCIAL_FIELDS)  # every detail field, registry order

RESIDENTIAL_PLOT = ("sale", "plot", "residential_plot")
COMMERCIAL_PLOT = ("sale", "plot", "commercial_plot")
PLOT_FILE = ("sale", "plot", "plot_file")
SHOP = ("rent", "commercial", "shop")
OFFICE = ("rent", "commercial", "office")

# Story 2.6: the sale and rent terms, in registry order, written out independently of the registry.
BOTH_TERMS = ("price_negotiable",)
SALE_TERMS = ("possession_availability",)
RENT_TERMS = (
    "security_deposit",
    "availability_date",
    "advance_rent",
    "minimum_lease_months",
    "notice_period_days",
    "utility_electricity_included",
    "utility_gas_included",
    "utility_water_included",
    "utility_internet_included",
    "pets_allowed",
    "occupant_restrictions",
)
TERMS_IDS = (*BOTH_TERMS, *SALE_TERMS, *RENT_TERMS)  # the new terms columns, registry order
# Story 2.6 appends the terms after every details field, so the registry, the storage map and the
# wire all end with this run (the derived `area_sqft` stays last on the wire).
TAIL_IDS = (*DETAIL_IDS, *TERMS_IDS)

SALE_HOUSE = HOUSE  # a sale Draft
RENT_HOUSE = ("rent", "house", "house")


def seed_media_policies(individual=None, agency=None):
    """The image caps and upload limit (a transactional test may have flushed the seed rows)."""
    mb = 1024 * 1024
    for scope, limit in (
        ("storage_cap_bytes_individual", individual or 200 * mb),
        ("storage_cap_bytes_agency", agency or 1024 * mb),
    ):
        RateLimitPolicy.objects.update_or_create(
            scope=scope,
            defaults={"limit": limit, "window_seconds": None, "key_kind": "account", "note": "t"},
        )
    RateLimitPolicy.objects.update_or_create(
        scope="image_upload",
        defaults={"limit": 30, "window_seconds": 3600, "key_kind": "account", "note": "t"},
    )
    RateLimitPolicy.objects.update_or_create(
        scope="profile_image_upload",
        defaults={"limit": 10, "window_seconds": 3600, "key_kind": "account", "note": "t"},
    )
    RateLimitPolicy.objects.update_or_create(
        scope="image_order",
        defaults={"limit": 120, "window_seconds": 3600, "key_kind": "account", "note": "t"},
    )


def set_account_state(account_id, state):
    """Move an existing account to a state (`unverified`, `active`, `suspended`, `deactivated`)."""
    now = timezone.now()
    Account.objects.filter(id=account_id).update(
        email_verified_at=None if state == "unverified" else now,
        suspended_at=now if state == "suspended" else None,
        suspension_reason="r" if state == "suspended" else "",
        deactivated_at=now if state == "deactivated" else None,
    )


def set_listing_visibility(listing_id, status="published", restricted=False):
    """Put a Listing in a status and restricted state directly through the model. Nothing in the
    product sets `published` or `restricted` yet (Story 2.15 and moderation do), so fixtures do."""
    from apps.listings.models import PropertyListing

    PropertyListing.objects.filter(id=listing_id).update(status=status, restricted=restricted)
