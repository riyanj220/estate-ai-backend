import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

ROOT = Path(__file__).resolve().parents[2]
LINT_IMPORTS = shutil.which("lint-imports") or str(Path(sys.executable).parent / "lint-imports")
SKIP_DIRS = {"__pycache__", "kernel", "presentation"}
DOMAIN_APPS = sorted(
    p.name for p in (ROOT / "apps").iterdir() if p.is_dir() and p.name not in SKIP_DIRS
)


def run_contracts(project: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(project)}
    return subprocess.run(  # noqa: S603
        [LINT_IMPORTS],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def append_code(path: Path, code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + code)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A copy of the real apps tree and contract config, safe to violate."""
    shutil.copytree(ROOT / "apps", tmp_path / "apps", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


def test_clean_tree_passes() -> None:
    result = run_contracts(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


def test_http_import_contract_covers_every_domain_app() -> None:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    http = next(
        c for c in contracts if c["name"] == "Domain apps never import HTTP or WebSocket code"
    )
    assert sorted(http["source_modules"]) == [f"apps.{app}" for app in DOMAIN_APPS]
    for module in (
        "django.http",
        "django.shortcuts",
        "django.views",
        "django.urls",
        "django.template.response",
        "django.core.handlers",
        "rest_framework",
        "channels",
    ):
        assert module in http["forbidden_modules"]


def test_domain_apps_have_public_modules() -> None:
    assert DOMAIN_APPS
    for app in DOMAIN_APPS:
        for module in ("services", "selectors", "events", "subscribers"):
            path = ROOT / "apps" / app / module
            assert path.with_suffix(".py").is_file() or (path / "__init__.py").is_file(), (
                f"apps/{app}/{module}"
            )


def test_contracts_cover_every_domain_app() -> None:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    by_type = {}
    for c in contracts:
        by_type.setdefault(c["type"], []).append(c)
    layers = " ".join(by_type["layers"][0]["layers"])
    forbidden = by_type["forbidden"]
    subscribers = next(c for c in forbidden if "subscribers" in c["forbidden_modules"][0])
    for app in DOMAIN_APPS:
        assert app in layers, f"{app} missing from layers contract"
        assert any(c["forbidden_modules"] == [f"apps.{app}.models"] for c in forbidden), app
        assert f"apps.{app}.subscribers" in subscribers["forbidden_modules"], app
        assert f"apps.{app}.apps -> apps.{app}.subscribers" in subscribers["ignore_imports"], app


VIOLATIONS = [
    pytest.param(
        "apps/kernel/upward.py",
        "import apps.listings.services\n",
        "Layers point downward only",
        id="upward-import",
    ),
    pytest.param(
        "apps/valuation/services.py",
        "import apps.search.services\n",
        "Tier-4 apps never import each other",
        id="tier4-peers",
    ),
    pytest.param(
        "apps/messaging/services.py",
        "import apps.identity.subscribers\n",
        "Nothing imports a subscribers module",
        id="emitter-imports-subscriber",
    ),
    pytest.param(
        "apps/identity/apps.py",
        "import apps.catalog.subscribers\n",
        "Nothing imports a subscribers module",
        id="apps-py-imports-foreign-subscribers",
    ),
    pytest.param(
        "apps/assistant/services.py",
        "import apps.listings.models\n",
        "Assistant never imports listings models",
        id="assistant-to-listings-models",
    ),
    pytest.param(
        "apps/assistant/services.py",
        "import apps.listings.publication\n",
        "Assistant never imports publication",
        id="assistant-to-publication",
    ),
    pytest.param(
        "apps/listings/services.py",
        "import django.http\n",
        "Domain apps never import HTTP or WebSocket code",
        id="domain-imports-django-http",
    ),
    pytest.param(
        "apps/catalog/selectors.py",
        "import django.shortcuts\n",
        "Domain apps never import HTTP or WebSocket code",
        id="domain-imports-django-shortcuts",
    ),
    pytest.param(
        "apps/catalog/services.py",
        "import django.urls\n",
        "Domain apps never import HTTP or WebSocket code",
        id="domain-imports-django-urls",
    ),
    pytest.param(
        "apps/identity/selectors.py",
        "import rest_framework\n",
        "Domain apps never import HTTP or WebSocket code",
        id="domain-imports-drf",
    ),
    pytest.param(
        "apps/listings/services.py",
        "import PIL.Image\n",
        "Pillow is imported only inside kernel",
        id="listings-imports-pillow",
    ),
    pytest.param(
        "apps/presentation/api/listings.py",
        "from PIL import Image\n",
        "Pillow is imported only inside kernel",
        id="presentation-imports-pillow",
    ),
    pytest.param(
        "apps/assistant/services.py",
        "import httpx\n",
        "Provider SDKs and HTTP clients are imported only inside kernel",
        id="assistant-imports-http-client",
    ),
    pytest.param(
        "apps/search/services.py",
        "import groq\n",
        "Provider SDKs and HTTP clients are imported only inside kernel",
        id="search-imports-provider-sdk",
    ),
    pytest.param(
        "apps/messaging/services.py",
        "import channels\n",
        "Domain apps never import HTTP or WebSocket code",
        id="domain-imports-channels",
    ),
]


def _model_violations() -> list:
    params = []
    for owner in DOMAIN_APPS:
        source = next(a for a in DOMAIN_APPS if a != owner)
        params.append(
            pytest.param(
                f"apps/{source}/services.py",
                f"import apps.{owner}.models\n",
                f"Nothing outside {owner} imports its models",
                id=f"foreign-models-{owner}",
            )
        )
    return params


def test_pillow_contract_covers_every_app_but_the_kernel() -> None:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    pillow = next(c for c in contracts if c["name"] == "Pillow is imported only inside kernel")
    everyone = sorted(
        p.name for p in (ROOT / "apps").iterdir() if p.is_dir() and p.name != "__pycache__"
    )
    assert sorted(pillow["source_modules"]) == [f"apps.{a}" for a in everyone if a != "kernel"]
    assert pillow["forbidden_modules"] == ["PIL"]


def test_kernel_may_import_pillow(project: Path) -> None:
    append_code(project / "apps/kernel/images.py", "from PIL import ImageFilter\n")
    result = run_contracts(project)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(("path", "code", "contract"), VIOLATIONS + _model_violations())
def test_violation_breaks_its_contract(project: Path, path: str, code: str, contract: str) -> None:
    append_code(project / path, code)
    result = run_contracts(project)
    assert result.returncode != 0, result.stdout
    assert f"{contract} BROKEN" in result.stdout
    assert code.split()[1] in result.stdout


def test_app_registering_its_own_subscribers_is_allowed(project: Path) -> None:
    (project / "apps/identity/apps.py").write_text("import apps.identity.subscribers\n")
    result = run_contracts(project)
    assert result.returncode == 0, result.stdout


def test_unlisted_app_breaks_exhaustive_layers(project: Path) -> None:
    (project / "apps/newapp").mkdir()
    (project / "apps/newapp/__init__.py").write_text("")
    result = run_contracts(project)
    assert result.returncode != 0, result.stdout
    assert "Layers point downward only BROKEN" in result.stdout
    assert "apps.newapp" in result.stdout


def test_domain_apps_may_use_django_db(project: Path) -> None:
    append_code(project / "apps/listings/services.py", "import django.db\n")
    result = run_contracts(project)
    assert result.returncode == 0, result.stdout
