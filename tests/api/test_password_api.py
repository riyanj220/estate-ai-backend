"""Password reset and change over HTTP (Story 1.18), with Django's CSRF checks switched on."""

import json
import logging
import re
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

REQUEST = "/api/v1/password-reset-requests"
CONFIRM = "/api/v1/password-resets/confirm"
CHANGE = "/api/v1/me/password"
SESSION = "/api/v1/session"
CSRF = "/api/v1/csrf"
ME = "/api/v1/me"
EMAIL = "Jane.Doe@Example.com"
PASSWORD = "correct horse battery staple"
NEW = "mango-river-lantern-42"
SYSTEM = Actor.system("r")
CHECK = {"status": "check_your_email"}
COOKIE = "estate_session"


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


def make(email=EMAIL, verified=True):
    view = services.create_account(SYSTEM, email, PASSWORD, "individual")
    if verified:
        services.mark_email_verified(SYSTEM, view.id)
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
    assert response.status_code == 200
    return client


def change(client, body, token=True):
    extra = {"HTTP_X_CSRFTOKEN": csrf(client)} if token else {}
    return post(client, CHANGE, body, **extra)


def last_token():
    return re.search(r"token=([A-Za-z0-9_-]+)", django_mail.outbox[-1].body).group(1)


@pytest.fixture
def asked(client, db, django_capture_on_commit_callbacks):
    make()
    with django_capture_on_commit_callbacks(execute=True):
        assert post(client, REQUEST, {"email": EMAIL}).status_code == 202
    return last_token()


# --- request -------------------------------------------------------------------------------------


def test_request_answers_202_and_mails_the_link(client, asked):
    assert len(django_mail.outbox) == 1
    assert PasswordResetToken.objects.get().used_at is None
    assert "/reset-password?token=" + asked in django_mail.outbox[0].body


def test_every_branch_answers_identically(client, db, django_capture_on_commit_callbacks):
    answers = []
    n = iter(range(1, 60))  # a fresh client address each time keeps the client limit out of it
    make()
    make("unverified@example.com", verified=False)
    make("suspended@example.com")
    make("gone@example.com")
    Account.objects.filter(email="suspended@example.com").update(
        suspended_at=timezone.now(), suspension_reason="x"
    )
    Account.objects.filter(email="gone@example.com").update(deactivated_at=timezone.now())

    def ask(email):
        answers.append(post(client, REQUEST, {"email": email}, REMOTE_ADDR=f"10.1.0.{next(n)}"))

    with django_capture_on_commit_callbacks(execute=True):
        for email in (
            "nobody@example.com",
            EMAIL,
            "unverified@example.com",
            "suspended@example.com",
            "gone@example.com",
        ):
            ask(email)
        for _ in range(7):  # over the account cap
            ask(EMAIL)
    assert answers[0].json() == CHECK
    assert {(a.status_code, a.content) for a in answers} == {(202, answers[0].content)}
    assert len({tuple(sorted(stable(a)[1].items())) for a in answers}) == 1
    assert len(django_mail.outbox) == 3 + 4  # 5 per account in total, minus the first one's 1


