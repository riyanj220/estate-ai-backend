"""Administrator sign-in and the protected admin API over HTTP, with Django's CSRF checks on."""

import json
import logging
import uuid
from datetime import timedelta

import pytest
import yaml
from django.core import mail as django_mail
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.identity import services, subscribers, verification
from apps.identity.models import Account, PasswordResetToken, Session
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord, RateLimitCounter

ADMIN_SESSION = "/api/v1/admin-session"
SESSION = "/api/v1/session"
ADMIN_ME = "/api/v1/admin/me"
CSRF = "/api/v1/csrf"
CHANGE = "/api/v1/me/password"
EMAIL_CHANGE = "/api/v1/me/email-change"
RESET_REQUEST = "/api/v1/password-reset-requests"
RESET_CONFIRM = "/api/v1/password-resets/confirm"
ADMIN_EMAIL = "Root.Admin@Example.com"
PASSWORD = "correct horse battery staple 42"
NEW = "mango river lantern 77 night"
USER_PASSWORD = "correct horse battery staple"
SYSTEM = Actor.system("r")
COOKIE = "estate_session"
GENERIC = "The email or password is incorrect."


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


def make_admin(email=ADMIN_EMAIL):
    return services.provision_admin(SYSTEM, email, PASSWORD)


def make_user(email="jane@example.com", kind="individual"):
    view = services.create_account(SYSTEM, email, USER_PASSWORD, kind)
    services.mark_email_verified(SYSTEM, view.id)
    return view


def browser():
    return Client(enforce_csrf_checks=True)


def token_for(client):
    return client.get(CSRF).json()["csrf_token"]


def post(client, path, body, token=None, **extra):
    if token is not None:
        extra["HTTP_X_CSRFTOKEN"] = token
    return client.post(path, json.dumps(body), content_type="application/json", **extra)


def admin_sign_in(client=None, email=ADMIN_EMAIL, password=PASSWORD, **extra):
    client = client or browser()
    return post(
        client, ADMIN_SESSION, {"email": email, "password": password}, token_for(client), **extra
    )


def signed_in_admin(email=ADMIN_EMAIL):
    client = browser()
    assert admin_sign_in(client, email).status_code == 200
    return client


def signed_in_user(email="jane@example.com"):
    client = browser()
    response = post(client, SESSION, {"email": email, "password": USER_PASSWORD}, token_for(client))
    assert response.status_code == 200
    return client


def error_code(response):
    return response.json()["error"]["code"]


def stable(response):
    headers = {k: v for k, v in response.headers.items() if k not in {"X-Request-ID", "Date"}}
    body = response.json()
    body["error"].pop("request_id")
    return response.status_code, headers, body


# --- admin sign-in -------------------------------------------------------------------------------


def test_admin_sign_in_returns_the_summary_and_a_short_hardened_cookie(db):
    view = make_admin()
    client = browser()
    response = admin_sign_in(client, "  ROOT.admin@example.com ")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "id": str(view.id),
        "account_type": "admin",
        "state": "active",
        "email": ADMIN_EMAIL,
        "full_name": "",
        "agency_name": "",
        "capabilities": ["sign_in", "browse"],
        "suspension_reason": None,
    }
    cookie = client.cookies[COOKIE]
    assert cookie["httponly"] and cookie["samesite"] == "Lax" and cookie["path"] == "/"
    assert cookie["max-age"] == 8 * 3600
    row = Session.objects.get()
    assert cookie.value != row.token_hash and len(cookie.value) == 43
    assert cookie.value not in json.dumps(response.json())
    assert AuditRecord.objects.filter(action="admin.signed_in", target_id=view.id).count() == 1


def test_admin_sign_in_rotates_the_csrf_token_and_replaces_a_held_session(db):
    make_admin()
    client = browser()
    before = token_for(client)
    assert admin_sign_in(client).status_code == 200
    first = client.cookies[COOKIE].value
    assert token_for(client) != before
    assert admin_sign_in(client).status_code == 200
    assert client.cookies[COOKIE].value != first
    assert Session.objects.filter(ended_reason="replaced").count() == 1


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_non_administrator_credentials_get_the_generic_401(db, kind):
    make_user("person@example.com", kind)
    response = admin_sign_in(email="person@example.com", password=USER_PASSWORD)
    assert (response.status_code, error_code(response)) == (401, "invalid_credentials")
    assert response.json()["error"]["message"] == GENERIC
    assert not Session.objects.exists()


