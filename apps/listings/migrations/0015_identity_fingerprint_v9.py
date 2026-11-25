"""Recompute the identity fingerprint of every publication event of a Listing that still exists,
for the identity set of registry version 9 (Story 2.17: purpose, category, subtype, city,
locality, area_value, area_unit, marla_basis).

The canonicalisation is FROZEN here on purpose: it must not import live code (`publication`,
`registry`) that may change later. It reproduces `publication.identity_fingerprint` as of this
migration: SHA-256 of the sorted-key, compact, ASCII JSON of the identity fields; decimals as
canonical decimal text (no exponent, no trailing zeros, `-0` is `0`); text in NFC, trimmed;
missing values as null. Nothing is consumed or written but the fingerprint column. Idempotent
(a pure function of the stored Listing); the reverse is a no-op. Events of deleted Listings are
left alone (no data to recompute from).
"""

import hashlib
import json
import unicodedata
from decimal import Decimal

from django.db import migrations

IDENTITY = (
    "area_unit",
    "area_value",
    "category",
    "city",
    "locality",
    "marla_basis",
    "purpose",
    "subtype",
)
DECIMALS = {"area_value", "marla_basis"}


def _number(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _canonical(name, value):
    if value is None:
        return None
    if name in DECIMALS:
        return _number(Decimal(value))
    return unicodedata.normalize("NFC", str(value)).strip()


def fingerprint(values: dict) -> str:
    parts = {name: _canonical(name, values.get(name)) for name in IDENTITY}
    text = json.dumps(
        parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(text.encode()).hexdigest()


def recompute(apps, schema_editor):
    Event = apps.get_model("listings", "PublicationEvent")
    Listing = apps.get_model("listings", "PropertyListing")
    Area = apps.get_model("listings", "ListingArea")
    Location = apps.get_model("listings", "ListingLocation")
    ids = set(Event.objects.values_list("listing_id", flat=True))
    if not ids:
        return
    values = {
        row["id"]: dict(row)
        for row in Listing.objects.filter(id__in=ids).values("id", "purpose", "category", "subtype")
    }
    for row in Area.objects.filter(listing_id__in=values).values(
        "listing_id", "area_value", "area_unit", "marla_basis"
    ):
        values[row["listing_id"]].update(row)
    for row in Location.objects.filter(listing_id__in=values).values(
        "listing_id", "city", "locality"
    ):
        values[row["listing_id"]].update(row)
    for listing_id, data in values.items():
        Event.objects.filter(listing_id=listing_id).update(identity_fingerprint=fingerprint(data))


class Migration(migrations.Migration):
    dependencies = [("listings", "0014_lifecycle_statuses")]
    operations = [migrations.RunPython(recompute, migrations.RunPython.noop)]
