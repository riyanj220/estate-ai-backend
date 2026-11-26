#!/usr/bin/env bash
# Runs the tests that match what changed against a base ref (default: main), plus the guard tests.
#   apps/<app>/...         -> tests/<app>
#   apps/presentation/...  -> tests/api (and tests/authz)
#   config/, scripts/ etc. -> guards only (run the full suite before merging)
# Usage: scripts/test-changed.sh [base-ref] [-- extra pytest args]
set -euo pipefail
cd "$(dirname "$0")/.."

base="${1:-main}"
[ "$#" -gt 0 ] && shift
[ "${1:-}" = "--" ] && shift

declare -A dirs=()
while IFS= read -r path; do
  case "$path" in
    apps/presentation/*) dirs[tests/api]=1; dirs[tests/authz]=1 ;;
    apps/*/*)
      app="${path#apps/}"; app="${app%%/*}"
      [ -d "tests/$app" ] && dirs["tests/$app"]=1 ;;
    tests/*/*)
      dir="${path%/*}"
      while [ "$dir" != "tests" ] && [ "$(dirname "$dir")" != "tests" ]; do dir="$(dirname "$dir")"; done
      [ -d "$dir" ] && dirs["$dir"]=1 ;;
  esac
done < <({ git diff --name-only "$base"; git ls-files --others --exclude-standard; } | sort -u)

selected=("${!dirs[@]}")
echo "Changed-test directories: ${selected[*]:-none}"

unset DJANGO_SETTINGS_MODULE
workers="${PYTEST_WORKERS:-3}"
common=(-q -n "$workers")
# Guards first (marker), then the affected directories without repeating the guards.
uv run pytest "${common[@]}" -m guard "$@"
if [ "${#selected[@]}" -gt 0 ]; then
  uv run pytest "${common[@]}" -m "not guard and not perf" "${selected[@]}" "$@"
fi
