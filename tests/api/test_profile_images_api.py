"""Profile picture, logo and public logo routes over HTTP (Story 2.11), CSRF checks on."""

import io
import json
import logging
import threading
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.utils import timezone

from apps.identity import services as identity_services
from apps.identity.models import Account, AgencyProfile, IndividualProfile
from apps.kernel import runtime, storage
from apps.kernel.actor import Actor
from apps.kernel.errors import field_message
from apps.kernel.models import AuditRecord, MediaAsset, RateLimitPolicy, StorageUsage
from apps.kernel.request_logging import JsonFormatter
from tests.api.test_listing_images_api import csrf, refused, stored_files
from tests.api.test_listings_api import ADMIN_PASSWORD, sign_in
from tests.imaging import make_image
from tests.listings.support import SYSTEM, make_account, seed_cap, seed_media_policies

MB = 1024 * 1024
PNG = make_image("PNG", (500, 400))
JPEG = make_image("JPEG", (1200, 800))
IMAGE_KEYS = {"content_type", "width", "height", "bytes", "url"}
ROUTES = {
    "individual": "/api/v1/me/profile/image",
    "agency": "/api/v1/me/agency-profile/image",
}
PROFILE = {"individual": "/api/v1/me/profile", "agency": "/api/v1/me/agency-profile"}
ADMIN_ACTOR = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "r")


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def signed_owner(kind="individual", email=None, state="active"):
    """A signed-in browser for an account of this type with its profile row."""
    email = email or f"{kind}.owner@example.com"
    view = make_account(email, kind, state)
    if kind == "individual":
        IndividualProfile.objects.create(
            account_id=view.id, full_name="Jane Doe", phone="+923001234567"
        )
    else:
        AgencyProfile.objects.create(
            account_id=view.id,
            agency_name="Skyline Estates",
            business_phone="+923215550123",
            office_address="Suite 4, Harbour Tower",
            city_id=uuid.uuid4(),
        )
    client = sign_in(email)
    client.account_id = view.id
    return client


def multipart(parts: dict):
    return encode_multipart(BOUNDARY, parts)


def put(client, kind, data=PNG, name="photo.jpg", declared="image/jpeg", token=True, **parts):
    body = {"file": SimpleUploadedFile(name, data, content_type=declared), **parts}
    extra = csrf(client) if token else {}
    return client.generic(
        "PUT", ROUTES[kind], multipart(body), content_type=MULTIPART_CONTENT, **extra
    )


def delete(client, kind, token=True):
    return client.delete(ROUTES[kind], **(csrf(client) if token else {}))


def file_of(kind):
    return f"{ROUTES[kind]}/file"


def logo_url(agency_id):
    return f"/api/v1/agencies/{agency_id}/logo"


def agency_id_of(client):
    return str(AgencyProfile.objects.get(account_id=client.account_id).id)


def uploaded_agency(email="agency.owner@example.com", state="active"):
    client = signed_owner("agency", email)
    assert put(client, "agency").status_code == 200
    if state != "active":
        Account.objects.filter(id=client.account_id).update(
            **{
                "unverified": {"email_verified_at": None},
                "suspended": {"suspended_at": timezone.now(), "suspension_reason": "r"},
                "deactivated": {"deactivated_at": timezone.now()},
            }[state]
        )
    return client


def admin_client():
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    return sign_in("root.admin@example.com", admin=True)


def no_cookie_headers(response):
    assert "Set-Cookie" not in response
    assert "cookie" not in response.get("Vary", "").lower()
    assert not response.cookies


