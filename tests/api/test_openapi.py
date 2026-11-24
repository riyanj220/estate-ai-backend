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
