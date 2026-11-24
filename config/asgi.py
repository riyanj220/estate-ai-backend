import os

from django.core.asgi import get_asgi_application

# No default: the settings module must be chosen explicitly (scripts/dev_server.sh
# selects local), so a deployment can never fall back to development settings.
if "DJANGO_SETTINGS_MODULE" not in os.environ:
    raise RuntimeError("DJANGO_SETTINGS_MODULE must be set.")

django_application = get_asgi_application()

from apps.kernel.upload_limit import BodyLimitASGI  # noqa: E402


def _refusal(field: str, code: str):
    import json

    from apps.kernel.errors import field_message
    from apps.kernel.request_logging import REQUEST_ID_HEADER, new_request_id
    from apps.presentation.api.exceptions import build_envelope

    request_id = new_request_id()
    body = json.dumps(
        build_envelope("validation_error", request_id, {field: [field_message(code)]})
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"cache-control", b"no-store"),
        (REQUEST_ID_HEADER.lower().encode(), request_id.encode()),
        (b"content-length", str(len(body)).encode()),
    ]
    return 400, headers, body


def _too_large():
    return _refusal("file", "too_large")


def _order_too_large():
    return _refusal("order", "invalid")


# Bounds the buffering of an image upload (and of the small reorder body) before Django reads it.
application = BodyLimitASGI(django_application, _too_large, _order_too_large)
