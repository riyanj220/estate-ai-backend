import io
import json
import logging
from pathlib import Path

import pytest

from apps.kernel.request_logging import ALLOWED_FIELDS, REQUEST_LOGGER, JsonFormatter

pytestmark = [pytest.mark.urls("tests.api.urls")]

SECRETS = [
    "0300-1234567",
    "private.person@example.com",
    "12 Hidden Lane",
    "sessionsecret",
    "tok-9f8e",
]


@pytest.fixture
def log_output(caplog):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    yield stream
    root.removeHandler(handler)


def lines(stream):
    return [json.loads(x) for x in stream.getvalue().splitlines() if x]


def test_request_line_is_allow_listed_and_leak_free(client, log_output, caplog):
    client.raise_request_exception = False
    client.cookies["sessionid"] = "sessionsecret"
    body = json.dumps({"phone": SECRETS[0], "email": SECRETS[1], "address": SECRETS[2]})
    for path in ("/validate", "/raise/boom", "/plain-failure", "/missing", "/things/7"):
        client.post(
            f"{path}?phone={SECRETS[0]}&email={SECRETS[1]}",
            body,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer tok-9f8e",
        )
    output = log_output.getvalue()
    assert output
    for secret in SECRETS:
        assert secret not in output
        assert secret not in caplog.text
        assert all(secret not in str(r.__dict__) for r in caplog.records)
    allowed = {"timestamp", "level", "logger", *ALLOWED_FIELDS}
    for entry in lines(log_output):
        assert set(entry) <= allowed


def test_request_line_fields_and_request_id(client, log_output):
    client.raise_request_exception = False
    response = client.get("/things/7")
    (entry,) = [e for e in lines(log_output) if e["logger"] == REQUEST_LOGGER]
    assert entry["request_id"] == response["X-Request-ID"]
    assert entry["method"] == "GET"
    assert entry["route"] == "things/<int:pk>"
    assert entry["status"] == 404
    assert isinstance(entry["duration_ms"], float)
    assert "exception_class" not in entry

    client.get("/raise/boom")
    entry = [e for e in lines(log_output) if e["logger"] == REQUEST_LOGGER][-1]
    assert entry["status"] == 500
    assert entry["exception_class"] == "RuntimeError"
    assert entry["level"] == "ERROR"


def test_non_drf_exception_logs_class_only(client, log_output):
    client.raise_request_exception = False
    response = client.get("/plain-failure")
    entry = [e for e in lines(log_output) if e["logger"] == REQUEST_LOGGER][-1]
    assert entry["exception_class"] == "RuntimeError"
    assert entry["request_id"] == response["X-Request-ID"]


def test_unresolved_route_is_logged_without_the_path(client, log_output):
    client.get("/no/such/0300-1234567")
    assert "0300-1234567" not in log_output.getvalue()
    assert lines(log_output)[-1]["route"] == "unresolved"


def test_formatter_drops_unlisted_extras():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg 0300-1234567", None, None)
    record.body = "0300-1234567"
    record.status = 200
    out = JsonFormatter().format(record)
    assert "0300-1234567" not in out
    assert json.loads(out)["status"] == 200


def test_settings_silence_path_logging_loggers(settings):
    for name in ("django.request", "django.server", "django.channels.server", "daphne.server"):
        assert settings.LOGGING["loggers"][name]["propagate"] is False
    assert settings.LOGGING["root"]["handlers"] == ["stdout"]


def test_the_server_script_discards_daphnes_own_access_log():
    """Daphne logs every path with its query string unless told otherwise (AD-26)."""
    script = (Path(__file__).resolve().parents[2] / "scripts" / "dev_server.sh").read_text()
    command = next(line for line in script.splitlines() if line.startswith("exec "))
    assert "--access-log /dev/null" in command
