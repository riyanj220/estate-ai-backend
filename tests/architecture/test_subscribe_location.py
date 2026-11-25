"""AD-18: `subscribe(` is called only from `subscribers.py` or `apps.py` modules."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"subscribers.py", "apps.py"}


def violations(path: Path) -> list[str]:
    if path.name in ALLOWED:
        return []
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return [f"{path.name}: unparsable"]
    names = {"subscribe"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "apps.kernel.events":
            names |= {a.asname for a in node.names if a.name == "subscribe" and a.asname}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in names:
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_subscribe_is_only_called_from_allowed_modules():
    modules = sorted((ROOT / "apps").rglob("*.py"))
    assert modules
    assert [v for m in modules for v in violations(m)] == []


def test_call_outside_the_convention_is_caught(tmp_path):
    bad = tmp_path / "services.py"
    bad.write_text(
        "from apps.kernel import events\n"
        "def wire():\n"
        "    events.subscribe(E, h)\n"
        "    subscribe(E, h)\n"
    )
    assert len(violations(bad)) == 2


def test_allowed_modules_pass(tmp_path):
    for name in ALLOWED:
        ok = tmp_path / name
        ok.write_text("events.subscribe(E, h)\n")
        assert violations(ok) == []


def test_unparsable_file_is_an_offender(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def (:\n")
    assert violations(bad) == ["broken.py: unparsable"]
    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff\xfe\x00")
    assert violations(binary) == ["binary.py: unparsable"]


def test_aliased_import_is_caught(tmp_path):
    bad = tmp_path / "services.py"
    bad.write_text("from apps.kernel.events import subscribe as wire\nwire(E, h)\n")
    assert len(violations(bad)) == 1
