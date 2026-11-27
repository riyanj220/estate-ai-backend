"""Stories 10.4 to 10.8: account review, reference data, AI oversight, audit review and the
dashboard."""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.catalog.models import Amenity, City, Locality
from apps.governance.models import ClosureRequest, Report
from apps.identity.models import Account, AgencyProfile, IndividualProfile
from apps.kernel import ai_log, audit, flags
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.ports import LLMRequest
from apps.kernel.ai.redaction import KnownValues, redact
from apps.kernel.models import AuditRecord, RateLimitPolicy
from apps.listings.models import ListingPrivateAddress, PropertyListing
from apps.messaging.models import Conversation, Message
from apps.valuation import services as valuation
from tests.api.test_listings_api import signed_in
from tests.api.test_public_images_api import admin_client
from tests.api.test_reports_api import code, post
from tests.listings.support import (
    make_account,
    publish_ready,
    ready_draft,
    seed_cap,
    seed_media_policies,
)

ADMIN = "/api/v1/admin"
WHY = "Checking a support complaint"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    RateLimitPolicy.objects.update_or_create(
        scope="estimates",
        defaults={"limit": 20, "window_seconds": 3600, "key_kind": "account", "note": "t"},
    )


def patch(client, url, body):
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    return client.patch(
        url, json.dumps(body), content_type="application/json", HTTP_X_CSRFTOKEN=token
    )


def individual(email="jane.doe@example.com", name="Jane Doe", state="active"):
    account = make_account(email, "individual", state)
    IndividualProfile.objects.create(account_id=account.id, full_name=name, phone="+923001234567")
    return account


def report_about(account_id, listing_id, status="pending"):
    now = timezone.now()
    return Report.objects.create(
        target_id=listing_id,
        reporter_account_id=make_account(f"r{Report.objects.count()}@example.com").id,
        subject_account_id=account_id,
        reason="ai_generated_content",
        snapshot={"title": "A house"},
        status=status,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "path",
    [
        "accounts",
        "accounts/{id}",
        "dashboard",
        "audit-records",
        "ai-oversight",
        "reference/fixed",
        "reference/cities",
        "reference/amenities",
    ],
)
def test_only_administrators_reach_the_new_routes(path):
    other = signed_in()
    url = f"{ADMIN}/{path.format(id=other.account_id)}"
    assert Client().get(url).status_code == 401
    assert code(other.get(url)) == "not_found"
    assert admin_client().get(url).status_code == 200


# --- 10.4 --------------------------------------------------------------------------------------


def test_the_directory_searches_and_filters_without_private_fields():
    jane = individual()
    agency = make_account("sales@acme.example.com", "agency", "suspended")
    AgencyProfile.objects.create(
        account_id=agency.id,
        agency_name="Acme Estates",
        business_phone="+922134567890",
        office_address="Office nine",
        city_id=City.objects.get_or_create(name="Karachi")[0].id,
    )
    admin = admin_client()

    def names(query=""):
        return {r["display_name"] for r in admin.get(f"{ADMIN}/accounts?{query}").json()["results"]}

    assert names() == {"Jane Doe", "Acme Estates"}  # no administrator
    assert names("q=acme") == {"Acme Estates"}
    assert names(f"q={jane.id}") == {"Jane Doe"}
    assert names("q=example.com") == set()  # email is not searched
    assert names("type=individual") == {"Jane Doe"}
    assert names("state=suspended") == {"Acme Estates"}
    assert names("state=active&type=agency") == set()
    assert admin.get(f"{ADMIN}/accounts?state=nope").status_code == 400
    text = admin.get(f"{ADMIN}/accounts").content.decode()
    review = admin.get(f"{ADMIN}/accounts/{agency.id}").content.decode()
    for private in ("+92", "Office nine", "sales@acme"):
        assert private not in text + review
    assert code(admin.get(f"{ADMIN}/accounts/{admin_id()}")) == "not_found"


def admin_id():
    return Account.objects.get(account_type="admin").id


def test_the_review_shows_reports_and_closure_requests():
    jane = individual()
    listing_id = publish_ready(jane.id)
    report_about(jane.id, listing_id)
    ClosureRequest.objects.create(account_id=jane.id, kind="deletion", submitted_at=timezone.now())

    body = admin_client().get(f"{ADMIN}/accounts/{jane.id}").json()

    assert body["account"]["state"] == "active"
    assert [r["listing_id"] for r in body["reports"]] == [str(listing_id)]
    assert [(r["kind"], r["status"]) for r in body["closure_requests"]] == [("deletion", "pending")]


