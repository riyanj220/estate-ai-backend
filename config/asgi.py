import os

from django.core.asgi import get_asgi_application

# No default: the settings module must be chosen explicitly (scripts/dev_server.sh
# selects local), so a deployment can never fall back to development settings.
if "DJANGO_SETTINGS_MODULE" not in os.environ:
    raise RuntimeError("DJANGO_SETTINGS_MODULE must be set.")

django_application = get_asgi_application()

from apps.kernel.upload_limit import BodyLimitASGI  # noqa: E402


def _too_large():
    import json

    from apps.kernel.errors import field_message
    from apps.kernel.request_logging import REQUEST_ID_HEADER, new_request_id
    from apps.presentation.api.exceptions import build_envelope

    request_id = new_request_id()
    body = json.dumps(
        build_envelope("validation_error", request_id, {"file": [field_message("too_large")]})
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"cache-control", b"no-store"),
        (REQUEST_ID_HEADER.lower().encode(), request_id.encode()),
        (b"content-length", str(len(body)).encode()),
    ]
    return 400, headers, body


# Bounds the buffering of an image upload before Django reads the body (Story 2.9).
application = BodyLimitASGI(django_application, _too_large)
