#!/usr/bin/env bash
# Pulls the latest main branch from GitHub and restarts TradeX if anything changed.
# A systemd timer runs this every 5 minutes, so merging a PR is all it takes to deploy.
# `--force` rebuilds even when nothing changed.
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch --quiet origin main
if [[ "${1:-}" != "--force" && "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]]; then
  exit 0
fi

# Caddy reads its Caddyfile only when it starts, so note whether this update changes it.
caddy_changed=0
git diff --quiet HEAD origin/main -- deploy/Caddyfile || caddy_changed=1

git merge --ff-only --quiet origin/main
echo "Deploying $(git log -1 --format='%h %s')"
deploy/tradex.sh start
if [[ "$caddy_changed" == 1 ]]; then
  docker compose --env-file .env -f deploy/docker-compose.yml restart caddy
fi
docker image prune -f >/dev/null