def test_private_fields_need_a_recorded_reason():
    jane = individual()
    listing_id = publish_ready(jane.id)
    ListingPrivateAddress.objects.update_or_create(
        listing_id=listing_id, defaults={"street": "Hidden Lane"}
    )
    admin = admin_client()
    account_url = f"{ADMIN}/accounts/{jane.id}/private"
    address_url = f"{ADMIN}/listings/{listing_id}/private-address"

    for url in (account_url, address_url):
        assert admin.get(url).status_code == 405
        assert post(admin, url, {}).status_code == 400
        assert post(admin, url, {"reason": "short"}).status_code == 400
        assert post(
            signed_in("x@example.com") if url == account_url else Client(), url, {"reason": WHY}
        ).status_code in (401, 404)
    assert not AuditRecord.objects.filter(action="privacy.viewed").exists()

    contact = post(admin, account_url, {"reason": WHY}).json()
    assert (contact["email"], contact["phone"]) == ("jane.doe@example.com", "+923001234567")
    assert post(admin, address_url, {"reason": WHY}).json()["street"] == "Hidden Lane"
    reads = AuditRecord.objects.filter(action="privacy.viewed")
    assert sorted((r.target_type, r.reason) for r in reads) == [
        ("account_contact", WHY),
        ("listing_address", WHY),
    ]


# --- 10.5 --------------------------------------------------------------------------------------


def test_reference_data_is_added_renamed_and_deactivated_with_audit():
    admin = admin_client()
    base = f"{ADMIN}/reference"
    assert post(admin, f"{base}/cities", {"name": "Quetta"}).status_code == 204
    city = City.objects.get(name="Quetta")
    assert (
        post(
            admin, f"{base}/cities/{city.id}/localities", {"name": "Jinnah Town", "kind": "area"}
        ).status_code
        == 204
    )
    locality = Locality.objects.get(city=city)
    amenity = {
        "code": "rooftop_garden",
        "label": "Rooftop garden",
        "kind": "on_property",
        "group": "outdoor",
    }
    assert post(admin, f"{base}/amenities", amenity).status_code == 204

    assert (
        patch(admin, f"{base}/localities/{locality.id}", {"name": "Jinnah Colony"}).status_code
        == 204
    )
    assert patch(admin, f"{base}/localities/{locality.id}", {"is_active": False}).status_code == 204
    assert patch(admin, f"{base}/cities/{city.id}", {"name": "quetta City"}).status_code == 204
    assert patch(admin, f"{base}/amenities/rooftop_garden", {"is_active": False}).status_code == 204
    # nothing changes, nothing is recorded
    assert patch(admin, f"{base}/amenities/rooftop_garden", {"is_active": False}).status_code == 204

    listed = admin.get(f"{base}/cities/{city.id}/localities").json()["items"]
    assert [(r["name"], r["is_active"]) for r in listed] == [("Jinnah Colony", False)]
    public = Client().get(f"/api/v1/reference/cities/{city.id}/localities").json()["items"]
    assert public == []
    assert "rooftop_garden" not in Client().get("/api/v1/reference/amenities").content.decode()
    assert Amenity.objects.filter(code="rooftop_garden").exists()  # never deleted
    changes = AuditRecord.objects.filter(action__endswith=".updated").values_list(
        "action", "metadata"
    )
    assert sorted((a, m["change"]) for a, m in changes) == [
        ("amenity.updated", "deactivated"),
        ("city.updated", "renamed"),
        ("locality.updated", "deactivated"),
        ("locality.updated", "renamed"),
    ]


