"""The S3 adapter against an in-memory fake client: no network."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from apps.kernel import media, storage
from apps.kernel.storage import s3 as storage_s3
from tests.imaging import make_image

DATA = b"image-bytes"


class FakeS3:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.fail: Exception | None = None

    def _call(self):
        if self.fail:
            raise self.fail

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self._call()
        self.objects[Key] = (Body, datetime.now(UTC))

    def _find(self, key, operation):
        self._call()
        if key not in self.objects:
            code = "404" if operation == "HeadObject" else "NoSuchKey"
            raise ClientError({"Error": {"Code": code}}, operation)
        return self.objects[key][0]

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self._find(Key, "GetObject"))}

    def head_object(self, *, Bucket, Key):
        self._find(Key, "HeadObject")

    def delete_object(self, *, Bucket, Key):
        self._call()
        self.objects.pop(Key, None)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class Paginator:
            def paginate(self, *, Bucket, Prefix):
                fake._call()
                for key in sorted(fake.objects):  # one object per page
                    if key.startswith(Prefix):
                        yield {"Contents": [{"Key": key, "LastModified": fake.objects[key][1]}]}

        return Paginator()


@pytest.fixture
def fake(settings, monkeypatch):
    settings.MEDIA_BACKEND = "s3"
    settings.S3_ENDPOINT_URL = "http://s3.invalid"
    settings.S3_REGION = "test"
    settings.S3_ACCESS_KEY_ID = "test"
    settings.S3_SECRET_ACCESS_KEY = "test"
    settings.S3_BUCKET = "bucket"
    settings.S3_PREFIX = "media/"
    s3 = FakeS3()
    monkeypatch.setattr(storage_s3, "client", lambda *args: s3)
    return s3


def test_the_default_backend_is_the_filesystem(settings):
    assert settings.MEDIA_BACKEND == "filesystem"
    assert isinstance(storage.get_storage(), storage.FilesystemStorage)


def test_put_get_exists_and_idempotent_delete(fake):
    store = storage.get_storage()
    key = storage.new_key()
    assert not store.exists(key)
    store.put(key, DATA)
    assert list(fake.objects) == ["media/" + key]
    assert store.exists(key) and store.get(key) == DATA
    store.delete(key)
    store.delete(key)
    assert not store.exists(key)
    with pytest.raises(FileNotFoundError):
        store.get(key)


@pytest.mark.parametrize("key", ["../x", "a" * 42, "media/" + "a" * 37])
def test_only_storage_keys_reach_the_store(fake, key):
    store = storage.get_storage()
    for call in (store.get, store.delete, store.exists):
        with pytest.raises(storage.InvalidKey):
            call(key)
    with pytest.raises(storage.InvalidKey):
        store.put(key, DATA)


@pytest.mark.parametrize(
    "failure",
    [
        ClientError({"Error": {"Code": "AccessDenied", "Message": "secret-bucket"}}, "GetObject"),
        EndpointConnectionError(endpoint_url="http://secret-host.invalid"),
    ],
)
def test_provider_failures_are_os_errors_without_details(fake, failure):
    store = storage.get_storage()
    key = storage.new_key()
    fake.fail = failure
    for call in (store.get, store.delete, store.exists, lambda k: store.put(k, DATA)):
        with pytest.raises(storage_s3.StorageUnavailable) as caught:
            call(key)
        assert isinstance(caught.value, OSError)
        assert "secret" not in str(caught.value) and key not in str(caught.value)
    storage.discard(key)  # never raises


def test_old_keys_ignore_young_and_foreign_objects(fake):
    store = storage.get_storage()
    old, young = storage.new_key(), storage.new_key()
    store.put(old, DATA)
    store.put(young, DATA)
    past = datetime.now(UTC) - timedelta(days=2)
    fake.objects["media/" + old] = (DATA, past)
    fake.objects["media/not-a-key.txt"] = (DATA, past)
    fake.objects["elsewhere/" + storage.new_key()] = (DATA, past)
    cutoff = (datetime.now(UTC) - timedelta(days=1)).timestamp()
    assert list(store.iter_old_keys(cutoff)) == [old]
    assert store.remove_old_temp_files(cutoff, 10) == 0


@pytest.mark.django_db
def test_media_service_reads_deletes_after_commit_and_purges_strays(
    fake, django_capture_on_commit_callbacks
):
    actor = _user_actor()
    asset = media.store_image(actor, make_image(), account_type="individual", attach=True)
    stored_name = "media/" + asset.storage_key
    assert stored_name in fake.objects
    assert media.read_asset(asset.id)[1] == fake.objects[stored_name][0]

    fake.fail = EndpointConnectionError(endpoint_url="http://s3.invalid")
    assert media.read_asset(asset.id) is None  # unreachable: not servable, no error page
    fake.fail = None

    stray = storage.new_key()
    fake.objects["media/" + stray] = (DATA, datetime.now(UTC) - timedelta(days=2))
    assert media.purge_orphans(datetime.now(UTC), dry_run=True) == 1
    assert media.purge_orphans(datetime.now(UTC)) == 1
    assert set(fake.objects) == {stored_name}

    with django_capture_on_commit_callbacks(execute=True):
        media.delete_asset(actor, asset.id)
        assert stored_name in fake.objects  # not before the commit
    assert fake.objects == {}


def _user_actor():
    import uuid

    from apps.kernel.actor import Actor

    return Actor.user(uuid.uuid4(), "test")
