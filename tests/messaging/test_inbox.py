"""Stories 7.5 and 7.6: inbox, unread state, history and continuity."""

import uuid
from datetime import timedelta

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.messaging import selectors
from apps.messaging.models import Conversation
from tests.listings.support import set_account_state, set_listing_visibility

# `listing` and `seller` are fixtures of the sibling module
from tests.messaging.test_conversations import URL, Api, code, listing, publish_for, seller, started  # noqa: F401


def inbox(who, query=""):
    response = who.client.get(URL + query)
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    return response.json()


def unread(who):
    return who.client.get(f"{URL}/unread-count").json()["unread"]


def mark(who, conversation, seq):
    return who.write("post", f"{URL}/{conversation}/read", {"seq": seq})


def test_inbox_unread_and_marking_read(seller, listing):  # noqa: F811
    buyer = Api()
    conversation = started(buyer, listing)
    assert (unread(buyer), unread(seller)) == (0, 1)
    [row] = inbox(seller)["results"]
    assert (row["id"], row["role"], row["unread_count"]) == (conversation, "owner", 1)
    assert row["listing"]["title"] and row["counterpart_name"] == "Sana Buyer"
    assert "still available" not in str(row)

    assert seller.read(conversation, "/messages").status_code == 200
    assert unread(seller) == 1  # reading the history alone marks nothing
    assert mark(seller, conversation, 1).status_code == 204
    assert unread(seller) == 0 and inbox(seller)["results"][0]["unread_count"] == 0

    seller.send(conversation, "a")
    seller.send(conversation, "b")
    assert unread(buyer) == 1 and inbox(buyer)["results"][0]["unread_count"] == 2
    mark(buyer, conversation, 2)
    assert inbox(buyer)["results"][0]["unread_count"] == 1
    mark(buyer, conversation, 0)  # never back
    mark(buyer, conversation, 999)  # nor past the newest
    row = Conversation.objects.get()
    assert (row.initiator_read_seq, row.owner_read_seq, row.last_seq) == (3, 3, 3)


def test_order_pages_agency_inbox_and_query_count(monkeypatch):
    agency = Api("agency")
    theirs = [publish_for(agency.account) for _ in range(3)]
    buyers = [Api(name=f"Buyer {n}") for n in range(3)]
    made = [started(buyer, item) for buyer, item in zip(buyers, theirs, strict=True)]
    # the agency also starts one itself: both roles in one inbox
    other = Api("agency")
    made.append(started(agency, publish_for(other.account)))
    base = timezone.now()
    for age, key in enumerate(reversed(made)):
        Conversation.objects.filter(id=key).update(last_message_at=base - timedelta(minutes=age))

    def queries():
        with CaptureQueriesContext(connection) as seen:
            inbox(agency)
        return len(seen)

    many = queries()
    assert [c["id"] for c in inbox(agency)["results"]] == made[::-1]
    Conversation.objects.exclude(id=made[0]).update(owner_account_id=uuid.uuid4())
    Conversation.objects.exclude(id=made[0]).filter(initiator_account_id=agency.account.id).delete()
    assert queries() == many  # one row or four: the same queries

    monkeypatch.setattr(selectors, "INBOX_PAGE", 2)
    Conversation.objects.all().update(owner_account_id=agency.account.id, last_message_at=base)
    first = inbox(agency)
    second = inbox(agency, f"?cursor={first['next']}")
    ids = [c["id"] for c in first["results"] + second["results"]]
    assert sorted(ids) == sorted(made[:3]) and second["next"] is None  # ties: none lost or twice
    bad = agency.client.get(f"{URL}?cursor=nonsense")
    assert bad.status_code == 400 and "cursor" in bad.json()["error"]["fields"]


def test_only_participants_and_valid_input(listing):  # noqa: F811
    buyer = Api()
    conversation = started(buyer, listing)
    intruder = Api()
    assert inbox(intruder) == {"results": [], "next": None} and unread(intruder) == 0
    assert mark(intruder, conversation, 1).status_code == 404
    admin = Api("admin")
    assert admin.client.get(URL).status_code == 404
    assert admin.client.get(f"{URL}/unread-count").status_code == 404
    assert mark(admin, conversation, 1).status_code == 404
    visitor = Client()
    assert visitor.get(URL).status_code == 401
    assert visitor.get(f"{URL}/unread-count").status_code == 401
    for seq in (-1, "1", 1.5, True, None, 10**12):
        assert mark(buyer, conversation, seq).status_code == 400, seq
    assert buyer.client.post(f"{URL}/{conversation}/read", {"seq": 1}).status_code == 403  # CSRF


def test_history_is_kept_and_inbox_names_what_happened(seller, listing):  # noqa: F811
    buyer = Api()
    conversation = started(buyer, listing)
    seller.send(conversation, "Yes")
    for changes, availability in (
        ({"status": "sold"}, "sold"),
        ({"status": "rented"}, "rented"),
        ({"status": "archived"}, "unavailable"),
        ({"restricted": True}, "unavailable"),
    ):
        set_listing_visibility(listing, **changes)
        [row] = inbox(buyer)["results"]
        assert row["listing"] == {"availability": availability, "title": None}
        assert row["writable"] is False
        assert len(buyer.read(conversation, "/messages").json()["results"]) == 2
    set_listing_visibility(listing)
    assert inbox(buyer)["results"][0]["writable"] is True


def test_suspended_reads_and_a_deactivated_counterpart_is_named_so(seller, listing):  # noqa: F811
    buyer = Api()
    conversation = started(buyer, listing)
    seller.send(conversation, "Yes")
    set_account_state(buyer.account.id, "suspended")
    assert unread(buyer) == 1
    assert inbox(buyer)["results"][0]["read_only_reason"] == "messaging_unavailable"
    assert mark(buyer, conversation, 2).status_code == 204 and unread(buyer) == 0
    refused = buyer.send(conversation)
    assert code(refused) == "messaging_unavailable"
    assert "suspended" in refused.json()["error"]["message"]

    set_account_state(buyer.account.id, "active")
    set_account_state(seller.account.id, "deactivated")
    [row] = inbox(buyer)["results"]
    assert (row["counterpart_name"], row["read_only_reason"]) == (None, "counterpart_unavailable")
    refused = buyer.send(conversation)
    assert code(refused) == "counterpart_unavailable"
    assert refused.json()["error"]["message"] == "This user is no longer available."
