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

## What comes later

- Localities (areas within a city) are not part of this story; open question OQ-2 (section 9 of the PRD under `_bmad-output/planning-artifacts/prds/`) covers the Karachi list and a
  later catalog story adds them.
- Managing cities (add, deactivate, rename) from the admin area arrives with platform administration. It will call
  `catalog.services`. Until then cities beyond Karachi can only be added by calling `create_city` in code; it has no endpoint. Deactivating a city hides it from the list without
  deleting rows that reference it.
