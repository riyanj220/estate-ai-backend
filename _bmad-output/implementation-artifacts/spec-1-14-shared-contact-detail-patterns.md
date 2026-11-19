---
title: 'Story 1.14: Shared contact-detail patterns'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e0ad17a41177899ef9fbf5181b8a26526f12978c'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-10-audit-trail-and-reasoned-private-reads.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Phone numbers, emails and private address details must be kept out of public text and AI requests, but today digit-run and `@` checks are scattered across the audit writer, the identity service, the mail port and the reasoned-read recorder, and the later Listing text check and AI redaction would each invent their own.

**Approach:** Create `kernel.privacy.patterns` as the single definition of phone, email and private-address patterns, with `find_contact_details(text)` (kinds and positions, for later redaction) and `reject_contact_details(text)` (an explanation naming what was found, never echoing it), a false-positive test set that must pass untouched, migrate the existing scattered checks onto it, and add a repository test proving no other contact-data regex exists.

## Boundaries & Constraints

**Always:** the module lives at `apps/kernel/privacy/patterns.py` and every contact-data regex in the repository is defined there; matching first normalises text (Unicode NFKC, Arabic-Indic and other Unicode digits mapped to ASCII digits, zero-width characters removed, case folded for words); phone detection covers digit runs of ten or more digits and groups of seven or more digits joined by spaces, dashes, dots or parentheses (landline and mobile shapes such as `0300-1234567`, `0300 1234567`, `03001234567`, `+92 300 1234567`, `+923001234567`, `0092 300 1234567`, `(021) 3456 7890`, `021-3456789`), digits separated one by one (`0 3 0 0 1 2 3 4 5 6 7`), and runs of seven or more English digit words (`zero three zero zero one two three four five six seven`, `oh` and `o` allowed as zero); contiguous digit runs of seven to nine characters with no separator are not phones by themselves (they look like prices and ids), but a run of seven or more digits must still be refused by the audit metadata guard through a separate documented constant that also lives in `patterns.py`; email detection covers ordinary addresses, `name at domain dot com` and bracketed `[at]` and `(dot)` obfuscations; private-address detection covers house, flat, apartment and unit numbers (`house 12`, `h.no 12-B`, `flat no. 4`), street, lane and gali numbers or names with numbers (`street 5`, `st. 14`, `gali 3`), and coordinate pairs (`24.8607, 67.0011`), while society, phase, sector and block (`DHA Phase 6`, `Block 13-D`, `Sector 11-A`) and plot numbers stay allowed because they are Public location per the PRD; `find_contact_details` returns an immutable sequence of findings (kind of phone, email or address, start and end offsets in the original text) and `reject_contact_details(text)` returns `None` for clean text or an English explanation that names only the kinds found (for example "The text appears to contain a phone number.") and never contains any part of the matched text; the false-positive test set must pass untouched: prices (`Rs. 1,25,00,000`, `PKR 12,500,000`, `25 lakh`, `1.5 crore`, `Rs 12500000`), areas (`120 sq yd`, `1,800 sq ft`, `500 square yards`, `10 marla`, `20x40`), counts (`3 bedrooms`, `2 kitchens`, `4 floors`), years and dates (`built in 2019`, `12/03/2024`), phases and blocks, ordinals (`5th floor`), distances (`2.5 km`), `24/7 security`, percentages and short ids (`ID 1234`), together with a catch set of positive cases for every pattern; the existing scattered checks move onto the module: `audit.DIGIT_RUN` becomes an alias of the constant in `patterns.py`, the single-address shape regexes in `kernel.mail` and `identity.services` use one `EMAIL_ADDRESS_SHAPE` defined in `patterns.py`, and the suspension reason (identity) and the private-read reason (kernel.privacy.reads) call `reject_contact_details` instead of their own `@` and digit checks, keeping their length rules, so Story 1.10's interim rule is replaced; a repository test scans every Python module under `apps/` and `config/` except `patterns.py` for regular expressions that look like contact-data detection (a pattern containing `@`, or a digit quantifier of three or more such as `\d{3,}` or `[0-9]{7}`, or the words `phone`, `mobile`, `email` in a pattern) and fails with the file and line, proven by a failing fixture; the existing exact-hash, token and name patterns elsewhere stay as they are; matching is linear-time (no nested quantifiers) and a long-input test shows a 100 000 character text is processed in well under a second; no matched text, offsets with text, or input is logged anywhere; import-linter contracts, convention tests, `openapi.yaml` and the authorisation matrix keep passing; comments only where intent is not obvious.

**Never:** build Listing text validation, Agency profile checks or AI redaction (their stories call this module), add endpoints or models, log or store matched text, treat society, phase, sector, block or plot numbers as private, add a third-party PII or phone-number library, or loosen an existing check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Phone shapes | each shape in the catch set (mobile, landline, `+92`, `0092`, parentheses, spaced digits, digit words, Unicode digits) | detected as `phone` | N/A |
| Email shapes | plain, `name at domain dot com`, `[at]` and `(dot)`, upper case | detected as `email` | N/A |
| Address shapes | house, flat, apartment, unit, street, lane, gali, coordinate pairs | detected as `address` | N/A |
| Allowed location | `DHA Phase 6`, `Block 13-D`, `Sector 11-A`, `Plot 45` | not detected | N/A |
| False positives | the whole false-positive set (prices, areas, counts, dates, distances, ids) | `reject_contact_details` returns `None` | N/A |
| Obfuscation | zero-width characters or full-width digits inside a number | normalised, then detected | N/A |
| Mixed text | a listing sentence with a price and a phone | only the phone is reported | N/A |
| Explanation | any hit | text names the kinds only, never the matched value | N/A |
| Findings | `find_contact_details` on a hit | kind and start and end offsets within the original text | N/A |
| Empty or None | empty string | `None` / no findings | non-string raises TypeError |
| Long input | 100 000 characters | finishes in well under a second | N/A |
| Existing callers | audit metadata, mail recipient and identity email shape, suspension and read reasons | behave as before for their existing tests, with reasons now using `reject_contact_details` | N/A |
| Repository rule | any contact-data regex outside `patterns.py` | repository test fails naming file and line (failing fixture) | N/A |
| Privacy | log output while checking text with a phone | no matched text in any log record | canary test |

