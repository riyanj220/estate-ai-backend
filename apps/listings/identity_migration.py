"""The reusable core of a fingerprint data migration (Story 2.18; used by migrations 0015 and 0016).

When the set of `quota_identity` registry fields changes, the stored `identity_fingerprint` of
every publication event is out of date. A migration to the pattern calls `recompute_fingerprints`
with the OLD and NEW identity field names and its own FROZEN canonicalisation (`canonical`, copied
into the migration, never imported from live code that may change later). This module only holds
the loop; it imports no live registry or publication code and reads models through the migration's
`apps` registry.

Safety rule (never overwrite baseline evidence with data that drifted): an event's fingerprint is
recomputed only when it equals the OLD-set fingerprint of the Listing's CURRENT stored data, which
proves the data is still what the event recorded. When it does not (for example an Archived Listing
whose identity was edited after the event), the stored fingerprint is set to NULL: a NULL baseline
never matches, so the next republication is COUNTED. That can only cost the owner a publication,
never let them dodge the quota. An event already holding the NEW fingerprint is left alone (a
second run changes nothing). Events of deleted Listings are left alone (no data to compare). No
quota is consumed, no event is written, deleted or moved; only the fingerprint column changes.
"""

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping

Canonical = Callable[[str, object], object]


def fingerprint(identity: Iterable[str], values: Mapping, canonical: Canonical) -> str:
    """SHA-256 of the sorted-key, compact, ASCII JSON of the identity fields (missing is null)."""
    parts = {name: canonical(name, values.get(name)) for name in identity}
    text = json.dumps(
        parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(text.encode()).hexdigest()


def load_listing_values(apps, listing_ids, using: str = "default") -> dict:
    """Purpose, category, subtype, city, locality and the three area columns of each Listing
    (registry version 9). A migration whose identity set needs other columns passes its own
    loader (same signature: apps, ids, database alias; Listing id to a dict of field values)."""
    Listing = apps.get_model("listings", "PropertyListing")
    Area = apps.get_model("listings", "ListingArea")
    Location = apps.get_model("listings", "ListingLocation")
    values = {
        row["id"]: dict(row)
        for row in Listing.objects.using(using)
        .filter(id__in=listing_ids)
        .values("id", "purpose", "category", "subtype")
    }
    for row in (
        Area.objects.using(using)
        .filter(listing_id__in=values)
        .values("listing_id", "area_value", "area_unit", "marla_basis")
    ):
        values[row["listing_id"]].update(row)
    for row in (
        Location.objects.using(using)
        .filter(listing_id__in=values)
        .values("listing_id", "city", "locality")
    ):
        values[row["listing_id"]].update(row)
    return values


def recompute_fingerprints(
    apps,
    old_identity: Iterable[str],
    new_identity: Iterable[str],
    canonical: Canonical,
    load: Callable = load_listing_values,
    using: str = "default",
) -> dict[str, int]:
    """Recompute stored fingerprints for a change of identity set (see the module docstring).
    Returns how many events were `recomputed`, `nulled` (drifted) and `kept` (already new)."""
    old_identity, new_identity = tuple(old_identity), tuple(new_identity)
    Event = apps.get_model("listings", "PublicationEvent")
    counts = {"recomputed": 0, "nulled": 0, "kept": 0}
    events = Event.objects.using(using)  # a migration passes the migrating connection
    ids = set(events.values_list("listing_id", flat=True))
    if not ids:
        return counts
    values = load(apps, ids, using)
    for listing_id, data in values.items():
        old = fingerprint(old_identity, data, canonical)
        new = fingerprint(new_identity, data, canonical)
        for event in events.filter(listing_id=listing_id):
            stored = event.identity_fingerprint
            if stored == new:
                counts["kept"] += 1
            elif stored == old:
                events.filter(pk=event.pk).update(identity_fingerprint=new)
                counts["recomputed"] += 1
            elif stored is not None:
                events.filter(pk=event.pk).update(identity_fingerprint=None)
                counts["nulled"] += 1
    return counts
