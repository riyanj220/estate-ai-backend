import uuid

import pytest

ACCOUNT = uuid.UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture(autouse=True)
def header_identity(settings):
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "tests.authz.middleware.HeaderIdentityMiddleware"]


def as_identity(kind: str, account_id=ACCOUNT) -> dict:
    return {"headers": {"X-Test-Identity": f"{kind}:{account_id}"}}
