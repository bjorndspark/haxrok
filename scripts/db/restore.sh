#!/usr/bin/env bash
#
# restore.sh — restore a dump produced by backup.sh into this project's
# database.
#
# DESTRUCTIVE: the database is dropped and recreated before the dump is
# replayed, so anything in it now is lost. Pass --yes to skip the
# confirmation (required when stdin isn't a terminal).
#
# Usage:
#   ./scripts/db/restore.sh data/backups/<db>-<timestamp>.sql.gz
#   ./scripts/db/restore.sh <file.sql[.gz]> --yes
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env
resolve_db

DUMP=""
ASSUME_YES=no
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=yes;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *)
      if [ -z "$DUMP" ]; then
        DUMP="$arg"
      else
        echo "unexpected arg: $arg" >&2
        exit 1
      fi;;
  esac
done

if [ -z "$DUMP" ]; then
  echo "Usage: $0 <file.sql[.gz]> [--yes]" >&2
  echo "    Backups in $BACKUP_DIR:" >&2
  ls -1 "$BACKUP_DIR" 2>/dev/null | sed 's/^/      /' >&2 || true
  exit 1
fi
if [ ! -f "$DUMP" ]; then
  echo "==> No such file: $DUMP" >&2
  exit 1
fi

if [ "$ASSUME_YES" != "yes" ]; then
  if [ ! -t 0 ]; then
    echo "==> Non-interactive shell; pass --yes to confirm the restore." >&2
    exit 1
  fi
  read -r -p "Drop and recreate \"$DB_NAME\", then restore $DUMP? [y/N] " ans
  case "$ans" in
    y|Y|yes) ;;
    *) echo "Aborted."; exit 1;;
  esac
fi

wait_for_pg

echo "==> Dropping and recreating \"$DB_NAME\"…"
# Terminate open connections first — DROP DATABASE fails while the backend
# holds sessions (pool_pre_ping keeps them alive).
pg_exec psql -U "$DB_USER" -d postgres -q -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity \
   WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid()"
pg_exec psql -U "$DB_USER" -d postgres -q -c "DROP DATABASE IF EXISTS \"$DB_NAME\""
pg_exec psql -U "$DB_USER" -d postgres -q -c "CREATE DATABASE \"$DB_NAME\""

echo "==> Restoring $DUMP into \"$DB_NAME\"…"
case "$DUMP" in
  *.gz) gunzip -c "$DUMP" | pg_exec psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 ;;
  *)    pg_exec psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 < "$DUMP" ;;
esac
echo "==> Restore complete."
