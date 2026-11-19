"""Regenerate the route table in docs/authorisation-matrix.md.

Run: uv run python scripts/authz_matrix.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "authorisation-matrix.md"


def main() -> None:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django

    django.setup()
    from apps.presentation.api.authz_matrix import render_document

    try:
        text = render_document(DOC.read_text(), "config.urls")
    except ValueError as exc:
        raise SystemExit(f"{DOC}: {exc}") from exc
    DOC.write_text(text)


if __name__ == "__main__":
    main()
