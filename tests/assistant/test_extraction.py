"""Extraction, applying updates and follow-up questions (Stories 5.9 and 5.10), against the stub."""

import json

import pytest

from apps.assistant import extraction, planner
from apps.assistant.models import AssistantTurn
from apps.catalog import extraction as schemas
from apps.catalog import selectors as catalog
from apps.kernel.ai.stub import stub
from apps.kernel.errors import DomainError
from apps.listings import services as listings
from apps.listings.lifecycle import Origin
from tests.assistant.test_turns import clean, llm_requests, say, start  # noqa: F401
from tests.listings.support import set_listing_visibility, user

SENTENCE = (
    "240-yard double-storey house in DHA Phase 6, five bedrooms, six bathrooms, asking 6 crore"
)
EXTRACTED = {
    "area_value": "240",
    "area_unit": "sqyd",
    "total_floors": 2,
    "society": "DHA",
    "phase": "6",
    "bedrooms": 5,
    "bathrooms": 6,
    "asking_price": "60000000",
}


def answer(fields, reply="Got it.", clarify=None):
    return json.dumps({"reply": reply, "clarify": clarify, "fields": fields})


def current(account, draft):
    return listings.require_draft(user(account), draft.id)


def test_the_schema_is_strict_and_leaves_the_description_out():
    schema = extraction.turn_schema("sale", "house")
    fields = schema["properties"]["fields"]
    assert schema["required"] == list(schema["properties"])
    assert fields["required"] == list(fields["properties"])
    assert "bedrooms" in fields["properties"]
    assert not {"description", "street", "city", "purpose"} & set(fields["properties"])


def test_a_sentence_fills_the_draft_as_unconfirmed_ai_values_in_one_call():
    account, draft, client, session_id = start()
    stub.reply = answer({**EXTRACTED, "purpose": "rent", "street": "Nope", "made_up": 1})
    body = say(client, session_id, SENTENCE).json()
    view = current(account, draft)
    assert view.revision == 2 and len(llm_requests()) == 1
    assert view.fields["bedrooms"] == 5 and view.fields["area_unit"] == "sqyd"
    assert view.fields["purpose"] == "sale" and view.fields["street"] is None
    assert view.provenance["asking_price"] == {"origin": "ai", "confirmed": False}
    applied = {a["field"]: a["value"] for a in body["turn"]["applied"]}
    assert set(applied) == set(EXTRACTED) and applied["asking_price"] == "60000000.00"
    assert body["turn"]["rejected"] == [] and body["turn"]["notices"] == []
    # 5.10: nothing given is asked again; the title is next
    assert body["next_step"]["fields_asked"] == ["title"]
    assert client.get(f"/api/v1/assistant/sessions/{session_id}").json()["turns"] == [body["turn"]]


def test_invalid_values_are_not_saved_and_are_reported():
    account, draft, client, session_id = start()
    stub.reply = answer(
        {"bedrooms": 500, "bathrooms": 2, "asking_price": "6 crore", "title": ["x"]},
        clarify="Did you mean five bedrooms?",
    )
    turn = say(client, session_id).json()["turn"]
    view = current(account, draft)
    assert view.fields["bathrooms"] == 2
    assert view.fields["bedrooms"] is None and view.fields["asking_price"] is None
    assert {r["field"] for r in turn["rejected"]} == {"bedrooms", "asking_price", "title"}
    assert turn["notices"][0] == "Did you mean five bedrooms?"
    assert turn["notices"][1].startswith("I could not save")


def test_area_fields_are_saved_together_or_not_at_all():
    account, draft, client, session_id = start()
    stub.reply = answer({"area_value": "240", "area_unit": "gaz", "bedrooms": 3})
    say(client, session_id)
    view = current(account, draft)
    assert view.fields["area_value"] is None and view.fields["bedrooms"] == 3


