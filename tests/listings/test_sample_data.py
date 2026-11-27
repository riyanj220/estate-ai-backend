"""Story 2.16: the sample-data foundation (`apps.listings.sample_data`, the `seed_sample_data`
command, `publish(kind="seed")` and the sample-account services). Values are low-entropy fake
words, never real data."""

import ast
import io
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.identity import selectors as identity
from apps.identity import services as identity_services
from apps.identity.models import Account, AgencyProfile, IndividualProfile
from apps.kernel import storage
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, MediaAsset, StorageUsage
from apps.listings import publication, quota, sample_data, selectors, services
from apps.listings.models import (
    ListingImage,
    ListingPrivateAddress,
    PropertyListing,
    PublicationEvent,
)
from tests.listings.support import (
    PASSWORD,
    SYSTEM,
    make_account,
    ready_draft,
    seed_cap,
    seed_media_policies,
    user,
)

SAMPLE_EMAIL = f"sample.individual.9@{identity.SAMPLE_EMAIL_DOMAIN}"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()


def run(*args, stdin=None, monkeypatch=None):
    """The command's output lines, with standard input replaced when `stdin` is given."""
    out = io.StringIO()
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    call_command("seed_sample_data", *args, stdout=out)
    return out.getvalue()


def counts(output: str) -> dict[str, int]:
    return {
        line.rpartition(": ")[0]: int(line.rpartition(": ")[2])
        for line in output.splitlines()
        if line.rpartition(": ")[2].isdigit()
    }


def sample_owner_ids():
    return set(identity.sample_account_ids().values())


def all_listings():
    return PropertyListing.objects.filter(owner_account_id__in=sample_owner_ids())


def snapshot():
    return (
        Account.objects.count(),
        PropertyListing.objects.count(),
        ListingImage.objects.count(),
        MediaAsset.objects.count(),
        PublicationEvent.objects.count(),
        AuditRecord.objects.count(),
    )


def sample_account(email=SAMPLE_EMAIL):
    return identity_services.create_sample_account(
        SYSTEM, email, PASSWORD, "individual", "Sample Person"
    )


def sample_draft(account):
    return ready_draft(account)


# --- first run, repeat, partial ------------------------------------------------------------


def test_a_first_run_creates_accounts_and_published_listings_with_images():
    report = sample_data.seed(12, PASSWORD)
    assert (report.accounts_created, report.listings_created) == (5, 12)
    assert Account.objects.filter(account_type="individual").count() == 3
    assert Account.objects.filter(account_type="agency").count() == 2
    assert IndividualProfile.objects.count() == 3 and AgencyProfile.objects.count() == 2
    owners = sample_owner_ids()
    assert len(owners) == 5
    rows = list(all_listings())
    assert len(rows) == 12 and {r.status for r in rows} == {"published"}
    assert {r.category for r in rows} == {"house", "apartment", "plot", "commercial"}
    assert {r.purpose for r in rows} == {"sale", "rent"}
    assert len({r.subtype for r in rows}) == 6
    assert {r.owner_account_id for r in rows} == owners
    for row in rows:
        links = ListingImage.objects.filter(listing=row)
        assert 1 <= links.count() <= 3 and links.filter(is_cover=True).count() == 1
    assert report.images_created == ListingImage.objects.count()
    store = storage.get_storage()
    for asset in MediaAsset.objects.all():
        assert store.get(asset.storage_key)[:4] == b"\x89PNG"  # the stored file is a real image
    assert StorageUsage.objects.filter(bytes_used=0).count() == 0
    assert sum(u.bytes_used for u in StorageUsage.objects.all()) == sum(
        a.bytes for a in MediaAsset.objects.all()
    )


def test_the_accounts_are_active_verified_and_filled_in():
    sample_data.seed(1, PASSWORD)
    for account in Account.objects.all():
        assert account.state.value == "active"
        assert account.email.endswith("@" + identity.SAMPLE_EMAIL_DOMAIN)
        assert check_password(PASSWORD, account.password)
    assert all(p.full_name for p in IndividualProfile.objects.all())
    assert all(p.agency_name and p.city_id for p in AgencyProfile.objects.all())


def test_a_second_run_creates_and_changes_nothing():
    sample_data.seed(8, PASSWORD)
    before = snapshot()
    titles = set(all_listings().values_list("title", flat=True))
    again = sample_data.seed(8)
    assert (again.accounts_created, again.listings_created, again.images_created) == (0, 0, 0)
    assert again.generated_password is None  # no account was created: no password needed
    assert snapshot() == before
    assert set(all_listings().values_list("title", flat=True)) == titles


def test_a_larger_run_adds_only_what_is_missing():
    sample_data.seed(3, PASSWORD)
    report = sample_data.seed(5)
    assert (report.accounts_created, report.listings_created) == (0, 2)
    assert all_listings().count() == 5


