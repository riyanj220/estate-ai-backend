"""The filesystem adapter of the storage port (the default, used by tests and local work). Keys
with folders are kept as they read; keys without are sharded into two directory levels. Writes are
atomic (temp file in the same directory, fsync, rename); files are 0600 and directories 0700."""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

from apps.kernel.storage.keys import InvalidKey, is_valid_key

_TEMP_PREFIX = ".tmp-"


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
