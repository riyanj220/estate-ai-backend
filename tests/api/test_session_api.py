"""Sign-in, sign-out, me and the CSRF token over HTTP, with Django's CSRF checks switched on."""

import json
import logging
import uuid
from datetime import timedelta

import pytest
import yaml
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.identity import services, subscribers
from apps.identity.models import Account, Session
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord

SESSION = "/api/v1/session"
ADMIN_SESSION = "/api/v1/admin-session"
CSRF = "/api/v1/csrf"
ME = "/api/v1/me"
EMAIL = "Jane.Doe@Example.com"
PASSWORD = "correct horse battery staple"
SYSTEM = Actor.system("r")
ADMIN = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "r")
COOKIE = "estate_session"
GENERIC = "The email or password is incorrect."


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


def make(email=EMAIL, kind="individual", verified=True):
    view = services.create_account(SYSTEM, email, PASSWORD, kind)
    if verified:
        services.mark_email_verified(SYSTEM, view.id)
    return view


def browser():
    return Client(enforce_csrf_checks=True)


def token_for(client):
    response = client.get(CSRF)
    assert response.status_code == 200
    return response.json()["csrf_token"]


def post(client, body, token=None, **extra):
    if token is not None:
        extra["HTTP_X_CSRFTOKEN"] = token
    return client.post(SESSION, json.dumps(body), content_type="application/json", **extra)


def sign_in(client, email=EMAIL, password=PASSWORD):
    return post(client, {"email": email, "password": password}, token_for(client))


def signed_in(email=EMAIL):
    client = browser()
    assert sign_in(client, email).status_code == 200
    return client


def delete(client, token=None):
    extra = {"HTTP_X_CSRFTOKEN": token} if token else {}
    return client.delete(SESSION, **extra)


def error_code(response):
    return response.json()["error"]["code"]


def stable(response):
    headers = {k: v for k, v in response.headers.items() if k not in {"X-Request-ID", "Date"}}
    body = response.json()
    body["error"].pop("request_id")
    return response.status_code, headers, body


# --- csrf ----------------------------------------------------------------------------------------


def test_csrf_route_returns_a_token_sets_the_cookie_and_is_never_cached(db):
    client = browser()
    response = client.get(CSRF)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["csrf_token"]
    cookie = client.cookies["csrftoken"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"


def test_safe_methods_never_need_a_token(db):
    make()
    client = signed_in()
    assert client.get(ME).status_code == 200
    assert client.head(ME).status_code == 200


# --- sign-in -------------------------------------------------------------------------------------


def test_sign_in_returns_the_summary_and_a_hardened_cookie(db):
    account = make()
    client = browser()
    response = sign_in(client, "  JANE.DOE@example.com ")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "id": str(account.id),
        "account_type": "individual",
        "state": "active",
        "email": EMAIL,
        "full_name": "",
        "agency_name": "",
        "capabilities": [
            "sign_in",
            "browse",
            "view_drafts",
            "edit_drafts",
            "send_messages",
            "read_conversations",
            "report",
            "request_support",
            "request_deactivation",
        ],
        "suspension_reason": None,
    }
    cookie = client.cookies[COOKIE]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert cookie["path"] == "/" and cookie["max-age"] == 14 * 24 * 3600
    assert not cookie["secure"]  # test settings only; prod keeps SESSION_COOKIE_SECURE true
    assert cookie.value not in json.dumps(response.json())
    row = Session.objects.get()
    assert cookie.value != row.token_hash and len(cookie.value) == 43


def test_secure_flag_follows_the_setting(db, settings):
    make()
    settings.SESSION_COOKIE_SECURE = True
    client = browser()
    sign_in(client)
    assert client.cookies[COOKIE]["secure"]


@pytest.mark.parametrize(("verified", "state"), [(False, "email_unverified"), (True, "suspended")])
def test_unverified_and_suspended_accounts_sign_in(db, verified, state):
    view = make(verified=verified)
    if state == "suspended":
        services.suspend_account(ADMIN, view.id, "policy breach")
    response = sign_in(browser())
    assert (response.status_code, response.json()["state"]) == (200, state)
    assert "publish" not in response.json()["capabilities"]


