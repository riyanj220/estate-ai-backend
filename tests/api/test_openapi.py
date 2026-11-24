from pathlib import Path

import pytest
import yaml
from django.core.management import call_command

from apps.kernel import errors

ROOT = Path(__file__).resolve().parents[2]


def generate(tmp_path, *args):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target), *args)
    return target.read_text()


def test_committed_openapi_is_fresh(tmp_path):
    committed = (ROOT / "openapi.yaml").read_text()
    assert generate(tmp_path) == committed, (
        "openapi.yaml is stale. Regenerate it with: "
        "uv run python manage.py spectacular --validate --file openapi.yaml"
    )


def test_every_error_code_is_published_as_an_enum(tmp_path):
    schema = yaml.safe_load(generate(tmp_path))
    assert schema["components"]["schemas"]["ErrorCode"]["enum"] == errors.error_codes()


def test_new_code_changes_the_schema(tmp_path, monkeypatch):
    before = generate(tmp_path)
    extra = errors.ErrorCode("brand_new", 418, "x")
    monkeypatch.setitem(errors.CATALOGUE, "brand_new", extra)
    assert generate(tmp_path) != before


def test_operations_list_default_error_responses(tmp_path):
    schema = yaml.safe_load(generate(tmp_path, "--urlconf", "tests.api.urls"))
    operations = [op for item in schema["paths"].values() for op in item.values()]
    assert operations
    for operation in operations:
        for status in ("400", "401", "403", "404", "429", "500"):
            ref = operation["responses"][status]["content"]["application/json"]["schema"]
            assert ref == {"$ref": "#/components/schemas/ErrorEnvelope"}
        assert "Retry-After" in operation["responses"]["429"]["headers"]


def test_envelope_schema_matches_serializer():
    from apps.presentation.api.schema import _error_components
    from apps.presentation.api.serializers import ErrorBodySerializer

    body = _error_components()["ErrorBody"]
    assert set(body["properties"]) == set(ErrorBodySerializer().fields)


@pytest.mark.django_db
def test_schema_route_serves_the_contract(client):
    response = client.get("/api/v1/schema")
    assert response.status_code == 200


def test_image_routes_declare_409_and_503_with_the_envelope_and_retry_after(tmp_path):
    schema = yaml.safe_load(generate(tmp_path))
    upload = schema["paths"]["/api/v1/listings/{listing_id}/images"]["post"]["responses"]
    for status in ("409", "503"):
        assert upload[status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorEnvelope"
        }
    assert "Retry-After" in upload["503"]["headers"] and "Retry-After" in upload["429"]["headers"]
    assert upload["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Image"
    }
    request = schema["paths"]["/api/v1/listings/{listing_id}/images"]["post"]["requestBody"]
    assert "multipart/form-data" in request["content"]
    assert "delete" in schema["paths"]["/api/v1/listings/{listing_id}/images/{image_id}"]


ENVELOPE = {"$ref": "#/components/schemas/ErrorEnvelope"}


def test_the_reorder_route_declares_200_409_403_404_and_a_strict_body(tmp_path):
    schema = yaml.safe_load(generate(tmp_path))
    patch = schema["paths"]["/api/v1/listings/{listing_id}/images"]["patch"]
    responses = patch["responses"]
    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Listing"
    }
    assert responses["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ListingConflict"
    }
    for status in ("400", "401", "403", "404"):
        assert responses[status]["content"]["application/json"]["schema"] == ENVELOPE
    body = schema["components"]["schemas"]["ImageOrderRequest"]
    assert set(body["properties"]) == {"base_revision", "order", "cover_id"}
    assert set(body["required"]) == {"base_revision", "order"}
    assert body["properties"]["order"]["items"]["format"] == "uuid"
    assert patch["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ImageOrderRequest"
    }


def test_the_media_route_documents_the_binary_responses_and_the_404(tmp_path):
    schema = yaml.safe_load(generate(tmp_path))
    operation = schema["paths"]["/api/v1/listings/{listing_id}/images/{image_id}/file"]["get"]
    ok = operation["responses"]["200"]["content"]
    assert set(ok) == {"image/jpeg", "image/png", "image/webp"}
    for content in ok.values():
        assert content["schema"] == {"type": "string", "format": "binary"}
    assert operation["responses"]["404"]["content"]["application/json"]["schema"] == ENVELOPE
    assert "Cache-Control: private, no-store" in operation["description"]


def test_the_listing_media_items_have_the_documented_shape(tmp_path):
    schema = yaml.safe_load(generate(tmp_path))["components"]["schemas"]
    image = schema["Image"]
    assert set(image["properties"]) == {
        "id",
        "position",
        "is_cover",
        "content_type",
        "width",
        "height",
        "bytes",
        "url",
    }
    assert schema["Listing"]["properties"]["media"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/Image"},
        "description": "The images in `position` order; empty when there are none.",
    }
    flat = " ".join(image["properties"])
    assert "asset" not in flat and "storage" not in flat and "owner" not in flat
