import hashlib
import logging
import re
import threading
from datetime import timedelta

import pytest
from django.core import mail as django_mail
from django.db import connection, transaction
from django.utils import timezone

from apps.identity import services, subscribers, verification
from apps.identity.events import AccountRegistered
from apps.identity.models import Account, EmailVerificationToken
from apps.identity.state import AccountState
from apps.kernel import events, retention, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord

VISITOR = Actor.visitor("req-1")
SYSTEM = Actor.system()
PASSWORD = "mango-river-lantern-42"


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


def register(email="ayesha@example.com"):
    services.register_individual(VISITOR, "Ayesha Khan", email, PASSWORD, "0300-1234567")


def token_from(message):
    return re.search(r"token=([A-Za-z0-9_-]+)", message.body).group(1)


@pytest.fixture
def registered(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        register()
    return Account.objects.get()


def codes(token):
    with pytest.raises(DomainError) as exc:
        services.confirm_email(VISITOR, token)
    return exc.value.code


# --- registration sends the link ---------------------------------------------------------------


def test_registration_issues_one_token_and_mails_the_link(registered, settings):
    message = django_mail.outbox[0]
    assert (len(django_mail.outbox), message.to, message.subject) == (
        1,
        ["ayesha@example.com"],
        "Verify your EstateAI email address",
    )
    token = token_from(message)
    assert f"{settings.FRONTEND_BASE_URL.rstrip('/')}/verify-email?token={token}" in message.body
    assert "This link works once and expires in 24 hours." in message.body
    row = EmailVerificationToken.objects.get()
    assert row.account_id == registered.id
    assert row.expires_at - row.created_at == timedelta(hours=24)
    assert row.used_at is None


def test_only_the_hash_is_stored(registered):
    token = token_from(django_mail.outbox[0])
    row = EmailVerificationToken.objects.get()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash
    assert len(token) >= 43  # token_urlsafe(32)


def test_rollback_leaves_no_token_and_no_email(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                register()
                raise RuntimeError
    assert EmailVerificationToken.objects.count() == 0
    assert django_mail.outbox == []


def test_failure_after_token_issue_rolls_the_token_back(registered, monkeypatch):
    def boom(*args):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("apps.identity.notices.send_verification", boom)
    before = EmailVerificationToken.objects.count()
    with pytest.raises(RuntimeError):
        subscribers.send_verification_email(
            subscribers.AccountVerificationRequested(account_id=registered.id)
        )
    assert EmailVerificationToken.objects.count() == before
    assert EmailVerificationToken.objects.filter(used_at__isnull=True).count() == 1


def test_mail_failure_is_logged_by_class_and_the_token_exists(
    db, django_capture_on_commit_callbacks, monkeypatch, caplog
):
    def boom(message):
        raise ConnectionError("secret-address@example.com")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", lambda *a, **k: boom(1))
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        register()
    assert EmailVerificationToken.objects.count() == 1
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    assert "ConnectionError" in logged and "secret-address" not in logged


def test_new_token_supersedes_older_ones(registered, django_capture_on_commit_callbacks):
    old = token_from(django_mail.outbox[0])
    with django_capture_on_commit_callbacks(execute=True):
        services.request_verification_link(VISITOR, "AYESHA@example.com")
    new = token_from(django_mail.outbox[1])
    assert old != new
    assert codes(old) == "link_used"
    services.confirm_email(VISITOR, new)
    assert Account.objects.get().state is AccountState.ACTIVE


# --- confirm -----------------------------------------------------------------------------------


def test_valid_link_verifies_once_and_audits(registered):
    token = token_from(django_mail.outbox[0])
    assert services.confirm_email(VISITOR, token) is None
    account = Account.objects.get()
    assert account.state is AccountState.ACTIVE
    assert EmailVerificationToken.objects.get().used_at is not None
    assert AuditRecord.objects.filter(action="account.email_verified").count() == 1
    assert codes(token) == "link_used"
    assert AuditRecord.objects.filter(action="account.email_verified").count() == 1


def test_expired_link_changes_nothing(registered):
    token = token_from(django_mail.outbox[0])
    EmailVerificationToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert codes(token) == "link_expired"
    assert Account.objects.get().state is AccountState.EMAIL_UNVERIFIED
    assert EmailVerificationToken.objects.get().used_at is None


@pytest.mark.parametrize(
    "bad", ["", "nope", "a" * 201, "a" * 43, "bad token", "tok\x00en", None, 5, ["x"], {"a": 1}]
)
def test_unknown_or_malformed_tokens_are_invalid(registered, bad):
    assert codes(bad) == "link_invalid"


def test_confirming_for_a_verified_account_is_idempotent(registered):
    services.mark_email_verified(SYSTEM, registered.id)
    token = EmailVerificationToken.objects.create(
        account=registered,
        token_hash=verification.hash_token("fresh-token"),
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    services.confirm_email(VISITOR, "fresh-token")
    token.refresh_from_db()
    assert token.used_at is not None
    assert AuditRecord.objects.filter(action="account.email_verified").count() == 1


@pytest.mark.parametrize("condition", ["suspended", "deactivated"])
def test_suspended_or_deactivated_accounts_keep_their_state(registered, condition):
    token = token_from(django_mail.outbox[0])
    now = timezone.now()
    if condition == "suspended":
        Account.objects.update(suspended_at=now, suspension_reason="because")
        expected = AccountState.SUSPENDED
    else:
        Account.objects.update(deactivated_at=now)
        expected = AccountState.DEACTIVATED
    services.confirm_email(VISITOR, token)
    account = Account.objects.get()
    assert account.email_verified_at is not None
    assert account.state is expected


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmations_yield_exactly_one_success():
    runtime.shutdown()
    register()
    account = Account.objects.get()
    token = "concurrent-token-value"
    EmailVerificationToken.objects.create(
        account=account,
        token_hash=verification.hash_token(token),
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    workers = 8
    barrier = threading.Barrier(workers)
    outcomes = []

    def work():
        try:
            barrier.wait()
            services.confirm_email(VISITOR, token)
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("link_used") == workers - 1
    assert AuditRecord.objects.filter(action="account.email_verified").count() == 1


# --- resend ------------------------------------------------------------------------------------


def resend(capture, email):
    with capture(execute=True):
        assert services.request_verification_link(VISITOR, email) is None


def test_resend_for_unverified_sends_a_new_link(registered, django_capture_on_commit_callbacks):
    resend(django_capture_on_commit_callbacks, "ayesha@example.com")
    assert len(django_mail.outbox) == 2
    assert EmailVerificationToken.objects.filter(used_at__isnull=True).count() == 1


def test_resend_for_verified_sends_the_sign_in_notice(
    registered, django_capture_on_commit_callbacks
):
    services.mark_email_verified(SYSTEM, registered.id)
    django_mail.outbox.clear()
    resend(django_capture_on_commit_callbacks, "ayesha@example.com")
    assert [m.subject for m in django_mail.outbox] == ["You already have an EstateAI account"]
    assert EmailVerificationToken.objects.count() == 1


@pytest.mark.parametrize("email", ["nobody@example.com", "", "x" * 300, None, 5])
def test_resend_for_unknown_or_odd_input_does_nothing(
    registered, django_capture_on_commit_callbacks, email
):
    django_mail.outbox.clear()
    resend(django_capture_on_commit_callbacks, email)
    assert django_mail.outbox == []


@pytest.mark.parametrize("condition", ["suspended", "deactivated"])
def test_resend_for_suspended_or_deactivated_does_nothing(
    registered, django_capture_on_commit_callbacks, condition
):
    if condition == "suspended":
        Account.objects.update(suspended_at=timezone.now(), suspension_reason="because")
    else:
        Account.objects.update(deactivated_at=timezone.now())
    django_mail.outbox.clear()
    resend(django_capture_on_commit_callbacks, "ayesha@example.com")
    assert django_mail.outbox == []


def test_account_cap_silently_stops_the_sixth_email(registered, django_capture_on_commit_callbacks):
    for _ in range(8):
        resend(django_capture_on_commit_callbacks, "ayesha@example.com")
    # The registration email is not counted by the resend cap: 1 registration + 5 resends.
    assert len(django_mail.outbox) == 6
    assert EmailVerificationToken.objects.count() == 6


# --- audit, privacy, retention -----------------------------------------------------------------


def test_only_the_verified_audit_record_is_written_and_nothing_leaks(
    registered, django_capture_on_commit_callbacks, caplog
):
    caplog.set_level(logging.DEBUG)
    token = token_from(django_mail.outbox[0])
    resend(django_capture_on_commit_callbacks, "ayesha@example.com")
    services.confirm_email(VISITOR, token_from(django_mail.outbox[1]))
    assert sorted(AuditRecord.objects.values_list("action", flat=True)) == [
        "account.created",
        "account.email_verified",
    ]
    blob = " ".join(str(a.metadata) + a.action + a.reason for a in AuditRecord.objects.all())
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for secret in (token, verification.hash_token(token), "ayesha@example.com"):
        assert secret not in blob and secret not in logged


def test_retention_purges_stale_used_or_expired_tokens(registered):
    now = timezone.now()

    def make(label, expires, used=None):
        EmailVerificationToken.objects.create(
            account=registered,
            token_hash=label,
            created_at=now - timedelta(days=30),
            expires_at=expires,
            used_at=used,
        )

    EmailVerificationToken.objects.all().delete()
    make("expired-old", now - timedelta(days=8))
    make("used-old", now + timedelta(hours=1), now - timedelta(days=8))
    make("expired-recent", now - timedelta(days=6))
    make("used-recent", now + timedelta(hours=1), now - timedelta(days=1))
    make("live", now + timedelta(hours=1))
    assert verification.purge_expired(now, True) == 2
    assert EmailVerificationToken.objects.count() == 5
    assert verification.purge_expired(now, False) == 2
    assert set(EmailVerificationToken.objects.values_list("token_hash", flat=True)) == {
        "expired-recent",
        "used-recent",
        "live",
    }


def test_retention_job_is_registered():
    assert "identity.email_verification_tokens" in retention.registered()


# --- direct service and edge cases -------------------------------------------------------------


def test_issue_only_for_exactly_email_unverified_accounts(registered):
    import uuid

    before = EmailVerificationToken.objects.count()
    django_mail.outbox.clear()

    def issue(account_id):
        assert services.issue_verification_token(SYSTEM, account_id) is None

    issue(uuid.uuid4())  # unknown
    Account.objects.update(suspended_at=timezone.now(), suspension_reason="because")
    issue(registered.id)  # suspended and unverified
    Account.objects.update(suspended_at=None, suspension_reason="")
    Account.objects.update(deactivated_at=timezone.now())
    issue(registered.id)  # deactivated
    Account.objects.update(deactivated_at=None, email_verified_at=timezone.now())
    issue(registered.id)  # verified
    assert EmailVerificationToken.objects.count() == before
    assert django_mail.outbox == []


def test_expiry_boundary(registered, monkeypatch):
    token = token_from(django_mail.outbox[0])
    row = EmailVerificationToken.objects.get()
    now = timezone.now()
    monkeypatch.setattr("apps.identity.services.timezone.now", lambda: now)
    row.expires_at = now
    row.save()
    assert codes(token) == "link_expired"  # expires_at == now is expired
    row.expires_at = now + timedelta(seconds=1)
    row.save()
    services.confirm_email(VISITOR, token)
    assert Account.objects.get().state is AccountState.ACTIVE


def test_a_purged_token_is_invalid(registered):
    token = token_from(django_mail.outbox[0])
    EmailVerificationToken.objects.update(expires_at=timezone.now() - timedelta(days=8))
    assert verification.purge_expired(timezone.now(), False) == 1
    assert codes(token) == "link_invalid"


def test_repeat_registration_of_an_unverified_address_sends_a_fresh_link(
    registered, django_capture_on_commit_callbacks
):
    old = token_from(django_mail.outbox[0])
    with django_capture_on_commit_callbacks(execute=True):
        register()  # same address again
    assert len(django_mail.outbox) == 2
    new = token_from(django_mail.outbox[1])
    assert new != old
    assert codes(old) == "link_used"
    services.confirm_email(VISITOR, new)
    assert Account.objects.get().state is AccountState.ACTIVE


def test_resend_normalises_the_address_like_registration(
    registered, django_capture_on_commit_callbacks
):
    django_mail.outbox.clear()
    resend(django_capture_on_commit_callbacks, "  AYESHA@example.com ")
    assert len(django_mail.outbox) == 1
    resend(django_capture_on_commit_callbacks, "ayesha@example.com\x00")
    assert len(django_mail.outbox) == 1


def test_subscribe_helper_is_idempotent_and_public():
    assert events.is_subscribed(AccountRegistered, subscribers.send_verification_email)
    subscribers.register()
    subscribers.register()
