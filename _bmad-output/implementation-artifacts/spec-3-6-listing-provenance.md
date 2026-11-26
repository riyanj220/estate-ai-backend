---
title: 'Story 3.6: Listing provenance on cards and detail'
type: 'feature'
created: '2026-11-27'
status: 'in-review'
---

## Intent

**Problem:** A buyer could not tell who advertises a Listing (FR-39).

**Approach:** the public card and detail carry `advertiser: {type, agency_name, logo_url, verified}`, batch-loaded by
`identity.selectors.public_advertisers`; the frontend shows it as text on cards and detail. Both repositories.

## Decisions

- The key is `advertiser`, not `owner`: no public answer names the owner account. `agency_name` and `logo_url` are null
  for an Individual, whose personal name is not shown. The logo URL (public Agency profile id) is the only id.
- `verified` is read from `verification_status == "verified"`, a value the database constraint does not yet allow, so it
  is always false until Epic 8. No migration.
- The advertiser belongs to discovery (`PublicEntry`), not to `PublicListingView`: the owner's preview is unchanged.
- "Seller-declared" needed no new code: the registry already flags possession, balloting, development, approval and
  Plot File status, and the facts list labels them. Cards show none of these. The verified mark sits only beside the
  Agency name.
- The logo is decorative (`alt=""`) because the Agency name is the adjacent text.
- `ENUM_NAME_OVERRIDES` gains `TypeEnum` so the new `type` field does not rename the registry's enum.
