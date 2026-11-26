"""Story 4.2: a registered model, loaded only after its hash check."""

import hashlib
import io
import json
import pickle
import shutil

import pytest
from django.core.management import CommandError, call_command

from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.valuation import features, model_store, selectors, services
from apps.valuation.models import ValuationModelVersion

pytestmark = pytest.mark.django_db

SYSTEM = Actor.system("test")

HOUSE = {"subtype": "house", "locality": "Clifton", "area_value": "240", "area_unit": "sqyd"}


@pytest.fixture
def copy(tmp_path):
    return shutil.copytree(model_store.ARTIFACTS, tmp_path / "artifacts")


def test_the_command_registers_the_committed_model_once():
    version = model_store.versions()[-1]
    out = io.StringIO()
    call_command("register_valuation_model", stdout=out)
    call_command("register_valuation_model", stdout=out)
    assert out.getvalue().splitlines()[1].endswith("already registered.")
    row = ValuationModelVersion.objects.get()
    manifest = model_store.read_manifest(version)
    assert (row.name, row.version) == (model_store.MODEL_NAME, version)
    assert row.feature_schema_version == features.FEATURE_SCHEMA_VERSION
    assert row.metrics == manifest["metrics"] and row.trained_at.year >= 2026
    assert row.artifact_sha256 == manifest["artifact_sha256"]
    assert selectors.current_model().id == row.id


def test_the_registered_model_loads_and_estimates():
    services.register_model(SYSTEM)
    model = selectors.load_model(selectors.current_model())
    built = features.build_features(HOUSE)
    price = model.estimate(built)
    assert price == model.estimate(built)
    cheaper = model.estimate(features.build_features({**HOUSE, "locality": "Orangi Town"}))
    assert 0 < cheaper < price


def test_an_altered_artifact_is_never_unpickled(copy, monkeypatch):
    services.register_model(SYSTEM, directory=copy)
    ref = selectors.current_model()
    path = model_store.artifact_path(ref.version, copy)
    path.write_bytes(path.read_bytes() + b"x")
    monkeypatch.setattr(pickle, "loads", lambda *a, **k: pytest.fail("unpickled"))
    with pytest.raises(model_store.ArtifactError, match="hash check"):
        selectors.load_model(ref, copy)
    with pytest.raises(services.RegistrationRefused, match="hash check"):
        services.register_model(SYSTEM, directory=copy)


def test_a_version_cannot_change_its_artifact_even_with_a_matching_manifest(copy):
    services.register_model(SYSTEM, directory=copy)
    ref = selectors.current_model()
    path = model_store.artifact_path(ref.version, copy)
    path.write_bytes(path.read_bytes() + b"x")
    manifest = model_store.read_manifest(ref.version, copy)
    manifest["artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    model_store.manifest_path(ref.version, copy).write_text(json.dumps(manifest))
    with pytest.raises(services.RegistrationRefused, match="another artifact"):
        services.register_model(SYSTEM, directory=copy)
    # the registered hash, not the manifest's, decides what loads
    with pytest.raises(model_store.ArtifactError, match="hash check"):
        selectors.load_model(ref, copy)


def test_registration_refuses_nothing_to_register_and_another_feature_schema(copy, tmp_path):
    with pytest.raises(services.RegistrationRefused, match="no trained model"):
        services.register_model(SYSTEM, directory=tmp_path / "empty")
    version = model_store.versions(copy)[-1]
    manifest = model_store.read_manifest(version, copy)
    manifest["feature_schema_version"] += 1
    model_store.manifest_path(version, copy).write_text(json.dumps(manifest))
    with pytest.raises(services.RegistrationRefused, match="feature schema"):
        services.register_model(SYSTEM, directory=copy)
    assert not ValuationModelVersion.objects.exists()


def test_only_the_system_actor_registers():
    with pytest.raises(DomainError):
        services.register_model(Actor.visitor())
    assert not ValuationModelVersion.objects.exists()


def test_training_never_overwrites_a_version():
    with pytest.raises(CommandError, match="exists already"):
        call_command("train_valuation_model", model_version=model_store.versions()[-1])
