import time

import pytest

from apps.kernel import privacy
from apps.kernel.privacy.patterns import (
    MAX_TEXT_LENGTH,
    ContactKind,
    find_contact_details,
    reject_contact_details,
)

ZW = chr(0x200B)


def uni(codes: str) -> str:
    return "".join(chr(int(code, 16)) for code in codes.split())


PHONES = [
    "0300 - 1234567",
    "0300\u20131234567",
    "0300\u20141234567",
    "0300\u22121234567",
    "0300/1234567",
    "0300_1234567",
    "0300 \n 1234567",
    "0300\n1234567",
    "0300\u20601234567",
    "0300\ufeff1234567",
    "0300\u00ad1234567",
    "0300\u03011234567",
    "0300\u31641234567",
    "0300\u115f1234567",
    "0300\u28001234567",
    "0300 one two three four five six seven",
    "zero/three/zero/zero/one/two/three/four/five",
    "zero_three_zero_zero_one_two_three_four_five",
    "one, two, three, four, five, six, seven",
    "1/2/3/4/5/6/7",
    "0300-1234567",
    "0300 1234567",
    "03001234567",
    "+92 300 1234567",
    "+923001234567",
    "0092 300 1234567",
    "(021) 3456 7890",
    "021-3456789",
    "0 3 0 0 1 2 3 4 5 6 7",
    "zero three zero zero one two three four five six seven",
    "oh three oh oh one two three four five six seven",
    "O3OO".replace("O", "0") + "1234567",
    uni("ff10 ff13 ff10 ff10 ff11 ff12 ff13 ff14 ff15 ff16 ff17"),  # full-width
    f"0300{ZW}1234567",
    ZW.join("03001234567"),
]
EMAILS = [
    "john_doe@x.com",
    uni("6a 6f 73 e9") + "@x.com",
    "jane@gmail",
    "ping @bob",
    "call me at x@y",
    "jane@example.com",
    "Jane.Doe+x@Example.CO.UK",
    "jane at example dot com",
    "jane [at] example [dot] com",
    "jane (at) example (dot) com",
    "JANE AT EXAMPLE DOT COM",
]
ADDRESSES = [
    "H#12",
    "h no 12",
    "14 Main Street",
    "5 Gali 3",
    "bungalow 7",
    "villa 22",
    "building 9",
    "house 12",
    "H.No 12-B",
    "flat no. 4",
    "apartment 7",
    "unit 3",
    "street 5",
    "St. 14",
    "gali 3",
    "lane 4",
    "24.8607, 67.0011",
]
ALLOWED = [
    "H-12",
    "F-7 Markaz",
    "G-11/3",
    "2019-2023",
    "2019 - 2023",
    "2019-23",
    "2024-03-12",
    "12.03.2024",
    "Posted 12-03-2024 10:30",
    "Posted 2024-03-12 10:30 by agent",
    "Open 12-03-2024, closes 2024-05-01",
    "1200-1800 sq ft",
    "Area 1200 1500 sq ft",
    "10 - 12 - 15 marla",
    "Floors 1 2 3 4",
    "Rs 12 500 000",
    "PKR 12 500 000",
    "Rs. 1 25 00 000",
    "house 10 marla",
    "flat 3 bedrooms",
    "unit 4 floors",
    "unit 20 percent",
    "st 5 km",
    "house 120 sq yd",
    "meet @ 5pm",
    "at 5pm",
    "3 bedrooms near the park",
    "DHA Phase 6",
    "Block 13-D",
    "Sector 11-A",
    "Plot 45",
    "Rs. 1,25,00,000",
    "PKR 12,500,000",
    "25 lakh",
    "1.5 crore",
    "Rs 12500000",
    "120 sq yd",
    "1,800 sq ft",
    "500 square yards",
    "10 marla",
    "20x40",
    "3 bedrooms",
    "2 kitchens",
    "4 floors",
    "built in 2019",
    "12/03/2024",
    "12-03-2024",
    "5th floor",
    "2.5 km",
    "24/7 security",
    "ID 1234",
    "20% off",
    "Spacious 10 marla house in DHA Phase 6, Block 13-D, Plot 45, 5th floor, 3 bedrooms",
    "Price Rs 25,000,000 negotiable, 10 marla corner",
    "Asking 25,000,000, 3 bedrooms, 4 bathrooms",
    "Areas 5, 10, 20 marla available, floors 1, 2, 3",
    "Sizes: 120, 240, 500 sq yd; prices 4,500,000, 9,000,000",
    "Built 2015, renovated 2021, 3 bed, 3 bath, 2 kitchen",
    "Floor 03 of 12, 1,450 sq ft, 2 parking",
    "Rs 92,000,000 total for the 1 kanal plot",
    "Rent 150,000 monthly, deposit 300,000, 2 years lease",
    "House 3 beds | 2 baths | 1 lounge | 5 marla | 2019",
    "Plots: 10 marla * 3, 5 marla * 2, 1 kanal * 1, 2 acres",
    "Floors 0, 1, 2, 3, 4, 5, 6 and basement 1",
    "Per marla 4,200,000; total 21,000,000; token 500,000",
    "Phase 6, Block 13, Street 5 km away from 24/7 mall",
]

