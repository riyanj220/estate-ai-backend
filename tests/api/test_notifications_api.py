"""In-app notification routes over HTTP (Story 1.24), with Django's CSRF checks on."""

import json
import logging
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from apps.identity import services, subscribers, verification
from apps.identity.models import Account, EmailChangeToken, IndividualProfile, Session
from apps.kernel import events, notifications, runtime
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord, Notification, RateLimitPolicy

ROOT = Path(__file__).resolve().parents[2]
LIST = "/api/v1/notifications"
COUNT = "/api/v1/notifications/unread-count"
READ_ALL = "/api/v1/notifications/read-all"
SESSION = "/api/v1/session"
CSRF = "/api/v1/csrf"
PASSWORD_URL = "/api/v1/me/password"
CONFIRM = "/api/v1/email-changes/confirm"
EMAIL = "Jane.Doe@Example.com"
OTHER = "other.person@example.com"
NEW = "New.Address@Example.org"
PASSWORD = "correct horse battery staple"
NEXT_PASSWORD = "mango river lantern pillow"
SYSTEM = Actor.system("r")
ITEM_KEYS = {"id", "kind", "payload", "created_at", "read"}


def read_url(row_id):
    return f"/api/v1/notifications/{row_id}/read"


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


def make(email=EMAIL, account_type="individual"):
    view = services.create_account(SYSTEM, email, PASSWORD, account_type)
    services.mark_email_verified(SYSTEM, view.id)
    if account_type == "individual":
        IndividualProfile.objects.create(
            account_id=view.id, full_name="Jane Doe", phone="+923001234567"
        )
    return view


def post(client, url, body=None, **extra):
    return client.post(url, json.dumps(body or {}), content_type="application/json", **extra)


def csrf(client):
    return client.get(CSRF).json()["csrf_token"]


