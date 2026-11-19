import importlib
import json
import logging
import sys
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.test import Client

from apps.kernel import events, runtime
from apps.kernel import mail as port
from apps.kernel.mail import MailMessage, email_link
from apps.kernel.request_logging import JsonFormatter
from tests.mail import backends

pytestmark = pytest.mark.django_db

ADDRESS = "canary-rcpt@example.org"
SUBJECT = "canary-subject"
BODY = "canary-body https://app.example/verify?token=canary-token"


def msg(**kw):
    values = {"to": ADDRESS, "subject": SUBJECT, "body": BODY}
    values.update(kw)
    return MailMessage(**values)


def test_sent_once_after_commit(django_capture_on_commit_callbacks, outbox):
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg(html_body="<p>hi</p>"))
        assert outbox == []
    assert len(outbox) == 1
    sent = outbox[0]
    assert (sent.to, sent.subject, sent.body) == ([ADDRESS], SUBJECT, BODY)
    assert sent.from_email == settings.DEFAULT_FROM_EMAIL
    assert sent.alternatives[0][0] == "<p>hi</p>"
    assert sent.alternatives[0][1] == "text/html"


def test_plain_text_only_has_no_alternative(django_capture_on_commit_callbacks, outbox):
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg())
    assert outbox[0].alternatives == []


def test_rolled_back_sends_nothing(outbox):
    with pytest.raises(RuntimeError), transaction.atomic():
        port.send(msg())
        raise RuntimeError
    assert outbox == []


def test_outside_transaction_refused(transactional_db, outbox):
    with pytest.raises(RuntimeError):
        port.send(msg())
    assert outbox == []


@pytest.mark.parametrize(
    "bad",
    [
        {"to": "a@example.org\r\nBcc: x@example.org"},
        {"to": "a@example.org\nBcc: x@example.org"},
        {"to": "a@exa\x00mple.org"},
        {"subject": "hi\r\nBcc: x@example.org"},
        {"subject": "hi\nthere"},
        {"subject": "hi\x07"},
        {"subject": "hi there"},
        {"to": ""},
        {"to": "no-at-sign"},
        {"to": "a@b@example.org"},
        {"to": "a@example.org, b@example.org"},
        {"to": "a@example.org;b@example.org"},
        {"to": "Name <a@example.org>"},
        {"to": "a b@example.org"},
        {"to": "@example.org"},
        {"to": "a@"},
        {"to": "a" * 250 + "@example.org"},
        {"to": "a@b..c"},
        {"to": "a@.example.org"},
        {"to": "a@example.org."},
        {"to": "a@exämple.org"},
        {"to": "a@exa\u200bmple.org"},
        {"to": "a@example.org\u2028"},
        {"subject": "hi\u200bthere"},
        {"subject": "hi\u2029there"},
        {"body": ""},
        {"subject": ""},
        {"subject": "s" * 201},
        {"body": "b" * 100_001},
        {"html_body": "b" * 100_001},
    ],
)
def test_invalid_message_refused_and_nothing_queued(
    bad, django_capture_on_commit_callbacks, outbox
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(ValueError):
            port.send(msg(**bad))
    assert callbacks == [] and outbox == []


def test_limits_accept_the_boundary(django_capture_on_commit_callbacks, outbox):
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg(subject="s" * 200, body="b" * 100_000))
    assert len(outbox) == 1


def test_sender_with_control_characters_refused(settings):
    settings.DEFAULT_FROM_EMAIL = "a@example.org\r\nBcc: x@example.org"
    with pytest.raises(ValueError):
        port.send(msg())


def test_message_is_immutable():
    with pytest.raises(AttributeError):
        msg().to = "x@example.org"


def test_event_name():
    assert port.MailRequested.name == "mail.requested"


