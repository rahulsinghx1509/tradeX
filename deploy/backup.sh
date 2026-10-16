#!/usr/bin/env bash
# Saves a compressed copy of the database to ~/tradex-backups and keeps the last 14.
# A systemd timer runs this every evening after the market closes.
set -euo pipefail
cd "$(dirname "$0")/.."

dest="$HOME/tradex-backups"
mkdir -p "$dest"
file="$dest/tradex-$(date -u +%Y%m%d-%H%M).sql.gz"
trap 'rm -f "$file"; echo "Backup FAILED" >&2' ERR
docker compose --env-file .env -f deploy/docker-compose.yml exec -T db \
  pg_dump --clean --if-exists -U tradex -d tradex | gzip > "$file"
echo "Saved $file ($(du -h "$file" | cut -f1))"
ls -1t "$dest"/tradex-*.sql.gz | tail -n +15 | xargs -r rm --
