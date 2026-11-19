import pytest
from django.core import mail as django_mail

from apps.kernel import events, runtime


@pytest.fixture(autouse=True)
def fresh_runtime(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})  # the port re-subscribes itself on send
    runtime.shutdown()
    yield
    runtime.shutdown()


@pytest.fixture
def outbox():
    return django_mail.outbox