def test_account_cap_is_silent_across_clients(client, asked, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = post(client, REQUEST, {"email": EMAIL}, REMOTE_ADDR="10.0.0.1")
        for n in range(8):
            capped = post(client, REQUEST, {"email": EMAIL}, REMOTE_ADDR=f"10.0.1.{n + 1}")
            assert (capped.status_code, capped.content) == (202, first.content)
    assert len(django_mail.outbox) == 5  # never a sixth


def test_sixth_request_from_one_client_is_429_for_any_address(client, db):
    make()
    for address in (EMAIL, "b@example.com", "nobody@example.com", "c@x.co", "d@x.co"):
        assert post(client, REQUEST, {"email": address}).status_code == 202
    answers = [post(client, REQUEST, {"email": a}) for a in (EMAIL, "zzz@example.com")]
    for response in answers:
        assert (response.status_code, error_code(response)) == (429, "throttled")
        assert int(response.headers["Retry-After"]) > 0
    assert stable(answers[0])[1:] == stable(answers[1])[1:]
    assert post(client, REQUEST, {"email": EMAIL}, REMOTE_ADDR="10.9.8.7").status_code == 202


@pytest.mark.parametrize("body", [{}, {"email": "a@b.co", "x": 1}, {"email": 5}, [], "text"])
def test_request_wrong_body_shape(client, db, body):
    response = post(client, REQUEST, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


def test_request_ignores_a_session_cookie_and_needs_no_csrf(db):
    make()
    client = sign_in_browser()
    assert post(client, REQUEST, {"email": EMAIL}).status_code == 202  # no CSRF header


def test_get_never_requests_or_consumes(client, asked):
    assert client.get(REQUEST).status_code == 405
    assert client.get(CONFIRM + "?token=" + asked).status_code == 405
    assert PasswordResetToken.objects.get().used_at is None


# --- confirm -------------------------------------------------------------------------------------


def test_confirm_resets_and_ends_every_session(client, asked, django_capture_on_commit_callbacks):
    first, second = sign_in_browser(), sign_in_browser()
    django_mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        response = post(client, CONFIRM, {"token": asked, "password": NEW})
    assert (response.status_code, response.json()) == (200, {"status": "password_reset"})
    assert response.headers["Cache-Control"] == "no-store"
    assert first.get(ME).status_code == 401 and second.get(ME).status_code == 401
    assert Session.objects.filter(ended_at__isnull=True).count() == 0
    assert AuditRecord.objects.filter(action="account.password_reset").count() == 1
    assert [m.subject for m in django_mail.outbox] == ["Your EstateAI password was changed"]
    # old password refused with the generic answer, new password works
    fresh = Client(enforce_csrf_checks=True)
    old = post(fresh, SESSION, {"email": EMAIL, "password": PASSWORD}, HTTP_X_CSRFTOKEN=csrf(fresh))
    assert (old.status_code, error_code(old)) == (401, "invalid_credentials")
    assert sign_in_browser(password=NEW).get(ME).status_code == 200
    again = post(client, CONFIRM, {"token": asked, "password": NEW})
    assert (again.status_code, error_code(again)) == (409, "link_used")


def test_used_expired_and_superseded_links(client, asked, django_capture_on_commit_callbacks):
    first = asked
    with django_capture_on_commit_callbacks(execute=True):
        post(client, REQUEST, {"email": EMAIL})
    second = last_token()
    superseded = post(client, CONFIRM, {"token": first, "password": NEW})
    assert (superseded.status_code, error_code(superseded)) == (409, "link_used")
    PasswordResetToken.objects.filter(used_at__isnull=True).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    expired = post(client, CONFIRM, {"token": second, "password": NEW})
    assert (expired.status_code, error_code(expired)) == (410, "link_expired")
    assert check_unchanged()


def check_unchanged():
    from django.contrib.auth.hashers import check_password

    return check_password(PASSWORD, Account.objects.get().password)


@pytest.mark.parametrize("bad", ["", "unknown-token", "a" * 201, 5, None, ["x"], {"a": 1}])
def test_bad_tokens_are_link_invalid_and_never_echoed(client, asked, bad):
    response = post(client, CONFIRM, {"token": bad, "password": NEW})
    assert (response.status_code, error_code(response)) == (400, "link_invalid")
    if isinstance(bad, str) and bad:
        assert bad not in response.content.decode() + str(dict(response.headers))


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short", "password_too_short"),
        ("x" * 1025, "password_too_long"),
        ("jane.doe@example.com", "password_matches_email"),
        ("password123", "password_too_common"),
        ("", "required"),
        (None, "required"),
        (5, "invalid"),
    ],
)
def test_bad_password_is_a_field_error_and_the_link_survives(client, asked, password, code):
    response = post(client, CONFIRM, {"token": asked, "password": password})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(fields(response)) == ["password"]
    assert PasswordResetToken.objects.get().used_at is None
    assert post(client, CONFIRM, {"token": asked, "password": NEW}).status_code == 200


