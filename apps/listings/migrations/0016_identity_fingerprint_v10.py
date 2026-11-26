"""Recompute the identity fingerprint of every publication event of a Listing that still exists,
for the identity set of registry version 10 (Story 2.19: `marla_basis` leaves the set, which is
now purpose, category, subtype, city, locality, area_value and area_unit).

The canonicalisation is FROZEN here on purpose: it must not import live code (`publication`,
`registry`) that may change later. It reproduces `publication.identity_fingerprint` as of this
migration. The loop and its drift rule are `identity_migration.recompute_fingerprints`: an event
is recomputed only when its stored value is the OLD-set (version 9) fingerprint of the Listing's
current data; any other stored value becomes NULL, so the next republication is counted.
Idempotent; the reverse is a no-op. Only the fingerprint column changes.
"""

import unicodedata
from decimal import Decimal

from django.db import migrations

from apps.listings.identity_migration import fingerprint as _fingerprint
from apps.listings.identity_migration import recompute_fingerprints

OLD_IDENTITY = (  # registry version 9
    "area_unit",
    "area_value",
    "category",
    "city",
    "locality",
    "marla_basis",
    "purpose",
    "subtype",
)
IDENTITY = tuple(name for name in OLD_IDENTITY if name != "marla_basis")  # version 10
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
    # the migrating connection: the runtime role may not update this table
    db = schema_editor.connection.alias if schema_editor else "default"
    recompute_fingerprints(apps, OLD_IDENTITY, IDENTITY, _canonical, using=db)


class Migration(migrations.Migration):
    dependencies = [("listings", "0015_identity_fingerprint_v9")]
    operations = [migrations.RunPython(recompute, migrations.RunPython.noop)]
