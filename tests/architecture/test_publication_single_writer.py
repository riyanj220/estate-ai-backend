"""AD-7: `listings.publication.publish` is the only code that sets a Listing to published and the
only writer of `PublicationEvent`. Inside `apps/listings` every write of `status` must name the
Draft status (creating a Draft); anywhere in `apps`, a write of `status` that mentions the
published status, by name or as text, is refused. (`PublicationEvent` writes: see
`tests/listings/test_quota.py`.)"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
WRITER = "apps/listings/publication.py"
DEFINES = {"apps/listings/lifecycle.py"}  # the enum itself
# `apply_updates` sets column values by registry field id; a test below pins that no registry
# field (editable or derived) is named `status`.
COMPUTED_SETATTR_OK = {"apps/listings/services.py"}
WRITES = {"create", "update", "bulk_create", "get_or_create", "update_or_create", "bulk_update"}


def _mentions_published(node: ast.AST) -> bool:
    for part in ast.walk(node):
        if isinstance(part, ast.Name | ast.Attribute):
            name = part.id if isinstance(part, ast.Name) else part.attr
            if name == "PUBLISHED":
                return True
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            if part.value.lower() == "published":
                return True
    return False


def _is_draft(node: ast.AST) -> bool:
    """`ListingStatus.DRAFT`, `ListingStatus.DRAFT.value` or the text `"draft"`."""
    if isinstance(node, ast.Constant):
        return node.value == "draft"
    if isinstance(node, ast.Attribute):
        return node.attr == "DRAFT" or (node.attr == "value" and _is_draft(node.value))
    return False


def _status_writes(tree: ast.AST):
    """(line, value node) of each write of a `status` attribute, keyword, dict key or setattr."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "status" and node.value:
                    yield node.lineno, node.value
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in WRITES or name == "PropertyListing":
                for keyword in node.keywords:
                    if keyword.arg == "status":
                        yield keyword.value.lineno, keyword.value
            if name == "setattr" and len(node.args) == 3:
                if getattr(node.args[1], "value", None) == "status":
                    yield node.lineno, node.args[2]
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "status" and value is not None:
                    yield value.lineno, value


RAW_SQL = re.compile(r"(?is)\b(update|insert)\b[^;]*listings_propertylisting")


def _indirect_writes(tree: ast.AST, computed_setattr: bool = True):
    """Lines where a Listing status could be written without naming `status`: `**kwargs` into a
    write call, `setattr` with a computed attribute name, `bulk_update` of a `status` column, and
    raw SQL that updates the Listing table."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if (name in WRITES or name == "PropertyListing") and any(
                keyword.arg is None for keyword in node.keywords
            ):
                yield node.lineno
            computed = len(node.args) == 3 and not isinstance(node.args[1], ast.Constant)
            if name == "setattr" and computed and computed_setattr:
                yield node.lineno
            if name == "bulk_update" and any(
                isinstance(part, ast.Constant) and part.value == "status" for part in ast.walk(node)
            ):
                yield node.lineno
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if RAW_SQL.search(node.value):
                yield node.lineno


def violations(root: Path, files) -> list[str]:
    found = []
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        if "/migrations/" in relative or relative in {WRITER, *DEFINES}:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        in_listings = relative.startswith("apps/listings/")
        if in_listings or "listings_propertylisting" in path.read_text():
            found.extend(
                f"{relative}:{line}"
                for line in _indirect_writes(tree, relative not in COMPUTED_SETATTR_OK)
            )
        for line, value in _status_writes(tree):
            if _mentions_published(value) or (in_listings and not _is_draft(value)):
                found.append(f"{relative}:{line}")
    return found


def test_only_the_publish_service_sets_the_published_status():
    found = violations(ROOT, (ROOT / "apps").rglob("*.py"))
    assert found == [], found


@pytest.mark.parametrize(
    "code",
    [
        "row.status = ListingStatus.PUBLISHED.value",
        "row.status = 'published'",
        "PropertyListing.objects.filter(id=1).update(status=ListingStatus.PUBLISHED)",
        "PropertyListing.objects.create(status='Published')",
        "setattr(row, 'status', S.PUBLISHED)",
        "data = {'status': 'published'}",
        "row.status = compute()",  # inside apps/listings: anything but the Draft is refused
        "row.status += 'x'",
        "PropertyListing.objects.filter(id=1).update(**changes)",
        "PropertyListing.objects.create(**values)",
        "setattr(row, name, value)",
        "PropertyListing.objects.bulk_update(rows, ['status'])",
        "cursor.execute('UPDATE listings_propertylisting SET status = %s', [s])",
        "cursor.execute('INSERT INTO listings_propertylisting (id) VALUES (1)')",
    ],
)
def test_the_guard_can_fail(tmp_path, code):
    bad = tmp_path / "apps" / "listings"
    bad.mkdir(parents=True)
    (bad / "other.py").write_text(code + "\n")
    assert violations(tmp_path, tmp_path.rglob("*.py")) == ["apps/listings/other.py:1"]


def test_creating_a_draft_and_other_apps_are_fine(tmp_path):
    listings = tmp_path / "apps" / "listings"
    listings.mkdir(parents=True)
    (listings / "ok.py").write_text(
        "create(status=ListingStatus.DRAFT.value)\nx.status = 'draft'\n"
    )
    other = tmp_path / "apps" / "mail"
    other.mkdir()
    (other / "ok.py").write_text("job.status = compute()\njob.status = 'sent'\n")
    assert violations(tmp_path, tmp_path.rglob("*.py")) == []


def test_no_registry_field_can_be_the_status_column():
    from apps.catalog import registry
    from apps.listings.fields import DERIVED, EDITABLE_FIELDS

    assert "status" not in {f.id for f in registry.fields()}
    assert "status" not in {*EDITABLE_FIELDS, *DERIVED}