def test_administrator_credentials_are_refused_at_the_ordinary_sign_in(db):
    make_admin()
    client = browser()
    response = post(
        client, SESSION, {"email": ADMIN_EMAIL, "password": PASSWORD}, token_for(client)
    )
    assert (response.status_code, error_code(response)) == (401, "invalid_credentials")
    assert "estate_session" not in client.cookies and not Session.objects.exists()


def test_every_admin_sign_in_failure_is_byte_for_byte_alike(db):
    make_admin()
    make_user("person@example.com")
    make_admin("gone@example.com")
    services.disable_admin(SYSTEM, "gone@example.com")
    answers = [
        stable(admin_sign_in(password="wrong password 1")),
        stable(admin_sign_in(email="nobody@example.com")),
        stable(admin_sign_in(email="person@example.com", password=USER_PASSWORD)),
        stable(admin_sign_in(email="gone@example.com")),
    ]
    assert answers[0][0] == 401 and answers[0][2]["error"]["message"] == GENERIC
    assert all(a == answers[0] for a in answers)
    assert "Set-Cookie" not in answers[0][1] and "Retry-After" not in answers[0][1]
    # and identical to the ordinary door's refusal of an administrator
    client = browser()
    ordinary = post(
        client, SESSION, {"email": ADMIN_EMAIL, "password": PASSWORD}, token_for(client)
    )
    status, headers, body = stable(ordinary)
    headers.pop("Allow"), answers[0][1].pop("Allow")  # the route's own methods, not the outcome
    assert (status, headers, body) == answers[0]
    assert not Session.objects.exists()


def test_lockout_answers_identically_never_429(db):
    make_admin()
    first = stable(admin_sign_in(password="wrong password 1"))
    for _ in range(4):
        admin_sign_in(password="wrong password 1")
    sixth = admin_sign_in(password="wrong password 1")
    locked = admin_sign_in()  # the right password
    assert stable(sixth) == stable(locked) == first
    assert RateLimitCounter.objects.get(scope="admin_sign_in").count == 5
    assert not Session.objects.exists()


def test_the_admin_throttle_is_per_client_network(db):
    make_admin()
    for _ in range(6):
        admin_sign_in(password="wrong password 1", REMOTE_ADDR="198.51.100.1")
    assert admin_sign_in(REMOTE_ADDR="198.51.100.1").status_code == 401
    assert admin_sign_in(REMOTE_ADDR="198.51.100.2").status_code == 200


def test_admin_sign_in_needs_a_csrf_token(db):
    make_admin()
    client = browser()
    body = {"email": ADMIN_EMAIL, "password": PASSWORD}
    for response in (
        post(client, ADMIN_SESSION, body),
        post(client, ADMIN_SESSION, body, "wrong-token"),
        post(client, ADMIN_SESSION, body, token_for(browser())),
    ):
        assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert not Session.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": ADMIN_EMAIL},
        {"password": PASSWORD},
        {"email": 5, "password": PASSWORD},
        {"email": ADMIN_EMAIL, "password": None},
        {"email": ADMIN_EMAIL, "password": PASSWORD, "account_type": "admin"},
        [],
        "text",
    ],
)
def test_the_body_is_strict(db, body):
    make_admin()
    client = browser()
    response = post(client, ADMIN_SESSION, body, token_for(client))
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert not Session.objects.exists()


def test_admin_sign_in_is_post_only(db):
    client = browser()
    assert client.get(ADMIN_SESSION).status_code == 405
    assert client.delete(ADMIN_SESSION, HTTP_X_CSRFTOKEN=token_for(client)).status_code == 405


# --- /api/v1/admin/me ----------------------------------------------------------------------------


def test_admin_me_for_every_kind_of_caller(db):
    admin = make_admin()
    make_user("jane@example.com")
    make_user("agency@example.com", "agency")
    visitor = browser().get(ADMIN_ME)
    assert (visitor.status_code, error_code(visitor)) == (401, "unauthenticated")
    for email in ("jane@example.com", "agency@example.com"):
        response = signed_in_user(email).get(ADMIN_ME)
        assert (response.status_code, error_code(response)) == (404, "not_found")
    response = signed_in_admin().get(ADMIN_ME)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"id": str(admin.id), "account_type": "admin", "state": "active"}
    assert ADMIN_EMAIL not in response.content.decode()


def test_admin_me_refuses_a_disabled_administrator_at_once(db):
    make_admin()
    make_admin("second@example.com")
    client = signed_in_admin()
    services.disable_admin(SYSTEM, ADMIN_EMAIL)
    assert client.get(ADMIN_ME).status_code == 401


