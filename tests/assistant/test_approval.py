"""Nearby facilities and corrections (5.11), confirmation and publication (5.12) and description
generation (5.13), always against the recorded stub."""

import json

import pytest

from apps.assistant.models import AssistantTurn
from apps.catalog import selectors as catalog
from apps.kernel.ai.stub import stub
from apps.listings import publication
from apps.listings import services as listings
from apps.listings.completeness import problems_of
from apps.listings.models import PropertyListing
from tests.assistant.test_extraction import answer, current
from tests.assistant.test_turns import (  # noqa: F401
    clean,
    llm_requests,
    post,
    say,
    seller,
    signed_in,
    start,
)
from tests.listings.support import user


def turn_answer(fields=None, nearby=(), unmatched=(), clarify=None):
    body = json.loads(answer(fields or {}, clarify=clarify))
    body["nearby"] = [{"facility": code, "state": state} for code, state in nearby]
    body["unmatched_nearby"] = list(unmatched)
    return json.dumps(body)


def test_nearby_facilities_are_merged_and_unknown_ones_reported():
    account, draft, client, session_id = start()
    listings.apply_updates(user(account), draft.id, 1, {"amenities": {"nearby_gym": "present"}})
    stub.reply = turn_answer(
        nearby=[("nearby_mosque", "present"), ("nearby_park", "present")],
        unmatched=["bakery", "invented"],
    )
    turn = say(client, session_id, "a masjid, a park and a bakery nearby").json()["turn"]
    view = current(account, draft)
    assert dict(view.fields["amenities"]) == {
        "nearby_gym": "present",
        "nearby_mosque": "present",
        "nearby_park": "present",
    }
    assert view.provenance["amenities"] == {"origin": "ai", "confirmed": False}
    assert turn["applied"] == [{"field": "amenities", "value": "Mosque, Park"}]
    assert "bakery" in turn["notices"][0] and "invented" not in turn["notices"][0]


def test_a_correction_replaces_the_value_and_keeps_the_rest():
    account, draft, client, session_id = start()
    stub.reply = turn_answer({"bedrooms": 4, "bathrooms": 3})
    say(client, session_id, "four bedrooms, three bathrooms")
    stub.reply = turn_answer({"bedrooms": 5})
    body = say(client, session_id, "actually five").json()
    view = current(account, draft)
    assert (view.fields["bedrooms"], view.fields["bathrooms"]) == (5, 3)
    summary = {f["field"]: f for f in body["summary"]}
    assert summary["bedrooms"] == {"field": "bedrooms", "value": "5", "confirmed": False}


