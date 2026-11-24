import importlib
import json

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncRequestFactory, RequestFactory

from apps.kernel.upload_limit import BodyLimitASGI, BodySizeMiddleware, body_too_large

CAP = 6 * 1024 * 1024


UPLOAD = "/api/v1/listings/3f2c0c52-6f8d-4a53-9d0e-2f1a8e2d9a11/images"


def run(length, path=UPLOAD, method="POST"):
    seen = {}

    def get_response(request):
        seen["flag"] = body_too_large(request)
        seen["length"] = request.META.get("CONTENT_LENGTH")
        return object()

    request = RequestFactory().generic(
        method, path, data=b"", content_type="multipart/form-data; boundary=b"
    )
    if length is None:
        request.META.pop("CONTENT_LENGTH", None)
    else:
        request.META["CONTENT_LENGTH"] = length
    BodySizeMiddleware(get_response)(request)
    return seen


def test_the_cap_is_six_mebibytes_and_django_json_limits_are_not_loosened(settings):
    assert settings.MAX_REQUEST_BODY_BYTES == CAP
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE == 2_621_440  # Django's own default
    assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE == CAP
    assert settings.FILE_UPLOAD_HANDLERS == [
        "django.core.files.uploadhandler.MemoryFileUploadHandler"
    ]


def test_a_body_at_the_cap_passes_and_one_byte_over_is_flagged_and_zeroed():
    assert run(str(CAP)) == {"flag": False, "length": str(CAP)}
    assert run(str(CAP + 1)) == {"flag": True, "length": "0"}


@pytest.mark.parametrize("value", [None, "", "abc", "-5", "1e9", "١٢٣", " 5", "9" * 40])
def test_a_missing_or_malformed_length_on_an_upload_is_flagged(value):
    assert run(value) == {"flag": True, "length": "0"}


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/session", "POST"),
        ("/api/v1/listings", "POST"),
        ("/api/v1/listings/abc", "POST"),
        ("/api/v1/listings/abc/images/def", "POST"),
        ("/api/v1/listings/abc/images/extra/x", "POST"),
        (UPLOAD, "GET"),
        (UPLOAD, "DELETE"),
    ],
)
def test_every_other_route_is_left_alone(path, method):
    big = str(CAP * 10)
    assert run(big, path, method) == {"flag": False, "length": big}
    assert run(None, path, method)["flag"] is False


def test_a_malformed_listing_id_is_still_covered():
    assert run(str(CAP + 1), "/api/v1/listings/not-a-uuid/images")["flag"] is True


def test_the_async_path_does_the_same():
    async def get_response(request):
        return (body_too_large(request), request.META["CONTENT_LENGTH"])

    request = AsyncRequestFactory().post(
        UPLOAD, data=b"", content_type="multipart/form-data; boundary=b"
    )
    request.META["CONTENT_LENGTH"] = str(CAP + 1)
    assert async_to_sync(BodySizeMiddleware(get_response))(request) == (True, "0")


# --- the ASGI wrapper ---------------------------------------------------------------------------


def scope(path=UPLOAD, method="POST", length=None):
    headers = [(b"content-type", b"multipart/form-data; boundary=b")]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "asgi": {"version": "3.0"},
    }


def reject():
    return 400, [(b"content-type", b"application/json")], b'{"error": "too_large"}'


class Recorder:
    def __init__(self):
        self.seen = []
        self.called = False

    async def app(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            self.seen.append(len(message.get("body", b"")))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def drive(app, sc, chunks, reads):
    """Feed `chunks` through the wrapper; `reads` counts how many the server took."""
    queue = list(chunks)
    sent = []

    async def receive():
        reads.append(1)
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(sc, receive, send)
    return sent


def body_chunks(sizes):
    return [
        {"type": "http.request", "body": b"x" * n, "more_body": i < len(sizes) - 1}
        for i, n in enumerate(sizes)
    ]


def test_a_declared_oversize_length_is_refused_without_reading_a_byte():
    recorder, reads = Recorder(), []
    app = BodyLimitASGI(recorder.app, reject)
    sent = async_to_sync(drive)(app, scope(length=CAP + 1), body_chunks([10]), reads)
    assert sent[0]["status"] == 400 and not recorder.called and reads == []


def test_a_chunked_body_with_no_length_is_cut_off_once_past_the_cap():
    recorder, reads = Recorder(), []
    app = BodyLimitASGI(recorder.app, reject)
    chunks = body_chunks([CAP // 2, CAP // 2, 10, 10, 10, 10])
    sent = async_to_sync(drive)(app, scope(), chunks, reads)
    assert sent[0]["status"] == 400 and not recorder.called
    assert len(reads) == 3  # it stopped reading at the first chunk past the cap


def test_a_lying_small_length_cannot_buffer_past_the_cap():
    recorder, reads = Recorder(), []
    app = BodyLimitASGI(recorder.app, reject)
    sent = async_to_sync(drive)(app, scope(length=100), body_chunks([CAP, CAP]), reads)
    assert sent[0]["status"] == 400 and not recorder.called and len(reads) == 2


def test_a_body_within_the_cap_is_replayed_to_the_app_whole():
    recorder, reads = Recorder(), []
    app = BodyLimitASGI(recorder.app, reject)
    sent = async_to_sync(drive)(app, scope(length=30), body_chunks([10, 10, 10]), reads)
    assert recorder.seen == [10, 10, 10] and sent[0]["status"] == 204


def test_other_requests_and_scopes_pass_straight_through():
    for sc in (scope("/api/v1/session"), scope(method="GET"), {"type": "lifespan"}):
        recorder, reads = Recorder(), []
        app = BodyLimitASGI(recorder.app, reject)
        sent = async_to_sync(drive)(app, sc, body_chunks([CAP * 2]), reads)
        if sc["type"] == "http":
            assert recorder.called and sent[0]["status"] == 204


def test_the_real_application_answers_the_standard_envelope():
    import config.asgi as asgi

    assert isinstance(asgi.application, BodyLimitASGI)
    sent = async_to_sync(drive)(asgi.application, scope(length=CAP + 1), body_chunks([10]), [])
    headers = dict(sent[0]["headers"])
    assert sent[0]["status"] == 400 and headers[b"content-type"] == b"application/json"
    body = json.loads(sent[1]["body"])["error"]
    assert (body["code"], list(body["fields"])) == ("validation_error", ["file"])
    assert body["request_id"].encode() == headers[b"x-request-id"]
    assert headers[b"cache-control"] == b"no-store"


def test_media_root_must_be_an_absolute_path(monkeypatch):
    import config.settings.base as base

    monkeypatch.setenv("MEDIA_ROOT", "relative/media")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base)
    monkeypatch.delenv("MEDIA_ROOT")
    importlib.reload(base)
    assert base.MEDIA_ROOT.endswith("/media")
