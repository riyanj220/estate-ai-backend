"""The AI egress harness (SM-6, Story 5.1): canary phone, email and address values sit in account
data and in what the user types, and none may appear in any request the adapter records.

Every later AI entry point (assistant turn, extraction, description, NL and voice search) adds one
function `(actor, message)` to `ENTRY_POINTS` that drives it end to end.
"""

import logging
import re

import pytest

from apps.catalog.models import City
from apps.identity.models import AgencyProfile, IndividualProfile
from apps.kernel import runtime
from apps.kernel.ai import gateway
from apps.kernel.ai.known_values import known_values_for
from apps.kernel.ai.ports import LLMRequest, Message
from apps.kernel.ai.redaction import redact
from apps.kernel.ai.stub import stub
from apps.kernel.models import AiRequestLog
from apps.listings import services
from apps.listings.models import ListingPrivateAddress
from tests.listings.support import make_account, seed_cap, user

pytestmark = [pytest.mark.guard, pytest.mark.django_db]

# Invented values. The account's own address words match no shared pattern, so only the exact-value
# scrub can remove them; the typed-only values match no account, so only the patterns can.
OWN_PHONE = "+923000001201"
OWN_BUSINESS_PHONE = "+923000001202"
OWN_EMAIL = "canary.egress@example.org"
OWN_AGENCY_EMAIL = "canary.agency@example.org"
OWN_STREET = "Canaryzephyr Hollow"
OWN_HOUSE = "Canary-Lodge-Nine"
OWN_OFFICE = "Canaryoffice Tower, Canarymarket Square"
MESSAGE = (
    "Reach me on 0300-000-1201 or 0300 000 1202, CANARY.EGRESS@example.org or "
    "canary.agency@example.org. I live at canary lodge nine, canaryzephyr hollow; our office is "
    "Canarymarket Square at Canaryoffice Tower. A friend: 0321 7650001, "
    "canary.friend@elsewhere.org, House 77 Street 88. Asking 6 crore for 240 yards."
)
PHONE_DIGITS = ("3000001201", "3000001202", "3217650001")
WORDS = (
    "canaryegress", "canaryagency", "canaryfriend", "canaryzephyr", "canarylodge",
    "canaryoffice", "canarymarket", "house77", "street88",
)  # fmt: skip


def _safe(actor, text):
    return redact(text, known_values_for(actor))


def llm_assistant_turn(actor, message):
    safe = _safe(actor, message)
    request = LLMRequest(
        system=_safe(actor, "Help the seller."),
        input=safe,
        history=(Message("user", safe),),
        context=(_safe(actor, message),),
    )
    return gateway.complete(actor, gateway.ASSISTANT_TURN, request)


def llm_search_query(actor, message):
    request = LLMRequest(system=_safe(actor, "Return filters."), input=_safe(actor, message))
    return gateway.complete(actor, gateway.SEARCH_QUERY, request)


def embeddings(actor, message):
    return gateway.embed(actor, [_safe(actor, message)])


ENTRY_POINTS = [
    pytest.param(llm_assistant_turn, id="assistant-turn-port"),
    pytest.param(llm_search_query, id="search-query-port"),
    pytest.param(embeddings, id="embeddings-port"),
]


@pytest.fixture(autouse=True)
def clean_stub():
    seed_cap()
    stub.reset()
    yield
    runtime.shutdown()
    stub.reset()


def seller(kind):
    """An account holding every private value this account type can hold, plus a Listing's."""
    email = OWN_EMAIL if kind == "individual" else OWN_AGENCY_EMAIL
    account = make_account(email, kind)
    if kind == "individual":
        IndividualProfile.objects.create(
            account_id=account.id, full_name="Canary Seller", phone=OWN_PHONE
        )
    else:
        AgencyProfile.objects.create(
            account_id=account.id,
            agency_name="Quiet Estates",
            city_id=City.objects.first().id,
            business_phone=OWN_BUSINESS_PHONE,
            office_address=OWN_OFFICE,
        )
    listing = services.create_draft(user(account), "sale", "house", "house")
    ListingPrivateAddress.objects.create(
        listing_id=listing.id, street=OWN_STREET, house_number=OWN_HOUSE
    )
    return user(account)


def leaks(text: str) -> list[str]:
    digits = re.sub(r"[\s\-().+_/]", "", text)
    letters = re.sub(r"[\W_]", "", text.casefold())
    return [v for v in PHONE_DIGITS if v in digits] + [v for v in WORDS if v in letters]


@pytest.mark.parametrize("kind", ["individual", "agency"])
@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_no_canary_reaches_the_adapter_or_the_logs(entry, kind, caplog):
    actor = seller(kind)
    message = MESSAGE
    if kind == "individual":  # no office address is theirs, so nothing could match it exactly
        message = message.replace("Canarymarket Square at Canaryoffice Tower", "the market")
    with caplog.at_level(logging.DEBUG):
        assert entry(actor, message).ok

    assert stub.requests
    sent = "\n".join(text for request in stub.requests for text in request.texts)
    assert leaks(sent) == []
    assert "6 crore for 240 yards" in sent
    logs = "".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert leaks(logs + str(list(AiRequestLog.objects.values()))) == []


def test_the_harness_can_see_a_leak():
    assert leaks(MESSAGE)
