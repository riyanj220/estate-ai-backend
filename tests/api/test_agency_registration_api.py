import json
import logging
import uuid

import pytest
import yaml
from django.core import mail as django_mail
from django.core.management import call_command

from apps.catalog.models import City
from apps.identity import services, subscribers
from apps.identity.events import AccountRegistered
from apps.identity.models import Account, AgencyProfile, IndividualProfile
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord

URL = "/api/v1/registrations/agencies"
INDIVIDUAL_URL = "/api/v1/registrations/individuals"
ACCEPTED = {"status": "check_your_email"}
FIELDS = ["agency_name", "email", "phone", "office_address", "city_id", "password"]


@pytest.fixture(autouse=True)
def fresh_events(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    yield
    runtime.shutdown()


@pytest.fixture
def city(db):
    City.objects.all().delete()
    return City.objects.create(name="Karachi")


@pytest.fixture
def body(city):
    return {
        "agency_name": "Skyline Estates (Pvt) Ltd.",
        "email": "Office@Skyline-Estates.example",
        "phone": "0321-5550123",
        "office_address": "Suite 4, 12th Floor, Harbour Tower, I.I. Chundrigar Road",
        "city_id": str(city.id),
        "password": "mango-river-lantern-42",
    }


def post(client, payload, url=URL, **extra):
    return client.post(url, json.dumps(payload), content_type="application/json", **extra)


def fields_of(response):
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    return error["fields"]


def test_new_registration_is_accepted(client, body):
    response = post(client, body)
    assert response.status_code == 202
    assert response.json() == ACCEPTED
    account = Account.objects.get()
    assert (account.email, account.account_type) == (body["email"], "agency")
    profile = AgencyProfile.objects.get()
    assert (profile.business_phone, profile.verification_status) == ("+923215550123", "unverified")
    assert str(profile.city_id) == body["city_id"]


def test_response_never_carries_ids_or_private_values(client, body):
    response = post(client, body)
    text = response.content.decode() + str(dict(response.headers))
    for private in (
        str(Account.objects.get().id),
        body["email"],
        body["phone"],
        "5550123",
        body["office_address"],
        "Harbour",
        body["password"],
        body["city_id"],
    ):
        assert private not in text
    assert "set-cookie" not in {h.lower() for h in response.headers}


def test_new_existing_and_individual_addresses_are_indistinguishable(client, body):
    first = post(client, body)
    second = post(
        client,
        {**body, "email": body["email"].lower(), "phone": "0333-7654321", "agency_name": "Other"},
    )
    assert (first.status_code, first.content) == (second.status_code, second.content)
    ignored = {"X-Request-ID", "Date"}
    assert {k: v for k, v in first.headers.items() if k not in ignored} == {
        k: v for k, v in second.headers.items() if k not in ignored
    }
    assert Account.objects.count() == 1


def test_every_branch_answers_identically(client, body, django_capture_on_commit_callbacks):
    answers = []
    with django_capture_on_commit_callbacks(execute=True):
        answers.append(post(client, body))
        answers.append(post(client, body))
        services.mark_email_verified(Actor.system(), Account.objects.get().id)
        for _ in range(7):
            answers.append(post(client, body))
    assert answers[0].json() == ACCEPTED
    assert {(a.status_code, a.content) for a in answers} == {(202, answers[0].content)}
    assert len(django_mail.outbox) == 4  # shared per-account notice cap


def test_existing_individual_email_gets_the_same_answer(client, body):  # no second account
    individual = {
        "full_name": "Ayesha Khan",
        "email": body["email"],
        "password": body["password"],
        "phone": "0300-1234567",
    }
    first = post(client, individual, INDIVIDUAL_URL)
    second = post(client, body)
    assert (first.status_code, first.content) == (second.status_code, second.content)
    assert (Account.objects.count(), AgencyProfile.objects.count()) == (1, 0)


def test_verification_email_is_sent_to_the_agency(client, body, django_capture_on_commit_callbacks):
    subscribers.register()
    with django_capture_on_commit_callbacks(execute=True):
        assert post(client, body).status_code == 202
    assert [m.to for m in django_mail.outbox] == [[body["email"]]]


@pytest.mark.parametrize("field", FIELDS)
def test_missing_null_or_blank_field_is_required(client, body, field):
    missing = {k: v for k, v in body.items() if k != field}
    assert fields_of(post(client, missing)) == {field: ["This field is required."]}
    assert fields_of(post(client, {**body, field: "  "})) == {field: ["This field is required."]}
    assert fields_of(post(client, {**body, field: None})) == {field: ["This field is required."]}


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [123, ["x"], {"a": 1}, True])
def test_wrong_typed_values_are_invalid(client, body, field, value):
    fields = fields_of(post(client, {**body, field: value}))
    assert list(fields) == [field]
    assert fields[field] == ["Enter a valid value."]


