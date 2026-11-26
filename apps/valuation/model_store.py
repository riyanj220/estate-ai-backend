"""The model artifact files and their hash-checked loading (AD-12).

An artifact is a pickle, so it is only unpickled after its bytes match the SHA-256 recorded at
registration. The same bytes are hashed and loaded; the file is never read twice."""

import hashlib
import json
import math
import pickle
from functools import lru_cache
from pathlib import Path

from apps.valuation import features

MODEL_NAME = "karachi-sale-price"
ARTIFACTS = Path(__file__).resolve().parent / "data" / "artifacts"


class ArtifactError(Exception):
    """The artifact is missing, altered or built for other code. Never loaded."""


def artifact_path(version: int, directory: Path = ARTIFACTS) -> Path:
    return directory / f"{MODEL_NAME}-{int(version)}.pkl"


def manifest_path(version: int, directory: Path = ARTIFACTS) -> Path:
    return directory / f"{MODEL_NAME}-{int(version)}.json"


def versions(directory: Path = ARTIFACTS) -> list[int]:
    found = directory.glob(f"{MODEL_NAME}-*.json")
    return sorted(int(path.stem.rsplit("-", 1)[1]) for path in found)


def read_manifest(version: int, directory: Path = ARTIFACTS) -> dict:
    try:
        return json.loads(manifest_path(version, directory).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ArtifactError(f"No manifest for model version {version}.") from None


def read_checked(version: int, sha256: str, directory: Path = ARTIFACTS) -> bytes:
    """The artifact's bytes, only if they hash to `sha256`."""
    try:
        blob = artifact_path(version, directory).read_bytes()
    except FileNotFoundError:
        raise ArtifactError(f"No artifact for model version {version}.") from None
    if hashlib.sha256(blob).hexdigest() != sha256:
        raise ArtifactError(f"The artifact of model version {version} failed its hash check.")
    return blob


class LoadedModel:
    def __init__(self, estimator, localities: list[str]):
        self._estimator = estimator
        self.localities = localities

    def estimate(self, built: dict) -> float:
        """The estimated total price in PKR for `features.build_features` output."""
        import numpy as np
        from threadpoolctl import threadpool_limits

        row = np.array([features.encode(built, self.localities)])
        with threadpool_limits(limits=1):
            per_sqft = math.exp(float(self._estimator.predict(row)[0]))
        return per_sqft * math.exp(built["log_area_sqft"])


@lru_cache(maxsize=2)
def _unpickle(blob: bytes) -> LoadedModel:
    bundle = pickle.loads(blob)  # noqa: S301 - the caller has verified the registered hash
    if tuple(bundle["features"]) != features.FEATURE_NAMES:
        raise ArtifactError("The artifact was built for another feature list.")
    return LoadedModel(bundle["estimator"], bundle["localities"])


def load(version: int, sha256: str, directory: Path = ARTIFACTS) -> LoadedModel:
    """Hash check on every call; the unpickled model is cached by its exact bytes."""
    import sklearn

    blob = read_checked(version, sha256, directory)
    manifest = read_manifest(version, directory)
    if manifest["sklearn_version"] != sklearn.__version__:
        raise ArtifactError("The artifact was trained with another scikit-learn; retrain.")
    return _unpickle(blob)