def sign_in_browser(email=EMAIL):
    client = Client(enforce_csrf_checks=True)
    response = post(
        client, SESSION, {"email": email, "password": PASSWORD}, HTTP_X_CSRFTOKEN=csrf(client)
    )
    if response.status_code == 401:
        response = post(
            client,
            "/api/v1/admin-session",
            {"email": email, "password": PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(client),
        )
    assert response.status_code == 200
    return client


def mark(client, row_id, token=True):
    extra = {"HTTP_X_CSRFTOKEN": csrf(client)} if token else {}
    return post(client, read_url(row_id), **extra)


def mark_all(client, token=True):
    extra = {"HTTP_X_CSRFTOKEN": csrf(client)} if token else {}
    return post(client, READ_ALL, **extra)


def error_code(response):
    return response.json()["error"]["code"]


_counter = iter(range(10_000_000, 20_000_000))


def store(account, read=False, kind="account.password_changed", created=None):
    return Notification.objects.create(
        seq=next(_counter),
        account_id=account,
        kind=kind,
        payload={},
        created_at=created or timezone.now(),
        read_at=timezone.now() if read else None,
    )


@pytest.fixture
def owner(db):
    view = make()
    client = sign_in_browser()
    client.account_id = view.id
    return client


# --- authentication and CSRF -----------------------------------------------------------------


@pytest.mark.parametrize("url", [LIST, COUNT])
def test_reads_without_a_session_are_401(db, url):
    response = Client().get(url)
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("url", [READ_ALL, read_url(uuid.UUID(int=1))])
def test_posts_without_a_session_are_401(db, url):
    response = Client(enforce_csrf_checks=True).post(url, content_type="application/json")
    assert (response.status_code, error_code(response)) == (401, "unauthenticated")


def test_posts_without_csrf_are_403_and_change_nothing(owner):
    row = store(owner.account_id)
    for response in (mark(owner, row.id, token=False), mark_all(owner, token=False)):
        assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert Notification.objects.get().read_at is None


def test_wrong_methods_are_405(owner):
    token = {"HTTP_X_CSRFTOKEN": csrf(owner)}
    for response in (
        post(owner, LIST, **token),
        owner.delete(LIST, **token),
        owner.get(READ_ALL),
        owner.get(read_url(uuid.uuid4())),
        post(owner, COUNT, **token),
    ):
        assert (response.status_code, error_code(response)) == (405, "method_not_allowed")


# --- list ------------------------------------------------------------------------------------


def test_list_shape_and_headers(owner):
    store(owner.account_id)
    response = owner.get(LIST)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {"next", "previous", "results"}
    (item,) = body["results"]
    assert set(item) == ITEM_KEYS
    assert (item["kind"], item["payload"], item["read"]) == ("account.password_changed", {}, False)


def test_list_is_empty_for_a_new_account(owner):
    assert owner.get(LIST).json() == {"next": None, "previous": None, "results": []}


def test_pages_newest_first_without_gaps_or_duplicates(owner):
    rows = [store(owner.account_id) for _ in range(45)]
    expected = [str(r.id) for r in reversed(rows)]
    first = owner.get(LIST).json()
    assert len(first["results"]) == 20 and first["next"]
    seen, body, pages = [], first, 0
    while True:
        seen += [r["id"] for r in body["results"]]
        pages += 1
        if not body["next"]:
            break
        body = owner.get(body["next"]).json()
    assert pages == 3 and seen == expected


def test_previous_cursor_returns_the_prior_page(owner):
    for _ in range(30):
        store(owner.account_id)
    first = owner.get(f"{LIST}?page_size=10").json()
    second = owner.get(first["next"]).json()
    back = owner.get(second["previous"]).json()
    assert [r["id"] for r in back["results"]] == [r["id"] for r in first["results"]]


@pytest.mark.parametrize(
    ("value", "size"), [("0", 20), ("-3", 20), ("abc", 20), ("51", 50), ("500", 50), ("5", 5)]
)
def test_page_size_is_clamped_or_defaulted_like_the_project_pagination(owner, value, size):
    for _ in range(60):
        store(owner.account_id)
    assert len(owner.get(f"{LIST}?page_size={value}").json()["results"]) == size


@pytest.mark.parametrize(
    "cursor",
    ["garbage0300-1234567", "%%%", "cD1hYmM=", "cD05OTk5OTk5OTk5OTk5OTk5OTk5OQ==", "a" * 500],
)
def test_bad_cursor_is_a_validation_error_envelope(owner, cursor):
    for _ in range(25):
        store(owner.account_id)
    response = owner.get(LIST, {"cursor": cursor})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(response.json()["error"]["fields"]) == ["cursor"]
    assert cursor not in response.content.decode()
    assert response.headers["Cache-Control"] == "no-store"


def test_tampered_cursor_is_the_same_envelope(owner):
    for _ in range(25):
        store(owner.account_id)
    nxt = owner.get(LIST).json()["next"]
    tampered = nxt[:-6] + "AAAAAA"
    response = owner.get(tampered)
    assert (response.status_code, error_code(response)) == (400, "validation_error")


def test_unread_cursor_links_keep_state_and_page_size(owner):
    rows = [store(owner.account_id) for _ in range(12)]
    for row in rows[::2]:
        row.read_at = timezone.now()
        row.save()
    unread = [str(r.id) for r in reversed(rows[1::2])]
    first = owner.get(f"{LIST}?state=unread&page_size=4").json()
    assert "state=unread" in first["next"] and "page_size=4" in first["next"]
    seen = [r["id"] for r in first["results"]]
    # reading an item of the first page between pages must not skip or repeat the rest
    assert mark(owner, seen[0]).status_code == 200
    second = owner.get(first["next"]).json()
    seen += [r["id"] for r in second["results"]]
    assert seen == unread
    assert all(r["read"] is False for r in second["results"])
    assert second["next"] is None


def test_state_filter(owner):
    unread = store(owner.account_id)
    store(owner.account_id, read=True)
    only = owner.get(f"{LIST}?state=unread").json()["results"]
    assert [r["id"] for r in only] == [str(unread.id)] and only[0]["read"] is False
    assert len(owner.get(f"{LIST}?state=all").json()["results"]) == 2
    assert len(owner.get(LIST).json()["results"]) == 2


@pytest.mark.parametrize("state", ["bogus", "", "UNREAD", "unread,all"])
def test_bad_state_is_a_validation_error_without_echo(owner, state):
    response = owner.get(LIST, {"state": state})
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(response.json()["error"]["fields"]) == ["state"]
    if state:
        assert state not in response.content.decode()


def test_isolation_between_accounts(owner):
    other = make(OTHER)
    mine = store(owner.account_id)
    theirs = [store(other.id), store(other.id, read=True)]
    assert [r["id"] for r in owner.get(LIST).json()["results"]] == [str(mine.id)]
    assert owner.get(COUNT).json() == {"unread": 1}
    wire = owner.get(LIST).content.decode()
    assert not any(str(t.id) in wire for t in theirs) and str(other.id) not in wire
    stranger = sign_in_browser(OTHER)
    assert len(stranger.get(LIST).json()["results"]) == 2
    assert stranger.get(COUNT).json() == {"unread": 1}


def test_request_parameters_cannot_choose_the_account(owner):
    other = make(OTHER)
    store(other.id)
    for query in (f"account_id={other.id}", f"account={other.id}", f"user={other.id}"):
        assert owner.get(f"{LIST}?{query}").json()["results"] == []
    assert owner.get(f"{COUNT}?account_id={other.id}").json() == {"unread": 0}


@pytest.mark.parametrize("account_type", ["agency", "admin"])
def test_every_account_type_gets_its_own_notifications(db, account_type):
    view = make("someone@example.com", account_type)
    make(OTHER)
    mine = store(view.id)
    client = sign_in_browser("someone@example.com")
    assert [r["id"] for r in client.get(LIST).json()["results"]] == [str(mine.id)]
    assert client.get(COUNT).json() == {"unread": 1}
    assert mark(client, mine.id).json()["read"] is True
    assert mark_all(client).json() == {"marked": 0}


def test_item_never_exposes_seq_account_or_raw_read_time(owner):
    row = store(owner.account_id, read=True)
    wire = owner.get(LIST).content.decode()
    for hidden in ("seq", "account_id", "read_at", str(owner.account_id), str(row.seq)):
        assert hidden not in wire


# --- unread count ----------------------------------------------------------------------------


def test_unread_count_is_exact_and_no_store(owner):
    for read in (False, False, False, True, True):
        store(owner.account_id, read=read)
    response = owner.get(COUNT)
    assert response.json() == {"unread": 3}
    assert response.headers["Cache-Control"] == "no-store"
    for _ in range(60):
        store(owner.account_id)
    assert owner.get(COUNT).json() == {"unread": 63}  # counts all, not one page


# --- mark read -------------------------------------------------------------------------------


def test_mark_read_returns_the_item_and_is_idempotent(owner):
    row = store(owner.account_id)
    first = mark(owner, row.id)
    assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
    assert set(first.json()) == ITEM_KEYS and first.json()["read"] is True
    stamp = Notification.objects.get().read_at
    assert stamp is not None
    second = mark(owner, row.id)
    assert second.status_code == 200 and second.json() == first.json()
    assert Notification.objects.get().read_at == stamp
    assert owner.get(COUNT).json() == {"unread": 0}


def test_marking_someone_elses_is_404_and_changes_nothing(owner):
    other = make(OTHER)
    theirs = store(other.id)
    response = mark(owner, theirs.id)
    assert (response.status_code, error_code(response)) == (404, "not_found")
    assert response.headers["Cache-Control"] == "no-store"
    assert Notification.objects.get().read_at is None
    unknown = mark(owner, uuid.uuid4())
    assert unknown.status_code == 404
    assert {k: v for k, v in unknown.json()["error"].items() if k != "request_id"} == {
        k: v for k, v in response.json()["error"].items() if k != "request_id"
    }  # the same answer, so existence is never confirmed


@pytest.mark.parametrize(
    "raw", ["not-a-uuid", "123", "A" * 36, "%20", str(uuid.UUID(int=2)).upper()]
)
def test_malformed_id_is_404(owner, raw):
    store(owner.account_id)
    response = mark(owner, raw)
    assert (response.status_code, error_code(response)) == (404, "not_found")
    assert Notification.objects.get().read_at is None


def test_mark_all_read_marks_only_the_callers(owner):
    other = make(OTHER)
    for _ in range(3):
        store(owner.account_id)
    done = store(owner.account_id, read=True)
    theirs = store(other.id)
    stamp = Notification.objects.get(id=done.id).read_at
    response = mark_all(owner)
    assert response.status_code == 200 and response.json() == {"marked": 3}
    assert response.headers["Cache-Control"] == "no-store"
    assert Notification.objects.get(id=done.id).read_at == stamp
    assert Notification.objects.get(id=theirs.id).read_at is None
    assert mark_all(owner).json() == {"marked": 0}


# --- shipped kinds, end to end ---------------------------------------------------------------


def kinds_of(account_id):
    return list(Notification.objects.filter(account_id=account_id).values_list("kind", flat=True))


def test_password_change_notifies_the_account_after_commit(
    owner, django_capture_on_commit_callbacks
):
    other = make(OTHER)
    with django_capture_on_commit_callbacks(execute=True):
        response = post(
            owner,
            PASSWORD_URL,
            {"current_password": PASSWORD, "new_password": NEXT_PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(owner),
        )
        assert response.status_code == 200
        assert kinds_of(owner.account_id) == []  # not before the commit
    assert kinds_of(owner.account_id) == ["account.password_changed"]
    assert kinds_of(other.id) == []
    item = owner.get(LIST).json()["results"][0]
    assert (item["kind"], item["payload"], item["read"]) == ("account.password_changed", {}, False)


def test_failed_password_change_notifies_nobody(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        wrong = post(
            owner,
            PASSWORD_URL,
            {"current_password": "wrong words here", "new_password": NEXT_PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(owner),
        )
        same = post(
            owner,
            PASSWORD_URL,
            {"current_password": PASSWORD, "new_password": PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(owner),
        )
    assert wrong.status_code == same.status_code == 400
    assert not Notification.objects.exists()


def test_password_reset_notifies_the_account(db, django_capture_on_commit_callbacks):
    view = make()
    other = make(OTHER)
    with django_capture_on_commit_callbacks(execute=True):
        _, token = services.issue_password_reset_token(SYSTEM, view.id)
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm_password_reset(Actor.visitor(), token, NEXT_PASSWORD)
    assert kinds_of(view.id) == ["account.password_changed"] and kinds_of(other.id) == []


def test_refused_reset_notifies_nobody(db, django_capture_on_commit_callbacks):
    view = make()
    with django_capture_on_commit_callbacks(execute=True):
        _, token = services.issue_password_reset_token(SYSTEM, view.id)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(services.PasswordInvalid):
            services.confirm_password_reset(Actor.visitor(), token, "short")
    assert not Notification.objects.exists()


def issue_email_change(view):
    token = "change-link-token"
    now = timezone.now()
    EmailChangeToken.objects.create(
        link_issued=True,
        account_id=view.id,
        new_email=NEW,
        token_hash=verification.hash_token(token),
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return token


@pytest.fixture
def confirm_policy(db):
    RateLimitPolicy.objects.update_or_create(
        scope="email_change_confirm",
        defaults={"limit": 100, "window_seconds": 3600, "key_kind": "client", "note": "t"},
    )


def test_email_change_confirm_notifies_the_account(
    confirm_policy, django_capture_on_commit_callbacks
):
    view = make()
    other = make(OTHER)
    token = issue_email_change(view)
    with django_capture_on_commit_callbacks(execute=True):
        assert post(Client(), CONFIRM, {"token": token}).status_code == 200
    assert kinds_of(view.id) == ["account.email_changed"] and kinds_of(other.id) == []


def test_refused_email_change_notifies_nobody(confirm_policy, django_capture_on_commit_callbacks):
    make()
    with django_capture_on_commit_callbacks(execute=True):
        assert post(Client(), CONFIRM, {"token": "unknown-token-value"}).status_code == 400
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_rolled_back_account_change_leaves_no_notification(monkeypatch):
    view = make()
    token = issue_email_change(view)

    def broken(*args, **kwargs):
        raise RuntimeError("mail down")

    monkeypatch.setattr(services.notices, "send_email_changed", broken)
    RateLimitPolicy.objects.update_or_create(
        scope="email_change_confirm",
        defaults={"limit": 100, "window_seconds": 3600, "key_kind": "client", "note": "t"},
    )
    client = Client(raise_request_exception=False)
    assert post(client, CONFIRM, {"token": token}).status_code == 500
    assert not Notification.objects.exists()


def test_removing_an_administrator_deletes_their_notifications(db):
    make("root.admin@example.com", "admin")
    second = make("second.admin@example.com", "admin")
    gone = Account.objects.get(email="root.admin@example.com").id
    store(gone)
    keep = store(second.id)
    services.remove_admin(SYSTEM, "root.admin@example.com")
    assert list(Notification.objects.all()) == [keep]


@pytest.mark.django_db(transaction=True)
def test_removal_that_fails_keeps_the_notifications():
    make("root.admin@example.com", "admin")
    only = Account.objects.get().id
    store(only)
    with pytest.raises(services.AdminRefused):
        services.remove_admin(SYSTEM, "root.admin@example.com")
    assert Notification.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_failed_removal_keeps_the_account_and_its_notifications(monkeypatch):
    make("root.admin@example.com", "admin")
    second = make("second.admin@example.com", "admin")
    target = Account.objects.get(email="root.admin@example.com").id
    store(target)
    store(second.id)

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(services.audit, "record", boom)
    with pytest.raises(RuntimeError):
        services.remove_admin(SYSTEM, "root.admin@example.com")
    assert Account.objects.filter(id=target).exists()
    assert Notification.objects.filter(account_id=target).count() == 1
    assert Notification.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_notification_queued_in_the_removing_transaction_leaves_no_orphan():
    make("root.admin@example.com", "admin")
    make("second.admin@example.com", "admin")
    target = Account.objects.get(email="root.admin@example.com").id
    with transaction.atomic():
        notifications.notify(target, "account.password_changed", {})
        services.remove_admin(SYSTEM, "root.admin@example.com")
    assert not Account.objects.filter(id=target).exists()
    assert not Notification.objects.filter(account_id=target).exists()


@pytest.mark.django_db(transaction=True)
def test_failed_password_change_leaves_no_notification(monkeypatch):
    for scope, kind in (("sign_in", "client"), ("password_change_failed", "account")):
        RateLimitPolicy.objects.update_or_create(
            scope=scope,
            defaults={"limit": 50, "window_seconds": 900, "key_kind": kind, "note": "t"},
        )
    runtime.shutdown()
    view = make()
    sign_in_browser()
    session_id = Session.objects.get().id

    def boom(*args, **kwargs):
        raise RuntimeError("mail down")

    monkeypatch.setattr(services.notices, "send_password_changed", boom)
    with pytest.raises(RuntimeError):
        services.change_password(Actor.user(view.id), session_id, PASSWORD, NEXT_PASSWORD)
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_failed_password_reset_leaves_no_notification(monkeypatch):
    view = make()
    _, token = services.issue_password_reset_token(SYSTEM, view.id)

    def boom(*args, **kwargs):
        raise RuntimeError("mail down")

    monkeypatch.setattr(services.notices, "send_password_reset_done", boom)
    with pytest.raises(RuntimeError):
        services.confirm_password_reset(Actor.visitor(), token, NEXT_PASSWORD)
    assert not Notification.objects.exists()


# --- privacy and contract --------------------------------------------------------------------


def test_nothing_private_reaches_logs_audit_or_responses(
    owner, django_capture_on_commit_callbacks, caplog
):
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        post(
            owner,
            PASSWORD_URL,
            {"current_password": PASSWORD, "new_password": NEXT_PASSWORD},
            HTTP_X_CSRFTOKEN=csrf(owner),
        )
    responses = [owner.get(LIST), owner.get(COUNT), mark_all(owner)]
    wire = "\n".join(
        [r.content.decode() for r in responses]
        + [r.getMessage() + repr(r.__dict__) for r in caplog.records]
        + [repr(a.metadata) for a in AuditRecord.objects.all()]
        + [repr(n.payload) for n in Notification.objects.all()]
    )
    for secret in (EMAIL.lower(), "jane.doe", "+923001234567", PASSWORD, NEXT_PASSWORD):
        assert secret not in wire.lower()


def test_openapi_publishes_the_four_routes_and_item_schema():
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text())
    paths = spec["paths"]
    assert set(paths["/api/v1/notifications"]) == {"get"}
    assert set(paths["/api/v1/notifications/unread-count"]) == {"get"}
    assert set(paths["/api/v1/notifications/read-all"]) == {"post"}
    assert set(paths["/api/v1/notifications/{notification_id}/read"]) == {"post"}
    assert set(spec["components"]["schemas"]["Notification"]["properties"]) == ITEM_KEYS


# --- concurrency -----------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_parallel_mark_read_over_http_keeps_one_read_time():
    RateLimitPolicy.objects.update_or_create(
        scope="sign_in",
        defaults={"limit": 100, "window_seconds": 900, "key_kind": "client", "note": "t"},
    )
    runtime.shutdown()
    view = make()
    row = store(view.id)
    clients = [sign_in_browser() for _ in range(6)]
    tokens = [csrf(c) for c in clients]
    barrier, outcomes = threading.Barrier(6), []

    def work(client, token):
        try:
            barrier.wait()
            outcomes.append(post(client, read_url(row.id), HTTP_X_CSRFTOKEN=token).status_code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=work, args=pair) for pair in zip(clients, tokens, strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes == [200] * 6
    assert Notification.objects.get().read_at is not None


@pytest.mark.django_db(transaction=True)
def test_parallel_notify_for_one_account_stores_every_row():
    view = make()
    runtime.shutdown()
    before = Notification.objects.count()
    barrier, failures = threading.Barrier(8), []

    def work():
        try:
            barrier.wait()
            notifications.notify(view.id, "account.password_changed", {})
        except Exception as exc:  # noqa: BLE001
            failures.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    assert Notification.objects.count() - before == 8


def test_rich_kind_never_carries_disallowed_values_anywhere(
    owner, django_capture_on_commit_callbacks, caplog, monkeypatch
):
    monkeypatch.setattr(notifications, "_kinds", dict(notifications._kinds))
    notifications.register_kind(
        "probe.rich",
        {
            "agency": notifications.SHORT_TEXT,
            "city": notifications.SHORT_TEXT,
            "rooms": notifications.int_range(0, 20),
            "state": ["open", "closed"],
            "flag": bool,
        },
    )
    caplog.set_level(logging.DEBUG)
    canaries = ["jane.doe@example.com", "03001234567", "www.example.org"]
    errors = []
    with django_capture_on_commit_callbacks(execute=True):
        for canary in canaries:
            for key in ("agency", "city", "state"):
                payload = {
                    "agency": "Prime Estates",
                    "city": "Lahore",
                    "rooms": 3,
                    "state": "open",
                    "flag": True,
                }
                payload[key] = canary
                try:
                    notifications.notify(owner.account_id, "probe.rich", payload)
                except ValueError as exc:
                    errors.append(str(exc))
            try:
                notifications.notify(
                    owner.account_id,
                    "probe.rich",
                    {
                        "agency": "Prime Estates",
                        "city": "Lahore",
                        "rooms": canary,
                        "state": "open",
                        "flag": True,
                    },
                )
            except ValueError as exc:
                errors.append(str(exc))
        notifications.notify(
            owner.account_id,
            "probe.rich",
            {
                "agency": "Prime Estates",
                "city": "Lahore",
                "rooms": 3,
                "state": "open",
                "flag": True,
            },
        )
    assert len(errors) == len(canaries) * 4
    wire = "\n".join(
        errors
        + [owner.get(LIST).content.decode(), owner.get(COUNT).content.decode()]
        + [r.getMessage() + repr(r.__dict__) for r in caplog.records]
        + [repr(a.metadata) + a.reason for a in AuditRecord.objects.all()]
        + [repr(n.payload) for n in Notification.objects.all()]
    )
    for canary in canaries:
        assert canary not in wire
    assert "Prime Estates" in owner.get(LIST).content.decode()
