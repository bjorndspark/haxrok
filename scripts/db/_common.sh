#!/usr/bin/env bash
#
# _common.sh — helpers sourced by scripts/db/*.sh. Not executed directly.
#
# Resolves the project DB name/user (env var wins over .env) and wraps
# `docker compose -f docker-compose.infra.yml exec postgres` so every script
# talks to the same shared infra postgres.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
INFRA_COMPOSE="$ROOT_DIR/docker-compose.infra.yml"
BACKUP_DIR="$ROOT_DIR/data/backups"

# Read a single key from .env without sourcing it (env files may contain
# unquoted values with spaces, which would break `source`).
get_env_var() {
  local key="$1" file="$2" val
  val="$(grep -E "^${key}=" "$file" | head -1 | sed "s|^${key}=||")"
  # strip surrounding quotes
  val="${val#\"}"; val="${val%\"}"
  val="${val#\'}"; val="${val%\'}"
  printf '%s' "$val"
}

require_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "==> .env not found at $ENV_FILE." >&2
    echo "    Run ./scripts/setup/env.sh first." >&2
    exit 1
  fi
}

# Populates DB_NAME / DB_USER: real env vars win over .env values.
resolve_db() {
  DB_NAME="${POSTGRES_DB:-}"
  DB_USER="${POSTGRES_USER:-}"
  [ -z "$DB_NAME" ] && DB_NAME="$(get_env_var POSTGRES_DB "$ENV_FILE")"
  [ -z "$DB_USER" ] && DB_USER="$(get_env_var POSTGRES_USER "$ENV_FILE")"
  DB_NAME="${DB_NAME:-app}"
  DB_USER="${DB_USER:-postgres}"
}

# Run a command inside the shared infra postgres container.
pg_exec() {
  docker compose -f "$INFRA_COMPOSE" exec -T postgres "$@"
}

wait_for_pg() {
  echo "==> Waiting for shared PostgreSQL to accept connections…"
  for i in $(seq 1 30); do
    if pg_exec pg_isready -U "$DB_USER" -d postgres >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "==> PostgreSQL not ready after 30s. Is \`make infra\` running?" >&2
  exit 1
}
