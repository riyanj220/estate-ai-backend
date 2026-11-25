"""AD-7: `listings.publication.publish` is the only code that sets a Listing to published and the
only writer of `PublicationEvent`. Inside `apps/listings` every write of `status` must name the
Draft status (creating a Draft); anywhere in `apps`, a write of `status` that mentions the
published status, by name or as text, is refused. (`PublicationEvent` writes: see
`tests/listings/test_quota.py`.) Story 2.17: `listings/status.py` may also write exactly
archived, sold and rented (a literal, or a lookup in its `_WRITABLE` table, whose values are
pinned below), never published or removed; `removed` is written by nobody in `apps`."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
WRITER = "apps/listings/publication.py"
DEFINES = {"apps/listings/lifecycle.py"}  # the enum itself
STATUS_WRITER = "apps/listings/status.py"
STATUS_WRITES = {"archived", "sold", "rented"}  # all that `status.py` may ever write
STATUS_TABLE = "_WRITABLE"
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


def _is_allowed_move(node: ast.AST) -> bool:
    """For `status.py` only: `ListingStatus.ARCHIVED|SOLD|RENTED` (`.value` optional), the text
    of one of them, or a lookup in the module's `_WRITABLE` table."""
    if isinstance(node, ast.Constant):
        return node.value in STATUS_WRITES
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Name) and node.value.id == STATUS_TABLE
    if isinstance(node, ast.Attribute):
        if node.attr == "value":
            return _is_allowed_move(node.value)
        return node.attr in {name.upper() for name in STATUS_WRITES}
    return False


def _table_values(tree: ast.AST) -> list[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == STATUS_TABLE for t in node.targets
        ):
            return list(node.value.values) if isinstance(node.value, ast.Dict) else [node.value]
    return []


def _table_mutations(tree: ast.AST):
    """Lines that change `_WRITABLE` after its one definition: a subscript assignment, an
    augmented assignment or a mutating method call."""
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                if target.value.id == STATUS_TABLE:
                    yield node.lineno
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == STATUS_TABLE
        ):
            yield node.lineno
        if isinstance(node, ast.AugAssign) and getattr(node.target, "id", "") == STATUS_TABLE:
            yield node.lineno


REMOVED_OWNERS = {"apps/listings/lifecycle.py", "apps/listings/policy.py"}  # the enum, the view set


