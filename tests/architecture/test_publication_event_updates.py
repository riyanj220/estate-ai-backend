"""Story 2.19: a publication event is updated by `listings/publication.py` only, in exactly the
columns the runtime role may update (`settings.RUNTIME_COLUMN_UPDATES`); the one other writer is
the migration helper `identity_migration` (fingerprint only, imported by migrations only). The
Listing write core `services.write_locked`, which can unlock identity fields, is called by
`services.apply_updates` and `publication.correct` and nowhere else. (Who may INSERT or DELETE
events: `tests/listings/test_quota.py`.)"""

import ast
import re
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = {"correction_used", "identity_fingerprint"}
SERVICE = "apps/listings/publication.py"
CORE = "write_locked"
HELPER = "apps/listings/identity_migration.py"
UPDATES = {"update", "update_or_create", "bulk_update"}
RAW_SQL = re.compile(r"(?is)\bupdate\b[^;]*listings_publicationevent")


def _name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _trees(root: Path):
    for path in sorted((root / "apps").rglob("*.py")):
        if "migrations" not in path.parts:
            yield path.relative_to(root).as_posix(), ast.parse(path.read_text())


def column_violations(root: Path) -> list[str]:
    """Any way to change one of the two columns outside the allowed modules."""
    found = []
    for relative, tree in _trees(root):
        allowed = {SERVICE: COLUMNS, HELPER: {"identity_fingerprint"}}.get(relative, set())
        for node in ast.walk(tree):
            bad = False
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
                bad = node.attr in COLUMNS  # never by assigning to an instance, anywhere
            elif isinstance(node, ast.Call) and _name(node) in UPDATES:
                bad = any(k.arg in COLUMNS - allowed for k in node.keywords)
                if _name(node) == "bulk_update":
                    bad = any(
                        isinstance(p, ast.Constant) and p.value in COLUMNS for p in ast.walk(node)
                    )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                bad = bool(RAW_SQL.search(node.value))
            if bad:
                found.append(f"{relative}:{node.lineno}")
    return found


