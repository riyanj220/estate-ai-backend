"""AD-22: every public function in a domain app's services module takes `actor` first."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]


def violations(path: Path) -> list[str]:
    found = []
    for node in ast.parse(path.read_text()).body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        params = [*node.args.posonlyargs, *node.args.args]
        if not params or params[0].arg != "actor":
            found.append(f"{path.name}:{node.lineno} {node.name}")
    return found


def test_service_modules_take_actor_first():
    modules = sorted(
        [*(ROOT / "apps").glob("*/services.py"), *(ROOT / "apps").glob("*/services/*.py")]
    )
    assert modules
    assert [v for m in modules for v in violations(m)] == []


def test_sample_service_follows_the_convention():
    assert violations(ROOT / "tests" / "authz" / "sample_service.py") == []


def test_function_without_actor_first_is_caught(tmp_path):
    bad = tmp_path / "services.py"
    bad.write_text(
        "def publish(listing_id, actor): ...\n"
        "async def close(): ...\n"
        "def ok(actor, x): ...\n"
        "def _helper(x): ...\n"
    )
    assert [v.split()[1] for v in violations(bad)] == ["publish", "close"]