# 30 scripted scenarios (5.12): (message, fields the model reports, nearby it reports, what the
# Draft may hold afterwards). Missing data stays missing and an unmentioned amenity is refused.
MOSQUE, PARK, SCHOOL = "nearby_mosque", "nearby_park", "nearby_school"
SCENARIOS = [
    ("I want to sell my house", {}, [], {}),
    ("it is a nice place", {}, [PARK], {}),
    ("five bedrooms", {"bedrooms": 5}, [], {"bedrooms": 5}),
    ("five bedrooms", {"bedrooms": 5, "bathrooms": None}, [], {"bedrooms": 5}),
    ("two bathrooms", {"bathrooms": 2}, [MOSQUE], {"bathrooms": 2}),
    ("asking 6 crore", {"asking_price": "60000000"}, [], {"asking_price": "60000000.00"}),
    ("price not decided", {"asking_price": None}, [], {}),
    ("price not decided", {"asking_price": ""}, [], {}),
    ("240 yards", {"area_value": "240", "area_unit": "sqyd"}, [], {"area_unit": "sqyd"}),
    ("it is big", {"area_value": None, "area_unit": None}, [], {}),
    ("close to a masjid", {}, [MOSQUE], {"amenities": {MOSQUE}}),
    ("close to a mosque", {}, [MOSQUE, PARK], {"amenities": {MOSQUE}}),
    ("parks around", {}, [PARK], {"amenities": {PARK}}),
    ("plenty of parking", {}, [PARK], {}),
    ("school and park nearby", {}, [SCHOOL, PARK], {"amenities": {SCHOOL, PARK}}),
    ("good neighbourhood", {}, [SCHOOL, MOSQUE, "nearby_gym", "nearby_hospital"], {}),
    ("a market nearby", {}, ["nearby_market"], {}),
    ("furnished", {"furnishing": "furnished"}, [], {"furnishing": "furnished"}),
    ("maybe furnished", {"furnishing": "partly"}, [], {}),
    ("built recently", {"construction_year": None}, [], {}),
    ("built in 2015", {"construction_year": 2015}, [], {"construction_year": 2015}),
    ("has a basement", {"basement": True}, [], {"basement": True}),
    ("no idea about floors", {"total_floors": None}, [], {}),
    ("double storey", {"total_floors": 2}, [], {"total_floors": 2}),
    ("in DHA", {"society": "DHA", "phase": None}, [], {"society": "DHA"}),
    ("sell it", {"city": "Karachi", "purpose": "rent"}, [], {}),
    ("with a pool", {"swimming_pool": True}, [], {}),
    ("thousands of rooms", {"bedrooms": 5000}, [], {}),
    ("call me on 0321 7650001", {"title": "Call 0321 7650001 today"}, [], {}),
    ("hello", {}, [], {}),
]


@pytest.mark.parametrize(("message", "fields", "nearby", "expected"), SCENARIOS)
def test_the_assistant_invents_no_missing_facts_or_amenities(message, fields, nearby, expected):
    account, draft, client, session_id = start()
    stub.reply = turn_answer(fields, [(code, "present") for code in nearby])
    assert say(client, session_id, message).json()["outcome"] == "success"
    view = current(account, draft)
    held = {name for name, value in view.fields.items() if value is not None} - {
        "purpose",
        "category",
        "subtype",
        "area_sqft",
        "area_value",
    }
    assert held == set(expected) - {"area_value"}
    for name, value in expected.items():
        got = set(view.fields[name]) if name == "amenities" else view.fields[name]
        assert got == value
    assert len(SCENARIOS) == 30 and AssistantTurn.objects.count() == 1


def confirm(client, draft, revision, fields):
    url = f"/api/v1/listings/{draft.id}/confirm"
    return post(client, url, {"base_revision": revision, "fields": fields})


def problems(draft):
    row = PropertyListing.objects.get(id=draft.id)
    view = listings.view_of(row, with_private=True)
    return problems_of(row, view, True)


def test_confirmation_is_explicit_and_unblocks_publication():
    account, draft, client, session_id = start()
    city = next(c for c in catalog.list_cities() if catalog.list_localities(c.id))
    actor = user(account)
    place = {"city": str(city.id), "locality": str(catalog.list_localities(city.id)[0].id)}
    listings.apply_updates(actor, draft.id, 1, place, "ai")
    stub.reply = turn_answer(
        {"asking_price": "60000000", "area_value": "240", "area_unit": "sqyd"},
        [(MOSQUE, "present")],
    )
    say(client, session_id, "240 yards near a mosque, 6 crore")
    sensitive = ["asking_price", "locality", "area_value", "area_unit"]
    sensitive.append("city")  # part of the quota identity
    assert {k for k, v in problems(draft).items() if v == "confirm_required"} == set(sensitive)
    view = current(account, draft)
    assert confirm(client, draft, view.revision - 1, sensitive).status_code == 409
    assert confirm(client, draft, view.revision, ["bedrooms"]).status_code == 400
    assert confirm(client, draft, view.revision, ["nope"]).status_code == 400
    assert confirm(client, draft, view.revision, []).status_code == 400
    done = confirm(client, draft, view.revision, [*sensitive, "amenities"])
    assert done.status_code == 200, done.json()
    after = current(account, draft)
    assert all(after.provenance[n] == {"origin": "manual", "confirmed": True} for n in sensitive)
    assert after.fields["asking_price"] == view.fields["asking_price"]
    assert "confirm_required" not in problems(draft).values()
    with pytest.raises(listings.FieldsInvalid):  # still incomplete
        publication.publish(actor, draft.id, after.revision, "key-1")


