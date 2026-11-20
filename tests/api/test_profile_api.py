"""Individual profile and email change over HTTP (Story 1.20), with Django's CSRF checks on."""

import json
import logging
import re
import threading
from datetime import timedelta

import pytest
import yaml
from django.core import mail as django_mail
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.utils import timezone

from apps.identity import services, subscribers, verification
from apps.identity.models import Account, EmailChangeToken, IndividualProfile, Session
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord, RateLimitCounter, RateLimitPolicy

PROFILE = "/api/v1/me/profile"
CHANGE = "/api/v1/me/email-change"
CONFIRM = "/api/v1/email-changes/confirm"
PASSWORD_URL = "/api/v1/me/password"
SESSION = "/api/v1/session"
CSRF = "/api/v1/csrf"
ME = "/api/v1/me"
EMAIL = "Jane.Doe@Example.com"
NEW = "New.Address@Example.org"
PHONE = "+923001234567"
PASSWORD = "correct horse battery staple"
OTHER_PASSWORD = "mango-river-lantern-42"
SYSTEM = Actor.system("r")
CHECK = {"status": "check_your_email"}


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


def make(email=EMAIL, verified=True, account_type="individual", name="Jane Doe", phone=PHONE):
    view = services.create_account(SYSTEM, email, PASSWORD, account_type)
    if verified:
        services.mark_email_verified(SYSTEM, view.id)
    if account_type == "individual":
        IndividualProfile.objects.create(account_id=view.id, full_name=name, phone=phone)
    return view


def post(client, url, body, **extra):
    return client.post(url, json.dumps(body), content_type="application/json", **extra)


def error_code(response):
    return response.json()["error"]["code"]


def fields(response):
    return response.json()["error"]["fields"]


def stable(response):
    headers = {k: v for k, v in response.headers.items() if k not in {"X-Request-ID", "Date"}}
    body = response.json()
    body.get("error", {}).pop("request_id", None)
    return response.status_code, headers, body


def csrf(client):
    return client.get(CSRF).json()["csrf_token"]


def sign_in_browser(email=EMAIL, password=PASSWORD):
    client = Client(enforce_csrf_checks=True)
    response = post(
        client, SESSION, {"email": email, "password": password}, HTTP_X_CSRFTOKEN=csrf(client)
    )
    if response.status_code == 401:  # an administrator account signs in at its own door
        response = post(
            client,
            "/api/v1/admin-session",
            {"email": email, "password": password},
            HTTP_X_CSRFTOKEN=csrf(client),
        )
    assert response.status_code == 200
    return client


def patch(client, body, token=True):
    extra = {"HTTP_X_CSRFTOKEN": csrf(client)} if token else {}
    return client.patch(PROFILE, json.dumps(body), content_type="application/json", **extra)


def request_change(client, new_email=NEW, password=PASSWORD, token=True, **extra):
    headers = {"HTTP_X_CSRFTOKEN": csrf(client)} if token else {}
    body = {"new_email": new_email, "current_password": password}
    return post(client, CHANGE, body, **headers, **extra)


def token_in(message):
    return re.search(r"token=([A-Za-z0-9_-]+)", message.body).group(1)


@pytest.fixture
def owner(db):
    make()
    return sign_in_browser()


# --- read ----------------------------------------------------------------------------------------


def test_read_returns_exactly_the_four_fields_no_store(owner):
    response = owner.get(PROFILE)
    assert response.status_code == 200
    assert response.json() == {
        "full_name": "Jane Doe",
        "phone": PHONE,
        "email": EMAIL,
        "pending_email": None,
    }
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("account_type", ["agency", "admin"])
def test_read_for_another_account_type_is_forbidden(db, account_type):
    make(account_type=account_type)
    client = sign_in_browser()
    response = client.get(PROFILE)
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert response.headers["Cache-Control"] == "no-store"


def test_read_without_a_session_is_401(db):
    response = Client().get(PROFILE)
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")