def test_a_draft_left_by_an_interrupted_run_is_made_again_not_duplicated():
    sample_data.seed(1, PASSWORD)
    row = sample_data.row_for(1)
    owner = Actor.user(
        identity.sample_account_ids()[sample_data.email_of(sample_data.ACCOUNTS[1][1]).lower()], "r"
    )
    draft = services.create_draft(owner, *row["kind"])
    services.apply_updates(owner, draft.id, 1, {"title": row["title"]})
    report = sample_data.seed(2)
    assert report.listings_created == 1
    rows = all_listings().filter(title=row["title"])
    assert rows.count() == 1 and rows.get().status == "published" and rows.get().id != draft.id
    assert not all_listings().filter(status="draft").exists()


def test_the_count_is_bounded():
    for bad in (0, 61, -1, True, "5", None):
        with pytest.raises(sample_data.SeedRefused):
            sample_data.seed(bad, PASSWORD)
    assert Account.objects.count() == 0


def test_the_data_is_deterministic_and_every_title_is_unique():
    titles = [sample_data.row_for(i)["title"] for i in range(sample_data.MAX_LISTINGS)]
    assert len(set(titles)) == len(titles) and all(10 <= len(t) <= 100 for t in titles)
    assert sample_data.row_for(30) == sample_data.row_for(30)
    sample_data.seed(7, PASSWORD)
    names = sorted(all_listings().values_list("title", flat=True))
    sample_data.reseed(7, PASSWORD)
    assert sorted(all_listings().values_list("title", flat=True)) == names


def test_a_large_run_works():
    report = sample_data.seed(sample_data.MAX_LISTINGS, PASSWORD)
    assert report.listings_created == sample_data.MAX_LISTINGS


# --- reset and reseed ----------------------------------------------------------------------


def test_reset_removes_only_the_sample_records():
    real = make_account("real.person@example.com")
    lookalike = make_account(f"sample.individual.1@{identity.SAMPLE_EMAIL_DOMAIN}.example.com")
    lookalike_two = make_account("sample.individual.1@sample-estate-ai.invalid")
    mine = [ready_draft(a) for a in (real, lookalike, lookalike_two)]
    publication.publish(user(real), mine[0].id, mine[0].revision, "key-real")
    real_event = PublicationEvent.objects.get()
    sample_data.seed(6, PASSWORD)
    sample_ids = sample_owner_ids()
    others = [real.id, lookalike.id, lookalike_two.id]
    kept_assets = MediaAsset.objects.filter(owner_account_id__in=others)
    kept = (kept_assets.count(), ListingImage.objects.filter(listing_id__in=[m.id for m in mine]))
    audit_before = AuditRecord.objects.count()
    report = sample_data.reset()
    assert (report.accounts_removed, report.listings_removed, report.events_removed) == (5, 6, 6)
    assert identity.sample_account_ids() == {}
    assert {a.id for a in Account.objects.all()} == {real.id, lookalike.id, lookalike_two.id}
    assert {p.id for p in PropertyListing.objects.all()} == {m.id for m in mine}
    assert kept_assets.count() == kept[0] == 3 and kept[1].count() == 3
    assert list(PublicationEvent.objects.all()) == [real_event]
    assert AuditRecord.objects.count() > audit_before  # audit is kept, and grows
    assert AuditRecord.objects.filter(action="account.created").count() >= 5
    assert MediaAsset.objects.count() == kept[0]  # every sample asset row is gone
    assert not StorageUsage.objects.filter(account_id__in=sample_ids).exists()  # no row left
    assert not MediaAsset.objects.filter(owner_account_id__in=sample_ids).exists()
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()
    assert AuditRecord.objects.filter(action="listing.sample_removed").count() == 6


def test_reset_deletes_the_stored_files_and_gives_the_usage_back(
    django_capture_on_commit_callbacks,
):
    sample_data.seed(4, PASSWORD)
    keys = list(MediaAsset.objects.values_list("storage_key", flat=True))
    store = storage.get_storage()
    assert keys and all(store.get(k) for k in keys)
    with django_capture_on_commit_callbacks(execute=True):
        sample_data.reset()
    for key in keys:
        with pytest.raises((OSError, KeyError, ValueError)):
            store.get(key)
    assert not StorageUsage.objects.exists()


def test_reset_on_an_empty_database_is_a_no_op():
    report = sample_data.reset()
    assert (report.accounts_removed, report.listings_removed, report.events_removed) == (0, 0, 0)


def test_reseed_twice_in_a_row_ends_in_the_same_state():
    sample_data.seed(6, PASSWORD)
    ends = []
    for _ in range(2):
        report = sample_data.reseed(6, PASSWORD)
        assert (report.accounts_removed, report.accounts_created) == (5, 5)
        assert (report.listings_removed, report.listings_created) == (6, 6)
        ends.append(
            (
                sorted(all_listings().values_list("title", flat=True)),
                Account.objects.count(),
                PublicationEvent.objects.count(),
                MediaAsset.objects.count() > 0,
            )
        )
    assert ends[0] == ends[1]
    assert PublicationEvent.objects.filter(kind="seed").count() == 6  # none left over


# --- what the seeded data looks like to others ---------------------------------------------