def test_another_account_cannot_confirm():
    _, draft, _, _ = start()
    seller("seller.two@example.com")
    stranger = signed_in("seller.two@example.com")
    assert confirm(stranger, draft, 1, ["title"]).status_code == 404


FACTS = {"bedrooms": 5, "bathrooms": 6}


def written(*paragraphs):
    return json.dumps(
        {
            "paragraphs": [
                {"sentences": [{"text": t, "fields": f} for t, f in sentences]}
                for sentences in paragraphs
            ]
        }
    )


GOOD = written(
    [
        ("This house is for sale.", ["purpose", "subtype"]),
        ("It has 9 bedrooms.", ["bedrooms"]),  # a number the fact does not hold
        ("A park is next door.", ["subtype"]),  # a facility nobody stated
        ("The title is verified.", ["subtype"]),
        ("Call 0321 7650001.", ["subtype"]),
        ("It has a garden.", []),
        ("The price is low.", ["asking_price"]),  # cites an unconfirmed value
    ],
    [("It offers five bedrooms.", ["bedrooms"])],
)


def describe(client, session_id, revision, **extra):
    url = f"/api/v1/assistant/sessions/{session_id}/description"
    return post(client, url, {"base_revision": revision, **extra})


def test_a_description_uses_only_confirmed_details_and_traceable_claims():
    account, draft, client, session_id = start()
    actor = user(account)
    assert describe(client, session_id, 1).json()["error"]["fields"] == {
        "description": ["Fill in and confirm some details first."]
    }
    listings.apply_updates(actor, draft.id, 1, FACTS)
    listings.apply_updates(actor, draft.id, 2, {"asking_price": "60000000"}, "ai")
    stub.reply = GOOD
    body = describe(client, session_id, 3).json()
    assert body["outcome"] == "success", body
    assert body["listing"]["fields"]["description"] == (
        "This house is for sale.\n\nIt offers five bedrooms."
    )
    assert body["listing"]["provenance"]["description"] == {"origin": "ai", "confirmed": False}
    assert [c["fields"] for c in body["claims"]] == [["purpose", "subtype"], ["bedrooms"]]
    assert body["left_out"] == ["asking_price"] and len(llm_requests()) == 1
    assert "60000000" not in " ".join(llm_requests()[0].texts)


def test_a_manual_description_survives_turns_and_needs_replace():
    account, draft, client, session_id = start()
    listings.apply_updates(user(account), draft.id, 1, {**FACTS, "description": "My own words."})
    stub.reply = turn_answer({"description": "Model words.", "parking_spaces": 2})
    say(client, session_id)
    view = current(account, draft)
    assert view.fields["description"] == "My own words."
    stub.reply = GOOD
    refused = describe(client, session_id, view.revision)
    assert refused.json()["error"]["fields"] == {
        "description": ["You wrote this description. Choose to replace it."]
    }
    assert describe(client, session_id, 1).status_code == 409
    done = describe(client, session_id, view.revision, replace=True).json()
    assert done["outcome"] == "success"


@pytest.mark.parametrize("reply", ["plain prose", "{}", written([("A house.", ["subtype"])])])
def test_an_unusable_description_changes_nothing(reply):
    account, draft, client, session_id = start()
    listings.apply_updates(user(account), draft.id, 1, FACTS)
    stub.reply = reply
    body = describe(client, session_id, 2).json()
    assert body["outcome"] == "error" and body["listing"] is None
    assert current(account, draft).fields["description"] is None
