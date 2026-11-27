"""SM-4c: no user-facing string says "demo" or "demonstration" (Story 4.4).

Scanned: every string literal in `apps` and `config` except docstrings, the committed data files of
the apps, and the whole OpenAPI document (labels, descriptions, enum values)."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
BANNED = re.compile(r"(?<![a-z])(demos?|demonstrat[a-z]*)(?![a-z])", re.IGNORECASE)
DATA_SUFFIXES = {".yaml", ".yml", ".json", ".txt", ".html"}


def literals(source: str) -> list[str]:
    """Every string constant of a module that is not a docstring or other bare statement."""
    tree = ast.parse(source)
    bare = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in bare
    ]


def sources():
    for base in ("apps", "config"):
        for path in sorted((ROOT / base).rglob("*")):
            if path.suffix == ".py":
                yield path, "\n".join(literals(path.read_text()))
            elif path.suffix in DATA_SUFFIXES:
                yield path, path.read_text()
    yield ROOT / "openapi.yaml", (ROOT / "openapi.yaml").read_text()


def test_no_user_facing_string_says_demo():
    scanned = list(sources())
    assert len(scanned) > 100
    assert [f"{path}: {m.group()}" for path, text in scanned for m in BANNED.finditer(text)] == []


def test_the_scan_catches_the_words_and_skips_docstrings():
    source = '"""A demo docstring."""\nA = "Demo listing"\nB = f"a demonstration {A}"\n'
    found = [m.group() for text in literals(source) for m in BANNED.finditer(text)]
    assert sorted(found) == ["Demo", "demonstration"]
    assert not BANNED.search("demographic democracy")