SPLIT_PHONES = [
    "0300,1234567",
    "0300:1234567",
    "0300 | 123 | 4567",
    "0300\u2022123\u20224567",
    "03-00-12-34-567",
    "+92 300 1234567",
    "+92,300,1234567",
    "0092 300 1234567",
    "92;300;1234567",
    "0300*123*4567",
    "0300~1234567",
    "0300#1234567",
    "0300 (123) 4567",
    "Rs 5,000 0300,1234567",
    "\u0660\u0663\u0660\u0660,\u0661\u0662\u0663\u0664\u0665\u0666\u0667",
    "\u0660\u0663\u0660\u0660 | \u0661\u0662\u0663 | \u0664\u0665\u0666\u0667",
    "\u06f0\u06f3\u06f0\u06f0:\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7",
    "021,3456,7890",
]


def kinds(text):
    return {f.kind for f in find_contact_details(text)}


@pytest.mark.parametrize("text", PHONES)
def test_phone_shapes_are_detected(text):
    assert ContactKind.PHONE in kinds(f"call {text} now")


@pytest.mark.parametrize("text", EMAILS)
def test_email_shapes_are_detected(text):
    assert kinds(f"mail {text} please") == {ContactKind.EMAIL}


@pytest.mark.parametrize("text", ADDRESSES)
def test_address_shapes_are_detected(text):
    assert kinds(f"it is at {text}.") == {ContactKind.ADDRESS}


@pytest.mark.parametrize("text", ALLOWED)
def test_false_positive_set_passes_untouched(text):
    assert reject_contact_details(text) is None
    assert find_contact_details(text) == ()


@pytest.mark.parametrize("text", SPLIT_PHONES)
def test_split_phone_numbers_are_detected(text):
    assert ContactKind.PHONE in kinds(text), text
    assert reject_contact_details(f"Lovely house {text} call") is not None


def test_a_split_phone_span_covers_only_the_number():
    text = "Call 0300,1234567 now"
    (finding,) = [f for f in find_contact_details(text) if f.kind is ContactKind.PHONE]
    assert text[finding.start : finding.end] == "0300,1234567"


def test_unseparated_seven_to_nine_digits_are_not_phones():
    for run in ("1234567", "12345678", "123456789"):
        assert reject_contact_details(f"ref {run}") is None
    assert privacy.LONG_DIGIT_RUN.search("1234567")


def test_mixed_text_reports_only_the_phone():
    text = "Rs. 1,25,00,000, 10 marla, call 0300-1234567"
    (finding,) = find_contact_details(text)
    assert finding.kind is ContactKind.PHONE
    assert text[finding.start : finding.end] == "0300-1234567"