def logged(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def assert_no_canary(caplog):
    text = []
    for record in caplog.records:
        text.append(record.getMessage())
        text.append(json.dumps(record.__dict__, default=str))
        text.append(JsonFormatter().format(record))
    blob = "\n".join(text)
    for canary in ("canary-rcpt", "canary-subject", "canary-body", "canary-token", "refused"):
        assert canary not in blob


def test_failure_logged_by_class_only_and_isolated(
    settings, django_capture_on_commit_callbacks, caplog
):
    settings.EMAIL_BACKEND = "tests.mail.backends.AlwaysFailingBackend"
    logged(caplog)
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg())  # does not raise
    failures = [r for r in caplog.records if r.name == "estate.mail"]
    assert len(failures) == 1
    assert failures[0].exception_class == "ConnectionRefusedError"
    assert_no_canary(caplog)


def test_failure_does_not_affect_later_sends(settings, django_capture_on_commit_callbacks):
    settings.EMAIL_BACKEND = "tests.mail.backends.RecordingBackend"
    backends.sent_threads.clear()
    backends.fail_next["on"] = True
    try:
        with django_capture_on_commit_callbacks(execute=True):
            port.send(msg())
        backends.fail_next["on"] = False
        with django_capture_on_commit_callbacks(execute=True):
            port.send(msg())
    finally:
        backends.fail_next["on"] = False
    assert len(backends.sent_threads) == 1


def test_success_logs_nothing_private(django_capture_on_commit_callbacks, caplog, outbox):
    logged(caplog)
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg())
    assert len(outbox) == 1
    assert_no_canary(caplog)


def test_failing_backend_leaves_the_caller_outcome_unchanged(
    settings, django_capture_on_commit_callbacks, caplog
):
    settings.EMAIL_BACKEND = "tests.mail.backends.AlwaysFailingBackend"
    logged(caplog)
    result = None
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with transaction.atomic():
            port.send(msg())
            result = "committed"
    assert callbacks and result == "committed"
    assert [r.exception_class for r in caplog.records if r.name == "estate.mail"] == [
        "ConnectionRefusedError"
    ]
    assert_no_canary(caplog)


def test_mail_still_delivers_after_events_clear(django_capture_on_commit_callbacks, outbox):
    events.clear()
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg())
    assert len(outbox) == 1


def test_ready_twice_is_safe_and_subscribes_once():
    from django.apps import apps

    config = apps.get_app_config("kernel")
    config.ready()
    config.ready()
    assert events._registry[port.MailRequested].count(port.deliver) == 1


def test_non_string_arguments_refused():
    with pytest.raises(ValueError):
        port.send(msg(to=None))
    with pytest.raises(ValueError):
        port.send(msg(subject=5))
    with pytest.raises(ValueError):
        port.send(msg(body=None))
    with pytest.raises(ValueError):
        port.send(msg(html_body=3))


@pytest.mark.parametrize("params", [{"a": None}, {"a": 1}, {1: "a"}])
def test_email_link_requires_string_params(params):
    with pytest.raises(ValueError):
        email_link("/x", params)


def test_non_inline_delivery_runs_on_the_executor(settings, django_capture_on_commit_callbacks):
    settings.MAIL_SEND_INLINE = False
    settings.EMAIL_BACKEND = "tests.mail.backends.RecordingBackend"
    backends.sent_threads.clear()
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg())
    deadline = time.monotonic() + 5
    while not backends.sent_threads and time.monotonic() < deadline:
        time.sleep(0.01)
    assert backends.sent_threads
    assert all(name.startswith("estate-runtime") for name in backends.sent_threads)


def test_non_inline_uses_the_interactive_lane(
    settings, monkeypatch, django_capture_on_commit_callbacks
):
    settings.MAIL_SEND_INLINE = False
    calls = []
    monkeypatch.setattr(runtime, "submit_interactive", lambda fn, *a: calls.append((fn, a)))
    monkeypatch.setattr(runtime, "submit_heavy", lambda *a: pytest.fail("heavy lane used"))
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg())
    assert len(calls) == 1


