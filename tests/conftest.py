import pytest


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    """Every test stores media in its own empty directory, never the shared test root."""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    return tmp_path / "media"


@pytest.fixture(autouse=True)
def _reseed_flushed_policies(request):
    """A `transaction=True` test flushes every table at its end, rate-limit policies included, and
    a later ordinary test would then find no seeded policy (a 500 on sign-in). Before an ordinary
    database test, put the migration-seeded policy rows back if the table is empty (for an ordinary
    test inside its own transaction, so it is rolled back with it; a transactional test starts
    from the flushed tables of the one before it, so it gets them committed)."""
    marker = request.node.get_closest_marker("django_db")
    names = request.fixturenames
    transactional = "transactional_db" in names or bool(marker and marker.kwargs.get("transaction"))
    if marker is not None or "db" in names or "transactional_db" in names:
        request.getfixturevalue("transactional_db" if transactional else "db")
        from django.apps import apps
        from django.db import connection

        from apps.kernel.models import RateLimitPolicy

        if not RateLimitPolicy.objects.exists():
            import importlib
            import pkgutil

            import apps.kernel.migrations as package

            class _Schema:
                pass

            schema = _Schema()
            schema.connection = connection
            for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
                module = importlib.import_module(f"{package.__name__}.{info.name}")
                if info.name.split("_")[1:2] == ["seed"] and hasattr(module, "seed"):
                    module.seed(apps, schema)
    yield