def test_every_credential_failure_is_byte_for_byte_alike(db):
    make()
    deactivated = make("gone@example.com")
    services.deactivate(ADMIN, deactivated.id)
    answers = [
        stable(sign_in(browser(), password="wrong")),
        stable(sign_in(browser(), email="nobody@example.com")),
        stable(sign_in(browser(), email="gone@example.com")),  # right password, deactivated
    ]
    assert answers[0][0] == 401
    assert answers[0][2]["error"]["code"] == "invalid_credentials"
    assert answers[0][2]["error"]["message"] == GENERIC
    assert answers[0] == answers[1] == answers[2]
    assert "Set-Cookie" not in answers[0][1] and "Retry-After" not in answers[0][1]
    assert Session.objects.count() == 0


def test_lockout_answers_identically_never_429_and_the_right_password_fails(db):
    make()
    first = stable(sign_in(browser(), password="wrong"))
    for _ in range(4):
        sign_in(browser(), password="wrong")
    sixth = sign_in(browser(), password="wrong")
    locked = sign_in(browser())  # correct password
    assert stable(sixth) == stable(locked) == first
    assert Session.objects.count() == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": EMAIL},
        {"password": PASSWORD},
        {"email": 5, "password": PASSWORD},
        {"email": EMAIL, "password": None},
        {"email": EMAIL, "password": PASSWORD, "extra": 1},
        [],
        "text",
    ],
)
def test_missing_or_non_string_fields_are_validation_errors(db, body):
    make()
    client = browser()
    response = post(client, body, token_for(client))
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert Session.objects.count() == 0


def test_sign_in_needs_a_csrf_token_even_for_visitors(db):
    make()
    client = browser()
    body = {"email": EMAIL, "password": PASSWORD}
    for response in (
        post(client, body),  # no token at all
        post(client, body, "wrong-token"),
        post(client, body, token_for(browser())),  # a token from another browser
    ):
        assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert Session.objects.count() == 0


def test_csrf_checks_the_origin_against_the_frontend(db):
    make()
    client = browser()
    token = token_for(client)
    body = {"email": EMAIL, "password": PASSWORD}
    assert post(client, body, token, HTTP_ORIGIN="http://evil.example").status_code == 403
    assert post(client, body, token, HTTP_ORIGIN="http://localhost:3000").status_code == 200


def test_sign_in_while_holding_a_session_replaces_it(db):
    make()
    client = signed_in()
    old = client.cookies[COOKIE].value
    assert sign_in(client).status_code == 200
    assert client.cookies[COOKIE].value != old
    ended = Session.objects.get(ended_reason="replaced")
    assert ended.ended_at is not None
    stale = browser()
    stale.cookies[COOKIE] = old
    assert stale.get(ME).status_code == 401
    assert client.get(ME).status_code == 200


# --- me ------------------------------------------------------------------------------------------


