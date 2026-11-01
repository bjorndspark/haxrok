#!/usr/bin/env bash
#
# create.sh — idempotently create this project's PostgreSQL database in the
# shared infra container. Safe to run repeatedly; does nothing if the DB
# exists. Wired into `make dev` (via the `db` target) so the DB is created
# lazily on first start.
#
# Usage:
#   ./scripts/db/create.sh
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_env
resolve_db
wait_for_pg

exists="$(pg_exec psql -U "$DB_USER" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")"

if [ "$exists" = "1" ]; then
  echo "==> Database \"$DB_NAME\" already exists."
  exit 0
fi

echo "==> Creating database \"$DB_NAME\"…"
pg_exec psql -U "$DB_USER" -d postgres -c "CREATE DATABASE \"$DB_NAME\""
echo "==> Database \"$DB_NAME\" ready."