# --- upload ------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_the_owner_uploads_and_gets_the_image_object(kind, media_root):
    client = signed_owner(kind)
    response = put(client, kind, JPEG)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == IMAGE_KEYS
    assert (body["content_type"], body["width"], body["height"]) == ("image/jpeg", 1200, 800)
    assert body["url"] == file_of(kind)
    asset = MediaAsset.objects.get()
    assert body["bytes"] == asset.bytes and asset.attached_at is not None
    text = response.content.decode()
    for needle in (asset.storage_key, str(asset.id), str(client.account_id)):
        assert needle not in text
    assert stored_files(media_root) == [asset.storage_key]
    assert AuditRecord.objects.get(action="account.profile_image_changed").metadata == {}


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_the_profile_answers_carry_the_image_and_nothing_internal(kind):
    client = signed_owner(kind)
    assert client.get(PROFILE[kind]).json()["image"] is None
    put(client, kind)
    asset = MediaAsset.objects.get()
    response = client.get(PROFILE[kind])
    body = response.json()
    assert set(body["image"]) == IMAGE_KEYS and body["image"]["url"] == file_of(kind)
    text = response.content.decode()
    for needle in (asset.storage_key, str(asset.id), str(client.account_id)):
        assert needle not in text
    if kind == "agency":
        profile_id = agency_id_of(client)
        assert body["agency_id"] == profile_id != str(client.account_id)
        assert body["logo_url"] == logo_url(profile_id)
    else:
        assert "agency_id" not in body and "logo_url" not in body


def test_an_agency_without_a_logo_has_a_null_logo_url_and_the_id():
    client = signed_owner("agency")
    body = client.get(PROFILE["agency"]).json()
    assert body["logo_url"] is None and body["image"] is None
    assert body["agency_id"] == agency_id_of(client)


def test_a_profile_text_update_keeps_the_image():
    client = signed_owner("individual")
    put(client, "individual")
    response = client.patch(
        PROFILE["individual"],
        json.dumps({"full_name": "Jane Roe"}),
        content_type="application/json",
        **csrf(client),
    )
    assert response.status_code == 200 and response.json()["image"]["url"] == file_of("individual")


def test_replace_keeps_exactly_one_asset(media_root, django_capture_on_commit_callbacks):
    client = signed_owner("agency")
    put(client, "agency", PNG)
    with django_capture_on_commit_callbacks(execute=True):
        second = put(client, "agency", JPEG)
    assert second.status_code == 200 and second.json()["content_type"] == "image/jpeg"
    asset = MediaAsset.objects.get()
    assert stored_files(media_root) == [asset.storage_key]
    assert StorageUsage.objects.get().bytes_used == asset.bytes


def test_the_name_and_declared_type_are_ignored_and_never_kept(caplog):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    caplog.set_level(logging.DEBUG)
    client = signed_owner()
    try:
        response = put(
            client, "individual", PNG, name="holiday-canary-name.php", declared="text/html"
        )
    finally:
        logging.getLogger().removeHandler(handler)
    assert response.status_code == 200 and response.json()["content_type"] == "image/png"
    asset = MediaAsset.objects.get()
    logged = stream.getvalue() + caplog.text
    for needle in ("holiday-canary-name", "text/html", asset.storage_key):
        assert needle not in logged and needle not in response.content.decode()
    dumped = json.dumps(list(MediaAsset.objects.values()), default=str)
    assert "holiday-canary-name" not in dumped


# --- 400 ---------------------------------------------------------------------------------------


def svg():
    return b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"></svg>'


@pytest.mark.parametrize("kind", ["individual", "agency"])
@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(b"x" * (5 * MB + 1), "too_large", id="over-5mb"),
        pytest.param(svg(), "unsupported_type", id="svg"),
        pytest.param(make_image("PNG", (150, 150)), "too_small", id="small"),
        pytest.param(b"not an image", "invalid_image", id="garbage"),
    ],
)
def test_a_bad_image_is_a_static_validation_error_and_nothing_is_stored(
    kind, data, reason, media_root
):
    client = signed_owner(kind)
    response = put(client, kind, data, name=f"{reason}-secret.jpg")
    refused(response, 400, "validation_error", {"file": [field_message(reason)]})
    assert "secret" not in response.content.decode()
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_a_bad_replacement_keeps_the_current_image(media_root):
    client = signed_owner()
    put(client, "individual")
    keep = stored_files(media_root)
    refused(
        put(client, "individual", b"junk"),
        400,
        "validation_error",
        {"file": [field_message("invalid_image")]},
    )
    assert stored_files(media_root) == keep and MediaAsset.objects.count() == 1