def test_findings_are_ordered_and_can_mix_kinds():
    found = find_contact_details("flat 4, mail a@b.com, 03001234567")
    assert [f.kind.value for f in found] == ["address", "email", "phone"]
    assert [f.start for f in found] == sorted(f.start for f in found)


def test_explanation_names_kinds_only():
    assert reject_contact_details("call 03001234567") == (
        "The text appears to contain a phone number."
    )
    message = reject_contact_details("flat 4, a@b.com, 03001234567")
    assert message == (
        "The text appears to contain a phone number, an email address and a private address."
    )
    for secret in ("03001234567", "a@b.com", "flat 4"):
        assert secret not in message


def test_empty_and_non_strings():
    assert find_contact_details("") == ()
    assert reject_contact_details("") is None
    for bad in (None, 5, b"x"):
        with pytest.raises(TypeError):
            find_contact_details(bad)
        with pytest.raises(TypeError):
            reject_contact_details(bad)


def test_catch_set_literal_checks():
    assert kinds("a@b.co") == {ContactKind.EMAIL}


def test_text_over_the_limit_is_refused():
    find_contact_details("a" * MAX_TEXT_LENGTH)
    with pytest.raises(ValueError):
        find_contact_details("a" * (MAX_TEXT_LENGTH + 1))
    with pytest.raises(ValueError):
        reject_contact_details("a" * (MAX_TEXT_LENGTH + 1))


def _elapsed(text):
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        find_contact_details(text)
        best = min(best, time.perf_counter() - started)
    return best


ADVERSARIAL = [
    lambda n: "a" * n,
    lambda n: "1 " * n,
    lambda n: "1" * n,
    lambda n: "house " * n,
    lambda n: "house" + " " * n,
    lambda n: "a@" * n,
    lambda n: "a@" + "." * n,
    lambda n: "one " * n,
    lambda n: "a-" * n + "@",
    lambda n: "(" * n,
    lambda n: " at " * n,
    lambda n: "1-" * n,
    lambda n: "0 " * n + "x",
    lambda n: "st " * n,
    lambda n: "1 a " * n,
]


@pytest.mark.parametrize("make", ADVERSARIAL)
def test_processing_scales_linearly_and_stays_fast(make):
    small, large = _elapsed(make(20_000)[:20_000]), _elapsed(make(100_000)[:100_000])
    assert large < 2.0  # generous absolute bound
    assert large < max(small, 0.005) * 25  # 5x the input must not cost far more than 5x


def test_email_address_shape_has_no_quadratic_backtracking():
    for text in (
        "a@" + "." * 50_000,
        "a@" + "a" * 50_000,
        "a" * 50_000 + "@b",
        "a@b" + "." * 50_000,
    ):
        started = time.perf_counter()
        privacy.EMAIL_ADDRESS_SHAPE.fullmatch(text)
        assert time.perf_counter() - started < 1


def test_offsets_refer_to_the_original_text():
    text = "x " + uni("ff10 ff13") + ZW + "00 1234567 y"
    (finding,) = find_contact_details(text)
    assert text[finding.start : finding.end] == uni("ff10 ff13") + ZW + "00 1234567"
    with pytest.raises(AttributeError):
        finding.start = 0


def test_offsets_after_invisible_characters_and_casefold_expansion():
    text = "Stra\u00df" + ZW + "e " + ZW + "call 03001234567"
    (finding,) = [f for f in find_contact_details(text) if f.kind is ContactKind.PHONE]
    assert text[finding.start : finding.end] == "03001234567"
    text = "\u00df\u00df house 12"
    (finding,) = find_contact_details(text)
    assert text[finding.start : finding.end] == "house 12"


def test_trailing_format_character_is_not_part_of_the_span():
    text = "call 03001234567" + ZW + " ok"
    (finding,) = find_contact_details(text)
    assert text[finding.start : finding.end] == "03001234567"