def test_reference_data_refusals_and_the_fixed_catalogue():
    admin = admin_client()
    base = f"{ADMIN}/reference"
    City.objects.get_or_create(name="Karachi")
    City.objects.get_or_create(name="Lahore")
    lahore = City.objects.get(name="Lahore")
    assert (
        code(patch(admin, f"{base}/cities/{lahore.id}", {"name": "karachi"})) == "validation_error"
    )
    for bad in ({}, {"is_active": "no"}, {"name": 5}, {"colour": "red"}):
        assert patch(admin, f"{base}/cities/{lahore.id}", bad).status_code == 400
    assert code(patch(admin, f"{base}/amenities/missing_one", {"is_active": False})) == "not_found"
    assert (
        code(patch(signed_in(), f"{base}/cities/{lahore.id}", {"is_active": False})) == "not_found"
    )
    assert City.objects.get(id=lahore.id).is_active

    fixed = admin.get(f"{base}/fixed").json()
    assert fixed["purposes"] == ["sale", "rent"] and "marla" in fixed["area_units"]
    assert {c["id"] for c in fixed["categories"]} == {"house", "apartment", "plot", "commercial"}
    assert post(admin, f"{base}/fixed", {}).status_code == 405


# --- 10.6 --------------------------------------------------------------------------------------


def switch(client, name, enabled):
    return post(client, f"{ADMIN}/ai-oversight/switches/{name}", {"enabled": enabled})


def ask_assistant(account_id):
    text = redact("hello", KnownValues())
    request = LLMRequest(system=text, input=text)
    return gateway.complete(Actor.user(account_id), gateway.ASSISTANT_TURN, request)


def test_a_switch_applies_at_once_is_audited_and_leaves_manual_paths():
    owner = signed_in()
    admin = admin_client()

    assert switch(admin, "ai_assistant_turn", False).json()["enabled"] is False
    assert flags.is_enabled("ai_assistant_turn") is False
    assert ask_assistant(owner.account_id).outcome == "fallback"
    assert switch(admin, "ai_assistant_turn", False).status_code == 200  # no second record
    assert ready_draft(user_view(owner.account_id)).status == "draft"  # the manual form
    assert Client().get("/api/v1/public/listings").status_code == 200
    assert switch(admin, "ai_assistant_turn", True).status_code == 200

    records = AuditRecord.objects.filter(action="ai.feature_flag_changed").order_by("created_at")
    assert [r.metadata for r in records] == [
        {"flag": "ai_assistant_turn", "old_enabled": True, "new_enabled": False},
        {"flag": "ai_assistant_turn", "old_enabled": False, "new_enabled": True},
    ]
    assert all(r.actor_id == admin_id() for r in records)
    assert code(switch(admin, "unknown_flag", False)) == "not_found"
    assert switch(admin, "ai_gateway", "off").status_code == 400
    assert code(switch(owner, "ai_gateway", False)) == "not_found"


def user_view(account_id):
    from types import SimpleNamespace

    return SimpleNamespace(id=account_id)


def test_the_valuation_switch_stops_estimates():
    valuation.register_model(Actor.system("test"))
    owner = signed_in()
    city = City.objects.get_or_create(name="Karachi")[0]
    area = Locality.objects.get_or_create(city=city, name="Clifton", defaults={"kind": "area"})[0]
    draft = ready_draft(
        user_view(owner.account_id),
        extra={"city": str(city.id), "locality": str(area.id)},
    )

    def estimate():
        return valuation.estimate_for_listing(Actor.user(owner.account_id), draft.id)

    assert estimate().value is not None
    switch(admin := admin_client(), "price_estimate", False)
    assert (estimate().value, estimate().reason) == (None, "temporarily_unavailable")
    preview = owner.get(f"/api/v1/listings/{draft.id}/preview").json()
    assert preview["estimate"] is None  # the stored figure is hidden as well
    switch(admin, "price_estimate", True)
    assert estimate().value is not None


def test_oversight_aggregates_failures_without_personal_data():
    valuation.register_model(Actor.system("test"))
    ai_log.record_ai_call("assistant_turn", "stub", "stub-1", 5, "success")
    ai_log.record_ai_call("assistant_turn", "stub", "stub-1", 5, "timeout")
    ai_log.record_ai_call("price_estimate", "local", "price-1", 7, "error")
    ai_log.record_ai_call(
        "price_estimate", "local", "price-1", 7, "error", now=timezone.now() - timedelta(days=30)
    )

    body = admin_client().get(f"{ADMIN}/ai-oversight").json()

    calls = {c["feature"]: (c["total"], c["failed"]) for c in body["calls"]}
    assert calls == {"assistant_turn": (2, 1), "price_estimate": (1, 1)}
    assert body["valuation_model"]["version"] == 1
    assert [f["outcome"] for f in body["failed_valuations"]] == ["error"]
    assert {s["name"]: s["enabled"] for s in body["switches"]}["ai_gateway"] is True