</frozen-after-approval>

## Code Map

- `apps/kernel/privacy/patterns.py` -- normalisation, compiled patterns, `ContactKind`, `Finding`, `find_contact_details`, `reject_contact_details`, `EMAIL_ADDRESS_SHAPE`, the long-digit-run guard constant; export the public names from `apps/kernel/privacy/__init__.py` alongside the existing recorder
- `apps/kernel/audit.py`, `apps/kernel/mail.py`, `apps/identity/services.py`, `apps/kernel/privacy/reads.py` -- move onto the module as described
- `tests/privacy/test_patterns.py` -- catch set, false-positive set, obfuscation, normalisation, findings, explanation privacy, long input; `tests/architecture/test_contact_regex_single_source.py` -- repository scan with failing fixtures; update existing audit, identity and privacy tests only where the old checks' messages changed
- `docs/privacy-patterns.md`, `README.md` -- what is detected, what is deliberately allowed, how features and AI redaction call it, how to add a pattern with a test

## Tasks & Acceptance

**Execution:**
- [x] `apps/kernel/privacy/patterns.py` -- patterns, findings, reject helper
- [ ] migrate audit, mail, identity and read-reason checks onto it
- [x] `tests/privacy/test_patterns.py`, repository test with failing fixtures -- cover every matrix row
- [ ] docs and README

**Acceptance Criteria:**
- Given `kernel.privacy.patterns`, when it is used, then it defines phone patterns (digits with separators, "+92" forms and spelled-out digits), email patterns and exact-address patterns, with a false-positive test set of prices, areas and counts that must pass untouched.
- Given a text field, when `reject_contact_details(text)` is applied, then it returns an explanation naming what was found, and a repository test proves no other contact-data regex exists.
- Given later features, when they need contact checks, then Listing text, Agency profile text and AI redaction all call this module (documented as the only entry point).

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Phone detection bypassed by Unicode dashes, `/`, `_`, three separators, newlines, invisible marks, mixed digits and words (BH, EC) | high | patch | `0300 - 1234567` and `0300/1234567` passed as clean text; separator class and normalisation widened, comma kept out because it groups prices. |
| 2 | False positives on ranges, year ranges, dates, datetimes and unit-suffixed lists (BH, EC) | medium | patch | Exemptions added with a larger must-pass set. |
| 3 | Address false positives (`house 10 marla`, `flat 3 bedrooms`) and gaps (number-first, `H#12`, villa, building) (BH, EC) | medium | patch | Lookahead and forms added. `H-12` was briefly flagged by my own instruction; removed because sector names are public location. |
| 4 | Suspension and read reasons loosened when their own checks were replaced (BH, EC, VG) | medium | patch | Dotless `x@y`, bare `@handle` and separated digits now caught by the shared module; dropped cases pinned by tests. |
| 5 | Email local part and labels reject underscores and non-ASCII letters (EC) | low | patch | Allowed. |
| 6 | Single-source scan evadable and false-positive on string methods (BH, EC) | medium | patch | Rewritten with failing fixtures. |
| 7 | `_plausible_sender` under-tested; shared email shape tightening unpinned (VG, BH, EC) | low | patch | Refusals pinned. |
| 8 | No input bound; timing test fragile; quadratic backtracking inputs (BH, EC) | low | patch | 100 000 character limit, scaling test. |
| 9 | Invisible characters in test source; offset mapping edge cases untested (BH) | low | patch | Escapes and tests. |
| 10 | Leftover `DIGIT_RUN` alias; docs mark planned callers, known gaps, code-point offsets (BH) | low | patch | Removed and documented. |
| 11 | URLs and messenger links, "WhatsApp me", Roman-Urdu digit words, letter-for-digit substitutions, landmark addresses not detected (BH) | low | reject | Out of scope for this story; documented as known gaps. |
| 12 | Lazy `__getattr__` exports in the privacy package; patterns in `privacy` rather than a separate module (BH) | low | reject | Avoids a circular import; fix adds churn. |
| 13 | `user@localhost` now refused for mail recipients (BH, EC) | low | reject | Shape intentionally requires a dot; pinned by test. |

## Design Notes

Decisions made without asking: unseparated runs of seven to nine digits are not treated as phones because prices and ids look the same, while the audit metadata guard keeps its stricter seven-digit rule as a separate documented constant (metadata has no legitimate long numbers); plot numbers stay allowed and house or street numbers are private, following the PRD's public and private location split; digit words are English only because the product is English-only; Unicode digits and zero-width characters are normalised first so Urdu-script digits and spacing tricks do not evade detection; the explanation never echoes matched text so rejection messages cannot leak the value into logs or error bodies; the two email shape regexes (mail recipient, identity registration) move into `patterns.py` so the repository rule has no exceptions.

## Verification

**Commands:**
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