def test_the_seeded_listings_are_public_and_hide_the_private_address():
    sample_data.seed(12, PASSWORD)
    visitor = Actor.visitor("r")
    with_private = {a.listing_id for a in ListingPrivateAddress.objects.all()}
    assert 3 <= len(with_private) < 12  # some have a private address, so private tests have data
    private_values = set(ListingPrivateAddress.objects.values_list("street", flat=True))
    assert private_values - {None}
    for row in all_listings():
        shown = selectors.public_listing(visitor, row.id)
        assert 1 <= len(shown.media) <= 3
        text = repr(shown.fields)
        assert "street" not in shown.fields and "house_number" not in shown.fields
        assert not any(value in text for value in private_values if value)
    assert selectors.public_listings().filter(owner_account_id__in=sample_owner_ids()).count() == 12
    amenities = selectors.amenities_by_listing(r.id for r in all_listings())
    assert any(found for found in amenities.values())


def test_seeded_accounts_keep_their_full_allowance_and_a_real_publication_counts():
    sample_data.seed(12, PASSWORD)
    now = datetime.now(UTC)
    for account in Account.objects.all():
        allowance = quota.allowance_for(account.id, account.account_type, now)
        assert (allowance.used, allowance.remaining) == (0, allowance.limit)
    events = PublicationEvent.objects.all()
    assert events.count() == 12 and not events.filter(counted=True).exists()
    assert set(events.values_list("kind", flat=True)) == {"seed"}
    owner = Account.objects.filter(account_type="individual").first()
    view = ready_draft(SimpleNamespace(id=owner.id))
    done = publication.publish(user(owner), view.id, view.revision, "key-real-1")
    assert done.status == "published"
    used = quota.allowance_for(owner.id, owner.account_type, now)
    assert used.used == 1 and used.remaining == used.limit - 1
    assert PublicationEvent.objects.filter(kind="initial", counted=True).count() == 1


# --- publish(kind="seed") ------------------------------------------------------------------


def seed_publish(view, key="seed-key-1", actor=SYSTEM, **kw):
    return publication.publish(actor, view.id, view.revision, key, kind="seed", **kw)


def test_the_system_actor_publishes_with_the_seed_kind_and_uses_no_quota():
    owner = sample_account()
    view = sample_draft(owner)
    done = seed_publish(view, now=NOW)
    assert (done.status, done.visibility, done.revision) == (
        "published",
        "public",
        view.revision + 1,
    )
    event = PublicationEvent.objects.get()
    assert (event.kind, event.counted, event.account_id, event.listing_id) == (
        "seed",
        False,
        owner.id,
        view.id,
    )
    assert (event.idempotency_key, event.created_at) == ("seed-key-1", NOW)
    assert event.identity_fingerprint
    assert quota.allowance_for(owner.id, "individual", NOW).used == 0
    record = AuditRecord.objects.get(action="listing.published")
    assert record.target_id == view.id and record.actor_id is None
    assert PropertyListing.objects.get().status == "published"


def test_seed_needs_no_available_allowance():
    owner = sample_account()
    for number in range(2):  # an Individual's whole month
        view = sample_draft(owner)
        publication.publish(user(owner), view.id, view.revision, f"key-real-{number}")
    assert quota.allowance_for(owner.id, "individual", datetime.now(UTC)).remaining == 0
    assert seed_publish(sample_draft(owner)).status == "published"
    assert PublicationEvent.objects.filter(kind="seed", counted=False).count() == 1


@pytest.mark.parametrize("kind", ["user", "admin", "visitor"])
def test_no_other_actor_may_pass_the_seed_kind(kind):
    owner = sample_account()
    view = sample_draft(owner)
    actor = {
        "user": user(owner),
        "admin": Actor.admin(uuid.uuid4(), "r"),
        "visitor": Actor.visitor("r"),
    }[kind]
    before = snapshot()
    with pytest.raises(DomainError) as caught:
        seed_publish(view, actor=actor)
    assert caught.value.code == ("unauthenticated" if kind == "visitor" else "forbidden")
    assert snapshot() == before and PublicationEvent.objects.count() == 0
    assert PropertyListing.objects.get().status == "draft"


def test_the_owner_still_publishes_normally_and_an_unknown_kind_is_refused():
    owner = sample_account()
    view = sample_draft(owner)
    for bad in ("counted", "", None, "SEED", "republish"):
        with pytest.raises(DomainError) as caught:
            publication.publish(SYSTEM, view.id, view.revision, "key-bad", kind=bad)
        assert caught.value.code == "validation_error"
    assert PublicationEvent.objects.count() == 0
    done = publication.publish(user(owner), view.id, view.revision, "key-normal")
    assert done.status == "published"
    event = PublicationEvent.objects.get()
    assert (event.kind, event.counted) == ("initial", True)


def test_a_seed_publication_for_a_real_owner_is_not_found():
    real = make_account("real.person@example.com")
    view = ready_draft(real)
    before = snapshot()
    with pytest.raises(DomainError) as caught:
        seed_publish(view)
    assert caught.value.code == "not_found" and snapshot() == before
    with pytest.raises(DomainError) as caught:
        seed_publish(SimpleNamespace(id=uuid.uuid4(), revision=1))
    assert caught.value.code == "not_found"


