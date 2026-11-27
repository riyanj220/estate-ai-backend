import pytest

from apps.kernel.ai import known_values
from apps.kernel.ai.ports import LLMRequest, Message
from apps.kernel.ai.redaction import KnownValues, RedactedText, redact
from apps.kernel.ai.stub import stub

NOTHING = KnownValues()
KNOWN = KnownValues(
    phones=("+923000001101",),
    emails=("canary.owner@example.org",),
    addresses=("Zephyr Hollow Corner", "A1"),
)


@pytest.mark.parametrize(
    "text",
    [
        "call 03000001101 today",
        "call +92 300 000 1101 today",
        "call 0092-300-0001101 today",
        "call 300 000 1101 today",
        "call (0300) 000.11.01 today",
    ],
)
def test_the_actors_phone_goes_in_any_format(text):
    assert redact(text, KNOWN).value == "call [phone removed] today"


def test_known_email_and_address_go_whatever_the_case_or_punctuation():
    out = redact("Mail Canary.Owner@Example.org, near zephyr  hollow, corner.", KNOWN).value
    assert out == "Mail [email removed], near [address removed]."


def test_a_short_known_value_does_not_scrub_ordinary_text():
    assert redact("Block A1 plot", KNOWN).value == "Block A1 plot"


def test_shared_patterns_apply_to_values_nobody_knows():
    out = redact("0321 7654321, someone@elsewhere.org, House 12 Street 4", NOTHING).value
    assert "7654321" not in out and "elsewhere" not in out and "Street 4" not in out


@pytest.mark.parametrize(
    "text",
    [
        "asking 6 crore for a 240 yard house",
        "price 4,500,000 or Rs 9 000 000",
        "5 bedrooms, 6 bathrooms, 2 floors, built 2019",
        "120, 240 and 500 square yards in DHA Phase 6",
        "marla 10, kanal 2, covered area 3200 sqft",
    ],
)
def test_prices_areas_and_counts_are_preserved(text):
    assert redact(text, KNOWN).value == text


def test_source_length_is_the_length_before_redaction():
    text = "x " + "03000001101"
    assert redact(text, KNOWN).source_length == len(text)


def test_only_redact_makes_redacted_text():
    with pytest.raises(TypeError):
        RedactedText(object(), "raw", 3)
    with pytest.raises(TypeError):

        class Sneaky(RedactedText):
            pass

    safe = redact("hello", NOTHING)
    with pytest.raises(AttributeError):
        safe._value = "raw"
    assert "hello" not in repr(safe)


def test_ports_refuse_plain_strings():
    safe = redact("hello", NOTHING)
    for build in (
        lambda: LLMRequest(system=safe, input="raw"),
        lambda: LLMRequest(system="raw", input=safe),
        lambda: LLMRequest(system=safe, input=safe, context=("raw",)),
        lambda: LLMRequest(system=safe, input=safe, history=("raw",)),
        lambda: Message("user", "raw"),
        lambda: stub.embed(("raw",), timeout=1),
    ):
        with pytest.raises(TypeError):
            build()
    assert not stub.requests


def test_a_visitor_has_no_known_values():
    from apps.kernel.actor import Actor

    assert known_values.known_values_for(Actor.visitor()) == KnownValues()
