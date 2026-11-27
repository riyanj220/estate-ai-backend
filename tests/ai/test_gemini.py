"""The Gemini adapter against a fake connection: no network and no real key."""

import json
import traceback
from io import StringIO

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command

from apps.catalog import registry
from apps.catalog.extraction import extraction_schema
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway, transport
from apps.kernel.ai.adapters import get_adapter
from apps.kernel.ai.gemini import gemini
from apps.kernel.ai.ports import Audio, LLMRequest, Message
from apps.kernel.ai.redaction import KnownValues, redact
from apps.kernel.ai.transport import ProviderError
from apps.kernel.models import AiBudgetLedger, AiRequestLog

FAKE_KEY = "fake-key"
HOST = "generativelanguage.googleapis.com"


def text(value):
    return redact(value, KnownValues())


def answer(value, finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": value}]}}]}


class FakeWire:
    """Scripted (status, body) replies or exceptions; records what would have been sent."""

    def __init__(self):
        self.replies, self.sent = [], []

    def __call__(self, host, timeout):
        wire = self

        class Connection:
            def request(self, method, path, body=None, headers=None):
                wire.sent.append((host, method, path, body and json.loads(body), headers))
                if isinstance(wire.replies[0], Exception):
                    raise wire.replies.pop(0)

            def getresponse(self):
                status, body = wire.replies.pop(0)

                class Response:
                    def read(self, limit):
                        return json.dumps(body).encode()

                Response.status = status
                return Response()

            def close(self):
                pass

        return Connection()


@pytest.fixture
def wire(monkeypatch, settings):
    settings.GEMINI_API_KEY = FAKE_KEY
    fake = FakeWire()
    monkeypatch.setattr(transport, "_connect", fake)
    return fake


def request(**extra):
    return LLMRequest(system=text("Extract."), input=text("Call 0300 1234567"), **extra)


def test_complete_sends_the_key_only_in_its_header_and_a_structured_schema(wire):
    wire.replies = [(200, answer('{"a": 1}'))]
    schema = {"type": "object"}
    history = (Message("assistant", text("Hello")),)
    reply = gemini.complete(
        request(schema=schema, history=history, context=(text("Passage"),)), timeout=5
    )
    assert reply.text == '{"a": 1}'
    host, method, path, body, headers = wire.sent[0]
    assert (host, method) == (HOST, "POST")
    assert path == "/v1beta/models/gemini-test-model:generateContent"
    assert headers["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in path + json.dumps(body)
    assert "1234567" not in json.dumps(body)
    assert [c["role"] for c in body["contents"]] == ["model", "user"]
    assert body["generationConfig"]["responseJsonSchema"] == schema


def test_a_400_is_retried_once(wire):
    wire.replies = [(400, {}), (200, answer("fine"))]
    assert gemini.complete(request(), timeout=5).text == "fine"
    wire.replies, wire.sent = [(400, {}), (400, {}), (200, answer("never"))], []
    with pytest.raises(ProviderError):
        gemini.complete(request(), timeout=5)
    assert len(wire.sent) == 2


@pytest.mark.parametrize(
    "reply,category",
    [
        ((401, {"error": FAKE_KEY}), "auth"),
        ((429, {"error": "provider body"}), "quota"),
        ((503, {}), "server"),
        ((302, {}), "http"),
        (ValueError(f"Invalid header value {FAKE_KEY!r}"), "network"),
        (TimeoutError(), "timeout"),
        ((200, answer("half", "MAX_TOKENS")), "incomplete"),
        ((200, {"candidates": []}), "invalid_response"),
    ],
)
def test_errors_carry_a_category_and_never_the_key_or_a_body(wire, reply, category):
    wire.replies = [reply]
    with pytest.raises(ProviderError) as caught:
        gemini.complete(request(), timeout=5)
    assert caught.value.category == category
    shown = "".join(traceback.format_exception(caught.value))
    assert FAKE_KEY not in shown and "provider body" not in shown
    assert caught.value.__context__ is None


@pytest.mark.parametrize("key", ["", "bad\nkey", "two words"])
def test_an_unusable_key_is_refused_before_anything_is_sent(wire, settings, key):
    settings.GEMINI_API_KEY = key
    with pytest.raises(ProviderError) as caught:
        gemini.complete(request(), timeout=5)
    assert caught.value.category == "not_configured" and not wire.sent


def test_embeddings_and_speech(wire):
    wire.replies = [(200, {"embeddings": [{"values": [0.5]}, {"values": [0.25]}]})]
    assert gemini.embed((text("a"), text("b")), timeout=5) == [[0.5], [0.25]]
    assert wire.sent[0][2] == "/v1beta/models/gemini-test-embedding:batchEmbedContents"
    wire.replies = [(200, answer(" two bedrooms \n"))]
    assert gemini.transcribe(Audio(b"RIFF", "audio/wav"), timeout=5) == "two bedrooms"
    assert wire.sent[1][3]["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "audio/wav"


def test_configuration_selects_the_adapter(settings):
    settings.AI_PROVIDER = "gemini"
    with pytest.raises(ImproperlyConfigured, match="GEMINI_API_KEY"):
        get_adapter()
    settings.GEMINI_API_KEY = FAKE_KEY
    assert get_adapter() is gemini


@pytest.mark.django_db
def test_provider_embeddings_are_budgeted_and_logged_with_their_model(wire, settings):
    settings.AI_PROVIDER = "gemini"
    wire.replies = [(200, {"embeddings": [{"values": [0.5]}]})]
    assert gateway.embed(Actor.system(), [text("a")]).ok
    assert AiBudgetLedger.objects.get(provider="gemini").used == 1
    assert AiRequestLog.objects.get().model == "gemini-test-embedding"
    settings.AI_DAILY_BUDGET = 0
    assert gateway.embed(Actor.system(), [text("a")]).outcome == "budget_exhausted"
    assert len(wire.sent) == 1


def _strict(schema):
    assert not {"minimum", "maximum", "minLength", "maxLength", "pattern"} & set(schema)
    if "properties" in schema:
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])
        for child in schema["properties"].values():
            _strict(child)
    for child in schema.get("anyOf", ()):
        _strict(child)


def test_every_registry_schema_is_strict_and_small():
    for purpose in registry.purposes():
        for subtype in registry.all_subtypes():
            schema = extraction_schema(purpose, subtype)
            assert schema["properties"]
            _strict(schema)
            assert len(json.dumps(schema)) < 8000


def test_the_trial_stops_on_a_rejected_key_and_prints_no_secret(wire):
    wire.replies = [(403, {"error": FAKE_KEY})]
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("ai_provider_trial", stdout=out)
    assert "FAILED auth status=403" in out.getvalue() and FAKE_KEY not in out.getvalue()
    assert len(wire.sent) == 1