def test_a_seed_publication_replays_with_the_same_key_and_writes_once():
    owner = sample_account()
    view = sample_draft(owner)
    first = seed_publish(view)
    again = seed_publish(view)  # the same key, the stale base revision of the draft: a replay
    assert again.status == "published" and again.fields == first.fields
    assert PublicationEvent.objects.count() == 1
    assert AuditRecord.objects.filter(action="listing.published").count() == 1
    other = sample_draft(owner)
    with pytest.raises(DomainError) as caught:
        seed_publish(other)  # the same key for another listing
    assert caught.value.code == "idempotency_key_reused"
    assert PublicationEvent.objects.count() == 1


def test_seed_still_validates_the_draft_completely():
    owner = sample_account()
    view = ready_draft(owner, extra={"title": None})
    before = snapshot()
    with pytest.raises(publication.FieldsInvalid) as caught:
        seed_publish(view)
    assert caught.value.fields == {"title": "required"}
    assert snapshot() == before
    bare = services.create_draft(user(owner), "sale", "house", "house")
    with pytest.raises(publication.FieldsInvalid) as caught:
        seed_publish(bare)
    assert "images" in caught.value.fields and "asking_price" in caught.value.fields
    stale = sample_draft(owner)
    with pytest.raises(services.StaleRevision):
        publication.publish(SYSTEM, stale.id, stale.revision + 3, "key-stale", kind="seed")
    assert PublicationEvent.objects.count() == 0


def test_seed_keeps_the_account_state_refusals():
    owner = sample_account()
    view = sample_draft(owner)
    Account.objects.filter(id=owner.id).update(email_verified_at=None)
    with pytest.raises(DomainError) as caught:
        seed_publish(view)
    assert caught.value.code == "email_not_verified"
    assert PublicationEvent.objects.count() == 0


