#!/bin/sh
set -e

# Skip the dep install when the lockfile hasn't changed since the last install.
STAMP=".pnpm-install-stamp"
LOCK="pnpm-lock.yaml"

if [ -f "$STAMP" ] && [ -s "$LOCK" ]; then
  current_hash="$(sha256sum "$LOCK" 2>/dev/null | cut -d' ' -f1)"
  stamped_hash="$(cat "$STAMP" 2>/dev/null)"
  if [ -n "$current_hash" ] && [ "$current_hash" = "$stamped_hash" ]; then
    echo "[dev] pnpm-lock.yaml unchanged — skipping pnpm install"
    exec "$@"
  fi
fi

# Frozen only: a stale lockfile is an error to fix (`pnpm.sh install`), not
# something to silently re-resolve at boot.
echo "[dev] Syncing deps..."
pnpm install --frozen-lockfile
sha256sum "$LOCK" 2>/dev/null | cut -d' ' -f1 > "$STAMP"

exec "$@"