def test_an_oversize_body_is_too_large_and_without_a_token_is_403_unread(media_root):
    client = signed_owner()
    refused(
        put(client, "individual", b"x" * (6 * MB + 10)),
        400,
        "validation_error",
        {"file": [field_message("too_large")]},
    )
    refused(put(client, "individual", b"x" * (6 * MB + 10), token=False), 403, "forbidden")
    assert not MediaAsset.objects.exists()


def test_exactly_one_file_part_is_required():
    client = signed_owner()
    url, token = ROUTES["individual"], csrf(client)

    def send(parts):
        return client.generic("PUT", url, multipart(parts), content_type=MULTIPART_CONTENT, **token)

    def one():
        return SimpleUploadedFile("a.jpg", JPEG, content_type="image/jpeg")

    refused(send({}), 400, "validation_error", {"file": [field_message("required")]})
    refused(
        send({"file": [one(), one()]}),
        400,
        "validation_error",
        {"file": [field_message("invalid")]},
    )
    refused(
        send({"file": "just text"}), 400, "validation_error", {"file": [field_message("invalid")]}
    )
    unknown = {"non_field_errors": [field_message("unknown_field")]}
    refused(send({"file": one(), "caption": "hello"}), 400, "validation_error", unknown)
    refused(send({"file": one(), "second": one()}), 400, "validation_error", unknown)
    assert not MediaAsset.objects.exists()


def test_only_multipart_is_accepted():
    client = signed_owner()
    refused(
        client.generic(
            "PUT",
            ROUTES["individual"],
            json.dumps({"file": "x"}),
            content_type="application/json",
            **csrf(client),
        ),
        415,
        "unsupported_media_type",
    )
    refused(
        client.generic(
            "PUT", ROUTES["individual"], JPEG, content_type="image/jpeg", **csrf(client)
        ),
        415,
        "unsupported_media_type",
    )


def test_other_methods_are_405():
    client = signed_owner()
    for method in ("get", "post", "patch"):
        response = getattr(client, method)(ROUTES["individual"], **csrf(client))
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "method_not_allowed"
    assert client.put(file_of("individual"), **csrf(client)).status_code == 405
    assert client.delete(file_of("individual"), **csrf(client)).status_code == 405
    assert client.post(logo_url(uuid.uuid4()), **csrf(client)).status_code == 405


# --- who may -----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_a_visitor_is_401_and_a_missing_token_is_403(kind):
    anonymous = Client(enforce_csrf_checks=True)
    refused(put(anonymous, kind), 401, "unauthenticated")
    refused(delete(anonymous, kind), 401, "unauthenticated")
    owner = signed_owner(kind)
    refused(put(owner, kind, token=False), 403, "forbidden")
    refused(delete(owner, kind, token=False), 403, "forbidden")
    assert not MediaAsset.objects.exists()


@pytest.mark.parametrize(("route", "account"), [("agency", "individual"), ("individual", "agency")])
def test_the_other_account_type_is_403_whatever_the_body(route, account, media_root):
    client = signed_owner(account)
    refused(put(client, route), 403, "forbidden")
    refused(delete(client, route), 403, "forbidden")
    refused(put(client, route, b"junk"), 403, "forbidden")
    refused(put(client, route, b"x" * (6 * MB + 10)), 403, "forbidden")
    refused(
        client.generic(
            "PUT",
            ROUTES[route],
            multipart({"caption": "x"}),
            content_type=MULTIPART_CONTENT,
            **csrf(client),
        ),
        403,
        "forbidden",
    )
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_an_administrator_is_403_on_the_write_routes(kind):
    owner = signed_owner(kind)
    put(owner, kind)
    admin = admin_client()
    refused(put(admin, kind), 403, "forbidden")
    refused(delete(admin, kind), 403, "forbidden")
    assert MediaAsset.objects.count() == 1


