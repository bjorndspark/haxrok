#!/usr/bin/env bash
# uv.sh — run any uv command inside the backend container.
# Usage: ./scripts/docker/uv.sh add requests
#        ./scripts/docker/uv.sh sync
#        ./scripts/docker/uv.sh run pytest
set -euo pipefail
docker compose exec backend uv "$@"