@pytest.mark.parametrize(
    ("override", "field", "message"),
    [
        ({"agency_name": "A"}, "agency_name", "Enter a valid name without phone"),
        ({"agency_name": "12345"}, "agency_name", "Enter a valid name without phone"),
        ({"agency_name": "Call 0300 1234567"}, "agency_name", "Enter a valid name without phone"),
        ({"agency_name": "x@example.com"}, "agency_name", "Enter a valid name without phone"),
        ({"agency_name": "Bad_Name"}, "agency_name", "Enter a valid name without phone"),
        ({"agency_name": "A" * 101}, "agency_name", "Enter a valid name without phone"),
        ({"email": "not-an-email"}, "email", "Enter a valid email address."),
        ({"password": "short"}, "password", "Use at least 8 characters."),
        ({"password": "password123"}, "password", "This password is too common"),
        ({"password": "Office@Skyline-Estates.example"}, "password", "must not be the same"),
        ({"phone": "021-3456789"}, "phone", "Enter a Pakistani mobile number"),
        ({"phone": "abc"}, "phone", "Enter a Pakistani mobile number"),
        ({"office_address": "abc"}, "office_address", "Enter an office address"),
        ({"office_address": "a" * 301}, "office_address", "Enter an office address"),
        ({"office_address": "Line one\nline two"}, "office_address", "Enter an office address"),
        ({"office_address": "Suite\u0000 4"}, "office_address", "Enter an office address"),
        ({"office_address": "!!!!!!"}, "office_address", "Enter an office address"),
        ({"city_id": "not-a-uuid"}, "city_id", "Enter a valid value."),
        ({"city_id": str(uuid.uuid4())}, "city_id", "Choose a city from the list."),
    ],
)
def test_field_errors_use_static_messages_and_never_echo(client, body, override, field, message):
    response = post(client, {**body, **override})
    fields = fields_of(response)
    assert list(fields) == [field]
    assert message in fields[field][0]
    text = response.content.decode()
    for value in override.values():
        if len(value) > 6:
            assert value not in text
    assert Account.objects.count() == 0


def test_inactive_city_is_refused(client, body):
    inactive = City.objects.create(name="Quetta", is_active=False)
    fields = fields_of(post(client, {**body, "city_id": str(inactive.id)}))
    assert fields == {"city_id": ["Choose a city from the list."]}


def test_every_failing_field_is_listed_together(client, db):
    bad = {"agency_name": "1", "email": "x", "phone": "1", "office_address": "a", "city_id": "z"}
    assert set(fields_of(post(client, {**bad, "password": "p"}))) == set(FIELDS)


@pytest.mark.parametrize(
    "extra",
    [{"account_type": "admin"}, {"verification_status": "verified"}, {"role": "seller"}],
)
def test_unknown_field_is_rejected_and_nothing_is_created(client, body, extra):
    response = post(client, {**body, **extra})
    assert fields_of(response) == {
        "non_field_errors": ["The request contains fields that are not accepted."]
    }
    assert (Account.objects.count(), AgencyProfile.objects.count()) == (0, 0)


@pytest.mark.parametrize("raw", ["[]", '"text"', "5", "null"])
def test_body_must_be_an_object(client, db, raw):
    assert client.post(URL, raw, content_type="application/json").status_code == 400


def test_other_methods_are_not_allowed(client, db):
    assert client.get(URL).status_code == 405


def test_signed_in_and_admin_callers_are_refused_before_the_body_is_read(client, body, monkeypatch):
    for actor in (Actor.user(uuid.uuid4()), Actor.admin(uuid.uuid4())):
        monkeypatch.setattr("apps.presentation.api.registrations.get_actor", lambda r, a=actor: a)
        for payload in (body, {"role": "seller"}):
            response = post(client, payload)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "forbidden"
            assert response.json()["error"]["fields"] == {}
    assert (Account.objects.count(), AuditRecord.objects.count()) == (0, 0)


