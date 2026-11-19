import io
import json
import logging

import pytest
from asgiref.sync import async_to_sync
from django.test import AsyncClient

from apps.kernel.request_logging import ALLOWED_FIELDS, REQUEST_LOGGER, JsonFormatter


@pytest.mark.urls("tests.api.urls")
def test_async_path_header_envelope_and_log(caplog):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.INFO)

    async def run():
        client = AsyncClient(raise_request_exception=False)
        return await client.get("/raise/boom?phone=0300-1234567"), await client.get("/raise/stale")

    try:
        boom, stale = async_to_sync(run)()
    finally:
        root.removeHandler(handler)
    for response in (boom, stale):
        assert response.json()["error"]["request_id"] == response["X-Request-ID"]
    entries = [json.loads(x) for x in stream.getvalue().splitlines()]
    entries = [e for e in entries if e["logger"] == REQUEST_LOGGER]
    assert len(entries) == 2
    assert entries[0]["request_id"] == boom["X-Request-ID"]
    assert entries[0]["status"] == 500
    assert entries[0]["exception_class"] == "RuntimeError"
    assert set(entries[0]) <= {"timestamp", "level", "logger", *ALLOWED_FIELDS}
    assert "0300-1234567" not in stream.getvalue()


def test_default_urlconf_404_uses_envelope(client):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response["X-Request-ID"]
