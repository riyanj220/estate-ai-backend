import pytest


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    """Every test stores media in its own empty directory, never the shared test root."""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    return tmp_path / "media"
