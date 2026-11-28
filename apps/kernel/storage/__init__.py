"""Storage port for uploaded media (AD-15). `settings.MEDIA_BACKEND` chooses the adapter.

    keys.py         how files are named and foldered
    filesystem.py   local disk (the default)
    s3.py           a private S3-compatible bucket (Supabase Storage)

The port is four small operations on keys: `put`, `get`, `delete`, `exists`. It never produces a
URL: serving a file is the media endpoint's job (`ListingImageFileView`, after an access check),
through `get`. Both adapters refuse any key not in the layout of `keys.py`, so a key can never
traverse out of the store, and both list old files so retention can find those with no row
(`iter_old_keys`, `remove_old_temp_files`); that is a maintenance operation, not part of serving.
"""

import logging
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.kernel.storage.filesystem import FilesystemStorage
from apps.kernel.storage.keys import KEY_BYTES, InvalidKey, is_valid_key, new_key

__all__ = [
    "KEY_BYTES",
    "FilesystemStorage",
    "InvalidKey",
    "Storage",
    "discard",
    "get_storage",
    "is_valid_key",
    "new_key",
]

logger = logging.getLogger("estate.media")


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


def get_storage() -> Storage:
    """The configured store. Read per call so a test can point `MEDIA_ROOT` elsewhere."""
    backend = settings.MEDIA_BACKEND
    if backend == "filesystem":
        return FilesystemStorage(settings.MEDIA_ROOT)
    if backend == "s3":
        from apps.kernel.storage import s3  # boto3 is loaded only where it is used

        return s3.S3Storage(
            s3.client(
                settings.S3_ENDPOINT_URL,
                settings.S3_REGION,
                settings.S3_ACCESS_KEY_ID,
                settings.S3_SECRET_ACCESS_KEY,
            ),
            settings.S3_BUCKET,
            settings.S3_PREFIX,
        )
    raise ImproperlyConfigured("MEDIA_BACKEND must be 'filesystem' or 's3'.")


def discard(key: str) -> None:
    """Best-effort removal of a file; a failure is logged by class only (retention cleans up)."""
    try:
        get_storage().delete(key)
    except Exception as exc:
        logger.warning(
            "media_file_delete_failed",
            extra={"event": "media_file_delete_failed", "exception_class": type(exc).__name__},
        )
