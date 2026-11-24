# Reference data

Reference values such as cities are rows owned by the `catalog` app, never code enums or OpenAPI enums. A new
city needs no deploy and no regenerated client; the frontend reads the list at runtime.

## Cities

- Table `catalog_city`: UUID `id`, `name`, `is_active`, `created_at`. Names are unique ignoring case
  (`Lower("name")` constraint) and are 2 to 80 characters (the only rule the database enforces). The service is the
  only guard for the rest: letters (with combining marks), single spaces, hyphens and apostrophes, starting with a
  letter and not ending with a space, hyphen or apostrophe. Names are stored NFC-normalised. An inactive city still
  reserves its name.
- Seeded with Karachi by data migration `0002_seed_karachi`. The seed is idempotent: an existing row in any casing
  is left untouched, so re-running it never duplicates or overwrites an edited city.
- Writes go only through `catalog.services` (`create_city(actor, name)`, administrators only, audited as
  `city.created` in the same transaction). The data migration is the only other writer.
- Reads go through `catalog.selectors.list_cities()`, which returns frozen `CityRef(id, name)` values for active
  cities ordered by name. Other apps never import `catalog.models` (import-linter enforces it).
- Public endpoint: `GET /api/v1/reference/cities` returns `{"items":[{"id":"<uuid>","name":"Karachi"}]}`.
  Unpaginated (small, bounded list) and uncached. In `openapi.yaml`, `name` is a plain `string`.
- Consumers store the city `id` (a plain UUID reference, never a foreign key across apps) and validate it
  through `catalog.selectors.get_city(city_id)` (None for unknown, inactive or malformed ids).

## Localities

- Table `catalog_locality`: UUID `id`, `city` (a foreign key to `catalog_city` inside the app; other apps hold the
  locality id as a plain UUID), `name`, `kind` (`area` or `society`, a CHECK), `is_active`, `created_at`. Names are
  unique per city ignoring case (`(city, Lower("name"))` constraint) and 2 to 80 characters. The service rule is the
  city rule plus decimal digits (`DHA Phase 6`, `Askari 4`); parentheses are never allowed. An inactive locality
  still reserves its name.
- Seeded with the Karachi areas and societies approved by the owner by data migration
  `0004_seed_karachi_localities` (the schema is `0003_locality`). The seed is idempotent: a locality of any casing that
  the Karachi city already has is left untouched, so re-running never duplicates or overwrites an edited row. If there
  is no Karachi city it does nothing. Cities other than Karachi have no localities. The list is editable later by a
  migration or an admin story.
- Writes go only through `catalog.services.create_locality(actor, city_id, name, kind)` (administrators only, audited
  as `locality.created` in the same transaction; an unknown or inactive city, a bad name or kind and a duplicate are
  `validation_error`) and the data migration.
- Reads: `catalog.selectors.list_localities(city_id)` returns frozen `LocalityRef(id, name, kind, city_id)` values for
  the active localities of an active city ordered by name ignoring case (None for an unknown, inactive or malformed
  city, at most `LOCALITY_LIMIT` = 500 rows; no city has more); `get_locality(locality_id)` returns one active locality of an active city with its city id, or None.
- Public endpoint: `GET /api/v1/reference/cities/{city_id}/localities` returns
  `{"items":[{"id":"<uuid>","name":"DHA Phase 6","kind":"society"}]}`. Unpaginated, uncached, `404 not_found` for an
  unknown, inactive or malformed city. In `openapi.yaml`, `name` is a plain `string` (never an enum); `kind` is the
  closed vocabulary `area` | `society`.
- Listings store the city and locality as plain UUID columns and validate them through these selectors (a locality
  must belong to the Listing's city; see `docs/listings.md`).

## Amenities (Story 2.8)

- Table `catalog_amenity`: UUID `id`, `code` (stable snake_case, unique, the identifier listings store), `label`,
  `kind` (`on_property`, `community` or `nearby`, a CHECK), `group` (snake_case, the sub-heading inside a kind),
  `sort_order`, `is_active`, `created_at`. A `nearby` entry is always the seller's own claim; there is no distance and no
  verification concept in the catalogue.
- Seeded by data migration `0006_seed_amenities` (the schema is `0005_amenity`) with exactly this vocabulary:
  on_property / utilities `electricity_backup`, `broadband_internet`, `waste_disposal`; on_property / building
  `double_glazed_windows`, `central_air_conditioning`, `central_heating`, `elevator`, `lobby`, `intercom`;
  community / residential_community `community_lawn`, `swimming_pool`, `community_gym`, `daycare`, `childrens_play_area`,
  `bbq_area`; community / community_services `community_mosque`, `medical_facility`, `community_centre`;
  on_property / business `conference_room`, `business_centre`, `atm_in_building`; nearby / nearby `nearby_mosque`,
  `nearby_park`, `nearby_gym`, `nearby_school`, `nearby_hospital` (the five nearby types are the owner's decision OQ-1).
  The seed is idempotent: a row with a known code, edited or deactivated, is left exactly as it is.
- Writes go only through `catalog.services.create_amenity(actor, code, label, kind, group, sort_order)` (administrators
  only, audited as `amenity.created`) and the data migration.
- Reads: `catalog.selectors.list_amenities()` returns frozen `AmenityRef(code, label, kind, group)` values for the
  active entries ordered by kind (`on_property`, `community`, `nearby`), then `sort_order`; `amenity_codes()` returns the
  active codes for validation.
- Public endpoint: `GET /api/v1/reference/amenities` returns
  `{"items":[{"code":"elevator","label":"Elevator","kind":"on_property","group":"building"}]}`. Unpaginated; no `Cache-Control` header is set (like the other reference lists).
  In `openapi.yaml`, `code`, `label` and `group` are plain strings (never enums); `kind` is the closed vocabulary.
- Listings store the code as plain text and validate new entries through `amenity_codes()` (see `docs/listings.md`).

## What comes later

- Managing cities and localities (add, deactivate, rename) from the admin area arrives with platform administration.
  It will call `catalog.services`. Until then cities beyond Karachi can only be added by calling `create_city` in code;
  it has no endpoint. Deactivating a city hides it from the list without deleting rows that reference it.
