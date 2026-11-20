"""Route enumeration for the protected admin API and the no-public-admin-creation rule (1.22)."""

import ast
import json
import uuid
from pathlib import Path

import pytest
from django.test import Client

from apps.identity import services
from apps.identity.models import Account
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.presentation.api.authz_matrix import route_table
from apps.presentation.api.permissions import RequireAdmin

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "apps" / "identity" / "services.py"
ADMIN_PREFIX = "/api/v1/admin/"
REGISTRATION_PREFIX = "/api/v1/registrations/"


def view_class(route):
    module_name, _, name = route.view.rpartition(".")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def own_attribute(cls, name):
    """True when `name` is set on the view's own class chain, below DRF's base views."""
    for klass in cls.__mro__:
        if klass.__module__.startswith("rest_framework."):
            return False
        if name in vars(klass):
            return True
    return False


def test_there_is_at_least_one_admin_route():
    assert [r for r in route_table("config.urls") if r.path.startswith(ADMIN_PREFIX)]


def test_every_admin_route_requires_exactly_the_administrator_capability():
    rows = [r for r in route_table("config.urls") if r.path.startswith(ADMIN_PREFIX)]
    for row in rows:
        cls = view_class(row)
        assert tuple(cls.permission_classes) == (RequireAdmin,), row.path
        assert own_attribute(cls, "permission_classes"), row.path
        assert own_attribute(cls, "authentication_classes"), row.path
        assert cls.authentication_classes, f"{row.path} must authenticate by session"
        assert row.permissions == ("admin",) and row.explicit, row.path


def test_the_admin_sign_in_is_not_under_the_admin_prefix_and_is_public():
    row = next(r for r in route_table("config.urls") if r.path == "/api/v1/admin-session")
    assert not row.path.startswith(ADMIN_PREFIX) and row.permissions == ("public",)
    assert view_class(row).authentication_classes == []


@pytest.mark.django_db
def test_every_admin_route_denies_visitors_and_ordinary_accounts():
    rows = [r for r in route_table("config.urls") if r.path.startswith(ADMIN_PREFIX)]
    system = Actor.system("r")
    for kind in ("individual", "agency"):
        view = services.create_account(system, f"{kind}@example.com", "correct horse battery", kind)
        services.mark_email_verified(system, view.id)
    clients = {"visitor": Client(enforce_csrf_checks=True)}
    for kind in ("individual", "agency"):
        client = Client(enforce_csrf_checks=True)
        token = client.get("/api/v1/csrf").json()["csrf_token"]
        response = client.post(
            "/api/v1/session",
            json.dumps({"email": f"{kind}@example.com", "password": "correct horse battery"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        assert response.status_code == 200
        clients[kind] = client
    for row in rows:
        path = row.path.replace("<", "").replace(">", "")
        for method in row.methods or ("GET",):
            for who, expected in (("visitor", 401), ("individual", 404), ("agency", 404)):
                client = clients[who]
                token = client.get("/api/v1/csrf").json()["csrf_token"]
                response = client.generic(
                    method, path, "{}", content_type="application/json", HTTP_X_CSRFTOKEN=token
                )
                assert response.status_code == expected, (row.path, method, who)


# --- no path creates an administrator ------------------------------------------------------------


def registration_rows():
    return [r for r in route_table("config.urls") if r.path.startswith(REGISTRATION_PREFIX)]


def test_registration_routes_exist_and_are_public():
    rows = registration_rows()
    assert {r.path for r in rows} == {
        "/api/v1/registrations/individuals",
        "/api/v1/registrations/agencies",
    }
    assert all(r.permissions == ("public",) for r in rows)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hostile",
    [
        {"account_type": "admin"},
        {"role": "admin"},
        {"type": "admin", "is_admin": True},
        {
            "full_name": "A B",
            "email": "a@example.com",
            "password": "x" * 12,
            "account_type": "admin",
        },
    ],
)
def test_every_registration_route_refuses_a_hostile_type_or_role(hostile):
    for row in registration_rows():
        response = Client().post(row.path, json.dumps(hostile), content_type="application/json")
        assert response.status_code == 400, row.path
        body = response.json()["error"]
        assert body["code"] == "validation_error"
        assert body["fields"]["non_field_errors"], row.path
    assert not Account.objects.exists()


def _calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name
            ):
                yield node


def _enclosing_functions(tree):
    owners = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                owners.setdefault(id(child), node.name)
                owners[id(child)] = node.name
    return owners


def test_only_two_services_write_an_account_row_and_only_one_may_choose_admin():
    tree = ast.parse(SERVICES.read_text())
    owners = _enclosing_functions(tree)
    creators = {
        owners[id(call)]
        for call in _calls(tree, "create")
        if isinstance(call.func, ast.Attribute)
        and ast.unparse(call.func.value) == "Account.objects"
    }
    assert creators == {"_insert_account", "provision_admin"}


def test_account_inserts_from_registration_can_only_be_individual_or_agency():
    tree = ast.parse(SERVICES.read_text())
    owners = _enclosing_functions(tree)
    callers = {owners[id(c)] for c in _calls(tree, "_insert_account")}
    assert callers == {"create_account", "_register"}
    registers = {}
    for call in _calls(tree, "_register"):
        registers[owners[id(call)]] = ast.unparse(call.args[3])
    assert registers == {
        "register_individual": "AccountType.INDIVIDUAL.value",
        "register_agency": "AccountType.AGENCY.value",
    }
    assert not list(_calls(tree, "provision_admin")), "no service may call provision_admin"


def test_nothing_in_the_presentation_layer_can_reach_the_provisioning_services():
    forbidden = ("provision_admin", "disable_admin", "remove_admin", "reset_admin_password")
    for path in (ROOT / "apps" / "presentation").rglob("*.py"):
        text = path.read_text()
        assert not any(name in text for name in forbidden), path


@pytest.mark.django_db
def test_registration_services_create_only_individuals_and_agencies(
    django_capture_on_commit_callbacks,
):
    from apps.catalog.models import City

    City.objects.all().delete()
    city = City.objects.create(name="Karachi")
    visitor = Actor.visitor("r")
    with django_capture_on_commit_callbacks(execute=True):
        services.register_individual(
            visitor, "Ayesha Khan", "ayesha@example.com", "correct horse battery", "0300-1234567"
        )
        services.register_agency(
            visitor,
            "Skyline Estates",
            "office@skyline.example",
            "correct horse battery",
            "0321-5550123",
            "Suite 4, Harbour Tower, Karachi",
            city.id,
        )
    assert set(Account.objects.values_list("account_type", flat=True)) == {"individual", "agency"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "actor", [Actor.visitor("r"), Actor.user(uuid.uuid4()), Actor.admin(uuid.uuid4())]
)
def test_create_account_refuses_administrators_for_every_non_system_actor(actor):
    with pytest.raises(DomainError):
        services.create_account(actor, "x@example.com", "correct horse battery 42", "admin")
    assert not Account.objects.exists()


@pytest.mark.django_db
def test_no_administrator_exists_after_migrate():
    assert not Account.objects.filter(account_type="admin").exists()


def test_no_migration_settings_or_fixture_seeds_an_administrator():
    banned = (
        "provision_admin",
        'account_type="admin"',
        "account_type='admin'",
        "AccountType.ADMIN",
    )
    for folder in ("apps/identity/migrations", "apps/kernel/migrations", "config/settings"):
        for path in (ROOT / folder).glob("*.py"):
            text = path.read_text()
            assert not any(b in text for b in banned), path
    assert not list((ROOT / "apps").rglob("fixtures/*.json"))
