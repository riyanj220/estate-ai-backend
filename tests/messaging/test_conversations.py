"""Story 7.1: starting an inquiry and sending into it, over HTTP with CSRF checks on."""

import uuid

import pytest
from django.test import Client

from apps.identity import services as identity
from apps.identity.models import IndividualProfile
from apps.kernel import events
from apps.listings.models import PropertyListing
from apps.messaging.events import MessageSent
from apps.messaging.models import Conversation, Message
from tests.listings.support import (
    PASSWORD,
    SYSTEM,
    make_account,
    random_email,
    ready_draft,
    set_account_state,
    set_listing_visibility,
)
from tests.messaging.conftest import set_policy

URL = "/api/v1/conversations"
ADMIN_PASSWORD = "correct horse battery 9 staple"
PHONE = "+923001234567"


class Api:
    def __init__(self, account_type="individual", state="active", name="Sana Buyer"):
        email = random_email()
        admin = account_type == "admin"
        if admin:
            self.account = identity.provision_admin(SYSTEM, email, ADMIN_PASSWORD)
        else:
            self.account = make_account(email, account_type, state)
        if account_type == "individual":
            IndividualProfile.objects.create(
                account_id=self.account.id, full_name=name, phone=PHONE
            )
        self.client = Client(enforce_csrf_checks=True)
        response = self.write(
            "post",
            "/api/v1/admin-session" if admin else "/api/v1/session",
            {"email": email, "password": ADMIN_PASSWORD if admin else PASSWORD},
        )
        assert response.status_code == 200, response.content

    def write(self, method, url, body=None, key=None):
        token = self.client.get("/api/v1/csrf").json()["csrf_token"]
        headers = {"HTTP_X_CSRFTOKEN": token}
        if key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return getattr(self.client, method)(url, body, content_type="application/json", **headers)

    def start(self, listing_id, body="Is it still available?", key=None):
        return self.write(
            "post", URL, {"listing_id": listing_id, "body": body}, key or str(uuid.uuid4())
        )

    def send(self, conversation_id, body="Hello", key=None):
        return self.write(
            "post",
            f"{URL}/{conversation_id}/messages",
            {"body": body},
            key or str(uuid.uuid4()),
        )

    def read(self, conversation_id, suffix=""):
        return self.client.get(f"{URL}/{conversation_id}{suffix}")


def code(response):
    return response.json()["error"]["code"]


def publish_for(account_view):
    view = ready_draft(account_view, images=1)
    set_listing_visibility(view.id)
    return str(view.id)


@pytest.fixture
def seller():
    return Api(name="Hira Seller")


@pytest.fixture
def listing(seller):
    return publish_for(seller.account)


def started(buyer, listing):
    response = buyer.start(listing)
    assert response.status_code == 201, response.content
    return response.json()["conversation"]["id"]


def test_inquiry_creates_a_conversation_and_both_sides_talk(
    seller, listing, django_capture_on_commit_callbacks, monkeypatch
):
    seen = []
    monkeypatch.setattr(events, "_registry", {MessageSent: [seen.append]})
    buyer = Api()
    with django_capture_on_commit_callbacks(execute=True):
        response = buyer.start(listing, "  Is it still available?\r\nThanks  ", key="k1")
    assert response.status_code == 201 and response["Cache-Control"] == "no-store"
    body = response.json()
    conversation, message = body["conversation"], body["message"]
    title = PropertyListing.objects.get(id=listing).title
    assert conversation["listing"] == {"availability": "available", "title": title}
    assert conversation["role"] == "initiator" and conversation["writable"] is True
    assert conversation["counterpart_name"] == "Hira Seller"
    assert (message["seq"], message["mine"], message["client_message_id"]) == (1, True, "k1")
    assert message["body"] == "Is it still available?\nThanks"
    [event] = seen
    assert set(event.recipient_account_ids) == {buyer.account.id, seller.account.id}

    reply = seller.send(conversation["id"], "Yes")
    assert reply.status_code == 201 and reply.json()["seq"] == 2
    theirs = seller.read(conversation["id"]).json()
    assert (theirs["role"], theirs["counterpart_name"]) == ("owner", "Sana Buyer")
    page = buyer.read(conversation["id"], "/messages").json()
    assert [(m["seq"], m["mine"]) for m in page["results"]] == [(1, True), (2, False)]
    assert page["results"][1]["client_message_id"] is None and page["has_more"] is False
    later = buyer.read(conversation["id"], "/messages?after=1").json()
    assert [m["seq"] for m in later["results"]] == [2]
    row = Conversation.objects.get()
    assert (row.last_seq, row.initiator_read_seq, row.owner_read_seq) == (2, 1, 2)


