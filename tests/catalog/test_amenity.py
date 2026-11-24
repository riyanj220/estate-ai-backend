"""The amenity catalogue (Story 2.8): the model, the seed migration, the selectors, the admin
service and the public endpoint. Codes and labels in tests are low-entropy fake words."""

import importlib
import uuid

import pytest
import yaml
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction

from apps.catalog import selectors, services
from apps.catalog.models import Amenity
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord

ADMIN = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "req-1")
SEED = importlib.import_module("apps.catalog.migrations.0006_seed_amenities")

# The vocabulary of the story, written out independently of the migration: (code, label, kind,
# group) in the order the catalogue serves it.
EXPECTED = (
    ("electricity_backup", "Electricity backup", "on_property", "utilities"),
    ("broadband_internet", "Broadband internet", "on_property", "utilities"),
    ("waste_disposal", "Waste disposal", "on_property", "utilities"),
    ("double_glazed_windows", "Double-glazed windows", "on_property", "building"),
    ("central_air_conditioning", "Central air conditioning", "on_property", "building"),
    ("central_heating", "Central heating", "on_property", "building"),
    ("elevator", "Elevator", "on_property", "building"),
    ("lobby", "Lobby", "on_property", "building"),
    ("intercom", "Intercom", "on_property", "building"),
    ("conference_room", "Conference room", "on_property", "business"),
    ("business_centre", "Business centre", "on_property", "business"),
    ("atm_in_building", "ATM in building", "on_property", "business"),
    ("community_lawn", "Community lawn", "community", "residential_community"),
    ("swimming_pool", "Swimming pool", "community", "residential_community"),
    ("community_gym", "Gym", "community", "residential_community"),
    ("daycare", "Daycare", "community", "residential_community"),
    ("childrens_play_area", "Children's play area", "community", "residential_community"),
    ("bbq_area", "BBQ area", "community", "residential_community"),
    ("community_mosque", "Community mosque", "community", "community_services"),
    ("medical_facility", "Medical facility", "community", "community_services"),
    ("community_centre", "Community centre", "community", "community_services"),
    ("nearby_mosque", "Mosque", "nearby", "nearby"),
    ("nearby_park", "Park", "nearby", "nearby"),
    ("nearby_gym", "Gym", "nearby", "nearby"),
    ("nearby_school", "School", "nearby", "nearby"),
    ("nearby_hospital", "Hospital", "nearby", "nearby"),
)


def run_seed():
    SEED.seed_amenities(django_apps, None)


@pytest.fixture
def empty(db):
    Amenity.objects.all().delete()


# --- the seed ---------------------------------------------------------------------------------


def test_the_seeded_catalogue_is_exactly_the_story_vocabulary_in_order(db):
    # the migrated (or re-seeded) database, through the public selector
    served = tuple((a.code, a.label, a.kind, a.group) for a in selectors.list_amenities())
    assert served == EXPECTED
    assert len(EXPECTED) == 26
    assert {code for code, *_ in EXPECTED} == {row.code for row in Amenity.objects.all()}
    assert all(row.is_active for row in Amenity.objects.all())


def test_the_migration_list_equals_the_vocabulary_as_a_set(db):
    assert {tuple(entry) for entry in SEED.AMENITIES} == set(EXPECTED)
    assert len({entry[0] for entry in SEED.AMENITIES}) == len(SEED.AMENITIES)  # unique codes


def test_the_five_nearby_types_are_the_owner_decision(db):
    nearby = [a for a in selectors.list_amenities() if a.kind == "nearby"]
    assert [a.code for a in nearby] == [
        "nearby_mosque",
        "nearby_park",
        "nearby_gym",
        "nearby_school",
        "nearby_hospital",
    ]
    assert {a.group for a in nearby} == {"nearby"}


def test_seed_creates_the_list_once_and_reseed_neither_duplicates_nor_overwrites(empty):
    run_seed()
    assert Amenity.objects.count() == len(EXPECTED)
    Amenity.objects.filter(code="elevator").update(label="Lift", is_active=False, sort_order=999)
    Amenity.objects.filter(code="lobby").delete()
    run_seed()
    run_seed()
    assert Amenity.objects.count() == len(EXPECTED)
    edited = Amenity.objects.get(code="elevator")
    assert (edited.label, edited.is_active, edited.sort_order) == ("Lift", False, 999)
    assert Amenity.objects.filter(code="lobby").count() == 1  # a removed row comes back once


@pytest.mark.django_db(transaction=True)
def test_the_migration_path_is_idempotent_and_leaves_an_edited_row_alone():
    from django.db.migrations.executor import MigrationExecutor

    conn = connections["default"]
    try:
        MigrationExecutor(conn).migrate([("catalog", "0005_amenity")])
        Amenity.objects.all().delete()
        MigrationExecutor(conn).migrate([("catalog", "0006_seed_amenities")])
        assert Amenity.objects.count() == len(EXPECTED)
        Amenity.objects.filter(code="daycare").update(label="Creche")
        run_seed()
        MigrationExecutor(conn).migrate([("catalog", "0005_amenity")])
        MigrationExecutor(conn).migrate([("catalog", "0006_seed_amenities")])
        assert Amenity.objects.count() == len(EXPECTED)
        assert Amenity.objects.get(code="daycare").label == "Creche"
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())
        if not Amenity.objects.exists():
            run_seed()  # leave the shared database seeded for the tests after this one


