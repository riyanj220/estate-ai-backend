import os
import re
import stat
import time
import uuid

import pytest

from apps.kernel import storage

DATA = b"fake bytes of a stored file"


@pytest.fixture
def store(media_root):
    return storage.get_storage()


def test_keys_are_random_url_safe_and_long():
    keys = {storage.new_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", k) for k in keys)
    assert all(storage.is_valid_key(k) for k in keys)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../../etc/passwd",
        "a" * 42,
        "a" * 44,
        "a" * 42 + "/",
        "a" * 42 + ".",
        ("a" * 20) + "/../" + ("b" * 19),
        "a" * 41 + "\n",
        "é" + "a" * 42,
        None,
        42,
    ],
)
def test_the_adapter_refuses_any_key_that_is_not_the_random_format(store, key):
    assert not storage.is_valid_key(key)
    for call in (
        lambda: store.put(key, DATA),
        lambda: store.get(key),
        lambda: store.delete(key),
        lambda: store.exists(key),
    ):
        with pytest.raises(storage.InvalidKey):
            call()


def test_a_key_is_filed_under_its_owner_and_listing(store, media_root):
    owner, listing = uuid.uuid4(), uuid.uuid4()
    photo, picture = storage.new_key(owner, listing), storage.new_key(owner)
    assert photo.startswith(f"users/{owner}/listings/{listing}/")
    assert picture.startswith(f"users/{owner}/profile/")
    for key in (photo, picture):
        assert storage.is_valid_key(key)
        store.put(key, DATA)
        assert (media_root / key).read_bytes() == DATA
        assert stat.S_IMODE((media_root / key).parent.stat().st_mode) == 0o700
    assert sorted(store.iter_old_keys(time.time() + 60)) == sorted((photo, picture))
    for traversal in (
        f"users/{owner}/../x",
        f"users/{owner}/profile/..",
        photo + "/..",
        f"users/x/profile/{'a' * 43}",
    ):
        assert not storage.is_valid_key(traversal)
    store.delete(photo)
    assert not store.exists(photo) and store.exists(picture)


def test_put_get_exists_delete_round_trip(store, media_root):
    key = storage.new_key()
    assert not store.exists(key)
    store.put(key, DATA)
    assert store.exists(key) and store.get(key) == DATA
    # two directory levels from the key, nothing derived from names or owners
    assert (media_root / key[:2] / key[2:4] / key).read_bytes() == DATA
    store.delete(key)
    store.delete(key)  # idempotent
    assert not store.exists(key)


def test_files_are_private_to_the_process(store, media_root):
    key = storage.new_key()
    store.put(key, DATA)
    mode = stat.S_IMODE((media_root / key[:2] / key[2:4] / key).stat().st_mode)
    assert mode == 0o600


def test_the_port_has_no_url_operation(store):
    names = {n for n in dir(store) if not n.startswith("_")}
    assert not {n for n in names if "url" in n.lower() or "path" in n.lower()}


def test_a_write_is_atomic_and_leaves_no_temp_file_on_failure(store, media_root, monkeypatch):
    key = storage.new_key()

    def fail(*args, **kwargs):
        raise OSError("disk exploded")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.put(key, DATA)
    monkeypatch.undo()
    assert not store.exists(key)
    assert [p for p in media_root.rglob("*") if p.is_file()] == []


def test_a_second_put_replaces_whole_files_only(store):
    key = storage.new_key()
    store.put(key, DATA)
    store.put(key, b"other")
    assert store.get(key) == b"other"


def test_iter_old_keys_lists_only_old_valid_files(store, media_root):
    old, young = storage.new_key(), storage.new_key()
    store.put(old, DATA)
    store.put(young, DATA)
    past = time.time() - 3 * 24 * 3600
    path = media_root / old[:2] / old[2:4] / old
    os.utime(path, (past, past))
    (media_root / "not-a-shard.txt").write_text("x")
    stray = media_root / old[:2] / old[2:4] / "notakey"
    stray.write_text("x")
    os.utime(stray, (past, past))
    assert list(store.iter_old_keys(time.time() - 24 * 3600)) == [old]


def test_old_temp_files_are_swept_and_young_ones_kept(store, media_root):
    key = storage.new_key()
    store.put(key, DATA)
    directory = media_root / key[:2] / key[2:4]
    old, young = directory / ".tmp-old", directory / ".tmp-young"
    old.write_bytes(b"x")
    young.write_bytes(b"x")
    past = time.time() - 3 * 24 * 3600
    os.utime(old, (past, past))
    assert store.remove_old_temp_files(time.time() - 24 * 3600, 10) == 1
    assert not old.exists() and young.exists() and store.exists(key)


def test_discard_never_raises_and_logs_only_the_class(store, monkeypatch, caplog):
    class Broken:
        def delete(self, key):
            raise PermissionError("/secret/path/with-file-name.jpg")

    monkeypatch.setattr(storage, "get_storage", lambda: Broken())
    with caplog.at_level("WARNING", logger="estate.media"):
        storage.discard(storage.new_key())
    assert [r.exception_class for r in caplog.records] == ["PermissionError"]
    assert "secret" not in caplog.text and "with-file-name" not in caplog.text


def test_both_shard_directories_are_private_whatever_the_umask(store, media_root):
    old = os.umask(0o000)
    try:
        key = storage.new_key()
        store.put(key, DATA)
    finally:
        os.umask(old)
    first = media_root / key[:2]
    for directory in (first, first / key[2:4]):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((first / key[2:4] / key).stat().st_mode) == 0o600


def test_the_parent_directory_is_synced_after_the_rename(store, monkeypatch, media_root):
    order = []
    real_replace, real_fsync = os.replace, os.fsync

    def replace(src, dst):
        order.append("replace")
        real_replace(src, dst)

    def fsync(fd):
        order.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", fsync)
    store.put(storage.new_key(), DATA)
    assert order == ["file", "replace", "dir"]
