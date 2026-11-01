#!/usr/bin/env bash
#
# backup.sh — dump this project's database out of shared infra postgres into
# a gzipped SQL file under data/backups/ (gitignored).
#
# Usage:
#   ./scripts/db/backup.sh                  # -> data/backups/<db>-<timestamp>.sql.gz
#   ./scripts/db/backup.sh path/to/out.sql.gz
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env
resolve_db
wait_for_pg

mkdir -p "$BACKUP_DIR"
OUT="${1:-$BACKUP_DIR/${DB_NAME}-$(date +%Y%m%d-%H%M%S).sql.gz}"

echo "==> Dumping \"$DB_NAME\" -> $OUT"
pg_exec pg_dump -U "$DB_USER" "$DB_NAME" | gzip -9 -c > "$OUT"
echo "==> Backup complete ($(du -h "$OUT" | cut -f1))."
