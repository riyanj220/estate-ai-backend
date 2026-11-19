#!/usr/bin/env bash
# Start Daphne: a single process, a single worker, bound to loopback by default.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  # Load .env without overriding variables already set in the environment.
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    if [ -z "${!key+x}" ]; then
      export "$key=$value"
    fi
  done < .env
fi
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.local}"
exec uv run daphne -b "${DJANGO_BIND_HOST:-127.0.0.1}" -p "${DJANGO_PORT:-8000}" config.asgi:application
