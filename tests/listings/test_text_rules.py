"""Text and money rules of the editable Listing fields (Story 2.2), without a database."""

from decimal import Decimal

import pytest

from apps.catalog import registry
from apps.listings import validation as rules

TITLE = registry.field("title")
DESCRIPTION = registry.field("description")
PRICE = registry.field("asking_price")

# Realistic listing text with prices, areas and counts: none of it is contact data.
FALSE_POSITIVES = [
    "3 bedrooms, 120 sq yd, Rs 2.5 crore",
    "Beautiful 10 marla house in DHA Phase 6, Rs 4,50,00,000",
    "Asking Rs 15,000,000 for a 5 marla corner house",
    "Price PKR 12 500 000 negotiable, 1200-1800 sq ft",
    "2 bed 2 bath apartment, 1,250 sq ft, floor 7 of 12",
    "Rent Rs 85,000 per month, 3 bedrooms, 4 bathrooms",
    "Plot 45, Block 13-D, Sector 11-A, 1 kanal, Rs 25000000",
    "Built in 2019, 24/7 security, 3 floors, 6 rooms",
    "Shop 12 ground floor, 450 sq ft, Rs 3.2 crore only",
    "Available from 2026-12-01 until 2027-12-31, 1 year lease",
    "House of 5 marla near Gulberg III, 25 ft road, 2 kitchens",
    "Rs 1,500,000 per marla, total 7500000 for 5 marla",
    "Office 4th floor, 1800 sq ft, ample 12 car parking spots",
    "Flat in F-7 Markaz Islamabad, 900-1100 sq ft, 3rd floor",
    "Corner plot 60x90 ft, 1 kanal 5 marla, price 3.75 crore",
    "Per marla rate 4,200,000 PKR, 8 marla residential plot",
    "Spacious 7 marla house, 5 bedrooms, built 2021, 15% discount",
    "Land 2 acres, 3 km from motorway, Rs 12 000 000 per acre",
    "Prime location 100 ft road, 3 shops, monthly rent 450000",
]

CONTACT = [
    "Call me on 0300 1234567 any time",
    "Reach us at +92 300 1234567 today",
    "Mail sales@example.com for the details",
    "Visit House 12, Street 5, Gulberg Lahore",
    "WhatsApp 03001234567 for viewing",
    "agent at example dot com about this house",
]


@pytest.mark.parametrize("text", FALSE_POSITIVES)
def test_prices_areas_and_counts_are_not_contact_details(text):
    assert len(FALSE_POSITIVES) >= 15
    assert rules.check_text(TITLE, text, paragraphs=False) == (text, None)
    assert rules.check_text(DESCRIPTION, text, paragraphs=True) == (text, None)


def test_a_multi_line_description_with_prices_is_accepted():
    body = "Spacious house.\n\nRs 2.5 crore, 120 sq yd, 3 bedrooms.\nNear the park."
    assert rules.check_text(DESCRIPTION, body, paragraphs=True) == (body, None)


@pytest.mark.parametrize("text", CONTACT)
def test_contact_details_are_refused_in_title_and_description(text):
    padded = f"Lovely home. {text}"
    assert rules.check_text(TITLE, padded, paragraphs=False) == (None, "contains_contact_details")
    assert rules.check_text(DESCRIPTION, padded, paragraphs=True) == (
        None,
        "contains_contact_details",
    )


def test_the_check_uses_the_shared_patterns_only(monkeypatch):
    seen = []
    monkeypatch.setattr(rules, "reject_contact_details", lambda text: seen.append(text))
    assert rules.check_text(TITLE, "Plain family house", paragraphs=False)[1] is None
    assert seen == ["Plain family house"]


# --- shape --------------------------------------------------------------------------------------


def test_trim_collapse_and_nfc():
    assert rules.check_text(TITLE, "  Big \t  family\n house  ", False) == (
        "Big family house",
        None,
    )
    decomposed = "Café corner shop"
    assert rules.check_text(TITLE, decomposed, False) == ("Café corner shop", None)


def test_a_description_keeps_single_paragraph_breaks():
    value = "\n\n  First   line \r\n\r\n\r\n\r\nSecond line\u2028Third\n\n\n"
    assert rules.check_text(DESCRIPTION, value, True) == ("First line\n\nSecond line\nThird", None)
    assert rules.check_text(DESCRIPTION, "a\nb", True)[0] == "a\nb"
    assert rules.check_text(TITLE, "line one\nline two here", False)[0] == "line one line two here"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_text_clears(blank):
    assert rules.check_text(TITLE, blank, False) == (None, None)
    assert rules.check_text(DESCRIPTION, blank, True) == (None, None)


