# Search performance (NFR-1, Story 3.4)

Budgets, as server time on about 10,000 published Listings: conventional search p95 under 500 ms, Listing detail p95
under 300 ms.

## How to measure

```
uv run pytest -m perf -s
```

About a minute. The `perf` marker is deselected by default (`addopts` in `pyproject.toml`); CI runs it as its own
serial step. Run it without `-n`. The test fails when a budget is missed or the newest-first list stops using its index,
and prints p50, p95 and max per query shape. Server time is the whole request through the Django test client, without
network or browser; each search shape is requested 20 times and 200 different Listings are read.

## The fixture

`tests/perf/fixture.py` `generate()` bulk-inserts 10,000 published Listings (plus 1,000 drafts and archived ones), 300
owners, details, locations, amenities and publication events in about 10 seconds. Same arguments, same rows. No images;
accounts on `perf.estate-ai.invalid` that cannot sign in; titles start with `[perf]`. It is test code, not a management
command: only `listings.publication` may publish in application code, so the generator cannot live under `apps/`. It is
not the sample marketplace data (`docs/sample-data.md`). Epic 11 extends it.

## Measured (2026-11-27)

4-core laptop, 8 GB, PostgreSQL 18 in Docker, other work running (load average 6 to 9). p95 in ms:

| Query shape | Before | After |
| --- | --- | --- |
| newest, no filter | 325 | 59 |
| newest, 50 per page | 275 | 83 |
| newest, page 21 | **571** | 86 |
| city and purpose | 190 | 63 |
| locality and category | 85 | 59 |
| price range and bedrooms | 111 | 59 |
| area range | 170 | 64 |
| amenities | 229 | 69 |
| price ascending / descending | 245 / 225 | 123 / 129 |
| relevance (preferences) | 408 | 224 |
| everything at once | 142 | 76 |
| no match | 47 | 35 |
| **all searches** | 476 | **196** |
| **detail** | 39 | **21** |

"After" is one run; timings move with load (an earlier run of the same code had relevance at 385 ms and all searches at
257 ms), so compare shapes, not single milliseconds.

## What changed, from `EXPLAIN ANALYZE`

- The first publication time was a correlated subquery on `PublicationEvent`, run for every public row before sorting,
  and three times per row on a later page (select and both cursor comparisons). A composite index
  `(account_id, listing_id, created_at)` made each lookup an index-only scan but left 10,000 to 30,000 of them per
  request, so page 21 stayed over budget. It was not kept.
- It is now stored as `PropertyListing.first_published_at` (migration 0017, backfilled from the events), with the partial
  expression index `listings_public_newest_idx` on published, unrestricted rows. The newest-first list reads only the
  rows of its page: the database part fell from about 180 ms (600 ms on page 21) to about 1 ms.
- The cursor condition now also bounds the first sort key, so a later page starts inside the index.
- No other index was added. Price sorts and relevance still read and sort every match, which is within budget at this
  size; the filter columns are not selective enough to need their own indexes.

## Chat latency under load (Story 7.4, SM-7)

    uv run pytest -m perf -s tests/perf/test_chat_latency.py

Fifty Messages between two online accounts while five clients keep AI or image requests going (three re-encode
12-megapixel photos, two searches by description with the stub provider made to take 2 s). Timed from the sender's
POST until the recipient has had the socket event and read the Message. Budget: p95 under 1 s. It drives the ASGI
application in one process, without the network.

| 4-core laptop, 2026-11-27 | p50 | p95 | max |
| --- | --- | --- | --- |
| event received | 142 ms | 364 ms | 383 ms |
| event received and Message read | 224 ms | 459 ms | 490 ms |

The executor's limits (4 workers, 1 reserved) were not changed and Redis is not needed. With three heavy slots, five
requests at once means some are refused busy and retried, which is the reservation working.
