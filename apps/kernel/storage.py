"""Storage port for uploaded media (AD-15) and its filesystem adapter. `settings.MEDIA_BACKEND`
chooses this adapter (the default) or the S3-compatible one (`kernel.storage_s3`).


The port is four small operations on keys: `put`, `get`, `delete`, `exists`. A key is a random
name, filed under its owner and what it belongs to:
`users/<account id>/listings/<listing id>/<name>` or `users/<account id>/profile/<name>`. The
folders hold ids only, never a name or an address of a person or a place, and the port never
produces a URL: serving a file is the media endpoint's job (`ListingImageFileView`, after an access
check), through `get`. Keys without folders (files stored earlier) stay valid; the filesystem
adapter shards those into two directory levels. Any other form is refused (so a key can never
traverse out of the root), and writes are atomic (temp file in the same directory, fsync, rename).

The adapter also lists old files so retention can find files that have no row (`iter_old_keys`,
`remove_old_temp_files`); that is a maintenance operation, not part of serving.
"""

import logging
import os
import re
import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("estate.media")

KEY_BYTES = 32
# `secrets.token_urlsafe(32)` is 43 characters of the URL-safe base64 alphabet.
_NAME = r"[A-Za-z0-9_-]{43}"
_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_KEY = re.compile(rf"(?:users/{_ID}/(?:listings/{_ID}|profile)/)?{_NAME}")
_TEMP_PREFIX = ".tmp-"


class InvalidKey(ValueError):
    """The key is not in a format `new_key` produces."""


def new_key(owner: uuid.UUID | None = None, listing: uuid.UUID | None = None) -> str:
    """A fresh random key. With an `owner` it is filed under that account: in the Listing's folder,
    or in `profile` without one."""
    name = secrets.token_urlsafe(KEY_BYTES)
    if owner is None:
        return name
    folder = f"listings/{listing}" if listing is not None else "profile"
    return f"users/{owner}/{folder}/{name}"


def is_valid_key(key: object) -> bool:
    return isinstance(key, str) and _KEY.fullmatch(key) is not None and key.isascii()


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


class FilesystemStorage:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def _directory(self, key: str) -> Path:
        if not is_valid_key(key):
            raise InvalidKey("not a storage key")
        if "/" in key:
            return self._root.joinpath(*key.split("/")[:-1])
        return self._root / key[:2] / key[2:4]

    def _path(self, key: str) -> Path:
        return self._directory(key) / key.rsplit("/", 1)[-1]

    def _make_directories(self, key: str) -> None:
        """Every level below the root, mode 0700 whatever the umask (`mkdir` mode is masked)."""
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root
        for part in self._directory(key).relative_to(self._root).parts:
            path = path / part
            path.mkdir(mode=0o700, exist_ok=True)
            path.chmod(0o700)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def put(self, key: str, data: bytes) -> None:
        if not isinstance(data, bytes | bytearray | memoryview):
            raise TypeError("data must be bytes")
        directory = self._directory(key)
        self._make_directories(key)
        target = self._path(key)
        temp = directory / f"{_TEMP_PREFIX}{uuid.uuid4().hex}"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)  # atomic: a reader sees all of the file or none of it
            self._sync_directory(directory)  # the rename itself survives a crash
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)  # idempotent

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def _walk(self) -> Iterator[Path]:
        """Every entry in a place where a stored file can be: the shards and the owner folders."""
        if not self._root.is_dir():
            return
        for first in sorted(self._root.iterdir()):
            if first.is_dir() and len(first.name) == 2:
                for second in sorted(first.iterdir()):
                    if second.is_dir() and len(second.name) == 2:
                        yield from sorted(second.iterdir())
        for pattern in ("users/*/profile/*", "users/*/listings/*/*"):
            yield from sorted(self._root.glob(pattern))

    def iter_old_keys(self, older_than: float) -> Iterator[str]:
        """Keys of stored files last written before the Unix time `older_than`."""
        for path in self._walk():
            key = path.relative_to(self._root).as_posix()
            if not key.startswith("users/"):
                key = path.name
            if not is_valid_key(key) or path != self._path(key):
                continue
            try:
                if path.is_file() and path.stat().st_mtime < older_than:
                    yield key
            except OSError:
                continue  # removed meanwhile

    def remove_old_temp_files(self, older_than: float, limit: int) -> int:
        """Delete up to `limit` leftover temp files (a crash between write and rename)."""
        removed = 0
        for path in self._walk():
            if removed >= limit:
                break
            if not path.name.startswith(_TEMP_PREFIX):
                continue
            try:
                if path.is_file() and path.stat().st_mtime < older_than:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed


def get_storage() -> Storage:
    """The configured store. Read per call so a test can point `MEDIA_ROOT` elsewhere."""
    backend = settings.MEDIA_BACKEND
    if backend == "filesystem":
        return FilesystemStorage(settings.MEDIA_ROOT)
    if backend == "s3":
        from apps.kernel import storage_s3  # boto3 is loaded only where it is used

        return storage_s3.S3Storage(
            storage_s3.client(
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
