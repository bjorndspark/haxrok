#!/bin/sh
set -e

# Dev-only dep sync: the prod image installs deps at build time and has no
# bind-mounted lockfile, so syncing on every boot would just download the dev
# deps the build excluded. In dev the host lockfile is bind-mounted over the
# image's copy — skip the sync when it hasn't changed since the last stamp.
if [ "${ENVIRONMENT:-}" = "development" ]; then
  STAMP=".uv-sync-stamp"
  LOCK="uv.lock"
  SYNC_DEPS=no

  if [ -f "$STAMP" ] && [ -s "$LOCK" ]; then
    current_hash="$(sha256sum "$LOCK" 2>/dev/null | cut -d' ' -f1)"
    stamped_hash="$(cat "$STAMP" 2>/dev/null)"
    if [ -n "$current_hash" ] && [ "$current_hash" = "$stamped_hash" ]; then
      echo "[dev] uv.lock unchanged — skipping uv sync"
    else
      SYNC_DEPS=yes
    fi
  else
    SYNC_DEPS=yes
  fi

  if [ "$SYNC_DEPS" = yes ]; then
    # Frozen only: a stale lockfile is an error to fix (`uv.sh lock`), not
    # something to silently re-resolve at boot.
    echo "[dev] Syncing Python deps..."
    uv sync --frozen --extra dev
    sha256sum "$LOCK" 2>/dev/null | cut -d' ' -f1 > "$STAMP"
  fi
fi

# Apply migrations on dev startup so `make dev` is self-contained. Idempotent.
if [ "${ENVIRONMENT:-}" = "development" ]; then
  echo "[entrypoint] Applying alembic migrations..."
  uv run alembic upgrade head
fi

exec "$@"
