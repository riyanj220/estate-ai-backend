#!/usr/bin/env bash
# One entry point for daily work. It uses the hosted services (.env.hosted, docs/real-services.md);
# only `./app test` and `./app local ...` use the Docker database.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
frontend="../estate-ai-frontend"

usage() {
  cat <<'TEXT'
usage: ./app <command>

  start            run backend and frontend on http://localhost:3000 (Ctrl+C stops both)
  update           after code changes arrive: install packages and migrate the database
  seed             add the sample data (needs SEED_ALLOWED=true in .env.hosted)
  seed fresh       delete the sample data and create it again (same requirement)
  admin <email>    create an administrator (add "reset-password" to reset one)
  jobs             refresh listing flags and run data retention
  check            check database, storage and mail
  manage <args>    any other manage.py command
  test             run the backend tests for what changed (Docker database)
  local            run backend and frontend against the Docker database instead
TEXT
}

# Both servers in one terminal; when either stops, or on Ctrl+C, stop both.
serve_both() {
  if [ ! -d "$frontend" ]; then
    echo "frontend not found at $frontend" >&2
    exit 1
  fi
  if (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
    echo "port 8000 is in use: stop the other backend first" >&2
    exit 1
  fi
  trap 'trap - EXIT INT TERM; kill 0' EXIT INT TERM
  "$@" &
  (cd "$frontend" && AI_FEATURES=1 exec npm run dev) &
  wait -n
}

command="${1:-}"
[ "$#" -gt 0 ] && shift

case "$command" in
  start) serve_both scripts/hosted.sh serve ;;
  update)
    uv sync --frozen
    (cd "$frontend" && npm install --no-audit --no-fund)
    scripts/hosted.sh migrate --database=migrator
    scripts/hosted.sh explain_price_estimates
    ;;
  seed)
    if [ "${1:-}" = "fresh" ]; then
      scripts/hosted.sh seed_sample_data --reseed --yes
    else
      scripts/hosted.sh seed_sample_data
    fi
    ;;
  admin)
    email="${1:?usage: ./app admin <email> [reset-password]}"
    scripts/hosted.sh provision_admin "${2:-create}" --email "$email"
    ;;
  jobs) exec scripts/hosted_jobs.sh ;;
  check) scripts/hosted.sh check_services "$@" ;;
  manage) scripts/hosted.sh "$@" ;;
  test)
    docker compose up -d --wait
    env -u DJANGO_SETTINGS_MODULE scripts/test-changed.sh "$@"
    ;;
  local)
    docker compose up -d --wait
    env -u DJANGO_SETTINGS_MODULE uv run python manage.py migrate --database=migrator
    serve_both env -u DJANGO_SETTINGS_MODULE uv run python manage.py runserver
    ;;
  *) usage ;;
esac
