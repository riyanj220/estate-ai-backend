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
