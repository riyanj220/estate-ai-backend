"""Only `apps/kernel/ai` may import a provider SDK or open outbound network (AD-8), and any URL
written there must be on the allowlist. The import contract covers the domain apps; this covers
the rest of the kernel, config, stdlib network modules and string imports too."""

import ast
from pathlib import Path

import pytest

from apps.kernel.ai import egress

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
AI = ROOT / "apps" / "kernel" / "ai"
BANNED = (
    "aiohttp", "anthropic", "cohere", "ftplib", "google", "groq", "http.client", "httpx",
    "huggingface_hub", "langchain", "langchain_community", "langchain_core",
    "langchain_google_genai", "langchain_groq", "langsmith", "litellm", "mistralai", "ollama",
    "openai", "pycurl", "requests", "sentence_transformers", "smtplib", "socket", "telnetlib",
    "urllib.request", "urllib3", "websockets",
)  # fmt: skip


def banned(name: str) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in BANNED)


def imported_names(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            first = node.args[0]
            if called in ("import_module", "__import__") and isinstance(first, ast.Constant):
                if isinstance(first.value, str):
                    yield node.lineno, first.value


def import_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [f"{path.name}:{line} {name}" for line, name in imported_names(tree) if banned(name)]


def url_violations(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(("http://", "https://", "ws://", "wss://")):
                try:
                    egress.check_url(node.value)
                except egress.EgressRefused:
                    found.append(f"{path.name}:{node.lineno}")
    return found


def modules(base: str):
    return sorted((ROOT / base).rglob("*.py"))


def test_no_provider_or_network_import_outside_kernel_ai():
    paths = [p for base in ("apps", "config") for p in modules(base) if AI not in p.parents]
    assert paths
    assert [v for p in paths for v in import_violations(p)] == []


def test_urls_inside_kernel_ai_are_allowlisted():
    assert [v for p in sorted(AI.rglob("*.py")) for v in url_violations(p)] == []


@pytest.mark.parametrize(
    "source",
    [
        "import httpx\n",
        "import requests as r\n",
        "from groq import Groq\n",
        "from google import genai\n",
        "from langchain_core.callbacks import BaseCallbackHandler\n",
        "from urllib import request\n",
        "import urllib.request\n",
        "def f():\n    import socket\n",
        "import importlib\nimportlib.import_module('openai')\n",
    ],
)
def test_a_violating_fixture_fails(tmp_path, source):
    bad = tmp_path / "bad.py"
    bad.write_text(source)
    assert import_violations(bad)


def test_url_parsing_is_allowed(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("from urllib.parse import urlsplit\nimport http\n")
    assert import_violations(ok) == []


def test_a_url_outside_the_allowlist_fails(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("A = 'https://api.groq.com/v1'\nB = 'https://elsewhere.invalid/v1'\n")
    assert url_violations(bad) == ["bad.py:2"]


@pytest.mark.parametrize(
    "url",
    [
        "http://api.groq.com/v1",
        "https://api.groq.com.elsewhere.invalid/",
        "https://user@api.groq.com/",
        "https://api.groq.com:8443/",
        "https://127.0.0.1/",
    ],
)
def test_check_url_refuses(url):
    with pytest.raises(egress.EgressRefused):
        egress.check_url(url)