@pytest.mark.parametrize(
    "value",
    [
        "Nice\x00 family house",
        "Nice\x07 family house",
        "Nice\x1b[0m family house",
        "Nice\u200b family house",  # zero width space
        "\ufeffNice family house",
        "Nice \u202e family house",  # bidi override
        "Nice\u00ad family house",  # soft hyphen
        "Nice\u3164 family house",  # Hangul filler
        "Nice\x85 family house",  # NEL
        "Nice\x0b family house",
    ],
)
def test_control_and_invisible_characters_are_invalid(value):
    assert rules.check_text(TITLE, value, False) == (None, "invalid")
    assert rules.check_text(DESCRIPTION, value, True) == (None, "invalid")


def test_joiners_are_fine_inside_a_word_but_not_at_an_edge():
    inside = "کراچی \u200cگھر for sale"
    assert rules.check_text(TITLE, inside, False)[1] is None
    assert rules.check_text(TITLE, "Family house\u200d", False) == (None, "invalid")
    assert rules.check_text(TITLE, "\u200cFamily house", False) == (None, "invalid")
    assert rules.check_text(DESCRIPTION, "Good\n\u200dline", True) == (None, "invalid")


def test_length_bounds_come_from_the_registry():
    assert rules.check_text(TITLE, "x" * 9, False) == (None, "too_short")
    assert rules.check_text(TITLE, "x" * 10, False)[1] is None
    assert rules.check_text(TITLE, "x" * 100, False)[1] is None
    assert rules.check_text(TITLE, "x" * 101, False) == (None, "too_long")
    assert rules.check_text(DESCRIPTION, "x" * 5000, True)[1] is None
    assert rules.check_text(DESCRIPTION, "x" * 5001, True) == (None, "too_long")
    # the bound applies after collapse: runs of spaces do not count
    assert rules.check_text(TITLE, "ab" + " " * 50 + "cdefghij", False) == ("ab cdefghij", None)


def test_the_raw_size_is_bounded_before_any_scanning(monkeypatch):
    def forbidden(text):
        raise AssertionError("scanned oversize input")

    monkeypatch.setattr(rules, "reject_contact_details", forbidden)
    monkeypatch.setattr(rules, "_refused_character", forbidden)
    assert rules.check_text(DESCRIPTION, "x" * 20_001, True) == (None, "too_long")
    assert rules.check_text(TITLE, "x " * 10_001, False) == (None, "too_long")


def test_text_needs_a_letter_or_digit_and_a_string():
    assert rules.check_text(TITLE, "!!!!!!!!!!!!", False) == (None, "invalid")
    for value in (5, 1.5, True, ["a"], {"a": 1}, b"bytes"):
        assert rules.check_text(TITLE, value, False) == (None, "invalid")


# --- money --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1500000", Decimal("1500000.00")),
        ("1500000.5", Decimal("1500000.50")),
        ("1500000.00", Decimal("1500000.00")),
        (" 42.10 ", Decimal("42.10")),
        ("0.01", Decimal("0.01")),
        ("999999999999.99", Decimal("999999999999.99")),
        (250000, Decimal("250000.00")),
        (Decimal("19.5"), Decimal("19.50")),
    ],
)
def test_valid_money(value, expected):
    amount, code = rules.check_money(PRICE, value)
    assert code is None and amount == expected and str(amount) == f"{expected:.2f}"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("0", "invalid_price"),
        ("0.00", "invalid_price"),
        ("0.001", "invalid_price"),
        ("-5", "invalid_price"),
        ("-0.01", "invalid_price"),
        (0, "invalid_price"),
        (-1, "invalid_price"),
        ("1.234", "invalid_price"),
        ("1500000.000", "invalid_price"),
        ("1000000000000", "invalid_price"),
        ("999999999999.995", "invalid_price"),
        (10**40, "invalid_price"),
        (Decimal("Infinity"), "invalid_price"),
        (1500000.0, "invalid"),
        (0.5, "invalid"),
        (float("nan"), "invalid"),
        (True, "invalid"),
        ("1,500,000", "invalid"),
        ("Rs 1500", "invalid"),
        ("12abc", "invalid"),
        ("", "invalid"),
        (".5", "invalid"),
        ("1e6", "invalid"),
        ("١٢", "invalid"),  # non-ASCII digits
        ("1" * 60, "invalid"),
        (None, "invalid"),
        (["1"], "invalid"),
        ({"a": 1}, "invalid"),
    ],
)
def test_invalid_money(value, code):
    assert rules.check_money(PRICE, value) == (None, code)
