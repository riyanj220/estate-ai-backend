import pytest

from apps.kernel import runtime
from apps.kernel.ai.stub import stub


@pytest.fixture(autouse=True)
def recorded_stub():
    stub.reset()
    yield stub
    runtime.shutdown()  # no slow call from one test may hold a slot in the next
    stub.reset()
