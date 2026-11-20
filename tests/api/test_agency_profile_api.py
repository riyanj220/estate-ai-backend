"""Agency business profile over HTTP (Story 1.21), with Django's CSRF checks on."""

import json
import logging
import re
import threading
import uuid
from datetime import timedelta

import pytest
import yaml
from django.core import mail as django_mail
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.utils import timezone

from apps.catalog.models import City
from apps.identity import services, subscribers
from apps.identity.models import Account, AgencyProfile, EmailChangeToken, IndividualProfile
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord

PROFILE = "/api/v1/me/agency-profile"
CHANGE = "/api/v1/me/email-change"
CONFIRM = "/api/v1/email-changes/confirm"
SESSION = "/api/v1/session"
CSRF = "/api/v1/csrf"
ME = "/api/v1/me"
EMAIL = "Office@Skyline-Estates.example"
NEW = "New.Office@Skyline-Estates.example"
PASSWORD = "correct horse battery staple"
NAME = "Skyline Estates"
PHONE = "+923215550123"
ADDRESS = "Suite 4, Harbour Tower, Chundrigar Road"
ABOUT = "We help families buy and rent homes."
SYSTEM = Actor.system("r")
UNKNOWN_FIELD = {"non_field_errors": ["The request contains fields that are not accepted."]}


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


@pytest.fixture
def city(db):
    City.objects.all().delete()
    return City.objects.create(name="Karachi")


def make(city=None, email=EMAIL, account_type="agency", **overrides):
    view = services.create_account(SYSTEM, email, PASSWORD, account_type)
    services.mark_email_verified(SYSTEM, view.id)
    if account_type == "agency":
        values = {
            "agency_name": NAME,
            "business_phone": PHONE,
            "office_address": ADDRESS,
            "city_id": city.id,
        }
        values.update(overrides)
        AgencyProfile.objects.create(account_id=view.id, **values)
    elif account_type == "individual":
        IndividualProfile.objects.create(account_id=view.id, full_name="Jane Doe", phone=PHONE)
    return view


def post(client, url, body, **extra):
    return client.post(url, json.dumps(body), content_type="application/json", **extra)


def error_code(response):
    return response.json()["error"]["code"]


def fields(response):
    return response.json()["error"]["fields"]


def csrf(client):
    return client.get(CSRF).json()["csrf_token"]


