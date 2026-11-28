"""S3-compatible adapter of the storage port (AD-15), used for a private Supabase Storage bucket.

The same operations as the filesystem adapter, on the same opaque random keys, stored as
`<prefix><key>`. It never makes a public or signed URL: the bucket is private and the media
endpoint stays the only way to serve a file, through `get`. Objects are at most one processed
image, so they are read and written whole (no streaming).

Every provider failure becomes `StorageUnavailable`, an `OSError` like a failing disk, and a
missing object becomes `FileNotFoundError`; neither message carries a key, bucket or endpoint.

One bucket (and prefix) belongs to ONE database: retention deletes old objects that have no row.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from apps.kernel.storage.keys import InvalidKey, is_valid_key

_MISSING = frozenset({"404", "NoSuchKey", "NotFound"})


class StorageUnavailable(OSError):
    """The object store could not be reached or refused the request."""


@contextmanager
def _provider_errors():
    try:
        yield
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _MISSING:
            raise FileNotFoundError("no such object") from None
        raise StorageUnavailable(f"object store error ({code or 'unknown'})") from None
    except BotoCoreError as exc:
        raise StorageUnavailable(f"object store error ({type(exc).__name__})") from None


@lru_cache(maxsize=4)
def client(endpoint: str, region: str, access_key: str, secret_key: str):
    """One client per configuration; boto3 clients are safe to share between threads."""
    return boto3.session.Session().client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=20,
            retries={"max_attempts": 3, "mode": "standard"},
            # S3-compatible stores do not all accept the newer default checksum headers.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


class S3Storage:
    def __init__(self, s3, bucket: str, prefix: str = "media/") -> None:
        self._s3 = s3
        self._bucket = bucket
        self._prefix = prefix

    def _name(self, key: str) -> str:
        if not is_valid_key(key):
            raise InvalidKey("not a storage key")
        return self._prefix + key

    def put(self, key: str, data: bytes) -> None:
        if not isinstance(data, bytes | bytearray | memoryview):
            raise TypeError("data must be bytes")
        name = self._name(key)
        with _provider_errors():
            self._s3.put_object(
                Bucket=self._bucket,
                Key=name,
                Body=bytes(data),
                ContentType="application/octet-stream",
            )

    def get(self, key: str) -> bytes:
        name = self._name(key)
        with _provider_errors():
            body = self._s3.get_object(Bucket=self._bucket, Key=name)["Body"]
            try:
                return body.read()
            finally:
                body.close()

    def delete(self, key: str) -> None:
        name = self._name(key)
        try:
            with _provider_errors():
                self._s3.delete_object(Bucket=self._bucket, Key=name)
        except FileNotFoundError:
            pass  # idempotent

    def exists(self, key: str) -> bool:
        name = self._name(key)
        try:
            with _provider_errors():
                self._s3.head_object(Bucket=self._bucket, Key=name)
        except FileNotFoundError:
            return False
        return True

    def iter_old_keys(self, older_than: float) -> Iterator[str]:
        """Keys of stored objects last written before the Unix time `older_than`. Anything under
        the prefix that is not exactly a storage key is left alone."""
        with _provider_errors():
            pages = self._s3.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=self._prefix
            )
            for page in pages:
                for item in page.get("Contents", ()):
                    key = item["Key"][len(self._prefix) :]
                    if is_valid_key(key) and item["LastModified"].timestamp() < older_than:
                        yield key

    def remove_old_temp_files(self, older_than: float, limit: int) -> int:
        return 0  # an upload is one atomic request: it leaves no temporary object
