import pytest

from apps.kernel import runtime


@pytest.fixture(autouse=True)
def fresh_executor():
    runtime.shutdown()
    yield
    runtime.shutdown()