def sign_in_browser(email=EMAIL):
    client = Client(enforce_csrf_checks=True)
    response = post(
        client, SESSION, {"email": email, "password": PASSWORD}, HTTP_X_CSRFTOKEN=csrf(client)
    )
    if response.status_code == 401:  # an administrator account signs in at its own door
        response = post(
            client,
            "/api/v1/admin-session",
            {"email": email, "password": PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(client),
        )
    assert response.status_code == 200
    return client


def patch(client, body, token=True):
    extra = {"HTTP_X_CSRFTOKEN": csrf(client)} if token else {}
    return client.patch(PROFILE, json.dumps(body), content_type="application/json", **extra)


def audits():
    return AuditRecord.objects.filter(action="account.profile_updated").count()


def token_in(message):
    return re.search(r"token=([A-Za-z0-9_-]+)", message.body).group(1)


@pytest.fixture
def owner(city):
    make(city)
    return sign_in_browser()


def expected(city, **overrides):
    body = {
        "agency_name": NAME,
        "description": "",
        "city_id": str(city.id),
        "business_phone": PHONE,
        "office_address": ADDRESS,
        "email": EMAIL,
        "pending_email": None,
        "verification_status": "unverified",
    }
    body.update(overrides)
    return body


# --- read ----------------------------------------------------------------------------------------


def test_read_returns_exactly_the_eight_fields_no_store(owner, city):
    response = owner.get(PROFILE)
    assert response.status_code == 200
    assert response.json() == expected(city)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("account_type", ["individual", "admin"])
def test_read_for_another_account_type_is_forbidden(city, account_type):
    make(city, account_type=account_type)
    response = sign_in_browser().get(PROFILE)
    assert (response.status_code, error_code(response)) == (403, "forbidden")


def test_read_without_a_session_is_401(db):
    response = Client().get(PROFILE)
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")


def test_actor_summary_is_unchanged(city):
    make(city, description=ABOUT)
    body = sign_in_browser().get(ME).content.decode()
    for secret in (PHONE, ADDRESS, ABOUT, "phone", "description", "office_address"):
        assert secret not in body
    assert NAME in body


def test_a_suspended_agency_may_read_and_update(city):
    make(city)
    client = sign_in_browser()
    Account.objects.update(suspended_at=timezone.now(), suspension_reason="x")
    assert client.get(PROFILE).status_code == 200
    response = patch(client, {"agency_name": "Skyline Group"})
    assert response.status_code == 200 and response.json()["agency_name"] == "Skyline Group"


# --- update --------------------------------------------------------------------------------------


def test_update_name_audits_once_and_leaves_the_rest(owner, city):
    response = patch(owner, {"agency_name": "  Skyline   Group "})
    assert response.status_code == 200
    assert response.json() == expected(city, agency_name="Skyline Group")
    assert response.headers["Cache-Control"] == "no-store"
    assert audits() == 1
    assert AuditRecord.objects.get(action="account.profile_updated").metadata == {}


def test_update_phone_address_and_city(owner, city):
    other = City.objects.create(name="Lahore")
    response = patch(
        owner,
        {
            "business_phone": "0300 7654321",
            "office_address": "Plot 9, Main Road",
            "city_id": str(other.id),
        },
    )
    assert response.status_code == 200
    assert response.json() == expected(
        city,
        business_phone="+923007654321",
        office_address="Plot 9, Main Road",
        city_id=str(other.id),
    )
    row = AgencyProfile.objects.get()  # storage: E.164, collapsed, the id
    assert (row.business_phone, row.office_address, row.city_id) == (
        "+923007654321",
        "Plot 9, Main Road",
        other.id,
    )
    assert audits() == 1


def test_update_description_collapses_whitespace(owner, city):
    response = patch(owner, {"description": "  We sell   homes.\n\n\nAnd rent  them.\t"})
    assert response.json()["description"] == "We sell homes. And rent them."
    assert AgencyProfile.objects.get().description == "We sell homes. And rent them."
    assert audits() == 1


@pytest.mark.parametrize("blank", ["", "  \n ", None])
def test_clear_description(city, blank):
    make(city, description=ABOUT)
    client = sign_in_browser()
    response = patch(client, {"description": blank})
    assert response.status_code == 200 and response.json()["description"] == ""
    assert AgencyProfile.objects.get().description == ""
    assert audits() == 1


@pytest.mark.parametrize(
    "text",
    [
        "Call us on 0300 1234567",
        "Write to sales@example.com",
        "Visit us at House 12, Street 5, Gulberg",
    ],
)
def test_description_with_contact_details_is_explained(owner, text):
    response = patch(owner, {"description": text})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert fields(response) == {
        "description": [
            "This text cannot contain phone numbers, email addresses or street addresses."
        ]
    }
    assert AgencyProfile.objects.get().description == ""
    wire = response.content.decode()
    assert "1234567" not in wire and "sales@" not in wire and "Gulberg" not in wire


def test_invalid_description_shape(owner):
    response = patch(owner, {"description": "a" * 1001})
    assert fields(response) == {
        "description": ["Enter a description of up to 1000 characters, or leave it empty."]
    }


@pytest.mark.parametrize("text", ["Call 03001234567", "Mail sales@example.com"])
def test_name_with_contact_details_is_invalid_name(owner, text):
    response = patch(owner, {"agency_name": text})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert fields(response) == {
        "agency_name": ["Enter a valid name without phone numbers or email addresses."]
    }
    assert AgencyProfile.objects.get().agency_name == NAME


@pytest.mark.parametrize("field", ["agency_name", "business_phone", "office_address", "city_id"])
@pytest.mark.parametrize("blank", ["", "   ", None])
def test_mandatory_fields_cannot_be_cleared(owner, city, field, blank):
    response = patch(owner, {field: blank})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert fields(response) == {field: ["This field is required."]}
    assert owner.get(PROFILE).json() == expected(city)
    assert audits() == 0


def test_all_failing_fields_are_reported_at_once_without_echo(owner, city):
    response = patch(
        owner,
        {
            "agency_name": "Call 03001234567",
            "description": "mail secretvalue@example.com",
            "business_phone": "12ab-secretvalue",
            "office_address": "a",
            "city_id": "zzz-secretvalue",
        },
    )
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert sorted(fields(response)) == [
        "agency_name",
        "business_phone",
        "city_id",
        "description",
        "office_address",
    ]
    wire = response.content.decode()
    assert "03001234567" not in wire and "secretvalue" not in wire
    assert owner.get(PROFILE).json() == expected(city)


def test_wrong_types_are_invalid(owner):
    response = patch(
        owner,
        {
            "agency_name": 5,
            "description": 5,
            "business_phone": 5,
            "office_address": 5,
            "city_id": 5,
        },
    )
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    desc = "Enter a description of up to 1000 characters, or leave it empty."
    assert fields(response)["description"] == [desc]
    others = {k: v for k, v in fields(response).items() if k != "description"}
    assert set(map(tuple, others.values())) == {("Enter a valid value.",)}
    assert len(fields(response)) == 5


@pytest.mark.parametrize("bad", ["not-a-uuid", str(uuid.uuid4())])
def test_invalid_or_unknown_city(owner, city, bad):
    response = patch(owner, {"city_id": bad})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(fields(response)) == ["city_id"]
    assert owner.get(PROFILE).json()["city_id"] == str(city.id)


def test_inactive_city_is_refused(owner):
    dormant = City.objects.create(name="Quetta", is_active=False)
    response = patch(owner, {"city_id": str(dormant.id)})
    assert fields(response) == {"city_id": ["Choose a city from the list."]}


def test_unchanged_submission_writes_no_audit_record(owner, city):
    response = patch(
        owner,
        {
            "agency_name": NAME,
            "description": "",
            "business_phone": "0321-5550123",
            "office_address": ADDRESS,
            "city_id": str(city.id),
        },
    )
    assert response.status_code == 200 and response.json() == expected(city)
    assert audits() == 0


@pytest.mark.parametrize(
    "body",
    [
        {"email": "x@example.com"},
        {"verification_status": "verified"},
        {"account_type": "admin"},
        {"pending_email": "x@example.com"},
        {"agency_name": "Skyline Group", "extra": 1},
        {"agency_name": "Skyline Group", "email": EMAIL},
    ],
)
def test_unknown_fields_are_refused_and_nothing_changes(owner, city, body):
    response = patch(owner, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert fields(response) == UNKNOWN_FIELD
    assert owner.get(PROFILE).json() == expected(city)
    assert audits() == 0


@pytest.mark.parametrize("body", [{}, [], "text", 5])
def test_empty_or_wrong_shaped_body(owner, body):
    response = patch(owner, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert audits() == 0


def test_update_needs_csrf_and_a_session(owner, city):
    response = patch(owner, {"agency_name": "Skyline Group"}, token=False)
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    response = patch(Client(enforce_csrf_checks=True), {"agency_name": "Skyline Group"})
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")
    assert owner.get(PROFILE).json() == expected(city)


@pytest.mark.parametrize("account_type", ["individual", "admin"])
def test_update_for_another_account_type_is_forbidden(city, account_type):
    make(city, account_type=account_type)
    client = sign_in_browser()
    for body in (
        {"agency_name": "Skyline Group"},
        {"agency_name": "1"},
        {},
        {"email": "x@example.com"},
        {"business_phone": None},
        [],
        "text",
    ):  # the type refusal comes before any body check
        response = patch(client, body)
        assert (response.status_code, error_code(response)) == (403, "forbidden")


def test_the_individual_profile_route_stays_forbidden_for_an_agency(owner):
    response = owner.get("/api/v1/me/profile")
    assert (response.status_code, error_code(response)) == (403, "forbidden")


@pytest.mark.django_db(transaction=True)
def test_parallel_patches_audit_once_per_real_change():
    runtime.shutdown()
    City.objects.all().delete()
    make(City.objects.create(name="Karachi"))
    clients = [sign_in_browser() for _ in range(6)]
    tokens = [csrf(c) for c in clients]
    barrier, codes = threading.Barrier(6), []

    def work(client, token):
        try:
            barrier.wait()
            response = client.patch(
                PROFILE,
                json.dumps({"agency_name": "Skyline Group"}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            )
            codes.append(response.status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=work, args=pair) for pair in zip(clients, tokens, strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [200] * 6
    assert audits() == 1


# --- email change reuse --------------------------------------------------------------------------


def test_agency_requests_and_confirms_an_email_change(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = post(
            owner,
            CHANGE,
            {"new_email": NEW, "current_password": PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(owner),
        )
    assert response.status_code == 202
    assert owner.get(PROFILE).json()["pending_email"] == NEW
    assert owner.get(PROFILE).json()["email"] == EMAIL
    token = token_in([m for m in django_mail.outbox if m.to == [NEW]][0])
    with django_capture_on_commit_callbacks(execute=True):
        assert post(Client(), CONFIRM, {"token": token}).status_code == 200
    body = owner.get(PROFILE).json()
    assert (body["email"], body["pending_email"]) == (NEW, None)
    assert sign_in_browser(NEW).get(PROFILE).status_code == 200


def test_change_to_an_existing_address_shows_pending_and_sends_only_the_heads_up(
    owner, city, django_capture_on_commit_callbacks
):
    make(city, "taken@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        response = post(
            owner,
            CHANGE,
            {"new_email": "Taken@Example.com", "current_password": PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(owner),
        )
    assert response.status_code == 202
    assert owner.get(PROFILE).json()["pending_email"] == "Taken@Example.com"
    assert [m for m in django_mail.outbox if m.to == [EMAIL]] != []
    assert not any("token=" in m.body and m.to == ["Taken@Example.com"] for m in django_mail.outbox)
    assert not any("token=" in m.body and m.to == [EMAIL] for m in django_mail.outbox)
    EmailChangeToken.objects.update(used_at=timezone.now())
    assert owner.get(PROFILE).json()["pending_email"] is None


def test_pending_email_disappears_when_the_request_expires(
    owner, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        post(
            owner,
            CHANGE,
            {"new_email": NEW, "current_password": PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(owner),
        )
    assert owner.get(PROFILE).json()["pending_email"] == NEW
    EmailChangeToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert owner.get(PROFILE).json()["pending_email"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"description": "x\u0000y"},
        {"description": "\u200dabc"},
        {"description": "abc\u200d"},
        {"description": "abc\u200d\n"},
    ],
)
def test_control_and_edge_joiner_descriptions_are_400_not_500(owner, body):
    response = patch(owner, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert fields(response) == {
        "description": ["Enter a description of up to 1000 characters, or leave it empty."]
    }


def test_an_unchanged_deactivated_city_does_not_block_an_edit(owner, city):
    City.objects.filter(id=city.id).update(is_active=False)
    response = patch(owner, {"agency_name": "Skyline Group", "city_id": str(city.id)})
    assert response.status_code == 200 and response.json()["agency_name"] == "Skyline Group"
    dormant = City.objects.create(name="Quetta", is_active=False)
    response = patch(owner, {"city_id": str(dormant.id)})
    assert fields(response) == {"city_id": ["Choose a city from the list."]}


def test_mandatory_fields_are_not_nullable_in_the_schema(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--file", str(target))
    props = yaml.safe_load(target.read_text())["components"]["schemas"][
        "PatchedAgencyProfileUpdateRequest"
    ]["properties"]
    for name in ("agency_name", "city_id", "business_phone", "office_address"):
        assert not props[name].get("nullable"), name
    assert props["description"]["nullable"] is True


def test_the_profile_route_cannot_change_the_email(owner):
    assert patch(owner, {"email": NEW}).status_code == 400
    assert Account.objects.get().email == EMAIL


# --- privacy -------------------------------------------------------------------------------------


def test_only_the_owner_sees_the_phone_and_address(city):
    make(city)
    make(city, "other@example.com", business_phone="+923335550199", office_address="Other Road 77")
    mine = sign_in_browser("other@example.com")
    body = mine.get(PROFILE).content.decode()
    assert PHONE not in body and ADDRESS not in body and "Other Road 77" in body
    for url in (ME, "/api/v1/cities"):
        text = mine.get(url).content.decode()
        assert "Other Road 77" not in text and "+923335550199" not in text


def test_nothing_private_leaks_into_logs_audit_events_or_errors(
    owner, city, caplog, monkeypatch, django_capture_on_commit_callbacks
):
    caplog.set_level(logging.DEBUG)
    captured = []
    real_emit = events.emit

    def spy(event):
        captured.append(repr(event))
        real_emit(event)

    monkeypatch.setattr("apps.identity.events.emit", spy)
    responses = [
        patch(
            owner,
            {
                "business_phone": "+923111234567",
                "office_address": "Hidden Tower 99, Private Lane",
                "description": "Reliable homes",
            },
        ),
        patch(owner, {"description": "call 0300 7654321 or mail leak@example.com"}),
        patch(owner, {"business_phone": "12ab", "office_address": "a"}),
        owner.get(PROFILE),
    ]
    with django_capture_on_commit_callbacks(execute=True):
        responses.append(
            post(
                owner,
                CHANGE,
                {"new_email": NEW, "current_password": PASSWORD},
                HTTP_X_CSRFTOKEN=csrf(owner),
            )
        )
    assert [r.status_code for r in responses] == [200, 400, 400, 200, 202]
    wire = " ".join(r.content.decode() for r in responses[1:3])
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    audit = " ".join(str(a.metadata) + a.reason for a in AuditRecord.objects.all())
    event_text = " ".join(captured)
    secrets = (
        "3111234567",
        PHONE,
        "5550123",
        "Hidden Tower",
        ADDRESS,
        "7654321",
        "leak@example.com",
        NEW,
        EMAIL,
        PASSWORD,
    )
    for secret in secrets:
        for where, text in (("log", logged), ("audit", audit), ("event", event_text)):
            assert secret not in text, (where, secret)
    for secret in ("7654321", "leak@example.com", "12ab"):
        assert secret not in wire
    for message in django_mail.outbox:
        assert "Hidden Tower" not in message.body and "3111234567" not in message.body


# --- contract ------------------------------------------------------------------------------------


def test_openapi_documents_the_route(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    assert set(schema["paths"][PROFILE]) == {"get", "patch"}
    components = schema["components"]["schemas"]
    assert set(components["AgencyProfile"]["properties"]) == set(expected_keys())
    assert set(components["PatchedAgencyProfileUpdateRequest"]["properties"]) == {
        "agency_name",
        "description",
        "city_id",
        "business_phone",
        "office_address",
    }
    patch_doc = schema["paths"][PROFILE]["patch"]["description"]
    assert "contains_contact_details" in patch_doc and "unknown_field" in patch_doc


def expected_keys():
    return (
        "agency_name",
        "description",
        "city_id",
        "business_phone",
        "office_address",
        "email",
        "pending_email",
        "verification_status",
    )
