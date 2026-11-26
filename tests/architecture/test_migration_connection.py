"""A data migration runs on the migrating connection (`migrate --database=migrator`), not on the
default one: a table created in the same migration is invisible elsewhere, and the runtime role may
not update the append-only tables. Tests use one connection, so only this guard catches it."""

from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "apps"
SCHEMA = ("migrations.CreateModel(", "migrations.AddField(")
LEDGER = APPS / "listings" / "migrations"  # may touch append-only tables


def needs_alias(path: Path, source: str) -> bool:
    if "RunPython(" not in source:
        return False
    return path.parent == LEDGER or any(marker in source for marker in SCHEMA)


@pytest.mark.guard
def test_data_migrations_that_need_it_use_the_migrating_connection():
    offenders = []
    for path in sorted(APPS.glob("*/migrations/0*.py")):
        source = path.read_text()
        if needs_alias(path, source) and "schema_editor.connection.alias" not in source:
            offenders.append(str(path.relative_to(APPS)))
    assert offenders == []


@pytest.mark.guard
def test_the_guard_can_fail():
    source = "migrations.CreateModel(\nmigrations.RunPython(seed)\nModel.objects.create()"
    assert needs_alias(APPS / "catalog" / "migrations" / "0001_x.py", source)
    assert "schema_editor.connection.alias" not in source
