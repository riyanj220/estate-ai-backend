#!/usr/bin/env python3
"""Create the ignored .env from .env.example with freshly generated secrets."""

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def main() -> int:
    if TARGET.exists():
        print(".env already exists; leaving it untouched.")
        return 0
    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.startswith("#") and value == "change-me":
            line = f"{key}={secrets.token_urlsafe(32)}"
        lines.append(line)
    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("Wrote .env with generated secrets (not tracked by git).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