@pytest.mark.parametrize("kind", ["individual", "agency"])
@pytest.mark.parametrize(
    ("state", "change", "status"),
    [
        ("active", {}, 200),
        ("unverified", {"email_verified_at": None}, 200),
        ("suspended", {"suspended_at": "now", "suspension_reason": "r"}, 200),
    ],
)
def test_the_account_states_follow_the_profile_update(kind, state, change, status):
    client = signed_owner(kind)
    Account.objects.filter(id=client.account_id).update(
        **{k: (timezone.now() if v == "now" else v) for k, v in change.items()}
    )
    patch = client.patch(
        PROFILE[kind],
        json.dumps({"full_name" if kind == "individual" else "agency_name": "Jane Roe"}),
        content_type="application/json",
        **csrf(client),
    )
    assert patch.status_code == status  # the profile PATCH answer is the reference
    assert put(client, kind).status_code == status
    assert delete(client, kind).status_code == 204


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_a_deactivated_account_is_refused(kind):
    client = signed_owner(kind)
    Account.objects.filter(id=client.account_id).update(deactivated_at=timezone.now())
    # its sessions have ended in real life; a leftover one is still refused by the service
    assert put(client, kind).status_code == 401
    assert delete(client, kind).status_code == 401
    assert not MediaAsset.objects.exists()


# --- delete ------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_delete_removes_the_image_and_a_repeat_is_204(
    kind, media_root, django_capture_on_commit_callbacks
):
    client = signed_owner(kind)
    put(client, kind)
    with django_capture_on_commit_callbacks(execute=True):
        response = delete(client, kind)
    assert response.status_code == 204 and response.content == b""
    assert response["Cache-Control"] == "no-store"
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert StorageUsage.objects.get().bytes_used == 0
    assert client.get(PROFILE[kind]).json()["image"] is None
    assert delete(client, kind).status_code == 204
    assert client.get(file_of(kind)).status_code == 404


# --- the owner's own file ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_the_owner_reads_their_own_file_with_the_media_headers(kind):
    client = signed_owner(kind)
    put(client, kind, JPEG)
    asset = MediaAsset.objects.get()
    stored = storage.get_storage().get(asset.storage_key)
    response = client.get(file_of(kind), HTTP_ACCEPT="application/json")
    assert response.status_code == 200 and response.content == stored
    assert response["Content-Type"] == "image/jpeg"
    assert response["Content-Length"] == str(len(stored))
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Disposition"] == "inline"
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert "Set-Cookie" not in response
    head = client.head(file_of(kind))
    assert head.status_code == 200 and head.content == b""
    for name in ("Content-Type", "Content-Length", "Cache-Control", "Content-Security-Policy"):
        assert head[name] == response[name]


def test_the_file_is_404_when_there_is_none_or_the_file_is_lost(media_root):
    client = signed_owner()
    refused(client.get(file_of("individual")), 404, "not_found")
    put(client, "individual")
    storage.get_storage().delete(MediaAsset.objects.get().storage_key)
    refused(client.get(file_of("individual")), 404, "not_found")


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_everyone_but_the_owner_gets_the_same_404_for_the_file(kind):
    owner = signed_owner(kind)
    put(owner, kind)
    other_email = "other.owner@example.com"
    other = signed_owner(kind, other_email)
    wrong_type = signed_owner(
        "agency" if kind == "individual" else "individual", "wrong@example.com"
    )
    admin = admin_client()
    bodies = []
    for client in (other, wrong_type, admin, Client()):
        for method in ("get", "head"):
            response = getattr(client, method)(file_of(kind))
            assert response.status_code == 404
            assert response["Cache-Control"] == "no-store"
            if method == "get":
                bodies.append({**response.json()["error"], "request_id": ""})
    assert all(body == bodies[0] for body in bodies)


def test_an_individuals_picture_has_no_public_route():
    owner = signed_owner("individual")
    put(owner, "individual")
    asset = MediaAsset.objects.get()
    profile_id = IndividualProfile.objects.get().id
    visitor = Client()
    for ident in (owner.account_id, profile_id, asset.id):
        for path in (logo_url(ident), f"/api/v1/individuals/{ident}/picture"):
            assert visitor.get(path).status_code == 404 and visitor.head(path).status_code == 404


