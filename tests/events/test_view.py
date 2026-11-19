import pytest

from apps.kernel import events
from tests.events.sample_events import ThingHappened

pytestmark = [pytest.mark.urls("tests.events.urls"), pytest.mark.django_db(transaction=True)]


def test_failing_subscriber_does_not_change_the_response(client, monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []

    def broken(event):
        raise RuntimeError("boom")

    events.subscribe(ThingHappened, broken)
    events.subscribe(ThingHappened, seen.append)
    response = client.post("/emit")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert len(seen) == 1