def test_missing_password_key_is_a_field_error_only_for_a_valid_link(client, asked):
    response = post(client, CONFIRM, {"token": asked})
    assert (response.status_code, list(fields(response))) == (400, ["password"])
    response = post(client, CONFIRM, {"token": "unknown-token"})
    assert error_code(response) == "link_invalid"


@pytest.mark.parametrize(
    "body",
    [{}, {"password": "x"}, {"token": "x", "password": "y", "extra": 1}, [], "text", 5],
)
def test_wrong_body_shape_is_a_validation_error(client, asked, body):
    response = post(client, CONFIRM, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


def test_deactivated_accounts_link_is_invalid(client, asked):
    Account.objects.update(deactivated_at=timezone.now())
    response = post(client, CONFIRM, {"token": asked, "password": NEW})
    assert (response.status_code, error_code(response)) == (400, "link_invalid")


def test_confirm_needs_no_csrf_token(asked):
    strict = Client(enforce_csrf_checks=True)
    assert post(strict, CONFIRM, {"token": asked, "password": NEW}).status_code == 200


# --- change --------------------------------------------------------------------------------------


def test_change_ends_other_sessions_keeps_the_current_one(db, django_capture_on_commit_callbacks):
    make()
    mine, other_a, other_b = sign_in_browser(), sign_in_browser(), sign_in_browser()
    django_mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        response = change(mine, {"current_password": PASSWORD, "new_password": NEW})
    assert (response.status_code, response.json()) == (200, {"status": "password_changed"})
    assert response.headers["Cache-Control"] == "no-store"
    assert mine.get(ME).status_code == 200
    assert other_a.get(ME).status_code == 401 and other_b.get(ME).status_code == 401
    assert Session.objects.filter(ended_at__isnull=True).count() == 1
    assert AuditRecord.objects.filter(action="account.password_changed").count() == 1
    assert [m.subject for m in django_mail.outbox] == ["Your EstateAI password was changed"]
    assert sign_in_browser(password=NEW).get(ME).status_code == 200


def test_wrong_current_password(db):
    make()
    client = sign_in_browser()
    response = change(client, {"current_password": "wrong", "new_password": NEW})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert fields(response) == {"current_password": ["The current password is incorrect."]}
    assert check_unchanged()


def test_throttle_never_answers_429_and_hides_the_right_password(db):
    make()
    client = sign_in_browser()
    answers = [
        stable(change(client, {"current_password": "wrong", "new_password": NEW})) for _ in range(6)
    ]
    locked = stable(change(client, {"current_password": PASSWORD, "new_password": NEW}))
    assert len({json.dumps(a, sort_keys=True) for a in answers + [locked]}) == 1
    assert locked[0] == 400 and "Retry-After" not in locked[1]
    assert RateLimitCounter.objects.get(scope="password_change_failed").count == 5
    assert check_unchanged()


def test_same_and_weak_new_password(db):
    make()
    client = sign_in_browser()
    same = change(client, {"current_password": PASSWORD, "new_password": PASSWORD})
    assert fields(same) == {"new_password": ["Choose a password different from your current one."]}
    weak = change(client, {"current_password": PASSWORD, "new_password": "short"})
    assert (weak.status_code, list(fields(weak))) == (400, ["new_password"])
    assert check_unchanged()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"current_password": PASSWORD},
        {"new_password": NEW},
        {"current_password": 5, "new_password": NEW},
        {"current_password": PASSWORD, "new_password": None},
        {"current_password": PASSWORD, "new_password": NEW, "extra": 1},
        [],
        "text",
    ],
)
def test_wrong_body_shape(db, body):
    make()
    client = sign_in_browser()
    response = change(client, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert RateLimitCounter.objects.filter(scope="password_change_failed").count() == 0


def test_not_signed_in_is_401_and_missing_csrf_is_403(db):
    make()
    anonymous = Client(enforce_csrf_checks=True)
    response = change(anonymous, {"current_password": PASSWORD, "new_password": NEW})
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")
    client = sign_in_browser()
    response = change(client, {"current_password": PASSWORD, "new_password": NEW}, token=False)
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert check_unchanged()


def test_ended_session_cannot_change(db):
    make()
    client = sign_in_browser()
    token = csrf(client)
    Session.objects.update(ended_at=timezone.now(), ended_reason="sign_out")
    response = post(
        client,
        CHANGE,
        {"current_password": PASSWORD, "new_password": NEW},
        HTTP_X_CSRFTOKEN=token,
    )
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")


def test_visitor_header_cannot_stand_in_for_a_session(client, db):
    make()
    response = post(client, CHANGE, {"current_password": PASSWORD, "new_password": NEW})
    assert response.status_code in (401, 403)
    assert check_unchanged()


# --- privacy and contract ------------------------------------------------------------------------


def test_nothing_secret_is_echoed_or_logged_on_any_path(
    client, db, django_capture_on_commit_callbacks, caplog
):
    make()
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        responses = [post(client, REQUEST, {"email": EMAIL})]
    token = last_token()
    responses.append(post(client, CONFIRM, {"token": token, "password": "short"}))
    responses.append(post(client, CONFIRM, {"token": "x" * 40, "password": NEW}))
    with django_capture_on_commit_callbacks(execute=True):
        responses.append(post(client, CONFIRM, {"token": token, "password": NEW}))
    responses.append(post(client, CONFIRM, {"token": token, "password": NEW}))
    browser = sign_in_browser(password=NEW)
    responses.append(change(browser, {"current_password": "wrong-password-1", "new_password": NEW}))
    responses.append(change(browser, {"current_password": NEW, "new_password": NEW}))
    with django_capture_on_commit_callbacks(execute=True):
        responses.append(
            change(browser, {"current_password": NEW, "new_password": "other-long-pass-9"})
        )
    assert [r.status_code for r in responses] == [202, 400, 400, 200, 409, 400, 400, 200]
    wire = " ".join(r.content.decode() + str(dict(r.headers)) for r in responses)
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    audit = " ".join(str(a.metadata) + a.reason for a in AuditRecord.objects.all())
    for secret in (
        token,
        verification.hash_token(token),
        EMAIL,
        EMAIL.lower(),
        "wrong-password-1",
        "other-long-pass-9",
        NEW,
        PASSWORD,
    ):
        assert secret not in wire, secret
        assert secret not in logged, secret
        assert secret not in audit, secret
    assert "short" not in wire.replace("password_too_short", "")


def test_the_stored_token_is_a_hash_only(client, asked):
    row = PasswordResetToken.objects.get()
    assert row.token_hash == verification.hash_token(asked) and asked != row.token_hash


def test_handler_failure_does_not_change_the_response(
    client, db, django_capture_on_commit_callbacks, monkeypatch, caplog
):
    make()
    monkeypatch.setattr(
        "apps.identity.notices.send_password_reset", lambda *a: (_ for _ in ()).throw(OSError("x"))
    )
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        response = post(client, REQUEST, {"email": EMAIL})
    assert (response.status_code, response.json()) == (202, CHECK)
    assert django_mail.outbox == [] and PasswordResetToken.objects.count() == 0
    assert "OSError" in " ".join(str(r.__dict__) for r in caplog.records)
    assert RateLimitCounter.objects.exists()


def test_openapi_documents_the_three_routes(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    request = schema["paths"][REQUEST]["post"]
    confirm = schema["paths"][CONFIRM]["post"]
    changed = schema["paths"][CHANGE]["post"]
    assert "202" in request["responses"] and "Retry-After" in request["responses"]["429"]["headers"]
    assert "200" in confirm["responses"] and "200" in changed["responses"]
    components = schema["components"]["schemas"]
    assert set(components["PasswordResetRequestRequest"]["properties"]) == {"email"}
    assert set(components["PasswordResetConfirmRequest"]["properties"]) == {"token", "password"}
    assert set(components["ChangePasswordRequest"]["properties"]) == {
        "current_password",
        "new_password",
    }


# --- review follow-ups ---------------------------------------------------------------------------


@pytest.mark.parametrize("password", [None, 5, ["x"], {"a": 1}])
def test_missing_or_non_string_password_on_a_valid_link_keeps_the_token(client, asked, password):
    body = {"token": asked} if password is None else {"token": asked, "password": password}
    response = post(client, CONFIRM, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(fields(response)) == ["password"]
    assert PasswordResetToken.objects.get().used_at is None
    assert post(client, CONFIRM, {"token": asked, "password": NEW}).status_code == 200


def test_confirm_is_limited_per_client_with_retry_after(client, asked):
    for _ in range(20):
        assert post(client, CONFIRM, {"token": "unknown-token", "password": NEW}).status_code == 400
    response = post(client, CONFIRM, {"token": asked, "password": NEW})
    assert (response.status_code, error_code(response)) == (429, "throttled")
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["Cache-Control"] == "no-store"
    assert PasswordResetToken.objects.get().used_at is None
    other = post(client, CONFIRM, {"token": asked, "password": NEW}, REMOTE_ADDR="10.7.7.7")
    assert other.status_code == 200


def test_change_voids_an_outstanding_reset_link(client, asked):
    browser = sign_in_browser()
    assert change(browser, {"current_password": PASSWORD, "new_password": NEW}).status_code == 200
    response = post(client, CONFIRM, {"token": asked, "password": "another-long-phrase-77"})
    assert (response.status_code, error_code(response)) == (409, "link_used")


def test_unusable_password_account_request_sends_nothing(
    client, db, django_capture_on_commit_callbacks
):
    from django.contrib.auth.hashers import make_password

    make()
    Account.objects.update(password=make_password(None))
    with django_capture_on_commit_callbacks(execute=True):
        response = post(client, REQUEST, {"email": EMAIL})
    assert (response.status_code, response.json()) == (202, CHECK)
    assert django_mail.outbox == [] and PasswordResetToken.objects.count() == 0


def test_unknown_address_writes_the_same_kind_of_counter(client, db):
    assert post(client, REQUEST, {"email": "nobody@example.com"}).status_code == 202
    assert RateLimitCounter.objects.filter(scope="password_reset_email").count() == 1


def test_error_answers_of_confirm_and_change_are_no_store(client, asked, db):
    answers = [
        post(client, CONFIRM, {"token": "unknown-token", "password": NEW}),  # 400
        post(client, CONFIRM, {"token": asked, "password": "short"}),  # 400 validation
    ]
    PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    answers.append(post(client, CONFIRM, {"token": asked, "password": NEW}))  # 410
    PasswordResetToken.objects.update(expires_at=timezone.now() + timedelta(hours=1))
    assert post(client, CONFIRM, {"token": asked, "password": NEW}).status_code == 200
    answers.append(post(client, CONFIRM, {"token": asked, "password": NEW}))  # 409
    browser = sign_in_browser(password=NEW)
    answers.append(change(browser, {"current_password": "wrong", "new_password": NEW}))  # 400
    answers.append(change(browser, {}, token=False))  # 403
    answers.append(change(Client(enforce_csrf_checks=True), {}))  # 401
    assert [a.status_code for a in answers] == [400, 400, 410, 409, 400, 403, 401]
    assert {a.headers["Cache-Control"] for a in answers} == {"no-store"}
