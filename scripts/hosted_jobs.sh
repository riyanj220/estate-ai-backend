#!/usr/bin/env bash
# The scheduled jobs of hosted mode: duplicate and suspicious flags, then data retention.
# Safe to repeat. Skips silently if a previous run is still going. Cron and systemd examples are
# in docs/real-services.md; nothing installs them.
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9<"scripts/hosted_jobs.sh"
flock -n 9 || exit 0
status=0
scripts/hosted.sh run_flags || status=1
scripts/hosted.sh run_retention || status=1
exit "$status"