def test_executor_unavailable_is_logged_not_raised(
    settings, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    settings.MAIL_SEND_INLINE = False

    def boom(*args):
        raise RuntimeError(ADDRESS)

    monkeypatch.setattr(runtime, "submit_interactive", boom)
    logged(caplog)
    with django_capture_on_commit_callbacks(execute=True):
        port.send(msg())
    assert_no_canary(caplog)


def test_inline_mode_is_on_in_test_settings(settings):
    assert settings.MAIL_SEND_INLINE is True


# --- links ---------------------------------------------------------------------------------


def test_email_link_uses_frontend_base_url_not_host_header(settings):
    settings.FRONTEND_BASE_URL = "https://app.example.org"
    Client().get("/", HTTP_HOST="evil.example.com")
    assert (
        email_link("/verify-email", {"token": "t"})
        == "https://app.example.org/verify-email?token=t"
    )


def test_email_link_without_params(settings):
    settings.FRONTEND_BASE_URL = "https://app.example.org/"
    assert email_link("/reset") == "https://app.example.org/reset"


def test_email_link_encodes_and_sorts_params():
    url = email_link("/x", {"b": "a b&c=d", "a": "é/?#"})
    query = urlsplit(url).query
    assert query.startswith("a=")
    assert "&b=" in query and " " not in query and "#" not in url
    assert parse_qs(query) == {"a": ["é/?#"], "b": ["a b&c=d"]}


@pytest.mark.parametrize(
    "path", ["//evil.example.com", "https://evil.example.com", "/a/../b", "/a?x=1", "x", "/a%2fb"]
)
def test_email_link_rejects_hostile_paths(path):
    with pytest.raises(ValueError):
        email_link(path)


# --- settings ------------------------------------------------------------------------------


def load_local(monkeypatch, **env):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("RUNTIME_PASSWORD", "r" * 40)
    monkeypatch.setenv("MIGRATOR_PASSWORD", "m" * 40)
    # Explicit loopback defaults: CI exports a non-loopback MAIL_HOST for the prod-settings tests.
    monkeypatch.setenv("MAIL_HOST", "127.0.0.1")
    monkeypatch.setenv("MAIL_PORT", "1025")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("config.settings.local", None)
    return importlib.import_module("config.settings.local")


@pytest.fixture
def restore_local(monkeypatch):
    yield
    monkeypatch.undo()
    # Drop the module instead of reloading: reloading needs secrets that CI does not have.
    sys.modules.pop("config.settings.local", None)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_accepts_loopback_mail_host(monkeypatch, restore_local, host):
    local = load_local(monkeypatch, MAIL_HOST=host)
    assert local.EMAIL_HOST == host
    assert local.EMAIL_BACKEND.endswith("smtp.EmailBackend")
    assert local.MAIL_SEND_INLINE is False


def test_local_has_smtp_timeout(monkeypatch, restore_local):
    assert load_local(monkeypatch).EMAIL_TIMEOUT == 10


def test_local_refuses_bad_port(monkeypatch, restore_local):
    with pytest.raises(ImproperlyConfigured, match="MAIL_PORT"):
        load_local(monkeypatch, MAIL_PORT="70000")


def test_loopback_validator_returns_unbracketed_host():
    from config.env import validate_loopback_host

    assert validate_loopback_host("MAIL_HOST", "[::1]") == "::1"
    assert validate_loopback_host("MAIL_HOST", " LocalHost ") == "localhost"


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        (None, True, True),
        ("", False, False),
        ("TRUE", False, True),
        ("0", True, False),
        (" yes ", False, True),
        ("off", True, False),
    ],
)
def test_env_bool(monkeypatch, raw, default, expected):
    from config.env import env_bool

    if raw is None:
        monkeypatch.delenv("X_FLAG", raising=False)
    else:
        monkeypatch.setenv("X_FLAG", raw)
    assert env_bool("X_FLAG", default) is expected


def test_env_bool_rejects_garbage(monkeypatch):
    from config.env import env_bool

    monkeypatch.setenv("X_FLAG", "maybe")
    with pytest.raises(ImproperlyConfigured, match="X_FLAG"):
        env_bool("X_FLAG", True)


@pytest.mark.parametrize("host", ["smtp.example.org", "10.0.0.5", "0.0.0.0", "127.0.0.1.evil.com"])  # noqa: S104
def test_local_refuses_non_loopback_mail_host(monkeypatch, restore_local, host):
    with pytest.raises(ImproperlyConfigured, match="MAIL_HOST") as exc:
        load_local(monkeypatch, MAIL_HOST=host)
    assert host not in str(exc.value)


def test_test_settings_use_memory_backend(settings):
    assert settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