def test_one_conversation_per_listing_and_account_and_a_retry_never_duplicates(listing):
    buyer = Api()
    first = buyer.start(listing, "One", key="same")
    again = buyer.start(listing, "One", key="same")
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["message"]["id"] == first.json()["message"]["id"]
    assert Message.objects.count() == 1
    assert code(buyer.start(listing, "Other", key="same")) == "idempotency_key_reused"

    second = buyer.start(listing, "Two")
    assert second.status_code == 200 and second.json()["message"]["seq"] == 2
    assert Conversation.objects.count() == 1
    found = buyer.client.get(f"{URL}?listing_id={listing}").json()["results"]
    assert [c["id"] for c in found] == [first.json()["conversation"]["id"]]
    sent = buyer.send(found[0]["id"], "Three", key="three")
    assert buyer.send(found[0]["id"], "Three", key="three").json()["id"] == sent.json()["id"]
    assert Message.objects.count() == 3


def test_the_same_key_after_its_record_expired_still_does_not_duplicate(listing):
    from apps.kernel.models import IdempotencyRecord

    buyer = Api()
    conversation = started(buyer, listing)
    first = buyer.send(conversation, "Once", key="old")
    IdempotencyRecord.objects.all().delete()
    again = buyer.send(conversation, "Once", key="old")
    assert again.json()["id"] == first.json()["id"] and Message.objects.count() == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "blank"),
        ("   \n ", "blank"),
        ("x" * 2001, "max_length"),
        (None, "required"),
        (5, "invalid"),
        ("a\x00b", "invalid"),
    ],
)
def test_message_length_and_shape(listing, text, expected):
    response = Api().start(listing, text)
    assert response.status_code == 400
    assert list(response.json()["error"]["fields"]) == ["body"]
    assert Conversation.objects.count() == 0


def test_two_thousand_characters_and_a_missing_key(listing):
    buyer = Api()
    assert buyer.start(listing, "é" * 2000).status_code == 201
    response = buyer.write("post", URL, {"listing_id": listing, "body": "x"})
    assert list(response.json()["error"]["fields"]) == ["idempotency_key"]


@pytest.mark.parametrize(
    ("state", "expected"),
    [("unverified", "messaging_email_unverified"), ("suspended", "messaging_unavailable")],
)
def test_unverified_and_suspended_accounts_cannot_send(listing, state, expected):
    response = Api(state=state).start(listing)
    assert (response.status_code, code(response)) == (403, expected)
    assert Conversation.objects.count() == 0


def test_a_suspended_participant_reads_but_cannot_send(listing):
    buyer = Api()
    conversation = started(buyer, listing)
    set_account_state(buyer.account.id, "suspended")
    assert buyer.read(conversation).json()["read_only_reason"] == "messaging_unavailable"
    assert buyer.read(conversation, "/messages").status_code == 200
    assert code(buyer.send(conversation)) == "messaging_unavailable"
    assert code(buyer.start(listing)) == "messaging_unavailable"


def test_own_listing_unknown_listing_and_visitor_are_refused(seller, listing):
    response = seller.start(listing)
    assert (response.status_code, code(response)) == (409, "own_listing")
    buyer = Api()
    for target in (str(uuid.uuid4()), "nonsense"):
        assert buyer.start(target).status_code == 404
    set_listing_visibility(listing, restricted=True)
    assert buyer.start(listing).status_code == 404
    assert Conversation.objects.count() == 0
    visitor = Client()
    assert visitor.get(f"{URL}?listing_id={listing}").status_code == 401
    assert visitor.get(f"{URL}/{uuid.uuid4()}").status_code == 401


