"""AD-5: visibility has two implementations, `listings.policy` and `listings.selectors`
(`public_listings`). No other module may filter on the published status or the `restricted` flag,
or reimplement the predicate, and nothing outside `apps/listings` may reach the Listing or image
tables at all (later stories start from `public_listings()` / `can_view` via the selectors)."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {
    "apps/listings/policy.py",
    "apps/listings/selectors.py",
    "apps/listings/lifecycle.py",  # defines the status and its owner-facing label
    "apps/listings/publication.py",  # the one writer of the published status (AD-7)
    "apps/listings/models.py",  # defines the `restricted` column
}
# The owner resource serializer may NAME the `restricted` column to expose it to the owner
# (Story 2.17); it may never filter on it or name the published status.
OWNER_FLAG_FILES = {"apps/presentation/api/listings.py"}
GUARDED_NAMES = {"PUBLISHED", "restricted"}
GUARDED_MODELS = {"PropertyListing", "ListingImage"}
# a string that spells the predicate: a quoted status, `status = published`, `restricted =`/`is`
SQLISH = re.compile(
    r"""(?ix)
    ['"]published['"]
    | \bstatus\s*(=|in|!=|<>)\s*\(?\s*['"]?published
    | \brestricted\s*(=|is\b|!=)
    """
)


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def mentions_the_predicate(tree: ast.Module, owner_flag_ok: bool = False) -> list[int]:
    """Lines naming the published status or the `restricted` flag: as a name, attribute, keyword
    or lookup, `ListingStatus["PUBLISHED"]`, `getattr(x, "PUBLISHED")`, or a string (an f-string
    part or raw SQL included) that spells the predicate."""
    docstrings = _docstring_ids(tree)
    lines = []
    for node in ast.walk(tree):
        hit = (
            (isinstance(node, ast.Attribute) and node.attr in GUARDED_NAMES)
            or (isinstance(node, ast.Name) and node.id in GUARDED_NAMES)
            or (isinstance(node, ast.keyword) and (node.arg or "").split("__")[0] == "restricted")
            or (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and (
                    node.value in {"published", "restricted", "PUBLISHED"}
                    or SQLISH.search(node.value)
                )
            )
        )
        if hit and owner_flag_ok and _names_only_the_flag(node):
            continue
        if hit:
            lines.append(node.lineno)
    return lines


def _names_only_the_flag(node: ast.AST) -> bool:
    """A bare mention of the `restricted` name, attribute or text (no lookup such as
    `restricted__...`, no comparison logic: the file is only allowed to hand the value over)."""
    if isinstance(node, ast.Name):
        return node.id == "restricted"
    if isinstance(node, ast.Attribute):
        return node.attr == "restricted"
    if isinstance(node, ast.Constant):
        return node.value == "restricted"
    return False


def reaches_the_listing_tables(tree: ast.Module) -> list[int]:
    """Lines outside `apps/listings` that import or name the Listing or image models."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "apps.listings.models"
        ):
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in GUARDED_MODELS:
            lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in GUARDED_MODELS:
            lines.append(node.lineno)
    return lines


def violations(root: Path, files, allowed=ALLOWED) -> list[str]:
    found = []
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        if "/migrations/" in relative:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        if relative not in allowed:
            found.extend(
                f"{relative}:{line} predicate"
                for line in mentions_the_predicate(tree, relative in OWNER_FLAG_FILES)
            )
        if not relative.startswith("apps/listings/"):
            found.extend(f"{relative}:{line} tables" for line in reaches_the_listing_tables(tree))
    return found


def test_only_the_policy_and_selectors_decide_visibility():
    found = violations(ROOT, (ROOT / "apps").rglob("*.py"))
    assert found == [], found


def test_the_guard_can_fail(tmp_path):
    bad = tmp_path / "apps" / "search"
    bad.mkdir(parents=True)
    cases = {
        "enum_member.py": "def f(qs, S):\n    return qs.filter(status=S.PUBLISHED.value)\n",
        "string.py": "def f(qs):\n    return qs.filter(status='published')\n",
        "subscript.py": "def f(S):\n    return S['PUBLISHED']\n",
        "getattr.py": "def f(S):\n    return getattr(S, 'PUBLISHED')\n",
        "fstring.py": "def f(n):\n    return f\"WHERE status = 'published' AND {n}\"\n",
        "raw_sql.py": "SQL = 'SELECT 1 FROM t WHERE status = published AND restricted = false'\n",
        "keyword.py": "def f(qs):\n    return qs.filter(restricted=False)\n",
        "model_import.py": "from apps.listings.models import PropertyListing\n",
        "model_name.py": "def f(m):\n    return m.PropertyListing.objects.all()\n",
        "image_model.py": "def f():\n    return ListingImage.objects.all()\n",
    }
    for name, code in cases.items():
        (bad / name).write_text(code)
    (bad / "clean.py").write_text('"""Mentions published and restricted only in prose."""\n')
    found = violations(tmp_path, tmp_path.rglob("*.py"))
    flagged = {f.split(":")[0].split("/")[-1] for f in found}
    assert flagged == set(cases), (flagged, found)


def test_the_owner_serializer_may_hand_over_the_flag_but_never_decide_with_it(tmp_path):
    api = tmp_path / "apps" / "presentation" / "api"
    api.mkdir(parents=True)
    target = api / "listings.py"
    target.write_text(
        "class S:\n    restricted = field()\n\ndef d(v):\n    return {'restricted': v.restricted}\n"
    )
    assert violations(tmp_path, [target]) == []
    for code in (
        "def f(qs):\n    return qs.filter(restricted=False)\n",
        "def f(S):\n    return S.PUBLISHED\n",
        "def f():\n    return 'published'\n",
    ):
        target.write_text(code)
        assert violations(tmp_path, [target]) == ["apps/presentation/api/listings.py:2 predicate"]
