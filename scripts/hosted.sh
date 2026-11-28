#!/usr/bin/env bash
# Run against the hosted services configured in .env.hosted (docs/real-services.md).
#   scripts/hosted.sh serve              start the backend on 127.0.0.1:8000 (one process)
#   scripts/hosted.sh <command> [args]   any manage.py command
set -euo pipefail
cd "$(dirname "$0")/.."
export DJANGO_SETTINGS_MODULE=config.settings.hosted
if [ "$#" -eq 0 ]; then
  echo "usage: scripts/hosted.sh serve | <manage.py command> [args]" >&2
  exit 2
fi
if [ "$1" = "serve" ]; then
  # As scripts/dev_server.sh: no access log (it would record query strings, AD-26).
  exec uv run daphne --access-log /dev/null \
    --websocket-max-message-size 1024 --websocket-max-frame-size 1024 \
    -b 127.0.0.1 -p "${DJANGO_PORT:-8000}" config.asgi:application
fi
exec uv run python manage.py "$@"