def test_admin_me_is_get_only(db):
    make_admin()
    client = signed_in_admin()
    assert client.post(ADMIN_ME, "{}", content_type="application/json").status_code in (403, 405)
    assert client.get(ADMIN_ME + "/extra").status_code == 404


# --- lifetimes over HTTP -------------------------------------------------------------------------


def test_admin_idle_expiry_is_30_minutes_but_an_ordinary_session_stays_valid(db):
    make_admin()
    make_user()
    admin, user = signed_in_admin(), signed_in_user()
    Session.objects.update(last_seen_at=timezone.now() - timedelta(minutes=31))
    assert admin.get(ADMIN_ME).status_code == 401
    assert user.get("/api/v1/me").status_code == 200


def test_admin_absolute_expiry_is_8_hours_even_with_activity(db):
    make_admin()
    client = signed_in_admin()
    Session.objects.filter(ended_at=None).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get(ADMIN_ME).status_code == 401


def test_sliding_refresh_leaves_last_seen_alone_within_five_minutes(db):
    make_admin()
    client = signed_in_admin()
    first = Session.objects.get().last_seen_at
    client.get(ADMIN_ME)
    assert Session.objects.get().last_seen_at == first
    Session.objects.update(last_seen_at=first - timedelta(minutes=6))
    client.get(ADMIN_ME)
    assert Session.objects.get().last_seen_at > first - timedelta(minutes=1)


# --- sign-out, password change, email change -----------------------------------------------------


def test_admin_signs_out_through_the_ordinary_route(db):
    make_admin()
    client = signed_in_admin()
    response = client.delete(SESSION, HTTP_X_CSRFTOKEN=token_for(client))
    assert response.status_code == 204
    assert Session.objects.filter(ended_reason="sign_out").count() == 1
    assert client.get(ADMIN_ME).status_code == 401


def test_admin_password_change_applies_the_strong_policy(db, django_capture_on_commit_callbacks):
    make_admin()
    other = signed_in_admin()
    client = signed_in_admin()
    for new, code in (
        ("short1", "admin_password_too_short"),
        ("nodigitsinthispassword", "admin_password_weak"),
        ("password1234", "password_too_common"),
        (PASSWORD, "password_same_as_current"),
    ):
        response = post(
            client,
            CHANGE,
            {"current_password": PASSWORD, "new_password": new},
            token_for(client),
        )
        assert response.status_code == 400
        assert list(response.json()["error"]["fields"]) == ["new_password"], code
    short = post(
        client,
        CHANGE,
        {"current_password": PASSWORD, "new_password": "short1"},
        token_for(client),
    )
    assert short.json()["error"]["fields"]["new_password"] == [
        "Administrator passwords need at least 12 characters."
    ]
    with django_capture_on_commit_callbacks(execute=True):
        ok = post(
            client,
            CHANGE,
            {"current_password": PASSWORD, "new_password": NEW},
            token_for(client),
        )
    assert (ok.status_code, ok.json()) == (200, {"status": "password_changed"})
    assert client.get(ADMIN_ME).status_code == 200
    assert other.get(ADMIN_ME).status_code == 401  # other sessions end
    assert admin_sign_in(password=NEW).status_code == 200


def test_admin_email_change_is_forbidden(db):
    make_admin()
    client = signed_in_admin()
    response = post(
        client,
        EMAIL_CHANGE,
        {"new_email": "new@example.com", "current_password": PASSWORD},
        token_for(client),
    )
    assert (response.status_code, error_code(response)) == (403, "forbidden")


# --- recovery only through the command -----------------------------------------------------------