def test_spans_can_redact_the_original_without_leaving_digits():
    text = "call 0\u20603\u200b00 - 1234567 or " + uni("ff10 ff13") + "001234567 now"
    redacted = text
    for finding in reversed(find_contact_details(text)):
        redacted = redacted[: finding.start] + "[removed]" + redacted[finding.end :]
    assert redacted == "call [removed] or [removed] now"
    assert not any(ch.isdigit() for ch in redacted)


# --- phone validation (registration) ----------------------------------------------------------

ACCEPTED_PHONES = [
    ("0300-1234567", "+923001234567"),
    ("0300 1234567", "+923001234567"),
    ("03001234567", "+923001234567"),
    ("(0300) 123-4567", "+923001234567"),
    ("0300.1234567", "+923001234567"),
    ("+92 300 1234567", "+923001234567"),
    ("+92-300-1234567", "+923001234567"),
    ("+923001234567", "+923001234567"),
    ("0092 300 1234567", "+923001234567"),
    ("00923001234567", "+923001234567"),
    ("  0345 1234567  ", "+923451234567"),
    ("+44 20 7946 0958", "+442079460958"),
    ("+1 (415) 555-2671", "+14155552671"),
    ("+12345678", "+12345678"),  # eight digits: the shortest accepted
    ("+123456789012345", "+123456789012345"),  # fifteen digits: the longest accepted
]

REFUSED_PHONES = [
    "",
    "   ",
    "021-3456789",  # landline
    "0213456789",
    "+92 21 34567890",  # Pakistani landline in international form
    "0092 21 3456789",
    "0300123456",  # too short
    "030012345678",  # too long
    "0400-1234567",
    "923001234567",  # no plus and no leading zero
    "+1234567",  # seven digits
    "+",
    "+0300123456789",  # country code cannot start with zero
    "+1234567890123456",  # sixteen digits
    "0300-ABC4567",
    "call me on 0300 1234567",
    "0300 1234567 ext 5",
    "+92 300 12345+67",
    "03001234567\u0000",
    "x" * 500,
    "0" * 500,
]


@pytest.mark.parametrize(("raw", "want"), ACCEPTED_PHONES)
def test_normalise_phone_accepts_and_returns_e164(raw, want):
    assert privacy.normalise_phone(raw) == want


@pytest.mark.parametrize("raw", REFUSED_PHONES)
def test_normalise_phone_refuses(raw):
    assert privacy.normalise_phone(raw) is None


def test_normalise_phone_maps_unicode_digits_and_drops_invisible_characters():
    arabic_indic = "".join(chr(0x0660 + int(d)) for d in "03001234567")
    assert privacy.normalise_phone(arabic_indic) == "+923001234567"
    fullwidth = "".join(chr(0xFF10 + int(d)) for d in "03001234567")
    assert privacy.normalise_phone(fullwidth) == "+923001234567"


@pytest.mark.parametrize(
    "raw",
    ["0300" + ZW + "1234567", "0300\u03011234567", "0300\u20601234567", "0300\u31641234567",
     "0300\u28001234567", "+92\u00ad3001234567", "\u200e0300 1234567"],
)  # fmt: skip
def test_normalise_phone_refuses_invisible_and_combining_characters(raw):
    assert privacy.normalise_phone(raw) is None


def test_normalise_phone_treats_00_as_plus():
    assert privacy.normalise_phone("0044 20 7946 0958") == "+442079460958"
    assert privacy.normalise_phone("001 415 555 2671") == "+14155552671"
    assert privacy.normalise_phone("00") is None
    assert privacy.normalise_phone("00 1234567") is None  # seven digits
    assert privacy.normalise_phone("0092 21 3456789") is None


def test_normalise_phone_rejects_non_strings():
    for value in (None, 3001234567, b"03001234567", ["0300"]):
        with pytest.raises(TypeError):
            privacy.normalise_phone(value)


def test_normalise_phone_is_exported_from_the_patterns_module():
    from apps.kernel.privacy import patterns

    assert privacy.normalise_phone is patterns.normalise_phone
