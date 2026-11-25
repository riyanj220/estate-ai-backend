"""Every contact-data regex lives in `kernel.privacy.patterns` (AD-9)."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = ROOT / "apps" / "kernel" / "privacy" / "patterns.py"
RE_FUNCTIONS = {
    "compile", "match", "fullmatch", "search", "sub", "subn", "findall", "finditer", "split",
}  # fmt: skip
EMAIL_TOOLS = {"EmailField", "EmailValidator", "validate_email"}
LONG_DIGITS = re.compile(r"(?:\\d|\[0-9\])\{(\d+)")
WORDS = ("phone", "mobile", "email")


def looks_like_contact_regex(pattern: str) -> bool:
    return (
        "@" in pattern
        or any(int(n) >= 3 for n in LONG_DIGITS.findall(pattern))
        or any(word in pattern.lower() for word in WORDS)
    )


def literal(node: ast.AST) -> str:
    """The literal text of a string, f-string or concatenation (non-literal parts are blank)."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        return "".join(literal(part) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return literal(node.left) + literal(node.right)
    return ""


def re_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to the `re` module and names imported from it that are regex functions."""
    modules, functions = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "re"}
        elif isinstance(node, ast.ImportFrom) and node.module == "re":
            functions |= {a.asname or a.name for a in node.names if a.name in RE_FUNCTIONS}
    return modules, functions


def violations(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return [f"{path}: unparsable"]
    modules, functions = re_aliases(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in EMAIL_TOOLS and isinstance(getattr(node, "ctx", None), ast.Load):
                found.append(f"{path}:{node.lineno}")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_re = (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
            and func.attr in RE_FUNCTIONS
        ) or (isinstance(func, ast.Name) and func.id in functions)
        if not is_re:
            continue
        pattern = node.args[0] if node.args else None
        for keyword in node.keywords:
            if keyword.arg == "pattern":
                pattern = keyword.value
        if pattern is not None and looks_like_contact_regex(literal(pattern)):
            found.append(f"{path}:{node.lineno}")
    return found


def test_no_contact_regex_outside_patterns_module():
    modules = [
        m
        for base in ("apps", "config")
        for m in sorted((ROOT / base).rglob("*.py"))
        if m != PATTERNS and "migrations" not in m.parts
    ]
    assert modules
    assert [v for m in modules for v in violations(m)] == []


def test_patterns_module_is_where_they_live():
    assert violations(PATTERNS) != []


def fixture(tmp_path, source):
    path = tmp_path / "bad.py"
    path.write_text(source)
    return path


def test_fixture_with_email_regex_is_caught(tmp_path):
    bad = fixture(tmp_path, 'import re\nX = 1\nP = re.compile(r"[a-z]+@[a-z]+")\n')
    assert violations(bad) == [f"{bad}:3"]


def test_fixture_with_digit_run_is_caught(tmp_path):
    bad = fixture(tmp_path, "import re\nre.search(r'\\d{3,}', x)\nre.compile('[0-9]{7}')\n")
    assert violations(bad) == [f"{bad}:2", f"{bad}:3"]


def test_fixture_with_contact_words_is_caught(tmp_path):
    bad = fixture(tmp_path, "from re import compile\nP = compile('(?i)phone|mobile')\n")
    assert violations(bad) == [f"{bad}:2"]


def test_fixture_with_aliased_module_and_keyword_pattern_is_caught(tmp_path):
    bad = fixture(tmp_path, "import re as r\nP = r.compile(pattern='a@b')\n")
    assert violations(bad) == [f"{bad}:2"]


def test_fixture_with_fstring_pattern_is_caught(tmp_path):
    bad = fixture(tmp_path, 'import re\nLOCAL = "x"\nP = re.compile(f"{LOCAL}@example")\n')
    assert violations(bad) == [f"{bad}:3"]


def test_fixture_with_concatenated_pattern_is_caught(tmp_path):
    bad = fixture(tmp_path, 'import re\nP = re.compile("[a-z]+" + "@" + "[a-z]+")\n')
    assert violations(bad) == [f"{bad}:2"]


def test_fixture_with_email_field_is_caught(tmp_path):
    bad = fixture(
        tmp_path,
        "from django.db import models\n"
        "from django.core.validators import validate_email\n"
        "class A(models.Model):\n"
        "    e = models.EmailField()\n",
    )
    assert violations(bad) == [f"{bad}:4"]
    use = fixture(
        tmp_path, "from django.core.validators import validate_email\nvalidate_email(x)\n"
    )
    assert violations(use) == [f"{use}:2"]


def test_string_methods_are_not_regex_calls(tmp_path):
    ok = fixture(
        tmp_path,
        'x.split("@")\nx.sub("@", "")\nx.search("phone")\nx.match("email")\nstr.split("a@b")\n',
    )
    assert violations(ok) == []


def test_existing_token_and_hash_patterns_are_not_contact_regexes():
    for pattern in (r"^[0-9a-f]{64}$", r"^[a-z][a-z0-9_]{0,47}$", r"%(2f|5c)", r"\d{1,2}"):
        assert not looks_like_contact_regex(pattern)


def test_fixture_with_phone_validation_regex_is_caught(tmp_path):
    bad = fixture(
        tmp_path,
        "import re\n"
        "PK = re.compile(r'^(\\+92|0)3[0-9]{9}$')\n"
        "ok = re.fullmatch(r'\\+\\d{8,15}', x)\n",
    )
    assert violations(bad) == [f"{bad}:2", f"{bad}:3"]


def test_registration_phone_rules_come_from_the_patterns_module():
    source = (ROOT / "apps" / "identity" / "validation.py").read_text()
    assert "normalise_phone" in source and "import re" not in source