def test_agencies_receive_and_start_conversations():
    first, second = Api("agency"), Api("agency")
    theirs = publish_for(first.account)
    conversation = started(second, theirs)
    assert first.send(conversation).status_code == 201
    assert started(first, publish_for(second.account)) != conversation


def test_only_participants_see_it_and_no_payload_has_a_phone(listing, seller):
    buyer = Api()
    conversation = started(buyer, listing)
    payloads = []
    for who in (buyer, seller):
        for suffix in ("", "/messages"):
            payloads.append(who.read(conversation, suffix).content.decode())
    payloads.append(buyer.client.get(f"{URL}?listing_id={listing}").content.decode())
    for text in payloads:
        assert PHONE[-10:] not in text and "@" not in text
        assert str(seller.account.id) not in text and str(buyer.account.id) not in text

    for intruder in (Api(), Api("agency"), Api("admin")):
        for suffix in ("", "/messages"):
            assert intruder.read(conversation, suffix).status_code == 404
        assert intruder.send(conversation).status_code in (403, 404)
        found = intruder.client.get(f"{URL}?listing_id={listing}")
        assert found.status_code == 404 or found.json()["results"] == []
    assert Message.objects.count() == 1


def test_a_typed_phone_is_the_users_own_content(listing):
    buyer = Api()
    response = buyer.start(listing, f"Call me on {PHONE}")
    assert response.status_code == 201 and PHONE in response.json()["message"]["body"]


@pytest.mark.parametrize(
    ("changes", "availability"),
    [
        ({"status": "sold"}, "sold"),
        ({"status": "rented"}, "rented"),
        ({"status": "archived"}, "unavailable"),
        ({"restricted": True}, "unavailable"),
    ],
)
def test_a_hidden_listing_leaves_the_thread_read_only(seller, listing, changes, availability):
    buyer = Api()
    conversation = started(buyer, listing)
    set_listing_visibility(listing, **changes)
    shown = buyer.read(conversation).json()
    assert shown["listing"] == {"availability": availability, "title": None}
    assert (shown["writable"], shown["read_only_reason"]) == (False, "conversation_read_only")
    again = buyer.start(listing)
    assert again.status_code == 200 and again.json()["message"] is None
    assert again.json()["conversation"]["id"] == conversation
    for who in (buyer, seller):
        assert code(who.send(conversation)) == "conversation_read_only"
    assert Message.objects.count() == 1
    set_listing_visibility(listing)
    assert seller.send(conversation).status_code == 201


def test_a_deactivated_counterpart(seller, listing):
    buyer = Api()
    conversation = started(buyer, listing)
    set_account_state(seller.account.id, "deactivated")
    shown = buyer.read(conversation).json()
    assert shown["counterpart_name"] is None
    assert code(buyer.send(conversation)) == "counterpart_unavailable"


def test_message_rate_limit(listing):
    set_policy("message_send", 2)
    buyer = Api()
    conversation = started(buyer, listing)
    assert buyer.send(conversation, key="two").status_code == 201
    response = buyer.send(conversation)
    assert (response.status_code, code(response)) == (429, "throttled")
    assert response["Retry-After"]
    assert buyer.send(conversation, key="two").status_code == 201  # a replay does not count
    assert Message.objects.count() == 2


def test_new_conversation_rate_limit(seller):
    set_policy("conversation_new", 1, 86400)
    buyer = Api()
    first, second = publish_for(seller.account), publish_for(seller.account)
    started(buyer, first)
    response = buyer.start(second)
    assert (response.status_code, code(response)) == (429, "throttled")
    assert Conversation.objects.count() == 1
    assert buyer.start(first).status_code == 200  # the existing one is not a new one


def test_message_text_is_not_logged(listing, caplog):
    caplog.set_level("DEBUG")
    Api().start(listing, "canary-secret-text")
    assert "canary-secret-text" not in "\n".join(str(vars(r)) for r in caplog.records)
