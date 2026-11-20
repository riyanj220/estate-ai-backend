"""Rewrite apps/catalog/registry/fields.lock after editing fields.yaml (and bumping its version).

Run: uv run python scripts/registry_lock.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django

    django.setup()
    from apps.catalog import registry

    current = registry.parse(registry.FIELDS_PATH.read_text(encoding="utf-8"))
    if registry.LOCK_PATH.exists():
        locked_version, locked_hash = registry.read_lock(registry.LOCK_PATH.read_text())
        if current.version < locked_version:
            raise SystemExit(
                f"version {current.version} is lower than the locked version {locked_version}."
            )
        if locked_hash != current.content_hash and current.version <= locked_version:
            raise SystemExit(
                f"fields.yaml changed but version is still {current.version}: bump `version` first."
            )
    registry.LOCK_PATH.write_text(registry.render_lock(current))
    print(f"fields.lock: version {current.version}, sha256 {current.content_hash}")


if __name__ == "__main__":
    main()
