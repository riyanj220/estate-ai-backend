"""Resume, idempotent turns (Story 5.14) and fault injection (Story 5.15), against the stub."""

import time
import uuid

import pytest

from apps.assistant.models import AssistantTurn
from apps.kernel import flags
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.stub import stub
from apps.kernel.ai.transport import ProviderError
from apps.listings import services as listings
from tests.api.test_listings_api import detail
from tests.api.test_publish_api import publish
from tests.assistant.test_approval import GOOD, current, describe, turn_answer
from tests.assistant.test_turns import (
    QUESTION,
    SESSIONS,
    clean,  # noqa: F401  (autouse: stub and executor are reset)
    llm_requests,
    opened,
    post,
    say,
    signed_in,
    start,
    user,
)
from tests.listings.support import ready_draft

FAULTS = {
    "unavailable": "error",
    "rate_limited": "error",
    "provider_timeout": "timeout",
    "slow": "timeout",
    "flag_off": "fallback",
    "budget_exhausted": "budget_exhausted",
}


def inject(fault, monkeypatch, settings):
    stub.reply = turn_answer({"bedrooms": 4})
    if fault == "unavailable":
        stub.fail_with = ConnectionError("private provider text")
    elif fault == "rate_limited":
        stub.fail_with = ProviderError("quota", 429)
    elif fault == "provider_timeout":
        stub.fail_with = ProviderError("timeout")
    elif fault == "slow":
        for name in ("ASSISTANT_TURN", "DESCRIPTION"):
            feature = getattr(gateway, name)
            monkeypatch.setattr(
                gateway, name, gateway.Feature(**{**vars(feature), "timeout": 0.05})
            )
        stub.delay = 0.3
    elif fault == "flag_off":
        flags.set_flag(Actor.system("test"), "ai_gateway", False)
    else:
        settings.AI_DAILY_BUDGET = 0


def test_resume_restores_history_and_the_draft_state():
    account, draft, client, session_id = start()
    stub.reply = turn_answer({"bedrooms": 5})
    say(client, session_id, "It has five bedrooms")
    view = current(account, draft)
    listings.apply_updates(user(account), draft.id, view.revision, {"bathrooms": 3})  # the form
    later = signed_in()
    resumed = opened(later, draft)
    assert resumed.status_code == 200 and resumed.json()["id"] == session_id
    body = resumed.json()
    assert body == later.get(f"{SESSIONS}/{session_id}").json()
    assert [t["message"] for t in body["turns"]] == ["It has five bedrooms"]
    assert {f["field"]: (f["value"], f["confirmed"]) for f in body["summary"]}.items() >= {
        "bedrooms": ("5", False),
        "bathrooms": ("3", True),
    }.items()
    assert body["next_step"]["fields_asked"] == ["area_value", "area_unit"]


def test_a_repeat_is_processed_once_and_returns_the_original_result():
    account, draft, client, session_id = start()  # nothing indexed: the answer carries a notice
    stub.reply = turn_answer({"bedrooms": 5})
    key = str(uuid.uuid4())
    first = say(client, session_id, QUESTION, key).json()
    assert first["message"] and first["turn"]["applied"]
    view = current(account, draft)
    stub.reply = turn_answer({"bedrooms": 9})
    stub.requests.clear()
    assert say(client, session_id, QUESTION, key).json() == first
    assert not stub.requests and current(account, draft) == view
    assert AssistantTurn.objects.count() == 1


@pytest.mark.parametrize("fault", FAULTS)
def test_a_failed_turn_leaves_the_draft_as_it_was(fault, monkeypatch, settings):
    account, draft, client, session_id = start()
    before, key = current(account, draft), str(uuid.uuid4())
    with monkeypatch.context() as patched:
        inject(fault, patched, settings)
        response = say(client, session_id, "It has four bedrooms", key)
    body = response.json()
    assert (response.status_code, body["outcome"], body["turn"]) == (200, FAULTS[fault], None)
    assert body["message"] and "private provider text" not in body["message"]
    assert current(account, draft) == before and not AssistantTurn.objects.exists()
    # nothing was processed, so the same id is answered once the fault is gone
    time.sleep(stub.delay)  # let the abandoned call finish
    stub.fail_with, stub.delay, settings.AI_DAILY_BUDGET = None, 0.0, 1000
    flags.set_flag(Actor.system("test"), "ai_gateway", True)
    assert say(client, session_id, "It has four bedrooms", key).json()["outcome"] == "success"
    assert current(account, draft).fields["bedrooms"] == 4


@pytest.mark.parametrize("fault", FAULTS)
def test_a_failed_description_leaves_the_draft_as_it_was(fault, monkeypatch, settings):
    account, draft, client, session_id = start()
    listings.apply_updates(user(account), draft.id, 1, {"bedrooms": 5, "bathrooms": 6})
    before = current(account, draft)
    inject(fault, monkeypatch, settings)
    stub.reply = GOOD
    body = describe(client, session_id, before.revision).json()
    assert (body["outcome"], body["listing"]) == (FAULTS[fault], None)
    assert body["message"] and current(account, draft) == before


@pytest.mark.parametrize("how", ["fails", "slow"])
def test_a_turn_is_answered_without_retrieval(how, monkeypatch):
    _, _, client, session_id = start()

    def embed(texts, *, timeout):
        if how == "slow":
            time.sleep(0.3)
        raise ConnectionError

    monkeypatch.setattr(gateway, "EMBEDDING_TIMEOUT", 0.05)
    monkeypatch.setattr(stub, "embed", embed)
    body = say(client, session_id, QUESTION).json()
    assert body["outcome"] == "success" and body["turn"]["sources"] == []
    assert body["message"].startswith("Explanations are unavailable")
    assert len(llm_requests()) == 1


@pytest.mark.parametrize("fault", ["unavailable", "flag_off", "budget_exhausted"])
def test_the_manual_flow_works_without_the_assistant(fault, monkeypatch, settings):
    account, _, client, _ = start()
    inject(fault, monkeypatch, settings)
    client.account_id = account.id
    created = post(
        client, "/api/v1/listings", {"purpose": "sale", "category": "house", "subtype": "house"}
    )
    assert created.status_code == 201
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    edited = client.patch(
        detail(created.json()["id"]),
        {"base_revision": 1, "fields": {"title": "A house I typed in myself"}},
        "application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert edited.status_code == 200
    ready = ready_draft(account)
    assert publish(client, ready.id, ready.revision).json()["status"] == "published"
    assert not stub.requests


def test_an_assistant_call_is_given_up_after_twenty_seconds():
    assert (gateway.ASSISTANT_TURN.timeout, gateway.DESCRIPTION.timeout) == (20.0, 20.0)