# --- the model and the selectors ----------------------------------------------------------------


def test_the_database_enforces_the_rules(empty):
    Amenity.objects.create(code="testing_a", label="Testing A", kind="on_property", group="testing")
    bad = (
        {"code": "testing_a", "kind": "community"},  # unique code
        {"code": "Testing_B"},  # snake_case only
        {"code": "testing b"},
        {"code": "1testing"},
        {"code": "t"},
        {"code": "testing_b", "kind": "far_away"},
        {"code": "testing_b", "label": "x"},
        {"code": "testing_b", "group": "Not Snake"},
    )
    for override in bad:
        values = {"code": "testing_b", "label": "Testing B", "kind": "nearby", "group": "testing"}
        values.update(override)
        with pytest.raises(IntegrityError), transaction.atomic():
            Amenity.objects.create(**values)


def test_list_is_active_only_ordered_by_kind_then_sort_order_and_plain(empty):
    for code, kind, order, active in (
        ("zz_nearby", "nearby", 1, True),
        ("zz_community", "community", 5, True),
        ("zz_prop_late", "on_property", 9, True),
        ("zz_prop_early", "on_property", 2, True),
        ("zz_hidden", "on_property", 1, False),
        ("zz_community_tie", "community", 5, True),
    ):
        Amenity.objects.create(
            code=code, label=code.upper(), kind=kind, group="testing", sort_order=order,
            is_active=active,
        )  # fmt: skip
    result = selectors.list_amenities()
    assert [a.code for a in result] == [
        "zz_prop_early",
        "zz_prop_late",
        "zz_community",
        "zz_community_tie",
        "zz_nearby",
    ]
    assert not any(isinstance(a, Amenity) for a in result)
    with pytest.raises(AttributeError):
        result[0].code = "x"
    assert selectors.amenity_codes() == frozenset(a.code for a in result)
    assert isinstance(selectors.amenity_codes(), frozenset)


# --- the admin service ----------------------------------------------------------------------------


def test_admin_creates_an_amenity_with_an_audit_record(empty):
    ref = services.create_amenity(
        ADMIN, "tennis_court", "  Tennis   court ", "community", "sports", 7
    )
    assert (ref.code, ref.label, ref.kind, ref.group) == (
        "tennis_court",
        "Tennis court",
        "community",
        "sports",
    )
    row = Amenity.objects.get()
    assert (row.sort_order, row.is_active) == (7, True)
    record = AuditRecord.objects.get(action="amenity.created")
    assert (record.target_type, record.target_id, record.metadata) == ("amenity", row.id, {})
    assert selectors.amenity_codes() == frozenset({"tennis_court"})


@pytest.mark.parametrize(
    ("actor", "code"),
    [
        (Actor.visitor(), "unauthenticated"),
        (Actor.user(uuid.uuid4()), "not_found"),
        (Actor.system(), "not_found"),
    ],
)
def test_non_admins_cannot_create(empty, actor, code):
    with pytest.raises(DomainError) as exc:
        services.create_amenity(actor, "tennis_court", "Tennis court", "community", "sports")
    assert exc.value.code == code
    assert not Amenity.objects.exists() and not AuditRecord.objects.exists()


@pytest.mark.parametrize(
    "args",
    [
        ("Tennis", "Tennis court", "community", "sports"),
        ("tennis court", "Tennis court", "community", "sports"),
        ("t", "Tennis court", "community", "sports"),
        (None, "Tennis court", "community", "sports"),
        ("tennis_court", "", "community", "sports"),
        ("tennis_court", "T", "community", "sports"),
        ("tennis_court", "T" * 81, "community", "sports"),
        ("tennis_court", "Tennis\x00court", "community", "sports"),
        ("tennis_court", 5, "community", "sports"),
        ("tennis_court", "Tennis court", "far_away", "sports"),
        ("tennis_court", "Tennis court", None, "sports"),
        ("tennis_court", "Tennis court", "community", "Not Snake"),
        ("tennis_court", "Tennis court", "community", None),
    ],
)
def test_bad_values_are_refused_and_write_nothing(empty, args):
    with pytest.raises(DomainError) as exc:
        services.create_amenity(ADMIN, *args)
    assert exc.value.code == "validation_error"
    assert not Amenity.objects.exists() and not AuditRecord.objects.exists()


