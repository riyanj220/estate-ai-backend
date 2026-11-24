"""Storage port for uploaded media (AD-15) and its filesystem adapter.

The port is four small operations on opaque random keys: `put`, `get`, `delete`, `exists`. A key is
never derived from a name, an owner or a Listing, and the port never produces a URL: serving a file
is the media endpoint's job (`ListingImageFileView`, after an access check), through `get`. The
filesystem adapter shards keys into two directory levels under `MEDIA_ROOT`, refuses any key that
is not the exact random format (so a key can never traverse out of the root) and writes atomically
(temp file in the same directory, fsync, rename).

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

logger = logging.getLogger("estate.media")

KEY_BYTES = 32
# `secrets.token_urlsafe(32)` is 43 characters of the URL-safe base64 alphabet.
_KEY = re.compile(r"[A-Za-z0-9_-]{43}")
_TEMP_PREFIX = ".tmp-"


class InvalidKey(ValueError):
    """The key is not the random format `new_key` produces."""


def new_key() -> str:
    return secrets.token_urlsafe(KEY_BYTES)


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
        return self._root / key[:2] / key[2:4]

    def _path(self, key: str) -> Path:
        return self._directory(key) / key

    def _make_directories(self, key: str) -> None:
        """Both shard levels, mode 0700 whatever the umask (`mkdir` mode is masked)."""
        self._root.mkdir(parents=True, exist_ok=True)
        first = self._root / key[:2]
        for path in (first, first / key[2:4]):
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
        target = directory / key
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
        if not self._root.is_dir():
            return
        for first in sorted(self._root.iterdir()):
            if not first.is_dir() or len(first.name) != 2:
                continue
            for second in sorted(first.iterdir()):
                if not second.is_dir() or len(second.name) != 2:
                    continue
                yield from sorted(second.iterdir())

    def iter_old_keys(self, older_than: float) -> Iterator[str]:
        """Keys of stored files last written before the Unix time `older_than`."""
        for path in self._walk():
            if not is_valid_key(path.name) or path.parent != self._directory(path.name):
                continue
            try:
                if path.is_file() and path.stat().st_mtime < older_than:
                    yield path.name
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
    return FilesystemStorage(settings.MEDIA_ROOT)


def discard(key: str) -> None:
    """Best-effort removal of a file; a failure is logged by class only (retention cleans up)."""
    try:
        get_storage().delete(key)
    except Exception as exc:
        logger.warning(
            "media_file_delete_failed",
            extra={"event": "media_file_delete_failed", "exception_class": type(exc).__name__},
        )
