"""City and locality from chat: a catalogue match made by code, against the recorded stub."""

import json

import pytest

from apps.assistant import places
from apps.catalog import selectors as catalog
from apps.kernel.ai.stub import stub
from apps.listings import services as listings
from tests.assistant.test_extraction import answer, current
from tests.assistant.test_turns import clean, say, start  # noqa: F401
from tests.listings.support import user

GULSHAN = "Gulshan-e-Iqbal"


def karachi():
    return next(c for c in catalog.list_cities() if c.name == "Karachi")


def locality(name):
    return next(ref for ref in catalog.list_localities(karachi().id) if ref.name == name)


@pytest.mark.parametrize(
    "message, expected",
    [
        ("A house in Gulshan-e-Iqbal", GULSHAN),
        ("gulshan e iqbal, 3 beds", GULSHAN),
        ("GULSHAN IQBAL house", GULSHAN),
        ("Gulshaneiqbal flat", GULSHAN),
        ("Gulshan-e-Iqbal mein ghar. Gulshan-e-Iqbal.", GULSHAN),
        ("flat in North Nazimabad", "North Nazimabad"),
        ("plot in DHA phase 2 extension", "DHA Phase 2 Extension"),
        ("Defence Phase 6 house", "DHA Phase 6"),
        ("Gulistan e Johar apartment", "Gulistan-e-Jauhar"),
        ("Clifton Block 5 apartment", "Clifton Block 5"),
        ("a house in Gulshan", None),
        ("Clifton or Saddar", None),
        ("Nazimabad, not North Nazimabad", None),
        ("house with a garden, visit the site", None),
        ("flat near Saddar", None),
        ("on Korangi Road", None),
        ("near North Nazimabad", None),
        ("Askari, 4 bedrooms", None),
        ("Cliftonia towers", None),
    ],
)
def test_whole_locality_names_are_matched(db, message, expected):
    found = places.find_locality(message, [])
    assert (found.name if found else None) == expected


def test_the_models_location_texts_are_matched_too(db):
    assert places.find_locality("a nice house", ["gulshan e iqbal"]).name == GULSHAN
    assert places.find_locality("a nice house", ["Garden"]).name == "Garden"
    assert places.find_locality("in North Nazimabad", ["Nazimabad"]).name == "North Nazimabad"
    assert places.find_locality("in Clifton", ["Saddar"]) is None


def test_a_named_locality_sets_city_and_locality_unconfirmed():
    account, draft, client, session_id = start()
    stub.reply = answer({"society": "Gulshan e Iqbal", "bedrooms": 3})
    body = say(client, session_id, "3 bed house in Gulshan-e-Iqbal").json()
    view = current(account, draft)
    assert view.fields["city"] == str(karachi().id)
    assert view.fields["locality"] == str(locality(GULSHAN).id)
    assert view.fields["society"] is None and view.fields["bedrooms"] == 3
    unconfirmed = {"origin": "ai", "confirmed": False}
    assert view.provenance["city"] == view.provenance["locality"] == unconfirmed
    applied = {a["field"]: a["value"] for a in body["turn"]["applied"]}
    assert applied == {"city": "Karachi", "locality": GULSHAN, "bedrooms": 3}
    summary = {f["field"]: (f["value"], f["confirmed"]) for f in body["summary"]}
    assert summary["locality"] == (GULSHAN, False)
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    done = client.post(
        f"/api/v1/listings/{draft.id}/confirm",
        json.dumps({"base_revision": view.revision, "fields": ["city", "locality"]}),
        "application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert done.status_code == 200, done.json()
    confirmed = {"origin": "manual", "confirmed": True}
    after = current(account, draft)
    assert after.provenance["city"] == after.provenance["locality"] == confirmed


def test_a_correction_replaces_an_unconfirmed_place_only():
    account, draft, client, session_id = start()
    stub.reply = answer({})
    say(client, session_id, "house in Clifton")
    say(client, session_id, "sorry, it is in Saddar")
    view = current(account, draft)
    assert view.fields["locality"] == str(locality("Saddar").id)
    listings.confirm_fields(user(account), draft.id, view.revision, ["locality"])
    turn = say(client, session_id, "it is in Clifton").json()["turn"]
    assert current(account, draft).fields["locality"] == str(locality("Saddar").id)
    assert turn["applied"] == [] and turn["conflicts"] == [] and turn["rejected"] == []


def test_a_manual_city_is_kept_and_the_locality_added():
    account, draft, client, session_id = start()
    listings.apply_updates(user(account), draft.id, 1, {"city": str(karachi().id)})
    stub.reply = answer({"society": "DHA Phase 6"})
    say(client, session_id, "in DHA Phase 6")
    view = current(account, draft)
    assert view.fields["locality"] == str(locality("DHA Phase 6").id)
    assert view.fields["society"] is None
    assert view.provenance["city"] == {"origin": "manual", "confirmed": True}


@pytest.mark.parametrize("name", ["Saddar", "Clifton"])
def test_a_place_chosen_while_the_model_worked_is_kept(monkeypatch, name):
    account, draft, client, session_id = start()
    stub.reply = answer({"bedrooms": 2})
    real, calls = listings.apply_updates, []
    mine = {"city": str(karachi().id), "locality": str(locality(name).id)}

    def choose_first(actor, listing_id, revision, updates, origin="manual"):
        if not calls:
            calls.append(1)
            real(actor, listing_id, 1, mine)
        return real(actor, listing_id, revision, updates, origin)

    monkeypatch.setattr(listings, "apply_updates", choose_first)
    turn = say(client, session_id, "2 beds in Clifton").json()["turn"]
    view = current(account, draft)
    assert view.fields["locality"] == str(locality(name).id) and view.fields["bedrooms"] == 2
    assert view.provenance["locality"] == {"origin": "manual", "confirmed": True}
    assert sorted(turn["conflicts"]) == ["city", "locality"]