def test_the_http_route_cannot_produce_a_seed_event():
    from tests.api.test_publish_api import csrf, draft, signed_in, url

    client = signed_in()
    listing_id, revision = draft(client)

    def post(body, key):
        return client.post(
            url(listing_id),
            json.dumps(body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
            HTTP_KIND="seed",  # a header of that name is ignored
            HTTP_X_KIND="seed",
            **csrf(client),
        )

    # the body takes `base_revision` only: an extra `kind` is refused and nothing is written
    refused = post({"base_revision": revision, "kind": "seed"}, "key-http-1")
    assert refused.status_code == 400 and PublicationEvent.objects.count() == 0
    assert PropertyListing.objects.get().status == "draft"
    done = post({"base_revision": revision}, "key-http-2")
    assert done.status_code == 200 and done.json()["status"] == "published"
    event = PublicationEvent.objects.get()
    assert (event.kind, event.counted) == ("initial", True)


def _publish_aliases(tree) -> set[str]:
    """Names that stand for `publish` or the publication module in this file."""
    names = {"publish", "publication"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in ("publish", "publication"):
                    names.add(alias.asname or alias.name.rsplit(".", 1)[-1])
        if isinstance(node, ast.Assign) and any(
            isinstance(n, ast.Attribute | ast.Name)
            and getattr(n, "attr", getattr(n, "id", "")) in names
            for n in ast.walk(node.value)
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def kind_call_sites(tree) -> list[int]:
    """Lines of a call to `publish` (by any alias, attribute or alias of the module) that passes
    a `kind` keyword or `**` arguments, and of dynamic access (`getattr`) to publish."""
    names = _publish_aliases(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            target = node.args[1]
            if not isinstance(target, ast.Constant) or target.value == "publish":
                if any(getattr(n, "id", "") in names for n in ast.walk(node.args[0])):
                    found.append(node.lineno)
            continue
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called in names and any(k.arg in ("kind", None) for k in node.keywords):
            found.append(node.lineno)
    return found


def test_only_the_sample_data_module_passes_a_kind_to_publish():
    offenders = []
    for path in sorted((ROOT / "apps").rglob("*.py")):
        if "migrations" not in path.parts and kind_call_sites(ast.parse(path.read_text())):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == ["apps/listings/sample_data.py"]


@pytest.mark.parametrize(
    "code",
    [
        "publication.publish(a, b, c, d, kind='seed')",
        "publish(a, b, c, d, kind='seed')",
        "from apps.listings.publication import publish as go\ngo(a, b, c, d, kind=k)",
        "import apps.listings.publication as pub\npub.publish(a, b, c, d, **extra)",
        "f = publication.publish\nf(a, b, c, d, kind='seed')",
        "getattr(publication, 'publish')(a, b, c, d)",
        "getattr(publication, name)(a, b)",
    ],
)
def test_the_kind_guard_can_fail(code):
    assert kind_call_sites(ast.parse(code))


def test_the_kind_guard_ignores_ordinary_calls():
    assert not kind_call_sites(ast.parse("publication.publish(a, b, c, d, now=n)\nother(kind=1)"))


# --- the privileged event removal and the identity services --------------------------------


def test_only_the_system_removes_seed_events_and_only_of_the_kind_seed():
    owner = sample_account()
    seed_publish(sample_draft(owner))
    view = sample_draft(owner)
    publication.publish(user(owner), view.id, view.revision, "key-real")
    with pytest.raises(DomainError) as caught:
        publication.remove_seed_events(user(owner), [owner.id])
    assert caught.value.code == "forbidden" and PublicationEvent.objects.count() == 2
    assert publication.remove_seed_events(SYSTEM, []) == 0
    assert publication.remove_seed_events(SYSTEM, [owner.id]) == 1
    assert list(PublicationEvent.objects.values_list("kind", flat=True)) == ["initial"]


def test_the_sample_listing_removal_is_system_only_and_sample_only():
    owner = sample_account()
    real = make_account("real.person@example.com")
    mine, theirs = sample_draft(owner), ready_draft(real)
    with pytest.raises(DomainError) as caught:
        services.remove_sample_listing(user(owner), mine.id)
    assert caught.value.code == "forbidden"
    assert services.remove_sample_listing(SYSTEM, theirs.id) is False
    assert services.remove_sample_listing(SYSTEM, "not-a-uuid") is False
    assert services.remove_sample_listing(SYSTEM, uuid.uuid4()) is False
    assert PropertyListing.objects.count() == 2
    assert services.remove_sample_listing(SYSTEM, mine.id) is True
    assert [p.id for p in PropertyListing.objects.all()] == [theirs.id]
    assert AuditRecord.objects.filter(action="listing.sample_removed").count() == 1
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()


def test_sample_accounts_are_created_and_removed_only_by_the_system_on_the_reserved_domain():
    real_email = "real.person@example.com"
    for bad in (
        (user(make_account(real_email)), SAMPLE_EMAIL),
        (SYSTEM, real_email),
        (SYSTEM, f"sample.x@{identity.SAMPLE_EMAIL_DOMAIN}.example.com"),
        (SYSTEM, "not an email"),
    ):
        with pytest.raises(DomainError):
            identity_services.create_sample_account(bad[0], bad[1], PASSWORD, "individual", "Ann")
    with pytest.raises(DomainError):
        identity_services.create_sample_account(SYSTEM, SAMPLE_EMAIL, PASSWORD, "admin", "Ann")
    with pytest.raises(DomainError):
        identity_services.create_sample_account(SYSTEM, SAMPLE_EMAIL, PASSWORD, "agency", "Ann")
    with pytest.raises(identity_services.PasswordInvalid):
        identity_services.create_sample_account(SYSTEM, SAMPLE_EMAIL, "short", "individual", "Ann")
    assert Account.objects.count() == 1  # only the real one
    created = sample_account()
    with pytest.raises(DomainError):
        sample_account()  # the address is taken
    real = Account.objects.get(email=real_email)
    assert identity_services.remove_sample_account(SYSTEM, real.id) is False
    with pytest.raises(DomainError):
        identity_services.remove_sample_account(user(real), created.id)
    assert identity_services.remove_sample_account(SYSTEM, "nonsense") is False
    assert identity_services.remove_sample_account(SYSTEM, created.id) is True
    assert identity_services.remove_sample_account(SYSTEM, created.id) is False
    assert list(Account.objects.values_list("id", flat=True)) == [real.id]
    assert AuditRecord.objects.filter(action="account.sample_removed").count() == 1


def test_an_administrator_cannot_be_provisioned_on_the_reserved_domain():
    with pytest.raises(identity_services.AdminRefused) as caught:
        identity_services.provision_admin(
            SYSTEM, f"admin.person@{identity.SAMPLE_EMAIL_DOMAIN}", PASSWORD
        )
    assert caught.value.reason == "invalid_email" and Account.objects.count() == 0


# --- the production guard ------------------------------------------------------------------


@pytest.mark.parametrize("call", ["seed", "reset", "reseed"])
def test_production_settings_refuse_everything_and_write_nothing(call):
    before = snapshot()
    with override_settings(SETTINGS_MODULE="config.settings.prod"):
        with pytest.raises(sample_data.SeedRefused, match="production"):
            getattr(sample_data, call)()
    # no flag overrides production
    with override_settings(SETTINGS_MODULE="config.settings.prod", SEED_ALLOWED=True):
        with pytest.raises(sample_data.SeedRefused, match="production"):
            getattr(sample_data, call)()
    assert snapshot() == before


def test_other_settings_need_an_explicit_allowance():
    with override_settings(SETTINGS_MODULE="config.settings.staging", SEED_ALLOWED=False):
        with pytest.raises(sample_data.SeedRefused, match="local and test"):
            sample_data.seed(1, PASSWORD)
    assert Account.objects.count() == 0
    with override_settings(SETTINGS_MODULE="config.settings.staging", SEED_ALLOWED=True):
        assert sample_data.seed(1, PASSWORD).listings_created == 1


def test_the_settings_pin_the_flag_off_in_production(monkeypatch):
    text = (ROOT / "config/settings/prod.py").read_text()
    assert "SEED_ALLOWED = False" in text
    assert "SEED_ALLOWED" in (ROOT / "config/settings/base.py").read_text()


def test_the_command_refuses_in_production_with_one_line_and_a_non_zero_exit():
    before = snapshot()
    with override_settings(SETTINGS_MODULE="config.settings.prod"):
        for args in ((), ("--reset",), ("--reseed",)):
            with pytest.raises(CommandError) as caught:
                run(*args)
            assert "production" in str(caught.value) and "\n" not in str(caught.value)
    assert snapshot() == before


def test_the_command_is_not_on_any_start_up_or_migration_path():
    for path in [*(ROOT / "apps").rglob("migrations/*.py"), *(ROOT / "config").rglob("*.py")]:
        assert "listings.sample_data" not in path.read_text(), path
        assert "import sample_data" not in path.read_text(), path
    assert "seed_sample_data" not in (ROOT / "config/apps.py").read_text()
    for path in (ROOT / "apps").rglob("apps.py"):
        assert "sample_data" not in path.read_text(), path


# --- the command ---------------------------------------------------------------------------


def test_the_command_prints_counts_and_a_generated_password_once(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # not a terminal: no prompt
    output = run("--listings", "5")
    lines = output.splitlines()
    assert counts(output) == {
        "accounts created": 5,
        "listings created": 5,
        "images created": ListingImage.objects.count(),
        "accounts removed": 0,
        "listings removed": 0,
        "publication events removed": 0,
    }
    passwords = [line for line in lines if line.startswith("seed accounts password: ")]
    assert len(passwords) == 1
    assert lines.index(passwords[0]) < lines.index("accounts created: 5")  # printed at once
    secret = passwords[0].partition(": ")[2]
    assert secret and output.count(secret) == 1
    assert all(check_password(secret, a.password) for a in Account.objects.all())
    assert secret not in caplog.text and secret not in "".join(
        str(r.__dict__) for r in caplog.records
    )
    again = run("--listings", "5")  # nothing to create: no password, none printed
    assert "password" not in again and counts(again)["accounts created"] == 0


def test_the_command_takes_the_password_from_one_line_of_stdin(monkeypatch):
    output = run(
        "--password-stdin",
        "--listings",
        "2",
        stdin=PASSWORD + "\nignored\n",
        monkeypatch=monkeypatch,
    )
    assert "password" not in output and PASSWORD not in output
    assert all(check_password(PASSWORD, a.password) for a in Account.objects.all())
    assert counts(output)["listings created"] == 2


def test_a_weak_stdin_password_is_refused_without_echo(monkeypatch):
    with pytest.raises(CommandError) as caught:
        run("--password-stdin", stdin="abc\n", monkeypatch=monkeypatch)
    assert "abc" not in str(caught.value) and "policy" in str(caught.value)
    assert Account.objects.count() == 0


def test_the_hidden_prompt_is_used_on_a_terminal(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    output = run("--listings", "1")
    assert "password" not in output
    assert all(check_password(PASSWORD, a.password) for a in Account.objects.all())
    # a mismatch is refused; an empty answer generates one
    sample_data.reset()
    answers = iter([PASSWORD, PASSWORD + "s"])
    with pytest.raises(CommandError, match="do not match"):
        run("--listings", "1")
    answers = iter([""])
    assert "seed accounts password:" in run("--listings", "1")


def test_a_password_is_never_an_argument():
    with pytest.raises(CommandError) as caught:
        run("--password", PASSWORD)
    assert PASSWORD not in str(caught.value)


def test_the_output_and_logs_never_carry_an_address_name_or_private_value(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    text = run("--reseed", "--yes", "--listings", "12") + run("--reset", "--yes")
    private = set(ListingPrivateAddress.objects.values_list("street", flat=True))
    for forbidden in (
        "@",
        identity.SAMPLE_EMAIL_DOMAIN,
        "sample.individual",
        "Sana Sample",
        "Sample Realty",
        "+92",
        "Sample Street",
        "Sample Lane",
        *[p for p in private if p],
    ):
        assert forbidden not in text and forbidden not in caplog.text, forbidden
    for line in text.splitlines():
        assert (
            line.startswith(("seed accounts password: ", "will remove: "))
            or line.rpartition(": ")[2].isdigit()
        )


def test_the_command_resets_and_reseeds(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    run("--listings", "4")
    assert all_listings().count() == 4
    out = counts(run("--reset", "--yes"))
    assert (
        out["accounts removed"],
        out["listings removed"],
        out["publication events removed"],
    ) == (5, 4, 4)
    assert Account.objects.count() == 0
    out = counts(run("--reseed", "--yes", "--listings", "3"))
    assert (out["accounts removed"], out["accounts created"], out["listings created"]) == (0, 5, 3)
    out = counts(run("--reseed", "--yes", "--listings", "3"))
    assert (out["accounts removed"], out["listings removed"], out["listings created"]) == (5, 3, 3)


@pytest.mark.parametrize(
    "args",
    [
        ("--reset", "--reseed"),
        ("--reset", "--password-stdin", "--yes"),
        ("--yes",),
        ("--listings", "0"),
        ("--listings", "61"),
        ("--listings", "many"),
    ],
)
def test_bad_arguments_are_refused_and_change_nothing(args, monkeypatch):
    with pytest.raises((CommandError, SystemExit)):
        run(*args, stdin="x\n", monkeypatch=monkeypatch)
    assert Account.objects.count() == 0


def test_a_failure_leaves_no_half_made_listing(monkeypatch):
    owner_calls = []

    def boom(*args, **kwargs):
        owner_calls.append(1)
        raise DomainError("conflict")

    monkeypatch.setattr(publication, "publish", boom)
    with pytest.raises(DomainError):
        sample_data.seed(2, PASSWORD)
    assert owner_calls == [1]
    assert PropertyListing.objects.count() == 0 and MediaAsset.objects.count() == 0
    assert not StorageUsage.objects.filter(bytes_used__gt=0).exists()


# --- the reserved domain -------------------------------------------------------------------

LOOKALIKES = [
    f"x@a.{identity.SAMPLE_EMAIL_DOMAIN}",  # a subdomain
    f"x@{identity.SAMPLE_EMAIL_DOMAIN}.com",  # another TLD after it
    "x@sample.estate-ai.com",
    "x@sample-estate-ai.invalid",
    "x@xsample.estate-ai.invalid",
    "x@sample.estate-ai.invalid.example.org",
    "x@sаmple.estate-ai.invalid",  # a Cyrillic "a"
    "x@sample.estate-aİ.invalid",  # a dotted capital I
    "sample.estate-ai.invalid@example.com",
    "x@example.com",
]


@pytest.mark.parametrize(
    "email",
    [
        f"x@{identity.SAMPLE_EMAIL_DOMAIN}",
        f"x@{identity.SAMPLE_EMAIL_DOMAIN.upper()}",
        f"x@sub.{identity.SAMPLE_EMAIL_DOMAIN}",
        f"x@Deep.Sub.{identity.SAMPLE_EMAIL_DOMAIN.upper()}",
    ],
)
def test_the_reserved_domain_is_refused_everywhere_but_the_sample_service(email):
    from apps.identity import validation

    assert validation.check_email(email) == ("", "invalid_email")
    assert validation.check_email(email, allow_sample=True)[1] is None
    with pytest.raises(DomainError):
        identity_services.create_account(SYSTEM, email, PASSWORD, "individual")
    with pytest.raises(identity_services.RegistrationInvalid) as caught:
        identity_services.register_individual(
            Actor.visitor("r"), "Ann Example", email, PASSWORD, "03001234567"
        )
    assert caught.value.fields == {"email": "invalid_email"}
    with pytest.raises(identity_services.AdminRefused):
        identity_services.provision_admin(SYSTEM, email, PASSWORD)
    real = make_account("real.person@example.com")
    with pytest.raises(identity_services.FieldsInvalid) as changed:
        identity_services.request_email_change(user(real), email, PASSWORD)
    assert changed.value.fields == {"new_email": "invalid_email"}
    assert Account.objects.count() == 1  # only the real one


def test_registration_of_an_agency_refuses_it_too():
    place = sample_data._city()
    with pytest.raises(identity_services.RegistrationInvalid) as caught:
        identity_services.register_agency(
            Actor.visitor("r"),
            "Example Agency",
            f"x@{identity.SAMPLE_EMAIL_DOMAIN}",
            PASSWORD,
            "03001234567",
            "Example Road, Karachi",
            place.id,
        )
    assert caught.value.fields == {"email": "invalid_email"}


@pytest.mark.parametrize("email", LOOKALIKES)
def test_lookalikes_are_never_sample_and_both_matchers_agree(email):
    assert identity.is_sample_email(email) is False
    row = Account.objects.create(
        email=email, password="!", account_type="individual", email_verified_at=NOW
    )
    assert identity.sample_account_ids() == {}
    assert identity.is_sample_account(row.id) is False
    assert identity_services.remove_sample_account(SYSTEM, row.id) is False
    assert sample_data.reset().accounts_removed == 0
    assert Account.objects.filter(id=row.id).exists()


def test_the_python_and_sql_matchers_agree_on_case():
    upper = f"X@{identity.SAMPLE_EMAIL_DOMAIN.upper()}"
    row = Account.objects.create(
        email=upper, password="!", account_type="agency", email_verified_at=NOW
    )
    assert identity.is_sample_email(upper) is True
    assert identity.sample_account_ids() == {upper.lower(): row.id}
    assert identity.is_sample_account(row.id) is True
    admin = Account.objects.create(
        email=f"a@{identity.SAMPLE_EMAIL_DOMAIN}", password="!", account_type="admin"
    )
    assert admin.id not in identity.sample_account_ids().values()  # an administrator never is


# --- removal on its own, the guard's other rules, the password handling ---------------------


def test_removing_an_account_alone_is_refused_while_its_listings_remain():
    sample_data.seed(1, PASSWORD)
    owner_id = next(i for i in sample_owner_ids() if all_listings().filter(owner_account_id=i))
    assets = MediaAsset.objects.filter(owner_account_id=owner_id).count()
    assert assets and StorageUsage.objects.filter(account_id=owner_id).exists()
    assert identity_services.remove_sample_account(SYSTEM, owner_id) is False
    assert Account.objects.filter(id=owner_id).exists()
    assert MediaAsset.objects.filter(owner_account_id=owner_id).count() == assets
    assert StorageUsage.objects.get(account_id=owner_id).bytes_used > 0
    for listing_id in selectors.listing_ids_owned_by([owner_id]):
        assert services.remove_sample_listing(SYSTEM, listing_id)
    assert identity_services.remove_sample_account(SYSTEM, owner_id) is True
    assert not StorageUsage.objects.filter(account_id=owner_id).exists()


def test_the_usage_row_is_forgotten_only_by_the_system_and_only_when_empty():
    from apps.kernel import media

    account = make_account()
    StorageUsage.objects.create(account_id=account.id, bytes_used=5)
    with pytest.raises(DomainError):
        media.forget_usage(user(account), account.id)
    assert media.forget_usage(SYSTEM, account.id) is False  # it still counts bytes
    StorageUsage.objects.filter(account_id=account.id).update(bytes_used=0)
    assert media.forget_usage(SYSTEM, account.id) is True
    assert media.forget_usage(SYSTEM, account.id) is False


def fake_settings(module="config.settings.local", allowed=False, host="127.0.0.1"):
    return SimpleNamespace(
        SETTINGS_MODULE=module, SEED_ALLOWED=allowed, DATABASES={"default": {"HOST": host}}
    )


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "", "/run/postgresql"]
)
def test_a_local_database_host_is_allowed(monkeypatch, host):
    monkeypatch.setattr(sample_data, "settings", fake_settings(host=host))
    sample_data.require_allowed()


@pytest.mark.parametrize("host", ["db.example.org", "10.0.0.5", "127.0.0.1.example.org"])
def test_a_remote_database_host_is_refused_unless_allowed(monkeypatch, host):
    monkeypatch.setattr(sample_data, "settings", fake_settings(host=host))
    with pytest.raises(sample_data.SeedRefused, match="not on this machine"):
        sample_data.require_allowed()
    monkeypatch.setattr(sample_data, "settings", fake_settings(host=host, allowed=True))
    sample_data.require_allowed()
    monkeypatch.setattr(sample_data, "settings", fake_settings("config.settings.prod", True, host))
    with pytest.raises(sample_data.SeedRefused, match="production"):
        sample_data.require_allowed()


def test_the_settings_module_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setattr(sample_data, "settings", fake_settings(module=None))
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.prod")
    with pytest.raises(sample_data.SeedRefused, match="production"):
        sample_data.require_allowed()
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.test")
    sample_data.require_allowed()
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.other")
    with pytest.raises(sample_data.SeedRefused, match="local and test"):
        sample_data.require_allowed()
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE")
    with pytest.raises(sample_data.SeedRefused):
        sample_data.require_allowed()


@pytest.mark.parametrize("args", [("--reset",), ("--reseed",)])
def test_reset_and_reseed_print_the_count_and_need_yes(args, monkeypatch):
    sample_data.seed(3, PASSWORD)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(CommandError, match="--yes"):
        call_command("seed_sample_data", *args, stdout=out)
    assert out.getvalue().splitlines() == [
        "will remove: 5 accounts, 3 listings, 3 publication events"
    ]
    assert all_listings().count() == 3 and Account.objects.count() == 5  # nothing removed


def test_a_failure_after_the_accounts_exist_still_printed_the_password_once(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def boom(*args, **kwargs):
        raise DomainError("conflict")

    monkeypatch.setattr(publication, "publish", boom)
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("seed_sample_data", "--listings", "2", stdout=out)
    lines = out.getvalue().splitlines()
    passwords = [line for line in lines if line.startswith("seed accounts password: ")]
    assert len(passwords) == 1 and out.getvalue().count(passwords[0].partition(": ")[2]) == 1
    assert "The run failed: run with --reseed to start over." in lines
    secret = passwords[0].partition(": ")[2]
    assert all(check_password(secret, a.password) for a in Account.objects.all())
    assert secret not in caplog.text


def test_a_rerun_with_a_given_password_says_that_existing_accounts_keep_theirs(monkeypatch):
    sample_data.seed(2, PASSWORD)
    out = run(
        "--password-stdin", "--listings", "2", stdin=PASSWORD + "s\n", monkeypatch=monkeypatch
    )
    assert out.count("notice: existing sample accounts keep their password") == 1
    assert PASSWORD not in out
    assert all(check_password(PASSWORD, a.password) for a in Account.objects.all())
    plain = run("--listings", "2")
    assert "notice" not in plain


def test_a_missing_locality_fails_loudly_and_creates_nothing(monkeypatch):
    real = sample_data.catalog.list_localities

    def without_clifton(city_id):
        return tuple(loc for loc in real(city_id) if loc.name != "Clifton")

    monkeypatch.setattr(sample_data.catalog, "list_localities", without_clifton)
    with pytest.raises(sample_data.SeedRefused, match="Clifton"):
        sample_data.seed(3, PASSWORD)
    assert Account.objects.count() == 0 and PropertyListing.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_seeding_with_background_estimates_on_runs_them_in_the_calling_thread(
    settings, monkeypatch
):
    """With them on the executor, estimates hold the heavy slots and a later upload is `busy`."""
    settings.VALUATION_BACKGROUND_ESTIMATES = True
    seen = []
    real = services.upload_image

    def upload(*args, **kwargs):
        seen.append(settings.VALUATION_BACKGROUND_ESTIMATES)
        return real(*args, **kwargs)

    monkeypatch.setattr(services, "upload_image", upload)
    sample_data.seed(3, PASSWORD)
    assert seen and not any(seen)
    assert settings.VALUATION_BACKGROUND_ESTIMATES is True
