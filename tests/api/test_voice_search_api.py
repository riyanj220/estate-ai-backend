"""Voice search (Story 6.3) and the availability of search (Story 6.4), against the recorded
stub."""

import json
import logging
import threading
import uuid
from dataclasses import replace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client

from apps.kernel import flags, runtime
from apps.kernel.actor import Actor
from apps.kernel.ai import budget, gateway
from apps.kernel.ai.known_values import known_values_for
from apps.kernel.ai.ports import LLMRequest
from apps.kernel.ai.redaction import redact
from apps.kernel.ai.stub import stub
from apps.kernel.models import AiBudgetLedger, AiRequestLog, FeatureFlag
from tests.listings.support import seed_cap

URL = "/api/v1/public/search/transcription"
SEARCH = "/api/v1/public/search"
WEBM = b"\x1a\x45\xdf\xa3\x9f\x42\x82\x84webm" + b"zebrawood-audio" * 4
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_stub():
    seed_cap(200)
    stub.reset()
    stub.reply = "house for sale in Clifton"
    yield
    runtime.shutdown()
    stub.reset()


def upload(data=WEBM, client=None, name="audio", **headers):
    part = SimpleUploadedFile("jane doe.exe", data, content_type="text/html")  # both ignored
    return (client or Client()).post(URL, {name: part}, **headers)


def search(client=None):
    body = json.dumps({"query": "a house"})
    return (client or Client()).post(SEARCH, body, "application/json")


def test_a_recording_becomes_a_transcript_and_nothing_is_searched_or_kept(caplog, media_root):
    stub.reply = "  house\nfor sale\tin Clifton  "
    with caplog.at_level(logging.DEBUG):
        response = upload()
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    assert response.json() == {
        "status": "transcribed",
        "transcript": "house for sale in Clifton",
        "message": "",
    }
    assert [(r.port, r.texts, r.audio) for r in stub.requests] == [("speech", (), WEBM)]
    assert AiRequestLog.objects.get().feature == "voice_search"
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "Clifton" not in logged and "zebrawood" not in logged and "jane doe" not in logged
    assert not media_root.exists()


def test_sound_tags_are_not_words():
    stub.reply = "<noise> a plot [inaudible] under 2 crore <noise>"
    assert upload().json()["transcript"] == "a plot under 2 crore"
    stub.reply = "<noise>"
    body = upload().json()
    assert body["status"] == "fallback" and body["transcript"] == ""


def test_a_long_transcript_is_cut_to_what_a_search_accepts():
    stub.reply = "house " * 200
    heard = upload().json()["transcript"]
    assert 490 <= len(heard) <= 500


@pytest.mark.parametrize("failure", ["error", "timeout", "silence", "flag", "budget"])
def test_every_failure_is_a_fallback_to_the_text_box(failure, settings, monkeypatch):
    if failure == "error":
        stub.fail_with = RuntimeError("secret provider text")
    elif failure == "timeout":
        monkeypatch.setattr(gateway, "VOICE_SEARCH", replace(gateway.VOICE_SEARCH, timeout=0.05))
        stub.delay = 0.3
    elif failure == "silence":
        stub.reply = " \n "
    elif failure == "flag":
        flags.set_flag(Actor.system(), "ai_gateway", False)
    else:
        settings.AI_DAILY_BUDGET = 0
    body = upload().json()
    assert body["status"] == "fallback" and body["transcript"] == ""
    assert "Type your search instead." in body["message"] and "secret" not in body["message"]
    FeatureFlag.objects.all().delete()


def test_a_text_search_over_its_timeout_falls_back_to_the_filters(monkeypatch):
    assert gateway.SEARCH_QUERY.timeout == gateway.VOICE_SEARCH.timeout == 12.0
    monkeypatch.setattr(gateway, "SEARCH_QUERY", replace(gateway.SEARCH_QUERY, timeout=0.05))
    stub.delay = 0.3
    body = search().json()
    assert body["status"] == "fallback" and "took too long" in body["message"]


@pytest.mark.parametrize(
    ("data", "name", "field", "message"),
    [
        (b"", "audio", "audio", "empty"),
        (b"GIF89a not audio", "audio", "audio", "WebM, Ogg, MP4 or WAV"),
        (WEBM + b"x" * gateway.MAX_AUDIO_BYTES, "audio", "audio", "too large"),
        (WEBM, "file", "non_field_errors", "not accepted"),
    ],
)
def test_a_bad_recording_is_refused_before_it_is_sent_or_counted(data, name, field, message):
    for _ in range(12):  # over the limit, had they counted
        response = upload(data, name=name)
        assert response.status_code == 400, response.content
    assert message in response.json()["error"]["fields"][field][0]
    assert not stub.requests and not AiBudgetLedger.objects.exists()


def test_the_csrf_token_is_required_and_only_post_is_allowed():
    client = Client(enforce_csrf_checks=True)
    assert upload(client=client).status_code == 403
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    assert upload(client=client, HTTP_X_CSRFTOKEN=token).status_code == 200
    assert client.get(URL).status_code == 405


def test_one_voice_search_counts_once_as_a_search():
    """10 spoken searches a minute fit: the transcription has its own counter."""
    stub.reply = json.dumps({"purpose": "sale"})
    for _ in range(10):
        assert upload().status_code == 200
        assert search().status_code == 200
    for refused in (upload(), search()):
        assert refused.status_code == 429 and int(refused["Retry-After"]) > 0
        assert refused.json()["error"]["code"] == "throttled"
    assert len(stub.requests) == 20


def someone():
    return Actor.user(uuid.uuid4(), "r")


def assistant_turn():
    actor = someone()
    safe = redact("hello", known_values_for(actor))
    return gateway.complete(actor, gateway.ASSISTANT_TURN, LLMRequest(system=safe, input=safe))


def test_under_search_load_half_the_budget_stays_for_listing_creation(settings):
    settings.AI_DAILY_BUDGET = 10
    outcomes = []
    for number in range(30):  # many visitors, each far under their own limit
        client = Client(REMOTE_ADDR=f"10.0.{number}.1")
        answer = upload(client=client) if number % 2 else search(client)
        assert answer.status_code == 200
        outcomes.append(answer.json())
    row = AiBudgetLedger.objects.get()
    assert (row.used, row.open_used) == (5, 5) and len(stub.requests) == 5
    assert all(o["status"] == "fallback" for o in outcomes[5:])
    assert all("capacity is used up" in o["message"] for o in outcomes[5:])
    assert [assistant_turn().ok for _ in range(6)] == [True] * 5 + [False]


@pytest.mark.django_db(transaction=True)
def test_concurrent_search_calls_cannot_overrun_the_reserved_half(settings):
    settings.AI_DAILY_BUDGET = 10
    taken, start = [], threading.Barrier(8)

    def worker():
        try:
            start.wait(timeout=10)
            for _ in range(3):
                taken.append(budget.take("stub", reserved=False))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert len(taken) == 24 and sum(taken) == 5
    assert all(budget.take("stub", reserved=True) for _ in range(5))
