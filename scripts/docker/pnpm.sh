#!/usr/bin/env bash
# pnpm.sh — run any pnpm command inside the frontend container.
# Usage: ./scripts/docker/pnpm.sh add axios
#        ./scripts/docker/pnpm.sh install
#        ./scripts/docker/pnpm.sh run build
#
# package.json and pnpm-lock.yaml are single-file bind mounts, and pnpm
# saves them by renaming a temp file over the original — which a file mount
# rejects (EBUSY). Commands that rewrite them run against a scratch copy;
# the results are written back in place (same inode, so the mount sees
# them) and installed from the now-frozen lockfile.
set -euo pipefail

case "${1:-}" in
  add | remove | rm | uninstall | un | update | up | upgrade | install | i | dedupe) ;;
  *) exec docker compose exec frontend pnpm "$@" ;;
esac

docker compose exec frontend sh -ec '
  tmp="$(mktemp -d)"
  trap "rm -rf \"$tmp\"" EXIT
  cp package.json pnpm-lock.yaml "$tmp/"
  (cd "$tmp" && pnpm "$@")
  cat "$tmp/package.json" > package.json
  cat "$tmp/pnpm-lock.yaml" > pnpm-lock.yaml
  pnpm install --frozen-lockfile
' sh "$@"