def removed_mentions(tree: ast.AST):
    """Lines naming the `removed` status (`REMOVED`, or the text) outside docstrings: nothing but
    the enum and the owner-visible set may, so an intermediate variable cannot carry it into a
    status write."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute):
            if (node.id if isinstance(node, ast.Name) else node.attr) == "REMOVED":
                yield node.lineno
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and node.value == "removed":
                yield node.lineno


def removed_violations(root: Path, files) -> list[str]:
    found = []
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        if "/migrations/" in relative or relative in REMOVED_OWNERS:
            continue
        found.extend(f"{relative}:{line}" for line in removed_mentions(ast.parse(path.read_text())))
    return found


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
        if relative == STATUS_WRITER:
            found.extend(f"{relative}:{line}" for line in _indirect_writes(tree, False))
            found.extend(f"{relative}:{line}" for line in _table_mutations(tree))
            found.extend(
                f"{relative}:{line}"
                for line, value in _status_writes(tree)
                if not _is_allowed_move(value)
            )
            found.extend(
                f"{relative}:{value.lineno}"
                for value in _table_values(tree)
                if not _is_allowed_move(value)
            )
            continue
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


STATUS_OK = (
    "row.status = ListingStatus.ARCHIVED.value\n"
    "row.status = 'sold'\n"
    "row.status = _WRITABLE[target]\n"
    "x = {'status': ListingStatus.RENTED}\n"
    "_WRITABLE = {'archived': ListingStatus.ARCHIVED.value, 'sold': 'sold'}\n"
)


def test_the_status_module_may_write_exactly_archived_sold_and_rented(tmp_path):
    listings = tmp_path / "apps" / "listings"
    listings.mkdir(parents=True)
    (listings / "status.py").write_text(STATUS_OK)
    assert violations(tmp_path, tmp_path.rglob("*.py")) == []


@pytest.mark.parametrize(
    "code",
    [
        "row.status = ListingStatus.PUBLISHED.value",
        "row.status = 'published'",
        "row.status = ListingStatus.REMOVED.value",
        "row.status = 'removed'",
        "row.status = ListingStatus.DRAFT.value",
        "row.status = target",
        "row.status = other_table[target]",
        "PropertyListing.objects.filter(id=1).update(status=ListingStatus.REMOVED)",
        "PropertyListing.objects.filter(id=1).update(**changes)",
        "_WRITABLE = {'published': ListingStatus.PUBLISHED.value}",
        "_WRITABLE = {'removed': 'removed'}",
        "cursor.execute('UPDATE listings_propertylisting SET status = %s', [s])",
    ],
)
def test_the_status_module_guard_can_fail(tmp_path, code):
    listings = tmp_path / "apps" / "listings"
    listings.mkdir(parents=True)
    (listings / "status.py").write_text(code + "\n")
    assert violations(tmp_path, tmp_path.rglob("*.py")) == ["apps/listings/status.py:1"]


def test_the_real_status_module_writes_only_its_three_statuses():
    from apps.listings import status

    tree = ast.parse((ROOT / STATUS_WRITER).read_text())
    assert {ast.unparse(v) for v in _table_values(tree)} == {
        "ListingStatus.ARCHIVED.value",
        "ListingStatus.SOLD.value",
        "ListingStatus.RENTED.value",
    }
    assert status._WRITABLE == {"archived": "archived", "sold": "sold", "rented": "rented"}
    assert set(status.TARGETS) == STATUS_WRITES


def test_removed_is_named_by_nobody_but_the_enum_and_the_owner_visible_set():
    assert removed_violations(ROOT, (ROOT / "apps").rglob("*.py")) == []


@pytest.mark.parametrize(
    "code",
    [
        "target = 'removed'\nrow.status = target\n",
        "target = ListingStatus.REMOVED\nrow.status = target.value\n",
        "row.status = S.REMOVED\n",
    ],
)
@pytest.mark.parametrize("module", ["apps/other/job.py", "apps/listings/other.py"])
def test_the_removed_guard_can_fail(tmp_path, module, code):
    path = tmp_path / module
    path.parent.mkdir(parents=True)
    path.write_text(code)
    assert removed_violations(tmp_path, [path])  # an intermediate variable does not hide it


@pytest.mark.parametrize("name", ["services.py", "other.py", "status_helpers.py"])
@pytest.mark.parametrize("target", ["ARCHIVED", "SOLD", "RENTED"])
def test_archived_sold_and_rented_are_written_by_the_status_module_only(tmp_path, name, target):
    listings = tmp_path / "apps" / "listings"
    listings.mkdir(parents=True)
    (listings / name).write_text(f"row.status = ListingStatus.{target}.value\n")
    assert violations(tmp_path, tmp_path.rglob("*.py")) == [f"apps/listings/{name}:1"]


@pytest.mark.parametrize(
    "code",
    [
        "_WRITABLE['x'] = 'archived'",
        "_WRITABLE['removed'] = ListingStatus.REMOVED.value",
        "_WRITABLE.update({'x': 'sold'})",
        "_WRITABLE.setdefault('x', 'sold')",
        "_WRITABLE |= {'x': 'sold'}",
    ],
)
def test_the_status_table_cannot_be_widened_after_its_definition(tmp_path, code):
    listings = tmp_path / "apps" / "listings"
    listings.mkdir(parents=True)
    (listings / "status.py").write_text(
        "_WRITABLE = {'archived': ListingStatus.ARCHIVED.value}\n" + code + "\n"
    )
    assert violations(tmp_path, tmp_path.rglob("*.py")) == ["apps/listings/status.py:2"]


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
