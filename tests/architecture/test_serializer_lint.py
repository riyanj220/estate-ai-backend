import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _is_wildcard(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        return value.value == "__all__"
    if isinstance(value, ast.List | ast.Tuple | ast.Set):
        return any(_is_wildcard(e) for e in value.elts)
    return False


def _is_fields_target(target: ast.expr) -> bool:
    return (isinstance(target, ast.Name) and target.id == "fields") or (
        isinstance(target, ast.Attribute) and target.attr == "fields"
    )


def find_wildcard_fields(root: Path) -> list[str]:
    """Return 'file:line' for every wildcard `fields` assignment, and for unparsable files."""
    found = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            found.append(f"{path}:{exc.lineno or 0} (unparsable)")
            continue
        for node in sorted(ast.walk(tree), key=lambda n: getattr(n, "lineno", 0)):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if any(_is_fields_target(t) for t in targets) and _is_wildcard(value):
                found.append(f"{path}:{node.lineno}")
    return found


def test_no_wildcard_serializer_fields_in_apps() -> None:
    offenders = find_wildcard_fields(ROOT / "apps")
    assert not offenders, "fields = '__all__' is forbidden (AD-3): " + ", ".join(offenders)


def test_lint_detects_wildcard_fields(tmp_path: Path) -> None:
    bad = tmp_path / "serializers.py"
    bad.write_text(
        "class S:\n"
        "    class Meta:\n"
        "        model = object\n"
        "        fields = '__all__'\n"
        "        fields: str = '__all__'\n"
        "        fields = ('id', '__all__')\n"
        "\n"
        "S.Meta.fields = '__all__'\n"
    )
    ok = tmp_path / "good.py"
    ok.write_text("class S:\n    class Meta:\n        fields = ['id']\n")
    broken = tmp_path / "broken.py"
    broken.write_text("def (:\n")
    assert find_wildcard_fields(tmp_path) == [
        f"{broken}:1 (unparsable)",
        f"{bad}:4",
        f"{bad}:5",
        f"{bad}:6",
        f"{bad}:8",
    ]
