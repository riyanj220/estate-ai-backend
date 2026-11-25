"""Recompute the identity fingerprint of every publication event of a Listing that still exists,
for the identity set of registry version 9 (Story 2.17: purpose, category, subtype, city,
locality, area_value, area_unit, marla_basis).

The canonicalisation is FROZEN here on purpose: it must not import live code (`publication`,
`registry`) that may change later. It reproduces `publication.identity_fingerprint` as of this
migration: SHA-256 of the sorted-key, compact, ASCII JSON of the identity fields; decimals as
canonical decimal text (no exponent, no trailing zeros, `-0` is `0`); text in NFC, trimmed;
missing values as null. Nothing is consumed or written but the fingerprint column. The loop is
`identity_migration.recompute_fingerprints` (Story 2.18): an event is recomputed only when its
stored value is the OLD-set (purpose, category, subtype) fingerprint of the Listing's current
data; a stored value that does not match (data drifted since) becomes NULL, so the next
republication is counted. Idempotent; the reverse is a no-op. Events of deleted Listings are left
alone (no data to compare).
"""

import unicodedata
from decimal import Decimal

from django.db import migrations

from apps.listings.identity_migration import fingerprint as _fingerprint
from apps.listings.identity_migration import recompute_fingerprints

OLD_IDENTITY = ("category", "purpose", "subtype")  # registry version 8
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
    return _fingerprint(IDENTITY, values, _canonical)


def recompute(apps, schema_editor):
    recompute_fingerprints(apps, OLD_IDENTITY, IDENTITY, _canonical)


class Migration(migrations.Migration):
    dependencies = [("listings", "0014_lifecycle_statuses")]
    operations = [migrations.RunPython(recompute, migrations.RunPython.noop)]