# --- the public logo ---------------------------------------------------------------------------


def test_a_visitor_gets_the_logo_of_an_active_agency_with_public_cache_headers():
    agency = uploaded_agency()
    asset = MediaAsset.objects.get()
    stored = storage.get_storage().get(asset.storage_key)
    path = logo_url(agency_id_of(agency))
    for client in (Client(), agency, signed_owner("individual", "visitor.person@example.com")):
        response = client.get(path, HTTP_ACCEPT="text/html")
        assert response.status_code == 200 and response.content == stored
        assert response["Content-Type"] == "image/png"
        assert response["Content-Length"] == str(len(stored))
        assert response["Cache-Control"] == "public, max-age=60"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["Content-Disposition"] == "inline"
        assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"
        no_cookie_headers(response)
        assert "ETag" not in response
        head = client.head(path)
        assert head.status_code == 200 and head.content == b""
        assert head["Content-Length"] == str(len(stored))
        assert head["Cache-Control"] == "public, max-age=60"
        no_cookie_headers(head)


def test_the_public_logo_ignores_a_cookie_it_is_sent():
    agency = uploaded_agency()
    anonymous = Client()
    anonymous.cookies["sessionid"] = "stale"
    anonymous.cookies["csrftoken"] = "x"
    response = anonymous.get(logo_url(agency_id_of(agency)))
    assert response.status_code == 200
    no_cookie_headers(response)


@pytest.mark.parametrize("state", ["unverified", "suspended", "deactivated"])
def test_a_logo_is_404_unless_the_agency_is_active(state):
    agency = uploaded_agency(state=state)
    active_twin = uploaded_agency("twin.agency@example.com")
    path = logo_url(agency_id_of(agency))
    reference = Client().get(logo_url(uuid.uuid4()))
    assert reference.status_code == 404
    for method in ("get", "head"):
        response = getattr(Client(), method)(path)
        assert response.status_code == 404
        assert response["Cache-Control"] == "no-store"
        no_cookie_headers(response)
    shown = Client().get(path).json()["error"]
    assert {**shown, "request_id": ""} == {**reference.json()["error"], "request_id": ""}
    assert Client().get(logo_url(agency_id_of(active_twin))).status_code == 200


def test_every_unavailable_logo_answers_the_identical_404():
    no_logo = signed_owner("agency", "nologo@example.com")
    suspended = uploaded_agency("susp@example.com", "suspended")
    cases = [
        logo_url(agency_id_of(no_logo)),  # an agency without a logo
        logo_url(agency_id_of(suspended)),  # suspended
        logo_url(uuid.uuid4()),  # unknown
        logo_url(no_logo.account_id),  # an account id is not the public id
        logo_url("not-a-uuid"),
        logo_url(str(uuid.uuid4()).upper()),
        logo_url("0" * 36),
    ]
    seen = []
    for path in cases:
        response = Client().get(path)
        assert response.status_code == 404, path
        assert response["Cache-Control"] == "no-store"
        no_cookie_headers(response)
        seen.append({**response.json()["error"], "request_id": ""})
    assert all(item == seen[0] for item in seen)


def test_suspension_hides_the_logo_and_reactivation_shows_it_again():
    agency = uploaded_agency()
    path = logo_url(agency_id_of(agency))
    assert Client().get(path).status_code == 200
    identity_services.suspend(ADMIN_ACTOR, agency.account_id, "policy breach")
    assert Client().get(path).status_code == 404
    assert MediaAsset.objects.count() == 1  # the asset is kept
    assert agency.get(file_of("agency")).status_code == 200  # the owner still sees it
    identity_services.reactivate(ADMIN_ACTOR, agency.account_id)
    assert Client().get(path).status_code == 200


