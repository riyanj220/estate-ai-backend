import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "admin_read_"
RECORDER = "record_private_read"
PRIVACY = "apps.kernel.privacy"


def _bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to the recorder, and names bound to a privacy module, by import."""
    funcs: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(PRIVACY):
            for alias in node.names:
                (funcs if alias.name == RECORDER else modules).add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PRIVACY) and alias.asname:
                    modules.add(alias.asname)
    return funcs, modules


def _is_recorder_call(stmt: ast.stmt, is_async: bool, funcs: set[str], modules: set[str]) -> bool:
    if not isinstance(stmt, ast.Expr):
        return False
    value = stmt.value
    if is_async and isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call) or len(value.args) + len(value.keywords) != 3:
        return False
    func = value.func
    if isinstance(func, ast.Name):
        return func.id in funcs
    return (
        isinstance(func, ast.Attribute)
        and func.attr == RECORDER
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


def _first_statement(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt | None:
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # a docstring is not a statement that reads anything
    return body[0] if body else None


def find_violations(paths) -> list[str]:
    found = []
    for path in sorted(paths):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            found.append(f"{path}:{exc.lineno or 0} (unparsable)")
            continue
        funcs, modules = _bindings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                PREFIX
            ):
                first = _first_statement(node)
                is_async = isinstance(node, ast.AsyncFunctionDef)
                if first is None or not _is_recorder_call(first, is_async, funcs, modules):
                    found.append(f"{path}:{node.lineno} {node.name}")
    return found


def selector_files(root: Path) -> list[Path]:
    return [
        *root.glob("*/selectors.py"),
        *(p for d in root.glob("*/selectors") for p in d.rglob("*.py")),
    ]


def test_every_admin_read_selector_records_first() -> None:
    files = selector_files(ROOT / "apps")
    assert files, "no selectors files were scanned"
    offenders = find_violations(files)
    assert not offenders, f"admin_read_* must call {RECORDER} first: " + ", ".join(offenders)


def test_convention_flags_failing_fixtures(tmp_path: Path) -> None:
    bad = tmp_path / "selectors.py"
    bad.write_text(
        "from apps.kernel.privacy.reads import record_private_read\n"
        "\n"
        "def admin_read_reads_first(actor, ref):\n"
        "    data = load(ref)\n"
        "    record_private_read(actor, 'a reason here', ref)\n"
        "    return data\n"
        "\n"
        "def admin_read_skips(actor, ref):\n"
        "    return load(ref)\n"
        "\n"
        "def admin_read_empty(actor, ref):\n"
        "    '''doc'''\n"
        "\n"
        "def admin_read_assigns(actor, ref):\n"
        "    x = record_private_read(actor, 'a reason here', ref)\n"
        "\n"
        "def admin_read_two_args(actor, ref):\n"
        "    record_private_read(actor, ref)\n"
        "\n"
        "def admin_read_sync_await(actor, ref):\n"
        "    await record_private_read(actor, 'a reason here', ref)\n"
    )
    lookalike = tmp_path / "lookalike" / "selectors.py"
    lookalike.parent.mkdir()
    lookalike.write_text(
        "from somewhere import record_private_read\n"
        "\n"
        "def admin_read_lookalike(actor, ref):\n"
        "    record_private_read(actor, 'a reason here', ref)\n"
        "\n"
        "def admin_read_local(actor, ref):\n"
        "    def record_private_read(*a): pass\n"
        "    other.record_private_read(actor, 'a reason here', ref)\n"
    )
    good = tmp_path / "ok" / "selectors.py"
    good.parent.mkdir()
    good.write_text(
        "from apps.kernel.privacy import reads\n"
        "from apps.kernel.privacy import reads as r\n"
        "from apps.kernel.privacy.reads import record_private_read as rec\n"
        "\n"
        "def admin_read_ok(actor, ref):\n"
        "    '''doc'''\n"
        "    reads.record_private_read(actor, 'a reason here', ref)\n"
        "    return load(ref)\n"
        "\n"
        "def admin_read_aliased(actor, ref):\n"
        "    rec(actor, 'a reason here', ref)\n"
        "\n"
        "def admin_read_module_alias(actor, ref):\n"
        "    r.record_private_read(actor, reason='a reason here', target_ref=ref)\n"
        "\n"
        "async def admin_read_async(actor, ref):\n"
        "    await reads.record_private_read(actor, 'a reason here', ref)\n"
        "\n"
        "def other_read(ref):\n"
        "    return load(ref)\n"
    )
    result = find_violations([bad, lookalike, good])
    assert sorted(r.split(" ")[-1] for r in result) == sorted(
        [
            "admin_read_reads_first",
            "admin_read_skips",
            "admin_read_empty",
            "admin_read_assigns",
            "admin_read_two_args",
            "admin_read_sync_await",
            "admin_read_lookalike",
            "admin_read_local",
        ]
    )


def test_selector_files_includes_packages(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "selectors.py").write_text("")
    (tmp_path / "b" / "selectors" / "deep").mkdir(parents=True)
    (tmp_path / "b" / "selectors" / "deep" / "x.py").write_text("")
    assert len(selector_files(tmp_path)) == 2