def test_actor_summary_never_carries_the_phone(owner):
    body = owner.get(ME).content.decode()
    assert PHONE not in body and "phone" not in body


# --- update --------------------------------------------------------------------------------------


def test_update_name_and_phone_audits_once(owner):
    response = patch(owner, {"full_name": "  Jane   Q Doe ", "phone": "0300 7654321"})
    assert response.status_code == 200
    assert response.json() == {
        "full_name": "Jane Q Doe",
        "phone": "+923007654321",
        "email": EMAIL,
        "pending_email": None,
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert AuditRecord.objects.filter(action="account.profile_updated").count() == 1
    assert AuditRecord.objects.get(action="account.profile_updated").metadata == {}
    assert owner.get(PROFILE).json()["phone"] == "+923007654321"


def test_update_one_field_leaves_the_other(owner):
    assert patch(owner, {"full_name": "Janet Doe"}).json()["phone"] == PHONE
    assert patch(owner, {"phone": "+442071234567"}).json()["full_name"] == "Janet Doe"
    assert IndividualProfile.objects.get().phone == "+442071234567"


def test_unchanged_submission_writes_no_audit_record(owner):
    response = patch(owner, {"full_name": "Jane Doe", "phone": "0300-1234567"})
    assert response.status_code == 200
    assert AuditRecord.objects.filter(action="account.profile_updated").count() == 0


@pytest.mark.parametrize("phone", ["", "   ", None])
def test_phone_cannot_be_removed(owner, phone):
    response = patch(owner, {"phone": phone})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(fields(response)) == ["phone"]
    assert IndividualProfile.objects.get().phone == PHONE


def test_all_failing_fields_are_reported_at_once_without_echo(owner):
    response = patch(owner, {"full_name": "Call 03001234567", "phone": "12ab-secretvalue"})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert sorted(fields(response)) == ["full_name", "phone"]
    wire = response.content.decode()
    assert "03001234567" not in wire and "secretvalue" not in wire
    assert IndividualProfile.objects.get().full_name == "Jane Doe"


@pytest.mark.parametrize(
    "body",
    [
        {"email": "x@example.com"},
        {"account_type": "admin"},
        {"full_name": "Jane Doe", "extra": 1},
        {"full_name": "Janet Doe", "email": EMAIL},
    ],
)
def test_unknown_fields_are_refused_and_nothing_changes(owner, body):
    response = patch(owner, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert fields(response) == {
        "non_field_errors": ["The request contains fields that are not accepted."]
    }
    assert IndividualProfile.objects.get().full_name == "Jane Doe"
    assert Account.objects.get().email == EMAIL
    assert AuditRecord.objects.filter(action="account.profile_updated").count() == 0


@pytest.mark.parametrize("body", [{}, [], "text", 5])
def test_empty_or_wrong_shaped_body(owner, body):
    response = patch(owner, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


@pytest.mark.parametrize("body", [{"full_name": 5}, {"phone": 5}, {"full_name": None}])
def test_wrong_types_are_field_errors(owner, body):
    response = patch(owner, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


def test_update_needs_csrf_and_a_session(owner):
    response = patch(owner, {"full_name": "Janet Doe"}, token=False)
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    anonymous = Client(enforce_csrf_checks=True)
    response = patch(anonymous, {"full_name": "Janet Doe"})
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")
    assert IndividualProfile.objects.get().full_name == "Jane Doe"


@pytest.mark.parametrize("account_type", ["agency", "admin"])
def test_update_for_another_account_type_is_forbidden(db, account_type):
    make(account_type=account_type)
    client = sign_in_browser()
    response = patch(client, {"full_name": "Janet Doe"})
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    for body in ({"full_name": "1"}, {}, {"email": "x@example.com"}, {"phone": None}, [], "text"):
        bad = patch(client, body)  # the type refusal comes before any body check
        assert (bad.status_code, error_code(bad)) == (403, "forbidden")


# --- email change request ------------------------------------------------------------------------


def test_request_to_a_free_address(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = request_change(owner)
    assert (response.status_code, response.json()) == (202, CHECK)
    assert response.headers["Cache-Control"] == "no-store"
    to_new = [m for m in django_mail.outbox if m.to == [NEW]]
    to_old = [m for m in django_mail.outbox if m.to == [EMAIL]]
    assert len(to_new) == 1 and len(to_old) == 1 and len(django_mail.outbox) == 2
    assert "/confirm-email-change?token=" in to_new[0].body
    assert "expires in 24 hours" in to_new[0].body
    assert "token=" not in to_old[0].body and NEW not in to_old[0].body
    assert "change your password" in to_old[0].body
    assert Account.objects.get().email == EMAIL  # nothing changed yet
    assert owner.get(PROFILE).json()["pending_email"] == NEW


def test_old_address_signs_in_until_confirmation_and_new_does_not(
    owner, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        request_change(owner)
    fresh = Client(enforce_csrf_checks=True)
    ok = post(fresh, SESSION, {"email": EMAIL, "password": PASSWORD}, HTTP_X_CSRFTOKEN=csrf(fresh))
    assert ok.status_code == 200
    other = Client(enforce_csrf_checks=True)
    bad = post(other, SESSION, {"email": NEW, "password": PASSWORD}, HTTP_X_CSRFTOKEN=csrf(other))
    assert (bad.status_code, error_code(bad)) == (401, "invalid_credentials")


def test_taken_address_gets_the_registered_notice_and_no_link(
    owner, django_capture_on_commit_callbacks
):
    make("taken@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        response = request_change(owner, "TAKEN@example.com")
    assert (response.status_code, response.json()) == (202, CHECK)
    assert sorted(m.subject for m in django_mail.outbox) == [
        "A change of your EstateAI sign-in email was requested",
        "You already have an EstateAI account",
    ]
    assert sorted(m.to[0] for m in django_mail.outbox) == [EMAIL, "taken@example.com"]
    assert all("token=" not in m.body for m in django_mail.outbox)
    assert Account.objects.get(email="taken@example.com")


def test_same_address_in_another_case_only_sends_the_heads_up(
    owner, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = request_change(owner, EMAIL.upper())
    assert (response.status_code, response.json()) == (202, CHECK)
    assert [m.to for m in django_mail.outbox] == [[EMAIL]]


def test_every_branch_answers_identically(db, django_capture_on_commit_callbacks):
    make()
    make("taken@example.com")
    client = sign_in_browser()
    answers = []
    with django_capture_on_commit_callbacks(execute=True):
        for address in (
            "free@example.org",
            "taken@example.com",
            EMAIL.lower(),
            "other@example.net",
        ):
            answers.append(request_change(client, address))
    assert {a.status_code for a in answers} == {202}
    assert len({json.dumps(stable(a), sort_keys=True) for a in answers}) == 1


def test_pending_is_identical_across_free_taken_same_and_capped(
    db, django_capture_on_commit_callbacks
):
    from apps.kernel import ratelimit

    make()
    make("taken@example.com")
    client = sign_in_browser()
    seen = []

    def check(address):
        with django_capture_on_commit_callbacks(execute=True):
            assert request_change(client, address).status_code == 202
        body = client.get(PROFILE).json()
        seen.append(body)
        assert body["pending_email"] == address

    for address in ("free@example.org", "taken@example.com", EMAIL.upper()):
        check(address)
    account = Account.objects.get(email=EMAIL)
    for _ in range(5):  # exhaust the silent mail cap: the next free address gets no link
        ratelimit.hit("verification_email", ratelimit.derive_key("account", str(account.id)))
    check("capped-free@example.org")
    assert {tuple(sorted(b)) for b in seen} == {("email", "full_name", "pending_email", "phone")}
    assert not EmailChangeToken.objects.filter(
        new_email="capped-free@example.org", link_issued=True
    ).exists()


def test_a_request_that_issues_no_link_keeps_the_valid_pending_link(
    owner, django_capture_on_commit_callbacks
):
    make("taken@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        request_change(owner, NEW)
    token = token_in([m for m in django_mail.outbox if m.to == [NEW]][0])
    for address in ("taken@example.com", EMAIL.upper()):
        with django_capture_on_commit_callbacks(execute=True):
            request_change(owner, address)
    # the profile shows the newest request, whatever its branch; the older link still confirms
    assert owner.get(PROFILE).json()["pending_email"] == EMAIL.upper()
    assert post(Client(), CONFIRM, {"token": token}).status_code == 200


def test_confirm_rejects_a_row_whose_link_was_never_issued(owner):
    services.request_email_change(Actor.user(Account.objects.get().id), NEW, PASSWORD)
    row = EmailChangeToken.objects.get()
    assert owner.get(PROFILE).json()["pending_email"] == NEW
    for guess in (row.token_hash, "never-issued-token"):
        response = post(Client(), CONFIRM, {"token": guess})
        assert (response.status_code, error_code(response)) == (400, "link_invalid")
    assert Account.objects.get().email == EMAIL


def test_pending_is_none_after_expiry(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        request_change(owner)
    EmailChangeToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert owner.get(PROFILE).json()["pending_email"] is None


def test_a_newer_request_supersedes_the_older_link(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        request_change(owner, "first@example.org")
    with django_capture_on_commit_callbacks(execute=True):
        request_change(owner, "second@example.org")
    first = token_in([m for m in django_mail.outbox if m.to == ["first@example.org"]][0])
    second = token_in([m for m in django_mail.outbox if m.to == ["second@example.org"]][0])
    assert owner.get(PROFILE).json()["pending_email"] == "second@example.org"
    response = post(Client(), CONFIRM, {"token": first})
    assert (response.status_code, error_code(response)) == (409, "link_used")
    assert post(Client(), CONFIRM, {"token": second}).status_code == 200


def test_wrong_current_password_never_burns_the_request_budget(owner):
    answers = [stable(request_change(owner, password="wrong")) for _ in range(5)]
    assert len({json.dumps(a, sort_keys=True) for a in answers}) == 1
    status, headers, body = answers[0]
    assert status == 400 and "Retry-After" not in headers
    assert body["error"]["fields"] == {"current_password": ["The current password is incorrect."]}
    assert RateLimitCounter.objects.get(scope="password_change_failed").count == 5
    assert not RateLimitCounter.objects.filter(scope="email_change_request").exists()
    assert EmailChangeToken.objects.count() == 0
    # the right password during the failure lockout is still the silent `incorrect`, never 429
    for _ in range(3):
        locked = request_change(owner)
        assert (locked.status_code, error_code(locked)) == (400, "validation_error")
        assert fields(locked) == {"current_password": ["The current password is incorrect."]}


def test_the_throttle_is_shared_with_the_password_change(owner):
    for _ in range(5):
        assert request_change(owner, password="wrong").status_code == 400
    changed = post(
        owner,
        PASSWORD_URL,
        {"current_password": PASSWORD, "new_password": OTHER_PASSWORD},
        HTTP_X_CSRFTOKEN=csrf(owner),
    )
    assert (changed.status_code, fields(changed)) == (
        400,
        {"current_password": ["The current password is incorrect."]},
    )


def test_sixth_request_in_an_hour_is_429_with_retry_after(owner):
    for n in range(5):
        assert request_change(owner, f"a{n}@example.org").status_code == 202
    response = request_change(owner, "a9@example.org")
    assert (response.status_code, error_code(response)) == (429, "throttled")
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("address", ["plain", "a@b", "a@@b.co", "", "x" * 250 + "@example.com"])
def test_invalid_new_address(owner, address):
    response = request_change(owner, address)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(fields(response)) == ["new_email"]
    assert EmailChangeToken.objects.count() == 0
    assert RateLimitCounter.objects.filter(scope="password_change_failed").count() == 0


def test_invalid_address_message_is_the_stable_code(owner):
    response = request_change(owner, "plain")
    assert fields(response) == {"new_email": ["Enter a valid email address."]}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"new_email": NEW},
        {"current_password": PASSWORD},
        {"new_email": 5, "current_password": PASSWORD},
        {"new_email": NEW, "current_password": None},
        {"new_email": NEW, "current_password": PASSWORD, "extra": 1},
        [],
        "text",
    ],
)
def test_wrong_body_shape(owner, body):
    response = post(owner, CHANGE, body, HTTP_X_CSRFTOKEN=csrf(owner))
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert RateLimitCounter.objects.filter(scope="password_change_failed").count() == 0


def test_admin_is_forbidden_and_does_not_burn_the_limit(db):
    make(account_type="admin")
    client = sign_in_browser()
    for _ in range(7):
        response = request_change(client)
        assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert RateLimitCounter.objects.filter(scope="email_change_request").count() == 0


def test_agency_may_request_a_change(db, django_capture_on_commit_callbacks):
    make(account_type="agency")
    client = sign_in_browser()
    with django_capture_on_commit_callbacks(execute=True):
        assert request_change(client).status_code == 202
    assert len(django_mail.outbox) == 2


def test_request_needs_csrf_and_a_session(owner):
    response = request_change(owner, token=False)
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    anonymous = Client(enforce_csrf_checks=True)
    response = post(anonymous, CHANGE, {"new_email": NEW, "current_password": PASSWORD})
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")
    assert EmailChangeToken.objects.count() == 0


def test_handler_failure_does_not_change_the_response(
    owner, django_capture_on_commit_callbacks, monkeypatch, caplog
):
    monkeypatch.setattr(
        "apps.identity.notices.send_email_change_confirmation",
        lambda *a: (_ for _ in ()).throw(OSError("boom " + NEW)),
    )
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        response = request_change(owner)
    assert (response.status_code, response.json()) == (202, CHECK)
    assert django_mail.outbox == []
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    assert "OSError" in logged and NEW not in logged


# --- confirm -------------------------------------------------------------------------------------


@pytest.fixture
def requested(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert request_change(owner).status_code == 202
    token = token_in([m for m in django_mail.outbox if m.to == [NEW]][0])
    django_mail.outbox.clear()
    return token


def test_confirm_swaps_the_address(owner, requested, django_capture_on_commit_callbacks):
    other = sign_in_browser()
    with django_capture_on_commit_callbacks(execute=True):
        response = post(Client(), CONFIRM, {"token": requested})
    assert (response.status_code, response.json()) == (200, {"status": "email_changed"})
    assert response.headers["Cache-Control"] == "no-store"
    assert Account.objects.get().email == NEW
    assert EmailChangeToken.objects.get().used_at is not None
    assert AuditRecord.objects.filter(action="account.email_changed").count() == 1
    assert AuditRecord.objects.get(action="account.email_changed").metadata == {}
    assert [(m.to, m.subject) for m in django_mail.outbox] == [
        ([EMAIL], "Your EstateAI sign-in email was changed")
    ]
    assert NEW not in django_mail.outbox[0].body
    assert owner.get(ME).status_code == 200 and other.get(ME).status_code == 200  # sessions stay
    assert Session.objects.filter(ended_at__isnull=True).count() == 2
    assert owner.get(PROFILE).json()["email"] == NEW
    assert owner.get(PROFILE).json()["pending_email"] is None
    fresh = Client(enforce_csrf_checks=True)
    old = post(fresh, SESSION, {"email": EMAIL, "password": PASSWORD}, HTTP_X_CSRFTOKEN=csrf(fresh))
    assert (old.status_code, error_code(old)) == (401, "invalid_credentials")
    assert sign_in_browser(NEW).get(ME).status_code == 200
    again = post(Client(), CONFIRM, {"token": requested})
    assert (again.status_code, error_code(again)) == (409, "link_used")


def test_confirm_verifies_an_unverified_account(db, django_capture_on_commit_callbacks):
    make(verified=False)
    client = sign_in_browser()
    with django_capture_on_commit_callbacks(execute=True):
        request_change(client)
    token = token_in([m for m in django_mail.outbox if m.to == [NEW]][0])
    assert Account.objects.get().email_verified_at is None
    assert post(Client(), CONFIRM, {"token": token}).status_code == 200
    assert Account.objects.get().email_verified_at is not None
    assert AuditRecord.objects.filter(action="account.email_verified").count() == 1


def test_confirm_needs_no_csrf_and_ignores_cookies(owner, requested):
    assert post(owner, CONFIRM, {"token": requested}).status_code == 200


def test_confirm_voids_outstanding_reset_and_verification_links(owner, requested):
    from apps.identity.models import EmailVerificationToken, PasswordResetToken

    now = timezone.now()
    account = Account.objects.get()
    PasswordResetToken.objects.create(
        account=account, token_hash="a" * 64, created_at=now, expires_at=now + timedelta(hours=1)
    )
    EmailVerificationToken.objects.create(
        account=account, token_hash="b" * 64, created_at=now, expires_at=now + timedelta(hours=1)
    )
    assert post(Client(), CONFIRM, {"token": requested}).status_code == 200
    assert not PasswordResetToken.objects.filter(used_at__isnull=True).exists()
    assert not EmailVerificationToken.objects.filter(used_at__isnull=True).exists()


def test_expired_unknown_and_malformed_links(owner, requested):
    EmailChangeToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    expired = post(Client(), CONFIRM, {"token": requested})
    assert (expired.status_code, error_code(expired)) == (410, "link_expired")
    for bad in ("unknown-token", "", "a" * 201, 5, None, ["x"], {"a": 1}):
        response = post(Client(), CONFIRM, {"token": bad})
        assert (response.status_code, error_code(response)) == (400, "link_invalid")
        if isinstance(bad, str) and bad:
            assert bad not in response.content.decode() + str(dict(response.headers))
    assert Account.objects.get().email == EMAIL


@pytest.mark.parametrize("body", [{}, {"token": "x", "extra": 1}, [], "text", 5])
def test_wrong_confirm_body_shape(db, body):
    response = post(Client(), CONFIRM, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


def test_confirm_when_the_address_was_taken_meanwhile(owner, requested):
    make(NEW.lower())
    response = post(Client(), CONFIRM, {"token": requested})
    assert (response.status_code, error_code(response)) == (400, "link_invalid")
    assert Account.objects.get(email=EMAIL)
    assert EmailChangeToken.objects.get().used_at is None
    assert not AuditRecord.objects.filter(action="account.email_changed").exists()


def test_confirm_for_a_deactivated_account(owner, requested):
    Account.objects.filter(email=EMAIL).update(deactivated_at=timezone.now())
    response = post(Client(), CONFIRM, {"token": requested})
    assert (response.status_code, error_code(response)) == (400, "link_invalid")
    assert Account.objects.get(email=EMAIL)


def test_get_never_consumes(owner, requested):
    assert Client().get(CONFIRM + "?token=" + requested).status_code == 405
    assert EmailChangeToken.objects.get().used_at is None


def test_confirm_is_limited_per_client(owner, requested):
    for _ in range(20):
        assert post(Client(), CONFIRM, {"token": "unknown-token"}).status_code == 400
    response = post(Client(), CONFIRM, {"token": requested})
    assert (response.status_code, error_code(response)) == (429, "throttled")
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["Cache-Control"] == "no-store"
    assert EmailChangeToken.objects.get().used_at is None
    other = post(Client(), CONFIRM, {"token": requested}, REMOTE_ADDR="10.7.7.7")
    assert other.status_code == 200


def test_two_accounts_racing_for_one_address_cannot_both_win(db):
    # Both rows exist (each handler saw the address free); only the unique index decides.
    first, second = make(), make("second@example.com")
    now = timezone.now()
    for view, token in ((first, "race-token-one"), (second, "race-token-two")):
        EmailChangeToken.objects.create(
            link_issued=True,
            account_id=view.id,
            new_email=NEW,
            token_hash=verification.hash_token(token),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
    assert post(Client(), CONFIRM, {"token": "race-token-one"}).status_code == 200
    response = post(Client(), CONFIRM, {"token": "race-token-two"})
    assert (response.status_code, error_code(response)) == (400, "link_invalid")


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirms_yield_exactly_one_success():
    RateLimitPolicy.objects.update_or_create(
        scope="email_change_confirm",
        defaults={"limit": 100, "window_seconds": 3600, "key_kind": "client", "note": "t"},
    )
    runtime.shutdown()
    view = make()
    token, now = "concurrent-change-token", timezone.now()
    EmailChangeToken.objects.create(
        link_issued=True,
        account_id=view.id,
        new_email=NEW,
        token_hash=verification.hash_token(token),
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    barrier, outcomes = threading.Barrier(8), []

    def work():
        try:
            barrier.wait()
            outcomes.append(post(Client(), CONFIRM, {"token": token}).status_code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == [200] + [409] * 7
    assert AuditRecord.objects.filter(action="account.email_changed").count() == 1


# --- privacy and contract ------------------------------------------------------------------------


def test_nothing_private_leaks_into_logs_audit_events_or_errors(
    db, django_capture_on_commit_callbacks, caplog, monkeypatch
):
    make()
    make("taken@example.com")
    client = sign_in_browser()
    caplog.set_level(logging.DEBUG)
    captured = []
    real_emit = events.emit

    def spy(event):
        captured.append(repr(event))
        real_emit(event)

    monkeypatch.setattr("apps.identity.events.emit", spy)
    responses = [patch(client, {"full_name": "Janet Doe", "phone": "+923111234567"})]
    responses.append(request_change(client, password="wrong-password-1"))
    with django_capture_on_commit_callbacks(execute=True):
        responses.append(request_change(client, "taken@example.com"))
    with django_capture_on_commit_callbacks(execute=True):
        responses.append(request_change(client, NEW))
    token = token_in([m for m in django_mail.outbox if m.to == [NEW]][0])
    responses.append(post(Client(), CONFIRM, {"token": "x" * 40}))
    with django_capture_on_commit_callbacks(execute=True):
        responses.append(post(Client(), CONFIRM, {"token": token}))
    responses.append(post(Client(), CONFIRM, {"token": token}))
    assert [r.status_code for r in responses] == [200, 400, 202, 202, 400, 200, 409]
    wire = " ".join(r.content.decode() + str(dict(r.headers)) for r in responses[1:])
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    audit = " ".join(str(a.metadata) + a.reason for a in AuditRecord.objects.all())
    event_text = " ".join(captured)
    for secret in (
        token,
        verification.hash_token(token),
        NEW,
        NEW.lower(),
        EMAIL,
        "taken@example.com",
        "wrong-password-1",
        PASSWORD,
        "3111234567",
        PHONE,
    ):
        for where, text in (
            ("wire", wire),
            ("log", logged),
            ("audit", audit),
            ("event", event_text),
        ):
            assert secret not in text, (where, secret)
    # nobody but the intended recipients is mailed, and only fixed copy
    for message in django_mail.outbox:
        assert message.to[0] in {NEW, EMAIL, "taken@example.com"}
        assert "3111234567" not in message.body
    assert [m for m in django_mail.outbox if m.to == [EMAIL] and NEW in m.body] == []


def test_the_stored_token_is_a_hash_only(owner, requested):
    row = EmailChangeToken.objects.get()
    assert row.token_hash == verification.hash_token(requested) and requested != row.token_hash


def test_openapi_documents_the_routes(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    paths = schema["paths"]
    assert set(paths[PROFILE]) == {"get", "patch"}
    assert "202" in paths[CHANGE]["post"]["responses"]
    assert "Retry-After" in paths[CHANGE]["post"]["responses"]["429"]["headers"]
    assert "Retry-After" in paths[CONFIRM]["post"]["responses"]["429"]["headers"]
    components = schema["components"]["schemas"]
    assert set(components["Profile"]["properties"]) == {
        "full_name",
        "phone",
        "email",
        "pending_email",
    }
    assert set(components["EmailChangeRequest"]["properties"]) == {"new_email", "current_password"}
    assert set(components["EmailChangeConfirmRequest"]["properties"]) == {"token"}


# --- review follow-ups ---------------------------------------------------------------------------


@pytest.mark.parametrize("phone", ["", "   ", None])
def test_phone_removal_codes_and_messages(owner, phone):
    response = patch(owner, {"phone": phone})
    assert fields(response) == {"phone": ["This field is required."]}


def test_exact_codes_for_bad_name_and_phone_together(owner):
    response = patch(owner, {"full_name": "Call 03001234567", "phone": "12ab"})
    assert fields(response) == {
        "full_name": ["Enter a valid name without phone numbers or email addresses."],
        "phone": ["Enter a Pakistani mobile number or a valid international number."],
    }
    blank = patch(owner, {"full_name": "  "})
    assert fields(blank) == {"full_name": ["This field is required."]}


def test_wrong_types_are_invalid(owner):
    response = patch(owner, {"full_name": 5, "phone": 5})
    assert fields(response) == {
        "full_name": ["Enter a valid value."],
        "phone": ["Enter a valid value."],
    }


def test_unknown_field_code(owner):
    response = patch(owner, {"email": "x@example.com"})
    assert fields(response) == {
        "non_field_errors": ["The request contains fields that are not accepted."]
    }


def test_all_branches_incl_capped_are_alike_over_http_and_in_the_current_inbox(
    db, django_capture_on_commit_callbacks
):
    from apps.kernel import ratelimit

    make()
    make("taken@example.com")
    client = sign_in_browser()
    answers, notices = [], []

    def ask_branch(address):
        django_mail.outbox.clear()
        with django_capture_on_commit_callbacks(execute=True):
            answers.append(request_change(client, address))
        notices.append(sorted((m.subject, m.body) for m in django_mail.outbox if m.to == [EMAIL]))

    ask_branch("free@example.org")
    ask_branch("taken@example.com")
    ask_branch(EMAIL.upper())
    account = Account.objects.get(email=EMAIL)
    for _ in range(5):  # exhaust the silent per-account mail cap
        ratelimit.hit("verification_email", ratelimit.derive_key("account", str(account.id)))
    ask_branch("capped-free@example.org")
    ask_branch("taken@example.com")
    assert {a.status_code for a in answers} == {202}
    assert len({json.dumps(stable(a), sort_keys=True) for a in answers}) == 1
    assert len(notices[0]) == 1 and all(n == notices[0] for n in notices)


def test_the_request_budget_is_per_account(db):
    make()
    make("second@example.com")
    first, second = sign_in_browser(), sign_in_browser("second@example.com")
    for n in range(5):
        assert request_change(first, f"a{n}@example.org").status_code == 202
    assert request_change(first, "a9@example.org").status_code == 429
    assert request_change(second, "b0@example.org").status_code == 202


def test_password_change_over_http_voids_the_outstanding_link(owner, requested):
    changed = post(
        owner,
        PASSWORD_URL,
        {"current_password": PASSWORD, "new_password": OTHER_PASSWORD},
        HTTP_X_CSRFTOKEN=csrf(owner),
    )
    assert changed.status_code == 200
    response = post(Client(), CONFIRM, {"token": requested})
    assert (response.status_code, error_code(response)) == (409, "link_used")
    assert Account.objects.get().email == EMAIL


def test_canary_also_covers_case_variants(owner, requested, caplog):
    caplog.set_level(logging.DEBUG)
    post(Client(), CONFIRM, {"token": "unknown-token"})
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records).lower()
    audit = " ".join(str(a.metadata) + a.reason for a in AuditRecord.objects.all()).lower()
    for variant in (NEW.lower(), EMAIL.lower(), PHONE):
        assert variant not in logged and variant not in audit