def test_removing_the_logo_makes_the_public_route_404(django_capture_on_commit_callbacks):
    agency = uploaded_agency()
    path = logo_url(agency_id_of(agency))
    with django_capture_on_commit_callbacks(execute=True):
        assert delete(agency, "agency").status_code == 204
    assert Client().get(path).status_code == 404


def test_a_deactivated_agencys_logo_is_removed_not_just_hidden(
    media_root, django_capture_on_commit_callbacks
):
    agency = uploaded_agency()
    path = logo_url(agency_id_of(agency))
    with django_capture_on_commit_callbacks(execute=True):
        identity_services.deactivate(ADMIN_ACTOR, agency.account_id)
    assert Client().get(path).status_code == 404
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_a_lost_logo_row_or_file_is_404_and_the_profile_shows_no_image(media_root):
    agency = uploaded_agency()
    path = logo_url(agency_id_of(agency))
    storage.get_storage().delete(MediaAsset.objects.get().storage_key)
    assert Client().get(path).status_code == 404
    MediaAsset.objects.all().delete()
    body = agency.get(PROFILE["agency"]).json()
    assert body["image"] is None and body["logo_url"] is None
    assert Client().head(path).status_code == 404
    assert put(agency, "agency").status_code == 200  # the next write cleans the ghost
    assert Client().get(path).status_code == 200


# --- cap, rate limit, busy ---------------------------------------------------------------------


def test_the_storage_cap_is_409_and_the_old_image_stays(media_root):
    client = signed_owner()
    first = put(client, "individual").json()
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=first["bytes"] + 1
    )
    big = make_image("PNG", (1600, 1200), color=(1, 200, 3))
    refused(put(client, "individual", big), 409, "storage_cap_exceeded")
    assert MediaAsset.objects.count() == 1 and len(stored_files(media_root)) == 1
    assert client.get(file_of("individual")).status_code == 200


def test_the_eleventh_upload_in_an_hour_is_429_and_removing_is_not_counted():
    client = signed_owner()
    for _ in range(10):
        assert put(client, "individual").status_code == 200
    response = put(client, "individual")
    refused(response, 429, "throttled")
    assert int(response["Retry-After"]) >= 1
    for _ in range(3):
        assert delete(client, "individual").status_code == 204
    assert client.get(PROFILE["individual"]).status_code == 200


def test_the_limit_is_per_account_and_shared_by_both_routes_of_an_account():
    one, two = signed_owner(), signed_owner(email="second.owner@example.com")
    RateLimitPolicy.objects.filter(scope="profile_image_upload").update(limit=1)
    assert put(one, "individual").status_code == 200
    refused(put(one, "individual", b"junk"), 429, "throttled")  # attempts count, accepted or not
    assert put(two, "individual").status_code == 200


def test_the_seeded_policy_is_ten_per_hour():
    import importlib

    module = importlib.import_module("apps.kernel.migrations.0014_seed_profile_image_policy")
    assert [(s, limit, w, k) for s, limit, w, k, _ in module.POLICIES] == [
        ("profile_image_upload", 10, 3600, "account")
    ]


def test_a_busy_executor_is_503_with_retry_after(settings, media_root):
    client = signed_owner()
    gate = threading.Event()
    heavy = settings.RUNTIME_EXECUTOR_MAX_WORKERS - settings.RUNTIME_EXECUTOR_RESERVED_WORKERS
    futures = [runtime.submit_heavy(gate.wait, 5) for _ in range(heavy)]
    try:
        response = put(client, "individual")
    finally:
        gate.set()
        for future in futures:
            future.result()
    refused(response, 503, "busy")
    assert int(response["Retry-After"]) >= 1
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert put(client, "individual").status_code == 200  # and it recovers


# --- logs and bodies ---------------------------------------------------------------------------


