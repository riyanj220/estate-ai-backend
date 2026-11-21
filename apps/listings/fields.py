"""The one map from editable registry field ids to where `listings` stores them.

Validation, the edit service, the read views and the API serializers all derive their field
sets from `STORAGE`, so a new editable field is one entry here plus its registry definition
(and a migration for its column). `tests/listings/test_fields.py` fails, naming the field, when
the registry and this map disagree.
"""

from types import MappingProxyType

LISTING = "listing"  # a column of PropertyListing
TERMS = "terms"  # a column of ListingTerms
AREA = "area"  # a column of ListingArea
RESIDENTIAL = "residential"  # a column of ListingResidential (House and Apartment details)

# Chosen when the Draft starts and never edited (Story 2.1); stored on PropertyListing.
IDENTITY_FIELDS = ("purpose", "category", "subtype")

# Editable registry field id -> table. The column carries the field id. Later stories add theirs.
STORAGE = MappingProxyType(
    {
        "title": LISTING,
        "description": LISTING,
        "asking_price": TERMS,
        "price_basis": TERMS,
        "rental_period": TERMS,
        "area_value": AREA,
        "area_unit": AREA,
        "marla_basis": AREA,
        "bedrooms": RESIDENTIAL,
        "bathrooms": RESIDENTIAL,
        "construction_year": RESIDENTIAL,
        "condition": RESIDENTIAL,
        "furnishing": RESIDENTIAL,
        "parking_spaces": RESIDENTIAL,
        "occupancy_status": RESIDENTIAL,
        "covered_area_sqft": RESIDENTIAL,
        "total_floors": RESIDENTIAL,
        "kitchens": RESIDENTIAL,
        "construction_status": RESIDENTIAL,
        "drawing_rooms": RESIDENTIAL,
        "dining_rooms": RESIDENTIAL,
        "tv_lounges": RESIDENTIAL,
        "study_rooms": RESIDENTIAL,
        "prayer_rooms": RESIDENTIAL,
        "store_rooms": RESIDENTIAL,
        "laundry_rooms": RESIDENTIAL,
        "powder_rooms": RESIDENTIAL,
        "servant_quarters": RESIDENTIAL,
        "basement": RESIDENTIAL,
        "floor_number": RESIDENTIAL,
        "building_floors": RESIDENTIAL,
        "lift": RESIDENTIAL,
        "backup_electricity": RESIDENTIAL,
        "building_security": RESIDENTIAL,
        "balcony_or_terrace": RESIDENTIAL,
        "building_lobby": RESIDENTIAL,
        "intercom": RESIDENTIAL,
        "maintenance_charges": RESIDENTIAL,
    }
)

# Registry fields the service computes: on the wire and in revisions, never accepted as input and
# never in `EDITABLE_FIELDS`. Value: the table holding them.
DERIVED = MappingProxyType({"area_sqft": AREA})

EDITABLE_FIELDS = tuple(STORAGE)
LISTING_FIELDS = tuple(name for name, table in STORAGE.items() if table == LISTING)
TERMS_FIELDS = tuple(name for name, table in STORAGE.items() if table == TERMS)
AREA_FIELDS = tuple(name for name, table in STORAGE.items() if table == AREA)
RESIDENTIAL_FIELDS = tuple(name for name, table in STORAGE.items() if table == RESIDENTIAL)
DERIVED_FIELDS = tuple(DERIVED)
WIRE_FIELDS = (
    *IDENTITY_FIELDS,
    *EDITABLE_FIELDS,
    *DERIVED_FIELDS,
)  # the order of `fields` on the wire