def service_update_violations(tree: ast.AST) -> list[int]:
    """In the publication module: every `update` names exactly allowed columns, nothing computed,
    no other update form, and only the Listing `row` is ever saved."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = node.func.attr
        if name == "update":
            keywords = {k.arg for k in node.keywords}
            if node.args or not keywords or not keywords <= COLUMNS:
                found.append(node.lineno)
        elif name in ("update_or_create", "bulk_update"):
            found.append(node.lineno)
        elif name == "save" and getattr(node.func.value, "id", "") != "row":
            found.append(node.lineno)
    return found


def _allowed_core_uses(relative: str, tree: ast.Module) -> set[int]:
    """The ids of the nodes that may name `write_locked` in this module: the called name inside
    `publication.correct` (and publication's plain import of it), and inside
    `services.apply_updates` when that call cannot unlock anything."""
    allowed: set[int] = set()
    if relative == SERVICE:
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                allowed.update(id(a) for a in node.names if a.name == CORE and a.asname is None)
    place = {SERVICE: "correct", "apps/listings/services.py": "apply_updates"}.get(relative)
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name != place:
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and _name(node) == CORE:
                if place == "correct" or not any(
                    k.arg in ("unlocked", None) for k in node.keywords
                ):
                    allowed.add(id(node.func))
    return allowed


def _names_the_core(node: ast.AST) -> bool:
    """A use of the write core in any form: the name or attribute (called or only referred to, so
    a later alias cannot hide), an import of it (aliased or not) or its name as text (`getattr`)."""
    if isinstance(node, ast.Name):
        return node.id == CORE
    if isinstance(node, ast.Attribute):
        return node.attr == CORE
    if isinstance(node, ast.alias):
        return node.name == CORE
    return isinstance(node, ast.Constant) and node.value == CORE


def core_violations(root: Path) -> list[str]:
    """Uses of `write_locked` (anywhere in a module: top level, `def`, `async def`, class) and
    users of `identity_migration` outside their one place."""
    found = []
    for relative, tree in _trees(root):
        allowed = _allowed_core_uses(relative, tree)
        for node in ast.walk(tree):
            if _names_the_core(node) and id(node) not in allowed:
                found.append(f"{relative}:{node.lineno}")
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(a.name for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("identity_migration" in n or "recompute_fingerprints" in n for n in names):
                found.append(f"{relative}:{node.lineno}")
    return found


def test_the_guarded_columns_are_the_granted_columns():
    assert set(settings.RUNTIME_COLUMN_UPDATES["listings_publicationevent"]) == COLUMNS
    assert "listings_publicationevent" in settings.RUNTIME_APPEND_ONLY_TABLES


def test_only_the_publication_module_updates_events():
    assert column_violations(ROOT) == []
    assert service_update_violations(ast.parse((ROOT / SERVICE).read_text())) == []
    assert core_violations(ROOT) == []


def test_the_publication_module_does_update_both_columns():
    tree = ast.parse((ROOT / SERVICE).read_text())
    updated = [
        {k.arg for k in n.keywords}
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _name(n) == "update"
    ]
    assert updated == [COLUMNS]


@pytest.mark.parametrize(
    "code",
    [
        "event.correction_used = False",
        "event.identity_fingerprint = value",
        "PublicationEvent.objects.update(correction_used=False)",
        "rows.update(identity_fingerprint=None)",
        "PublicationEvent.objects.bulk_update(rows, ['correction_used'])",
        "cursor.execute('UPDATE listings_publicationevent SET counted = true')",
    ],
)
def test_the_column_guard_can_fail(tmp_path, code):
    bad = tmp_path / "apps" / "listings"
    bad.mkdir(parents=True)
    (bad / "other.py").write_text(code + "\n")
    assert column_violations(tmp_path) == ["apps/listings/other.py:1"]


@pytest.mark.parametrize(
    "code",
    [
        "PublicationEvent.objects.filter(a=1).update(counted=False)",
        "PublicationEvent.objects.update(correction_used=True, created_at=now)",
        "PublicationEvent.objects.update(**changes)",
        "PublicationEvent.objects.update_or_create(id=1)",
        "event.save()",
    ],
)
def test_the_service_guard_can_fail(code):
    assert service_update_violations(ast.parse(code)) == [1]


@pytest.mark.parametrize(
    ("module", "code"),
    [
        ("apps/listings/other.py", "def f(a, r):\n    write_locked(a, r, {}, o)\n"),
        (
            "apps/listings/services.py",
            "def apply_updates(a, r):\n    write_locked(a, r, {}, o, unlocked={'city'})\n",
        ),
        ("apps/listings/publication.py", "def publish(a, r):\n    write_locked(a, r, {}, o)\n"),
        ("apps/listings/other.py", "\nfrom apps.listings import identity_migration\n"),
        ("apps/listings/other.py", "\nwrite_locked(a, r, {}, o)\n"),  # module level
        ("apps/listings/other.py", "async def f(a, r):\n    write_locked(a, r, {}, o)\n"),
        ("apps/listings/other.py", "class C:\n    x = services.write_locked(a, r, {}, o)\n"),
        ("apps/listings/other.py", "\nfrom apps.listings.services import write_locked as w\n"),
        ("apps/listings/other.py", "\nw = services.write_locked\n"),
        ("apps/listings/other.py", "\nw = getattr(services, 'write_locked')\n"),
        (
            "apps/listings/publication.py",
            "\nfrom apps.listings.services import write_locked as w\n",
        ),
        ("apps/listings/publication.py", "def correct(a, r):\n    w = write_locked\n"),
        ("apps/listings/publication.py", "async def correct(a, r):\n    write_locked(a, r)\n"),
        ("apps/listings/services.py", "def apply_updates(a, r):\n    write_locked(a, r, **kw)\n"),
    ],
)
def test_the_core_guard_can_fail(tmp_path, module, code):
    path = tmp_path / module
    path.parent.mkdir(parents=True)
    path.write_text(code)
    assert core_violations(tmp_path) == [f"{module}:2"]


def test_the_core_guard_accepts_the_two_real_forms(tmp_path):
    listings = tmp_path / "apps" / "listings"
    listings.mkdir(parents=True)
    (listings / "publication.py").write_text(
        "from apps.listings.services import write_locked\n"
        "def correct(a, r):\n    write_locked(a, r, {}, o, unlocked=x)\n"
    )
    (listings / "services.py").write_text(
        "def write_locked(actor, row): ...\ndef apply_updates(a, r):\n    write_locked(a, r)\n"
    )
    assert core_violations(tmp_path) == []