def test_failure_paths_leak_no_name_key_account_id_or_decoder_text(caplog):
    client = signed_owner()
    first = put(client, "individual", PNG, name="first-canary-name.jpg").json()
    key = MediaAsset.objects.get().storage_key
    needles = ["canary-name", key, str(client.account_id), "Traceback", "cannot identify"]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    caplog.set_level(logging.DEBUG)
    try:
        responses = [
            put(client, "individual", b"\xff\xd8\xff" + b"x" * 40, name="bad-canary-name.jpg")
        ]
        RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
            limit=first["bytes"] + 1
        )
        responses.append(
            put(client, "individual", make_image("PNG", (1600, 1200)), name="cap-canary-name.jpg")
        )
        RateLimitPolicy.objects.filter(scope="profile_image_upload").update(limit=1)
        responses.append(put(client, "individual", PNG, name="rate-canary-name.jpg"))
        responses.append(Client().get(logo_url(client.account_id)))
        responses.append(Client().get(file_of("individual")))
    finally:
        logging.getLogger().removeHandler(handler)
    assert [r.status_code for r in responses] == [400, 409, 429, 404, 404]
    logged = stream.getvalue() + caplog.text
    for needle in needles:
        assert needle not in logged
        for response in responses:
            assert needle not in response.content.decode()


def test_error_bodies_echo_nothing_but_the_catalogue_and_the_request_id():
    client = signed_owner()
    response = put(client, "individual", b"junk", name="secret-name.png")
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message", "fields", "request_id"}


# --- review patches ----------------------------------------------------------------------------


def test_refused_callers_do_not_burn_the_upload_quota_but_validation_failures_do():
    RateLimitPolicy.objects.filter(scope="profile_image_upload").update(limit=2)
    person = signed_owner("individual")
    for _ in range(5):  # the wrong account type is refused before the throttle counts
        refused(put(person, "agency"), 403, "forbidden")
    admin = admin_client()
    for _ in range(5):
        refused(put(admin, "individual"), 403, "forbidden")
    anonymous = Client(enforce_csrf_checks=True)
    for _ in range(5):
        refused(put(anonymous, "individual"), 401, "unauthenticated")
    assert put(person, "individual").status_code == 200  # the quota is intact
    refused(
        put(person, "individual", b"junk"),
        400,
        "validation_error",
        {"file": [field_message("invalid_image")]},
    )  # a validation failure DOES count (the body is read after the throttle)
    refused(put(person, "individual"), 429, "throttled")


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_head_is_404_like_get_when_the_file_is_lost(kind):
    client = signed_owner(kind)
    put(client, kind)
    path = file_of(kind)
    storage.get_storage().delete(MediaAsset.objects.get().storage_key)
    assert client.get(path).status_code == 404 and client.head(path).status_code == 404
    if kind == "agency":
        public = logo_url(agency_id_of(client))
        assert Client().get(public).status_code == 404 and Client().head(public).status_code == 404


def test_the_public_logo_view_has_no_authentication_classes():
    from apps.presentation.api.profile_images import AgencyLogoView

    assert AgencyLogoView.authentication_classes == []


def test_a_signed_in_client_gets_a_clean_logo_answer_and_its_session_is_never_read(monkeypatch):
    agency = uploaded_agency()
    path = logo_url(agency_id_of(agency))
    assert agency.cookies  # a real session cookie is sent
    from apps.identity import selectors as identity_selectors

    def forbidden(*args, **kwargs):
        raise AssertionError("the session was read")

    monkeypatch.setattr(identity_selectors, "live_session", forbidden)
    for method in ("get", "head"):
        response = getattr(agency, method)(path)
        assert response.status_code == 200
        no_cookie_headers(response)


@pytest.mark.parametrize("bad", ["x", "", "a" * 200, "%00%ff"])
def test_a_malformed_csrf_cookie_gets_no_cookie_and_no_vary(bad):
    agency = uploaded_agency()
    path = logo_url(agency_id_of(agency))
    for target in (path, logo_url(uuid.uuid4()), logo_url("not-a-uuid")):
        anonymous = Client()
        anonymous.cookies["csrftoken"] = bad
        response = anonymous.get(target)
        assert response.status_code in (200, 404)
        no_cookie_headers(response)
        head = anonymous.head(target)
        no_cookie_headers(head)