def test_contact_details_are_redacted_and_the_seller_is_sent_to_the_form():
    account, draft, client, session_id = start()
    stub.reply = answer({"title": "Call 0300 765 0001 for this house", "bedrooms": 4})
    turn = say(client, session_id, "4 beds, call 0321 7650001").json()["turn"]
    assert turn["notices"][0] == extraction.CONTACT_NOTICE
    assert turn["message"] == "4 beds, call [phone removed]"
    assert turn["rejected"] == [{"field": "title", "code": "contains_contact_details"}]
    assert current(account, draft).fields["title"] is None
    assert "765" not in json.dumps(AssistantTurn.objects.get().result)


@pytest.mark.parametrize("mine", [{"bedrooms": 2}, {"title": "My own manual title"}])
def test_an_edit_made_while_the_model_worked_is_never_overwritten(monkeypatch, mine):
    account, draft, client, session_id = start()
    stub.reply = answer({"bedrooms": 5, "bathrooms": 6})
    real = listings.apply_updates
    calls = []

    def edit_first(actor, listing_id, revision, updates, origin=Origin.MANUAL):
        if not calls:
            calls.append(1)
            real(actor, listing_id, 1, mine)
        return real(actor, listing_id, revision, updates, origin)

    monkeypatch.setattr(listings, "apply_updates", edit_first)
    turn = say(client, session_id).json()["turn"]
    view = current(account, draft)
    assert view.fields["bathrooms"] == 6
    if "bedrooms" in mine:
        assert view.fields["bedrooms"] == 2 and turn["conflicts"] == ["bedrooms"]
        assert view.provenance["bedrooms"] == {"origin": "manual", "confirmed": True}
    else:
        assert view.fields["bedrooms"] == 5 and turn["conflicts"] == []


@pytest.mark.parametrize("reply", ['{"reply": ', '{"fields": {}}', '{"reply": 1}'])
def test_a_broken_answer_changes_and_stores_nothing(reply):
    account, draft, client, session_id = start()
    stub.reply = reply
    body = say(client, session_id).json()
    assert body["outcome"] == "error" and body["turn"] is None and body["next_step"] is None
    assert current(account, draft).revision == 1 and not AssistantTurn.objects.exists()


def test_the_ai_origin_cannot_write_a_listing_that_is_not_a_draft():
    account, draft, _, _ = start()
    set_listing_visibility(draft.id, status="archived")
    with pytest.raises(DomainError) as refused:
        listings.apply_updates(user(account), draft.id, 1, {"bedrooms": 1}, Origin.AI)
    assert refused.value.code == "conflict"


def test_planner_asks_required_first_then_offers_review():
    account, draft, client, session_id = start()
    actor = user(account)
    revision = 1

    def save(**fields):
        nonlocal revision
        view = listings.apply_updates(actor, draft.id, revision, fields)
        revision = view.revision
        return planner.next_step(view)

    assert planner.next_step(draft).fields_asked == ("area_value", "area_unit")
    assert save(area_unit="sqyd").question == "What is the area value?"
    assert save(area_value="240").fields_asked == ("asking_price",)
    assert save(asking_price="60000000").fields_asked == ("bedrooms", "bathrooms")
    assert save(bedrooms=5, bathrooms=6).fields_asked == ("title",)
    step = save(title="Double storey house in DHA")
    assert (step.fields_asked, step.in_form) == (("price_basis",), True)
    city = next(c for c in catalog.list_cities() if catalog.list_localities(c.id))
    step = save(price_basis="total", city=str(city.id))
    assert step.fields_asked == ("locality",)
    step = save(locality=str(catalog.list_localities(city.id)[0].id))
    assert step.kind == "review" and step.fields_asked == ()
    assert "review" in step.question and len(step.optional) == planner.OPTIONAL_LIMIT


def test_extraction_schema_version_is_recorded():
    _, _, client, session_id = start()
    body = client.get(f"/api/v1/assistant/sessions/{session_id}").json()
    assert body["schema_version"] == schemas.SCHEMA_VERSION == 3