def test_ai_content_reports_use_the_queue_filter():
    jane = individual()
    report_about(jane.id, publish_ready(jane.id))
    body = admin_client().get(f"{ADMIN}/reports?reason=ai_generated_content").json()
    assert len(body["results"]) == 1


# --- 10.7 --------------------------------------------------------------------------------------


def test_the_audit_trail_is_filtered_and_read_only():
    jane = individual()
    admin = admin_client()
    post(admin, f"{ADMIN}/accounts/{jane.id}/private", {"reason": WHY})
    url = f"{ADMIN}/audit-records"

    def rows(query=""):
        return admin.get(f"{url}?{query}").json()["results"]

    found = rows("action=privacy.viewed")
    assert [(r["target_type"], r["reason"], r["actor_email"]) for r in found] == [
        ("account_contact", WHY, "r***@example.com")
    ]
    assert rows(f"target={jane.id}&target_type=account_contact") == found
    assert {r["actor_id"] for r in rows(f"actor={admin_id()}")} == {str(admin_id())}
    today = timezone.localdate().isoformat()
    assert rows("date_to=2000-01-01") == []
    assert rows(f"date_from={today}&action=admin.signed_in")
    for bad in ("action=nope.nope", "actor=x", "target=1", "date_from=yesterday", "cursor=x"):
        assert admin.get(f"{url}?{bad}").status_code == 400
    assert admin.delete(url).status_code in (403, 405)
    assert post(admin, url, {}).status_code == 405


def test_the_audit_trail_pages_without_repeats():
    admin = admin_client()
    for number in range(55):
        post(
            admin,
            f"{ADMIN}/reference/cities",
            {"name": f"Town {chr(65 + number // 26)}{chr(97 + number % 26)}"},
        )
    first = admin.get(f"{ADMIN}/audit-records?action=city.created").json()
    second = admin.get(first["next"]).json()
    ids = [r["id"] for r in first["results"] + second["results"]]
    assert len(first["results"]) == 50 and len(ids) == len(set(ids)) == 55
    assert second["next"] is None


def test_every_action_the_trail_must_cover_is_registered():
    assert {
        "admin.provisioned",
        "admin.signed_in",
        "admin.sign_in_failed",
        "admin.password_reset",
        "account.password_changed",
        "account.suspended",
        "closure.approved",
        "closure.rejected",
        "verification.approved",
        "listing.removed",
        "report.resolved",
        "quota.policy_changed",
        "city.updated",
        "amenity.created",
        "ai.feature_flag_changed",
        "privacy.viewed",
    } <= set(audit.registered_actions())


# --- 10.8 --------------------------------------------------------------------------------------


def test_the_dashboard_counts_match_the_seeded_data():
    jane, other = individual(), individual("other@example.com", "Other One")
    make_account("agency@example.com", "agency")
    old = timezone.now() - timedelta(days=8)
    Account.objects.filter(id=other.id).update(created_at=old)
    published = publish_ready(jane.id)
    draft = ready_draft(jane)
    PropertyListing.objects.filter(id=draft.id).update(created_at=old)
    report_about(jane.id, published)
    report_about(jane.id, published, status="reviewing")
    report_about(jane.id, published, status="dismissed")  # three reporters: three accounts
    conversation = Conversation.objects.create(
        listing_id=published, owner_account_id=jane.id, initiator_account_id=other.id
    )
    for number, sent in enumerate((timezone.now(), old), start=1):
        Message.objects.create(
            conversation=conversation,
            seq=number,
            sender_account_id=other.id,
            body="Hello",
            client_message_id=f"m{number}",
            created_at=sent,
        )

    body = admin_client().get(f"{ADMIN}/dashboard").json()

    assert (body["individual_accounts"], body["agency_accounts"]) == (5, 1)
    assert body["published_listings"] == 1
    assert body["listings_by_status"] == {
        "draft": 1,
        "published": 1,
        "archived": 0,
        "sold": 0,
        "rented": 0,
        "removed": 0,
    }
    assert body["pending_verifications"] == 0
    assert body["unresolved_reports"] == 2
    assert body["activity"] == {"days": 7, "new_accounts": 5, "new_listings": 1, "messages_sent": 1}