def test_a_bad_sort_order_and_a_taken_code_are_refused(empty):
    services.create_amenity(ADMIN, "tennis_court", "Tennis court", "community", "sports")
    for args in (
        ("tennis_court", "Other label", "nearby", "other"),
        ("other_court", "Other", "nearby", "other", "3"),
        ("other_court", "Other", "nearby", "other", True),
        ("other_court", "Other", "nearby", "other", 2**31),
        ("other_court", "Other", "nearby", "other", -(2**31) - 1),
        ("other_court", "Other", "nearby", "other", 10**30),
    ):
        with pytest.raises(DomainError) as exc:
            services.create_amenity(ADMIN, *args)
        assert exc.value.code == "validation_error"
    assert Amenity.objects.count() == 1
    assert AuditRecord.objects.filter(action="amenity.created").count() == 1


def test_a_lost_race_is_a_validation_error(empty, monkeypatch):
    Amenity.objects.create(code="tennis_court", label="Tennis", kind="community", group="sports")
    original = Amenity.objects.filter
    calls = {"n": 0}

    class Blind:
        def exists(self):
            return False

    def blind_once(*args, **kwargs):
        calls["n"] += 1
        return Blind() if calls["n"] == 1 else original(*args, **kwargs)

    monkeypatch.setattr(Amenity.objects, "filter", blind_once)
    with transaction.atomic():
        with pytest.raises(DomainError) as exc:
            services.create_amenity(ADMIN, "tennis_court", "Again", "community", "sports")
        assert exc.value.code == "validation_error"
    monkeypatch.undo()
    assert Amenity.objects.count() == 1
    assert not AuditRecord.objects.filter(action="amenity.created").exists()


@pytest.mark.django_db(transaction=True)
def test_failure_after_the_audit_write_rolls_back_both(monkeypatch):
    before = Amenity.objects.count()
    original = services.audit.record

    def record_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        assert AuditRecord.objects.filter(action="amenity.created").exists()
        raise RuntimeError

    monkeypatch.setattr(services.audit, "record", record_then_fail)
    with pytest.raises(RuntimeError):
        services.create_amenity(ADMIN, "rollback_court", "Rollback court", "community", "sports")
    assert Amenity.objects.count() == before
    assert not AuditRecord.objects.filter(action="amenity.created").exists()


# --- the public endpoint --------------------------------------------------------------------------

URL = "/api/v1/reference/amenities"


def test_endpoint_is_public_ordered_and_returns_exactly_code_label_kind_group(client, db):
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items"]
    assert [tuple(item.values()) for item in body["items"]] == list(EXPECTED)
    assert all(list(item) == ["code", "label", "kind", "group"] for item in body["items"])
    assert "sessionid" not in response.cookies


def test_endpoint_hides_inactive_entries_and_serves_added_ones_without_a_deploy(client, empty):
    Amenity.objects.create(code="zz_open", label="Open one", kind="nearby", group="nearby")
    Amenity.objects.create(
        code="zz_shut", label="Shut one", kind="nearby", group="nearby", is_active=False
    )
    items = client.get(URL).json()["items"]
    assert items == [{"code": "zz_open", "label": "Open one", "kind": "nearby", "group": "nearby"}]


def test_an_empty_catalogue_is_an_empty_list(client, empty):
    assert client.get(URL).json() == {"items": []}


def test_the_endpoint_is_get_only(client, db):
    assert client.post(URL).status_code in (403, 405)
    assert client.delete(URL).status_code in (403, 405)


def test_the_catalogue_has_no_distance_or_verification_concept(client, db):
    names = {f.name for f in Amenity._meta.get_fields()}
    assert names == {
        "id",
        "code",
        "label",
        "kind",
        "group",
        "sort_order",
        "is_active",
        "created_at",
    }
    for item in client.get(URL).json()["items"]:
        assert set(item) == {"code", "label", "kind", "group"}


def test_openapi_documents_plain_strings_and_kind_as_a_closed_list(tmp_path):
    target = tmp_path / "o.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    assert "get" in schema["paths"][URL]
    amenity = schema["components"]["schemas"]["Amenity"]
    assert set(amenity["properties"]) == {"code", "label", "kind", "group"}
    for name in ("code", "label", "group"):
        assert amenity["properties"][name] == {"type": "string"}  # never an enum
    assert schema["components"]["schemas"]["AmenityKindEnum"]["enum"] == [
        "on_property",
        "community",
        "nearby",
    ]


def test_the_endpoint_sends_no_cache_directive_like_the_other_reference_lists(client, db):
    # "uncached" in the docs means no `Cache-Control` header is set (as for cities)
    assert "Cache-Control" not in client.get(URL).headers
    assert "Cache-Control" not in client.get("/api/v1/reference/cities").headers


def test_the_int32_edges_of_sort_order_are_accepted(empty):
    services.create_amenity(ADMIN, "edge_high", "Edge high", "nearby", "edges", 2**31 - 1)
    services.create_amenity(ADMIN, "edge_low", "Edge low", "nearby", "edges", -(2**31))
    assert Amenity.objects.count() == 2
