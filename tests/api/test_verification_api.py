import json
import logging
import re
from datetime import timedelta

import pytest
import yaml
from django.core import mail as django_mail
from django.core.management import call_command
from django.utils import timezone

from apps.identity import services, subscribers, verification
from apps.identity.models import Account, EmailVerificationToken
from apps.identity.state import AccountState
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord, RateLimitCounter

CONFIRM = "/api/v1/email-verifications/confirm"
RESEND = "/api/v1/email-verification-requests"
REGISTER = "/api/v1/registrations/individuals"
BODY = {
    "full_name": "Ayesha Khan",
    "email": "Ayesha.Khan@Example.com",
    "password": "mango-river-lantern-42",
    "phone": "0300-1234567",
}
CHECK = {"status": "check_your_email"}


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


def post(client, url, body, **extra):
    return client.post(url, json.dumps(body), content_type="application/json", **extra)


@pytest.fixture
def registered(client, db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert post(client, REGISTER, BODY).status_code == 202
    return Account.objects.get()


def last_token():
    return re.search(r"token=([A-Za-z0-9_-]+)", django_mail.outbox[-1].body).group(1)


def error_code(response):
    return response.json()["error"]["code"]


def stable_headers(response):
    return {k: v for k, v in response.headers.items() if k not in {"X-Request-ID", "Date"}}


# --- confirm -----------------------------------------------------------------------------------


def test_registration_email_link_confirms_the_account(client, registered):
    response = post(client, CONFIRM, {"token": last_token()})
    assert (response.status_code, response.json()) == (200, {"status": "verified"})
    assert Account.objects.get().state is AccountState.ACTIVE
    assert AuditRecord.objects.filter(action="account.email_verified").count() == 1


def test_get_never_consumes_the_link(client, registered):
    assert client.get(CONFIRM + "?token=" + last_token()).status_code == 405
    assert Account.objects.get().state is AccountState.EMAIL_UNVERIFIED


def test_used_expired_and_superseded_links(client, registered, django_capture_on_commit_callbacks):
    first = last_token()
    with django_capture_on_commit_callbacks(execute=True):
        post(client, RESEND, {"email": BODY["email"]})
    second = last_token()
    superseded = post(client, CONFIRM, {"token": first})
    assert (superseded.status_code, error_code(superseded)) == (409, "link_used")
    assert post(client, CONFIRM, {"token": second}).status_code == 200
    again = post(client, CONFIRM, {"token": second})
    assert (again.status_code, error_code(again)) == (409, "link_used")
    EmailVerificationToken.objects.update(used_at=None, expires_at=timezone.now() - timedelta(1))
    expired = post(client, CONFIRM, {"token": second})
    assert (expired.status_code, error_code(expired)) == (410, "link_expired")


@pytest.mark.parametrize("bad", ["", "unknown-token", "a" * 201, 5, None, ["x"], {"a": 1}])
def test_bad_tokens_are_link_invalid_and_never_echoed(client, registered, bad):
    response = post(client, CONFIRM, {"token": bad})
    assert (response.status_code, error_code(response)) == (400, "link_invalid")
    if isinstance(bad, str) and bad:
        assert bad not in response.content.decode() + str(dict(response.headers))


@pytest.mark.parametrize("body", [{}, {"token": "x", "extra": 1}, [], "text", 5])
def test_wrong_body_shape_is_a_validation_error(client, registered, body):
    response = post(client, CONFIRM, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


# --- resend ------------------------------------------------------------------------------------


def test_every_branch_answers_identically(client, registered, django_capture_on_commit_callbacks):
    answers = []
    n = iter(range(1, 50))  # a fresh client address each time keeps the client limit out of it

    def ask(email):
        answers.append(post(client, RESEND, {"email": email}, REMOTE_ADDR=f"10.1.0.{next(n)}"))

    with django_capture_on_commit_callbacks(execute=True):
        ask("nobody@example.com")  # unknown
        ask(BODY["email"])  # unverified
        ask(BODY["email"].upper())
        services.mark_email_verified(Actor.system(), Account.objects.get().id)
        ask(BODY["email"])  # verified
        for _ in range(6):  # over the account cap
            ask(BODY["email"])
        Account.objects.update(suspended_at=timezone.now(), suspension_reason="x")
        ask(BODY["email"])  # suspended
        Account.objects.update(suspended_at=None, suspension_reason="")
        Account.objects.update(deactivated_at=timezone.now())
        ask(BODY["email"])  # deactivated
    assert answers[0].json() == CHECK
    assert {(a.status_code, a.content) for a in answers} == {(202, answers[0].content)}
    assert len({tuple(sorted(stable_headers(a).items())) for a in answers}) == 1


def test_capped_account_looks_the_same(client, registered, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = post(client, RESEND, {"email": BODY["email"]})
    # Different clients so the per-client limit is not what stops the sixth send.
    sent = len(django_mail.outbox)
    with django_capture_on_commit_callbacks(execute=True):
        for n in range(8):
            capped = post(client, RESEND, {"email": BODY["email"]}, REMOTE_ADDR=f"10.0.0.{n + 1}")
            assert (capped.status_code, capped.content) == (202, first.content)
            assert stable_headers(capped) == stable_headers(first)
    assert len(django_mail.outbox) - sent == 4  # 1 + 4 = the 5-per-hour account cap


def test_sixth_request_from_one_client_is_throttled_for_any_address(client, db):
    for address in ("a@example.com", "b@example.com", "nobody@example.com", "c@x.co", "d@x.co"):
        assert post(client, RESEND, {"email": address}).status_code == 202
    for address in ("a@example.com", "zzz@example.com"):  # known or unknown: same answer
        response = post(client, RESEND, {"email": address})
        assert (response.status_code, error_code(response)) == (429, "throttled")
        assert int(response.headers["Retry-After"]) > 0
    assert (
        post(client, RESEND, {"email": "a@example.com"}, REMOTE_ADDR="10.9.8.7").status_code == 202
    )


@pytest.mark.parametrize("body", [{}, {"email": "a@b.co", "x": 1}, {"email": 5}, []])
def test_resend_wrong_body_shape(client, db, body):
    response = post(client, RESEND, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


def needles(token):
    """Exact-case values that must never appear in a response or a log record."""
    return [
        token,
        verification.hash_token(token),
        f"verify-email?token={token}",
        BODY["email"],
        BODY["email"].lower(),
    ]


def assert_clean(secrets_, *texts):
    for secret in secrets_:
        for text in texts:
            assert secret not in text


def test_nothing_secret_is_echoed_or_logged_on_any_path(
    client, registered, django_capture_on_commit_callbacks, caplog
):
    caplog.set_level(logging.DEBUG)
    first = last_token()
    with django_capture_on_commit_callbacks(execute=True):
        resent = post(client, RESEND, {"email": BODY["email"]})
    second = last_token()
    responses = [resent]
    responses.append(post(client, CONFIRM, {"token": first}))  # link_used (superseded)
    responses.append(post(client, CONFIRM, {"token": "x" * 40}))  # link_invalid
    EmailVerificationToken.objects.filter(used_at__isnull=True).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    responses.append(post(client, CONFIRM, {"token": second}))  # link_expired
    EmailVerificationToken.objects.filter(used_at__isnull=True).update(
        expires_at=timezone.now() + timedelta(hours=1)
    )
    responses.append(post(client, CONFIRM, {"token": second}))  # success
    responses.append(post(client, CONFIRM, {"token": second}))  # link_used
    assert [r.status_code for r in responses] == [202, 409, 400, 410, 200, 409]
    assert responses[4].headers["Cache-Control"] == "no-store"
    wire = " ".join(r.content.decode() + str(dict(r.headers)) for r in responses)
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    audit = " ".join(str(a.metadata) + a.reason for a in AuditRecord.objects.all())
    assert_clean(needles(first) + needles(second), wire, logged, audit)


def test_failure_in_the_subscriber_does_not_change_responses(
    client, db, django_capture_on_commit_callbacks, monkeypatch, caplog
):
    monkeypatch.setattr(
        "apps.identity.notices.send_verification", lambda *a: (_ for _ in ()).throw(OSError("x"))
    )
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        assert post(client, REGISTER, BODY).status_code == 202
    assert django_mail.outbox == []
    assert EmailVerificationToken.objects.count() == 0
    assert "OSError" in " ".join(str(r.__dict__) for r in caplog.records)
    assert RateLimitCounter.objects.exists()


# --- contract ----------------------------------------------------------------------------------


def test_openapi_documents_both_routes_and_codes(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    confirm = schema["paths"][CONFIRM]["post"]
    resend = schema["paths"][RESEND]["post"]
    assert "200" in confirm["responses"] and "202" in resend["responses"]
    assert "Retry-After" in resend["responses"]["429"]["headers"]
    codes = schema["components"]["schemas"]["ErrorCode"]["enum"]
    assert {"link_invalid", "link_expired", "link_used"} <= set(codes)
    assert set(schema["components"]["schemas"]["ConfirmEmailRequest"]["properties"]) == {"token"}
    assert set(schema["components"]["schemas"]["VerificationRequestRequest"]["properties"]) == {
        "email"
    }