def test_public_password_reset_is_inert_for_administrators(db, django_capture_on_commit_callbacks):
    make_admin()
    make_user()
    answers = []
    django_mail.outbox.clear()
    for email in (ADMIN_EMAIL, "jane@example.com", "ghost@example.com"):
        client = browser()
        with django_capture_on_commit_callbacks(execute=True):
            response = post(client, RESET_REQUEST, {"email": email})
        answers.append((response.status_code, response.json()))
    assert answers[0] == answers[1] == answers[2] == (202, {"status": "check_your_email"})
    recipients = [m.to for m in django_mail.outbox]
    assert recipients == [["jane@example.com"]]  # only the Individual was mailed
    admin = Account.objects.get(email=ADMIN_EMAIL)
    assert not PasswordResetToken.objects.filter(account=admin).exists()
    token = verification.new_token()
    PasswordResetToken.objects.create(
        account=admin,
        token_hash=verification.hash_token(token),
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    response = post(browser(), RESET_CONFIRM, {"token": token, "password": NEW})
    assert (response.status_code, error_code(response)) == (400, "link_invalid")


# --- contract ------------------------------------------------------------------------------------


def test_openapi_documents_the_admin_routes_and_error_messages(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    paths = schema["paths"]
    assert set(paths["/api/v1/admin-session"]) == {"post"}
    assert set(paths["/api/v1/admin/me"]) == {"get"}
    assert paths["/api/v1/admin-session"]["post"]["operationId"] == "admin_sign_in"
    me = schema["components"]["schemas"]["AdminMe"]
    assert set(me["properties"]) == {"id", "account_type", "state"}
    request = schema["components"]["schemas"]["SignInRequest"]
    assert set(request["properties"]) == {"email", "password"}


# --- privacy -------------------------------------------------------------------------------------


def test_canary_values_never_reach_logs_audit_or_error_bodies(db, caplog):
    caplog.set_level(logging.DEBUG)
    make_admin("canary.root@example.com")
    client = browser()
    ok = admin_sign_in(client, "canary.root@example.com")
    cookie = client.cookies[COOKIE].value
    bad = admin_sign_in(email="canary.root@example.com", password="canary-wrong-pass")
    ghost = admin_sign_in(email="ghost.canary@example.com")
    client.get(ADMIN_ME)
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    audited = " ".join(
        f"{r.action}{r.metadata}{r.reason}{r.request_id}" for r in AuditRecord.objects.all()
    )
    bodies = bad.content.decode() + ghost.content.decode()
    for canary in ("canary.root", "canary-wrong-pass", "ghost.canary", PASSWORD, cookie):
        assert canary not in logged and canary not in audited and canary not in bodies
    assert ok.status_code == 200 and uuid.UUID(ok.json()["id"])


def test_admin_me_shows_the_state_of_a_suspended_administrator(db):
    view = make_admin()
    client = signed_in_admin()
    Account.objects.filter(id=view.id).update(
        suspended_at=timezone.now(), suspension_reason="reason"
    )
    response = client.get(ADMIN_ME)
    assert (response.status_code, response.json()["state"]) == (200, "suspended")


@pytest.mark.parametrize(
    ("new", "message"),
    [
        ("short1", "Administrator passwords need at least 12 characters."),
        (
            "nodigitsinthispassword",
            "Administrator passwords need at least one letter and one digit.",
        ),
        (" padded password 42", "Administrator passwords must not start or end with whitespace."),
        ("password1234", "This password is too common. Choose a less guessable one."),
        (ADMIN_EMAIL.lower(), "The password must not be the same as the email address."),
        (PASSWORD, "Choose a password different from your current one."),
    ],
)
def test_admin_password_change_codes_and_messages_over_http(db, new, message):
    make_admin()
    client = signed_in_admin()
    response = post(
        client, CHANGE, {"current_password": PASSWORD, "new_password": new}, token_for(client)
    )
    assert response.status_code == 400
    assert response.json()["error"]["fields"] == {"new_password": [message]}


def test_the_email_change_routes_refuse_administrators(db):
    make_admin()
    client = signed_in_admin()
    response = post(
        client,
        EMAIL_CHANGE,
        {"new_email": "new@example.com", "current_password": PASSWORD},
        token_for(client),
    )
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    admin = Account.objects.get()
    from apps.identity.models import EmailChangeToken

    token = verification.new_token()
    EmailChangeToken.objects.create(
        account=admin,
        new_email="hijack@example.com",
        token_hash=verification.hash_token(token),
        link_issued=True,
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    confirm = post(browser(), "/api/v1/email-changes/confirm", {"token": token})
    assert (confirm.status_code, error_code(confirm)) == (400, "link_invalid")
    assert Account.objects.get().email == ADMIN_EMAIL


def test_the_email_verification_routes_leave_an_administrator_unchanged(
    db, django_capture_on_commit_callbacks
):
    make_admin()
    before = Account.objects.values_list("email", "email_verified_at", "password").get()
    with django_capture_on_commit_callbacks(execute=True):
        response = post(browser(), "/api/v1/email-verification-requests", {"email": ADMIN_EMAIL})
    assert response.status_code == 202
    after = Account.objects.values_list("email", "email_verified_at", "password").get()
    assert after == before
