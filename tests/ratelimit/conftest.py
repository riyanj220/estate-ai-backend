import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from apps.kernel.models import RateLimitPolicy


@pytest.fixture
def probe(db):
    """A small windowed client-keyed policy so tests do not depend on production limits."""
    return RateLimitPolicy.objects.create(
        scope="probe", limit=3, window_seconds=60, key_kind="client", note="test"
    )


@pytest.fixture
def probe_account(db):
    return RateLimitPolicy.objects.create(
        scope="probe_account", limit=3, window_seconds=60, key_kind="account", note="test"
    )


@pytest.fixture
def seeded(db):
    """The seed rows, restored if a transactional test truncated them earlier."""
    module = importlib.import_module("apps.kernel.migrations.0002_seed_rate_limit_policies")
    module.seed(apps, SimpleNamespace(connection=connection))