def test_me_returns_the_summary_with_full_name_and_no_store(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        services.register_individual(
            Actor.visitor("r"), "Ayesha Khan", "ayesha@example.com", PASSWORD, "0300-1234567"
        )
    account = Account.objects.get()
    services.mark_email_verified(SYSTEM, account.id)
    client = signed_in("ayesha@example.com")
    response = client.get(ME)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["full_name"] == "Ayesha Khan" and body["email"] == "ayesha@example.com"
    assert set(body) == {
        "id",
        "account_type",
        "state",
        "email",
        "full_name",
        "agency_name",
        "capabilities",
        "suspension_reason",
    }
    assert "phone" not in json.dumps(body) and "password" not in json.dumps(body)


def test_me_for_a_verified_agency_shows_its_name_and_nothing_private(
    db, django_capture_on_commit_callbacks
):
    from apps.catalog.models import City

    City.objects.all().delete()
    city = City.objects.create(name="Karachi")
    with django_capture_on_commit_callbacks(execute=True):
        services.register_agency(
            Actor.visitor("r"),
            "Skyline Estates",
            "office@skyline.example",
            PASSWORD,
            "0321-5550123",
            "Suite 4, Harbour Tower, Karachi",
            city.id,
        )
    services.mark_email_verified(SYSTEM, Account.objects.get().id)
    response = signed_in("office@skyline.example").get(ME)
    body = response.json()
    assert (body["account_type"], body["agency_name"], body["full_name"]) == (
        "agency",
        "Skyline Estates",
        "",
    )
    text = json.dumps(body)
    assert "5550123" not in text and "Harbour" not in text and str(city.id) not in text


def test_me_for_an_admin_account(db):
    make("admin@example.com", kind="admin")
    client = browser()
    response = post(
        client,
        {"email": "admin@example.com", "password": PASSWORD},
        token_for(client),
    )
    assert response.status_code == 401  # the ordinary door refuses administrators
    client = browser()
    assert (
        client.post(
            ADMIN_SESSION,
            json.dumps({"email": "admin@example.com", "password": PASSWORD}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token_for(client),
        ).status_code
        == 200
    )
    body = client.get(ME).json()
    assert (body["account_type"], body["capabilities"]) == ("admin", ["sign_in", "browse"])


def test_me_without_a_valid_cookie_is_unauthenticated(db):
    make()
    assert error_code(browser().get(ME)) == "unauthenticated"
    for junk in ("", "x", "x" * 43, "x" * 500):
        client = browser()
        client.cookies[COOKIE] = junk
        response = client.get(ME)
        assert (response.status_code, error_code(response)) == (401, "unauthenticated")


def test_me_reflects_suspension_without_ending_the_session(db):
    view = make()
    client = signed_in()
    services.suspend_account(ADMIN, view.id, "policy breach")
    body = client.get(ME).json()
    assert (body["state"], body["capabilities"]) == (
        "suspended",
        [
            "sign_in",
            "browse",
            "view_drafts",
            "read_conversations",
            "request_support",
            "request_deactivation",
        ],
    )


def test_expired_and_ended_sessions_are_401(db):
    make()
    client = signed_in()
    Session.objects.update(last_seen_at=timezone.now() - timedelta(hours=25))
    assert client.get(ME).status_code == 401
    client = signed_in()
    Session.objects.filter(ended_at=None).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get(ME).status_code == 401
    client = signed_in()
    Session.objects.filter(ended_at=None).update(ended_at=timezone.now(), ended_reason="sign_out")
    assert client.get(ME).status_code == 401


def test_requests_refresh_last_seen_at_most_every_five_minutes(db):
    make()
    client = signed_in()
    row = Session.objects.get()
    first = row.last_seen_at
    client.get(ME)
    assert Session.objects.get().last_seen_at == first  # within 5 minutes
    Session.objects.update(last_seen_at=first - timedelta(minutes=6))
    client.get(ME)
    assert Session.objects.get().last_seen_at > first - timedelta(minutes=1)


def test_a_bad_cookie_does_not_break_public_routes(db):
    client = browser()
    client.cookies[COOKIE] = "junk"
    assert client.get("/api/v1/health").status_code == 200
    assert client.get(CSRF).status_code == 200


# --- sign-out ------------------------------------------------------------------------------------


def test_sign_out_ends_the_session_clears_the_cookie_and_emits(
    db, django_capture_on_commit_callbacks
):
    view = make()
    seen = []
    from apps.identity.events import AccountSessionEnded

    events.subscribe(AccountSessionEnded, seen.append)
    client = signed_in()
    other = signed_in()
    with django_capture_on_commit_callbacks(execute=True):
        response = delete(client, token_for(client))
    assert response.status_code == 204 and response.content == b""
    assert response.headers["Cache-Control"] == "no-store"
    cookie = client.cookies[COOKIE]
    assert cookie.value == "" and cookie["max-age"] == 0 and cookie["httponly"]
    assert cookie["path"] == "/" and cookie["samesite"] == "Lax" and not cookie["secure"]
    assert seen == [AccountSessionEnded(account_id=view.id, reason="sign_out")]
    assert Session.objects.filter(ended_reason="sign_out").count() == 1
    assert client.get(ME).status_code == 401
    assert other.get(ME).status_code == 200  # other sessions of the account stay valid


def test_sign_out_without_or_with_a_wrong_csrf_token_changes_nothing(db):
    make()
    client = signed_in()
    for response in (delete(client), delete(client, "wrong"), delete(client, token_for(browser()))):
        assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert Session.objects.filter(ended_at=None).count() == 1
    assert client.get(ME).status_code == 200


def test_sign_out_twice_and_without_a_session_is_401(db):
    make()
    client = signed_in()
    token = token_for(client)
    stale = browser()
    stale.cookies[COOKIE] = client.cookies[COOKIE].value
    assert delete(client, token).status_code == 204
    again = delete(stale, token_for(stale))
    assert (again.status_code, error_code(again)) == (401, "unauthenticated")
    visitor = browser()
    nothing = delete(visitor, token_for(visitor))
    assert (nothing.status_code, error_code(nothing)) == (401, "unauthenticated")


# --- account events -----------------------------------------------------------------------------


def test_deactivation_kills_every_session_at_once(db, django_capture_on_commit_callbacks):
    view = make()
    one, two = signed_in(), signed_in()
    with django_capture_on_commit_callbacks(execute=True):
        services.deactivate(ADMIN, view.id)
    assert one.get(ME).status_code == 401 and two.get(ME).status_code == 401
    assert Session.objects.filter(ended_at=None).count() == 0
    assert stable(sign_in(browser()))[2]["error"]["code"] == "invalid_credentials"


def test_deactivated_account_is_refused_even_if_the_event_never_ran(db):
    view = make()
    client = signed_in()
    services.deactivate(ADMIN, view.id)  # event not dispatched: no commit callbacks executed
    assert client.get(ME).status_code == 401
    assert delete(client, token_for(client)).status_code == 401


def test_password_changed_event_ends_all_sessions(db, django_capture_on_commit_callbacks):
    from apps.identity.events import emit_session_ended

    view = make()
    one, two = signed_in(), signed_in()
    with django_capture_on_commit_callbacks(execute=True):
        emit_session_ended(view.id, "password_changed")
    assert one.get(ME).status_code == 401 and two.get(ME).status_code == 401


# --- existing behaviour and routes --------------------------------------------------------------


def test_public_post_routes_still_need_no_csrf_token_or_cookie(db):
    client = browser()
    response = client.post(
        "/api/v1/email-verification-requests",
        json.dumps({"email": "x@example.com"}),
        content_type="application/json",
    )
    assert response.status_code == 202


def test_a_signed_in_caller_is_not_a_visitor_for_registration(db):
    make()
    client = signed_in()
    # registration authenticates nobody (authentication_classes = []), so the cookie is ignored
    response = client.post(
        "/api/v1/registrations/individuals", "{}", content_type="application/json"
    )
    assert response.status_code == 400


def test_audit_table_is_untouched_by_sign_in_and_out(db):
    make()
    before = AuditRecord.objects.count()
    client = signed_in()
    client.get(ME)
    delete(client, token_for(client))
    assert AuditRecord.objects.count() == before


# --- privacy -------------------------------------------------------------------------------------


def test_canary_values_never_reach_logs_or_error_bodies(db, caplog):
    caplog.set_level(logging.DEBUG)
    make("canary.person@example.com")
    client = browser()
    ok = sign_in(client, "canary.person@example.com")
    cookie = client.cookies[COOKIE].value
    bad = sign_in(browser(), "canary.person@example.com", "canary-wrong-pass")
    ghost = sign_in(browser(), "ghost.canary@example.com")
    delete(client, token_for(client))
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for canary in ("canary.person", "canary-wrong-pass", "ghost.canary", PASSWORD, cookie):
        assert canary not in logged
        assert canary not in bad.content.decode() + ghost.content.decode()
    assert ok.status_code == 200


# --- contract ------------------------------------------------------------------------------------


def test_openapi_documents_the_session_routes(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    paths = schema["paths"]
    assert set(paths["/api/v1/session"]) == {"post", "delete"}
    assert "get" in paths["/api/v1/me"] and "get" in paths["/api/v1/csrf"]
    assert "invalid_credentials" in schema["components"]["schemas"]["ErrorCode"]["enum"]
    request = schema["components"]["schemas"]["SignInRequest"]
    assert set(request["properties"]) == {"email", "password"}
    assert "204" in paths["/api/v1/session"]["delete"]["responses"]
    summary = schema["components"]["schemas"]["ActorSummary"]
    assert set(summary["properties"]) == {
        "id",
        "account_type",
        "state",
        "email",
        "full_name",
        "agency_name",
        "capabilities",
        "suspension_reason",
    }


# --- hardening -----------------------------------------------------------------------------------


def test_lockout_is_per_client_network(db):
    make()
    body = {"email": EMAIL, "password": "wrong"}
    for _ in range(6):
        client = browser()
        post(client, body, token_for(client), REMOTE_ADDR="198.51.100.1")
    locked = browser()
    right = {"email": EMAIL, "password": PASSWORD}
    assert post(locked, right, token_for(locked), REMOTE_ADDR="198.51.100.1").status_code == 401
    other = browser()
    assert post(other, right, token_for(other), REMOTE_ADDR="198.51.100.2").status_code == 200


def test_sign_out_without_a_live_session_is_401_and_clears_the_cookie(db):
    make()
    client = signed_in()
    stale = browser()
    stale.cookies[COOKIE] = client.cookies[COOKIE].value
    assert delete(client, token_for(client)).status_code == 204
    response = delete(stale, token_for(stale))
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")
    cookie = response.cookies[COOKIE]
    assert cookie.value == "" and cookie["max-age"] == 0 and cookie["path"] == "/"
    assert response.headers["Cache-Control"] == "no-store"
    visitor = browser()
    assert delete(visitor, token_for(visitor)).cookies[COOKIE]["max-age"] == 0


def test_csrf_token_is_rotated_by_sign_in_and_sign_out(db):
    make()
    client = browser()
    before = token_for(client)
    assert post(client, {"email": EMAIL, "password": PASSWORD}, before).status_code == 200
    assert delete(client, before).status_code == 403  # the pre-login token is dead
    after_in = token_for(client)
    assert delete(client, after_in).status_code == 204
    again = browser()
    again.cookies = client.cookies  # the cookie after sign-out holds a rotated secret
    assert post(again, {"email": EMAIL, "password": PASSWORD}, after_in).status_code == 403
    assert post(again, {"email": EMAIL, "password": PASSWORD}, token_for(again)).status_code == 200


@pytest.mark.parametrize(
    ("url", "body", "expected"),
    [
        ("/api/v1/registrations/individuals", {}, 400),
        ("/api/v1/email-verifications/confirm", {"token": "x"}, 400),
        ("/api/v1/email-verification-requests", {"email": "a@example.com"}, 202),
    ],
)
def test_public_posts_with_a_session_cookie_but_no_csrf_token_are_not_403(db, url, body, expected):
    make()
    client = signed_in()
    response = client.post(url, json.dumps(body), content_type="application/json")
    assert response.status_code == expected


def test_a_failed_last_seen_touch_never_fails_the_request(db, monkeypatch, caplog):
    from django.db import DatabaseError

    make()
    client = signed_in()
    Session.objects.update(last_seen_at=timezone.now() - timedelta(minutes=10))

    def boom(*args):
        raise DatabaseError("secret detail")

    monkeypatch.setattr(services, "refresh_session", boom)
    with caplog.at_level(logging.WARNING, logger="estate.session"):
        assert client.get(ME).status_code == 200
    assert caplog.records[-1].exception_class == "DatabaseError"
    assert "secret detail" not in caplog.text


def test_run_retention_purges_old_sessions(db):
    import io

    view = make()
    now = timezone.now()
    for name, ended in (("old", now - timedelta(days=9)), ("fresh", now)):
        Session.objects.create(
            account_id=view.id,
            token_hash=name * 8,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=14),
            ended_at=ended,
            ended_reason="sign_out",
        )
    out = io.StringIO()
    call_command("run_retention", "--dry-run", stdout=out)
    assert "identity.sessions: would delete 1" in out.getvalue()
    out = io.StringIO()
    call_command("run_retention", stdout=out)
    assert "identity.sessions: deleted 1" in out.getvalue()
    assert list(Session.objects.values_list("token_hash", flat=True)) == ["fresh" * 8]
