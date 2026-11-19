import json
import logging
import uuid

import pytest
import yaml
from django.core import mail as django_mail
from django.core.management import call_command

from apps.identity.events import AccountRegistered
from apps.identity.models import Account, IndividualProfile
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord

URL = "/api/v1/registrations/individuals"
BODY = {
    "full_name": "Ayesha Khan",
    "email": "Ayesha.Khan@Example.com",
    "password": "mango-river-lantern-42",
    "phone": "0300-1234567",
}
ACCEPTED = {"status": "check_your_email"}


@pytest.fixture(autouse=True)
def fresh_events(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    yield
    runtime.shutdown()


def post(client, body=None, **extra):
    payload = BODY if body is None else body
    return client.post(URL, json.dumps(payload), content_type="application/json", **extra)


def fields_of(response):
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    return error["fields"]


def test_new_registration_is_accepted(client, db):
    response = post(client)
    assert response.status_code == 202
    assert response.json() == ACCEPTED
    assert Account.objects.get().email == BODY["email"]
    assert IndividualProfile.objects.get().phone == "+923001234567"


def test_response_never_carries_ids_or_private_values(client, db):
    response = post(client)
    text = response.content.decode() + str(dict(response.headers))
    account = Account.objects.get()
    for private in (str(account.id), BODY["email"], BODY["phone"], "3001234567", BODY["password"]):
        assert private not in text
    assert "set-cookie" not in {h.lower() for h in response.headers}


def test_new_and_existing_addresses_are_indistinguishable(client, db):
    first = post(client)
    second = post(client, {**BODY, "email": BODY["email"].lower(), "phone": "0333-7654321"})
    assert (first.status_code, first.content) == (second.status_code, second.content)
    ignored = {"X-Request-ID", "Date"}
    assert {k: v for k, v in first.headers.items() if k not in ignored} == {
        k: v for k, v in second.headers.items() if k not in ignored
    }
    assert Account.objects.count() == 1


def test_existing_address_gets_the_notice_and_no_verification_event(
    client, db, django_capture_on_commit_callbacks
):
    seen = []
    events.subscribe(AccountRegistered, seen.append)
    with django_capture_on_commit_callbacks(execute=True):
        post(client)
    assert len(seen) == 1 and django_mail.outbox == []
    with django_capture_on_commit_callbacks(execute=True):
        post(client, {**BODY, "email": BODY["email"].upper()})
    assert len(seen) == 1
    assert django_mail.outbox == []  # still unverified: a verification request, not a notice


def test_shared_phone_is_accepted_silently(client, db):
    assert post(client).status_code == 202
    other = post(client, {**BODY, "email": "other@example.com"})
    assert (other.status_code, other.json()) == (202, ACCEPTED)
    assert IndividualProfile.objects.count() == 2


@pytest.mark.parametrize("field", list(BODY))
def test_missing_or_blank_field_is_required(client, db, field):
    missing = {k: v for k, v in BODY.items() if k != field}
    assert fields_of(post(client, missing)) == {field: ["This field is required."]}
    assert fields_of(post(client, {**BODY, field: "  "})) == {field: ["This field is required."]}
    assert fields_of(post(client, {**BODY, field: None})) == {field: ["This field is required."]}


@pytest.mark.parametrize(
    ("override", "field", "message"),
    [
        ({"full_name": "A"}, "full_name", "Enter a name of 2 to 80 letters"),
        ({"full_name": "Call 0300 1234567"}, "full_name", "Enter a name of 2 to 80 letters"),
        ({"full_name": "jane@example.com"}, "full_name", "Enter a name of 2 to 80 letters"),
        ({"email": "not-an-email"}, "email", "Enter a valid email address."),
        ({"email": "a" * 250 + "@x.co"}, "email", "Enter a valid email address."),
        ({"password": "short"}, "password", "Use at least 8 characters."),
        ({"password": "x" * 1025}, "password", "Use at most 1024 characters."),
        ({"password": "password123"}, "password", "This password is too common"),
        ({"password": "12345678"}, "password", "This password is too common"),
        ({"password": "Ayesha.Khan@Example.com"}, "password", "must not be the same as the email"),
        ({"password": "ayesha.khan"}, "password", "must not be the same as the email"),
        ({"phone": "021-3456789"}, "phone", "Enter a Pakistani mobile number"),
        ({"phone": "+1234567"}, "phone", "Enter a Pakistani mobile number"),
        ({"phone": "abc"}, "phone", "Enter a Pakistani mobile number"),
        ({"phone": "+1234567890123456"}, "phone", "Enter a Pakistani mobile number"),
    ],
)
def test_field_errors_use_static_messages_and_never_echo(client, db, override, field, message):
    response = post(client, {**BODY, **override})
    fields = fields_of(response)
    assert list(fields) == [field]
    assert message in fields[field][0]
    text = response.content.decode()
    for value in override.values():
        if len(value) > 3:
            assert value not in text
    assert Account.objects.count() == 0


def test_every_failing_field_is_listed_together(client, db):
    fields = fields_of(
        post(client, {"full_name": "1", "email": "x", "password": "p", "phone": "1"})
    )
    assert set(fields) == set(BODY)


@pytest.mark.parametrize("extra", [{"role": "seller"}, {"role": "buyer", "account_type": "admin"}])
def test_unknown_or_role_like_field_is_rejected(client, db, extra):
    response = post(client, {**BODY, **extra})
    assert fields_of(response) == {
        "non_field_errors": ["The request contains fields that are not accepted."]
    }
    assert "seller" not in response.content.decode()
    assert Account.objects.count() == 0


@pytest.mark.parametrize("raw", ["[]", '"text"', "5", "null"])
def test_body_must_be_an_object(client, db, raw):
    assert client.post(URL, raw, content_type="application/json").status_code == 400


def test_malformed_and_non_json_bodies(client, db):
    assert client.post(URL, "{", content_type="application/json").status_code == 400
    assert (
        client.post(URL, "a=b", content_type="application/x-www-form-urlencoded").status_code == 415
    )
    assert Account.objects.count() == 0


def test_wrong_types_are_rejected(client, db):
    fields = fields_of(post(client, {**BODY, "phone": 3001234567, "password": ["x"]}))
    assert set(fields) == {"phone", "password"}


def test_other_methods_are_not_allowed(client, db):
    assert client.get(URL).status_code == 405


def test_signed_in_and_admin_callers_are_refused(client, db, monkeypatch):
    for actor in (Actor.user(uuid.uuid4()), Actor.admin(uuid.uuid4())):
        monkeypatch.setattr("apps.presentation.api.registrations.get_actor", lambda r, a=actor: a)
        response = post(client)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
    assert (Account.objects.count(), AuditRecord.objects.count()) == (0, 0)


def test_eleventh_request_in_an_hour_is_throttled(client, db):
    for n in range(10):
        assert post(client, {**BODY, "email": f"user{n}@example.com"}).status_code == 202
    response = post(client, {**BODY, "email": "user10@example.com"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "throttled"
    assert int(response["Retry-After"]) >= 1
    assert Account.objects.count() == 10


def test_throttle_counts_invalid_requests_and_is_keyed_by_client_address(client, db):
    for _ in range(10):
        assert post(client, {}).status_code == 400
    assert post(client, {}).status_code == 429
    other = post(client, {}, REMOTE_ADDR="10.9.8.7")
    assert other.status_code == 400  # a different client address has its own counter


def test_nothing_private_is_logged(client, db, caplog):
    caplog.set_level(logging.DEBUG)
    body = {
        "full_name": "Canary Person",
        "email": "canary.person@example.com",
        "password": "canary-passphrase-77",
        "phone": "0300 9998877",
    }
    post(client, body)
    post(client, body)
    post(client, {**body, "phone": "bad canary"})
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for canary in (
        "canary.person",
        "canary-passphrase-77",
        "9998877",
        "Canary Person",
        "bad canary",
    ):
        assert canary not in logged


def test_openapi_documents_route_request_and_response(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    operation = schema["paths"][URL]["post"]
    request = schema["components"]["schemas"]["IndividualRegistrationRequest"]
    assert set(request["properties"]) == {"full_name", "email", "password", "phone"}
    assert set(request["required"]) == set(request["properties"])
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("IndividualRegistrationRequest")
    assert set(operation["responses"]) == {"202", "400", "401", "403", "404", "429", "500"}
    accepted = schema["components"]["schemas"]["RegistrationAcceptedStatusEnum"]
    assert accepted["enum"] == ["check_your_email"]
    assert "Retry-After" in operation["responses"]["429"]["headers"]


def test_every_branch_answers_identically(client, db, django_capture_on_commit_callbacks):
    from apps.identity import services

    answers = []
    with django_capture_on_commit_callbacks(execute=True):
        answers.append(post(client))  # new
        answers.append(post(client))  # existing, unverified
        services.mark_email_verified(Actor.system(), Account.objects.get().id)
        for _ in range(7):  # existing, verified, then over the notice cap
            answers.append(post(client))
    assert answers[0].json() == ACCEPTED
    assert {(a.status_code, a.content) for a in answers} == {(202, answers[0].content)}
    assert len(django_mail.outbox) == 4  # the unverified request used one of the five


def test_non_visitor_is_refused_before_the_body_is_read(client, db, monkeypatch):
    monkeypatch.setattr(
        "apps.presentation.api.registrations.get_actor", lambda r: Actor.user(uuid.uuid4())
    )
    response = post(client, {"role": "seller"})
    assert response.status_code == 403
    assert response.json()["error"]["fields"] == {}
