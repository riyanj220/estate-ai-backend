# Contact-detail patterns

`apps/kernel/privacy/patterns.py` is the only place where phone, email and private-address patterns are defined (AD-9).
A repository test (`tests/architecture/test_contact_regex_single_source.py`) fails, naming file and line, if any other
module under `apps/` or `config/` defines a contact-looking regex (an `@`, a digit quantifier of three or more, or the
words phone, mobile, email).

## Entry points (the only ones)

```python
from apps.kernel.privacy import find_contact_details, reject_contact_details

reject_contact_details(text)   # None if clean, else "The text appears to contain a phone number."
find_contact_details(text)     # tuple of Finding(kind, start, end), offsets into the original text, for redaction
```

Listing text validation, Agency profile text and AI redaction (`redact()`) are planned callers (their own stories) and
will call these and nothing else; none exists yet. The explanation
names kinds only and never echoes any part of the text; never log the text, matches or offsets alongside the text.
A non-string raises `TypeError`, text longer than 100 000 characters raises `ValueError` (callers enforce their own,
much smaller field limits first), and an empty string is clean.

`Finding` offsets are Python code-point indices into the original text. A JavaScript string indexes UTF-16 code units,
so the frontend must convert them (characters outside the Basic Multilingual Plane count as two there) before using
them. Show the explanation string to users as is: it is English and names kinds only, never the matched value.

## What is detected

Text is normalised first: NFKC, any Unicode decimal digit to ASCII, zero-width and other format characters removed, case
folded.

- Phone: ten or more digits in a row; seven or more digits joined by spaces, dashes, dots or parentheses (`0300-1234567`,
  `+92 300 1234567`, `(021) 3456 7890`, `021-3456789`); digits split one by one; seven or more English digit words
  (`oh` and `o` count as zero), digits and words mixed. Separators are spaces, dashes of any kind, dots, slashes,
  underscores, parentheses and newlines, never the comma. Invisible, combining and blank characters are ignored. Dates
  (also inside longer text), year ranges and ranges or lists followed by a unit (`1200-1800 sq ft`) are not phones, nor
  are spaced prices after `Rs`/`PKR`.
- Email: ordinary addresses (underscores and non-ASCII letters allowed), dotless `jane@gmail`, a bare `@handle`,
  `[at]`/`(at)` with `[dot]`/`(dot)` or `.`, and `name at domain dot com` (a bare "at" only counts when the dots are
  spelled out too). `meet @ 5pm` is clean.
- Private address: house, flat, apartment, apt, unit, bungalow, villa and building numbers (`H#12`, `h no 12`, but not
  `house 10 marla` or `flat 3 bedrooms`); `14 Main Street`; street, st, lane and gali numbers; `14th street`;
  coordinate pairs with three or more decimals.

## Deliberately allowed

Society, phase, sector, block and plot numbers are Public location (`DHA Phase 6`, `Block 13-D`, `Sector 11-A`,
`Plot 45`), and so are Islamabad-style sector names (`H-12`, `F-7 Markaz`, `G-11/3`). Prices, areas, counts, years, dates, ordinals, distances, `24/7` and short ids pass untouched. An unseparated
run of seven to nine digits is not a phone (it looks like a price or an id).

## Known gaps (out of scope for this story)

The policy is to accept some false negatives rather than reject ordinary listing text. Not detected: URLs and messenger
links (`wa.me`, `t.me`), phrasing such as "WhatsApp me", social handles other than a bare `@handle`, Roman-Urdu digit
words, letter-for-digit substitutions (`O3OO`), and landmark-style addresses ("behind the blue mosque"). Exact-value
scrubbing of an actor's known values belongs to `redact()`.

Further known gaps, also affecting the Agency description and name check of Story 1.21: a phone number split by words or
commas, very long separator runs, "info at agency.com", Urdu digit words, and `Plot`/`Block`/`Sector`/`Phase` style
addresses (allowed as public location). Public text can still carry contact details that evade the heuristics;
hardening the shared patterns is a separate story.

## Related constants

- `LONG_DIGIT_RUN` (seven or more digits): the stricter rule for audit metadata, which has no legitimate long numbers;
  `audit.DIGIT_RUN` is an alias. Suspension and private-read reasons also refuse it, on top of `reject_contact_details`.
- `EMAIL_ADDRESS_SHAPE`: one plain address, used for mail recipients and account registration.

## Adding or changing a pattern

Edit `patterns.py` only, keep every quantifier linear (no nested or adjacent unbounded quantifiers), add catch cases and,
if it could touch ordinary listing text, false-positive cases to `tests/privacy/test_patterns.py`, and make sure the
false-positive set and the long-input test still pass.

## Phone validation

`normalise_phone(text)` returns the E.164 form of a valid Pakistani mobile (`03xx`, `+92 3xx`, `0092 3xx`) or international (`+`, 8 to 15 digits) number, else `None` (landlines are refused). It is for validating a phone field, not for finding phones in free text. Registration (`docs/registration.md`, Individual and Agency) is its caller.