def test_the_registration_scope_is_shared_with_individuals(client, body):
    for n in range(5):
        assert post(client, {**body, "email": f"a{n}@example.com"}).status_code == 202
    for n in range(5):
        individual = {
            "full_name": "Ayesha Khan",
            "email": f"i{n}@example.com",
            "password": body["password"],
            "phone": "0300-1234567",
        }
        assert post(client, individual, INDIVIDUAL_URL).status_code == 202
    response = post(client, {**body, "email": "late@example.com"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "throttled"
    assert int(response["Retry-After"]) >= 1
    assert Account.objects.count() == 10
    assert IndividualProfile.objects.count() == 5


def test_eleventh_agency_request_is_throttled_and_invalid_ones_count(client, db):
    for _ in range(10):
        assert post(client, {}).status_code == 400
    assert post(client, {}).status_code == 429
    assert post(client, {}, REMOTE_ADDR="10.9.8.7").status_code == 400


def test_capped_response_is_the_same_for_a_new_and_an_existing_email(client, body):
    post(client, body)
    for n in range(9):
        post(client, {**body, "email": f"x{n}@example.com"})
    new = post(client, {**body, "email": "brand-new@example.com"})
    existing = post(client, body)
    assert new.status_code == existing.status_code == 429

    def comparable(response):
        skip = {"X-Request-ID", "Date", "Retry-After"}  # the countdown may tick between the two
        headers = {k: v for k, v in response.headers.items() if k not in skip}
        content = response.json()
        content["error"].pop("request_id", None)
        return headers, content

    assert comparable(new) == comparable(existing)
    assert abs(int(new["Retry-After"]) - int(existing["Retry-After"])) <= 2


def test_nothing_private_is_logged_returned_mailed_or_emitted(
    client, body, caplog, django_capture_on_commit_callbacks
):
    from apps.identity import events as identity_events

    subscribers.register()
    emitted = []
    for name in dir(identity_events):
        obj = getattr(identity_events, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, events.DomainEvent)
            and obj is not events.DomainEvent
            and obj.__module__ == identity_events.__name__
        ):
            events.subscribe(obj, emitted.append)
    caplog.set_level(logging.DEBUG)
    canary = {
        **body,
        "agency_name": "Canary Agency",
        "email": "canary.agency@example.com",
        "phone": "0300 9998877",
        "office_address": "Zephyr Quay 77, Block 9",
        "password": "canary-passphrase-77",
    }
    with django_capture_on_commit_callbacks(execute=True):
        responses = [
            post(client, canary),
            post(client, canary),
            post(client, {**canary, "phone": "9998877"}),
            post(client, {**canary, "office_address": "Zephyr\x00 Quay 77"}),
        ]
    assert django_mail.outbox and emitted
    mailed = " ".join(f"{m.subject} {m.body} {m.to}" for m in django_mail.outbox)
    event_text = " ".join(repr(e) for e in emitted)
    for value in ("9998877", "Zephyr"):
        assert value not in mailed and value not in event_text
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    returned = " ".join(r.content.decode() for r in responses)
    for value in ("canary.agency", "9998877", "Zephyr", "canary-passphrase-77"):
        assert value not in logged
        assert value not in returned


def test_openapi_documents_route_fields_and_actor_summary(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    operation = schema["paths"][URL]["post"]
    request = schema["components"]["schemas"]["AgencyRegistrationRequest"]
    assert set(request["properties"]) == set(FIELDS)
    assert set(request["required"]) == set(FIELDS)
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("AgencyRegistrationRequest")
    assert operation["operationId"] == "register_agency"
    assert set(operation["responses"]) == {"202", "400", "401", "403", "404", "429", "500"}
    assert "Retry-After" in operation["responses"]["429"]["headers"]
    summary = schema["components"]["schemas"]["ActorSummary"]
    assert "agency_name" in summary["properties"] and "agency_name" in summary["required"]
    assert not {"phone", "office_address", "business_phone"} & set(summary["properties"])


def test_event_is_emitted_once_for_a_new_agency(client, body, django_capture_on_commit_callbacks):
    seen = []
    events.subscribe(AccountRegistered, seen.append)
    with django_capture_on_commit_callbacks(execute=True):
        post(client, body)
        post(client, body)
    assert len(seen) == 1
