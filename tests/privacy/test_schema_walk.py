"""Gate 1 (Story 3.2): no public or other-account response schema has a private field."""

import copy
import re

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.catalog import registry
from apps.listings.fields import WIRE_FIELDS
from apps.presentation.api.authz_matrix import Route
from tests.privacy.audience import OTHER_ACCOUNT, PUBLIC, audience, routes

pytestmark = pytest.mark.guard

NOT_IN_SCHEMA = {("/api/v1/schema", "GET")}  # the contract itself
PHONE_LIKE = re.compile(r"phone|mobile|whatsapp|msisdn|contact_?(no|num)", re.IGNORECASE)
# The private profile columns of `identity` (not registry fields).
PRIVATE_PROFILE = {"phone", "business_phone", "office_address"}


def private_names() -> set[str]:
    listing = {
        name
        for name in WIRE_FIELDS
        if (found := registry.field(name)) is not None and found.field_class == "private"
    }
    assert listing  # the registry has private fields; an empty set would check nothing
    return listing | PRIVATE_PROFILE


def property_names(schema, components, seen=()):
    """Every property name reachable from `schema`, with `$ref`s resolved."""
    if isinstance(schema, list):
        for item in schema:
            yield from property_names(item, components, seen)
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name not in seen:
            yield from property_names(components[name], components, (*seen, name))
    for name, child in schema.get("properties", {}).items():
        yield name
        yield from property_names(child, components, seen)
    for key in ("items", "additionalProperties", "allOf", "anyOf", "oneOf"):
        yield from property_names(schema.get(key), components, seen)


def schema_leaks(document, table) -> list[str]:
    components = document["components"]["schemas"]
    by_operation = {
        (re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", route.path), method): route
        for route in table
        for method in route.methods
        if method != "HEAD"
    }
    operations = {
        (path, method.upper()): operation
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
    }
    unchecked = set(by_operation) - set(operations) - NOT_IN_SCHEMA
    assert not unchecked, f"routes missing from the OpenAPI schema: {sorted(unchecked)}"
    private, leaks = private_names(), []
    for (path, method), operation in operations.items():
        assert (path, method) in by_operation, f"{method} {path} is not in the route table"
        seen_by = audience(by_operation[path, method])
        if seen_by not in (PUBLIC, OTHER_ACCOUNT):
            continue
        for status, response in operation["responses"].items():
            names = set(response.get("headers", {}))
            for content in response.get("content", {}).values():
                names |= set(property_names(content.get("schema"), components))
            for name in sorted(names):
                if name in private or (seen_by == PUBLIC and PHONE_LIKE.search(name)):
                    leaks.append(f"{method} {path} {status}: {name}")
    return leaks


@pytest.fixture(scope="module")
def document():
    return SchemaGenerator().get_schema(request=None, public=True)


def test_no_private_field_in_a_public_or_other_account_schema(document):
    leaks = schema_leaks(document, routes())
    assert not leaks, "private fields in a response schema: " + "; ".join(leaks)


@pytest.mark.parametrize("name", ["street", "latitude", "office_address", "seller_phone"])
def test_the_walk_fails_when_a_private_field_is_added_behind_refs(document, name):
    broken = copy.deepcopy(document)
    # `PublicListingPage -> results[] -> PublicListingCard -> city -> Place`
    broken["components"]["schemas"]["Place"]["properties"][name] = {"type": "string"}
    leaks = schema_leaks(broken, routes())
    assert f"GET /api/v1/public/listings 200: {name}" in leaks


@pytest.mark.parametrize("permissions", [("signed_in",), ("deny",), ()])
def test_an_unclassified_route_fails(permissions):
    route = Route("/api/v1/new", ("GET",), "v", permissions, bool(permissions), bool(permissions))
    with pytest.raises(AssertionError, match="/api/v1/new"):
        audience(route)
