"""The one map from editable registry field ids to where `listings` stores them.

Validation, the edit service, the read views and the API serializers all derive their field
sets from `STORAGE`, so a new editable field is one entry here plus its registry definition
(and a migration for its column). `tests/listings/test_fields.py` fails, naming the field, when
the registry and this map disagree.
"""

from types import MappingProxyType

LISTING = "listing"  # a column of PropertyListing
TERMS = "terms"  # a column of ListingTerms

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
    }
)

EDITABLE_FIELDS = tuple(STORAGE)
LISTING_FIELDS = tuple(name for name, table in STORAGE.items() if table == LISTING)
TERMS_FIELDS = tuple(name for name, table in STORAGE.items() if table == TERMS)
WIRE_FIELDS = (*IDENTITY_FIELDS, *EDITABLE_FIELDS)  # the order of `fields` on the wire
