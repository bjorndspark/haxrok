#!/usr/bin/env bash
#
# env.sh — create .env with host ports that won't conflict with other
# running copies of this project.
#
# App ports (backend, frontend, docs) are per copy. The shared infra port
# (Redis) is machine-level and not touched here.
#
# Usage:
#   ./scripts/setup/env.sh                    # auto-pick free app ports
#   ./scripts/setup/env.sh --no-auto          # keep default ports
#   ./scripts/setup/env.sh --backend 8001 --frontend 5174
#   ./scripts/setup/env.sh --docs 8002        # mkdocs preview port
#   ./scripts/setup/env.sh --force            # overwrite .env from .env.example
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
EXAMPLE_FILE="$ROOT_DIR/.env.example"

DEFAULT_BACKEND=8000
DEFAULT_FRONTEND=5173
DEFAULT_DOCS=8001

# --- port helpers ------------------------------------------------------------

port_is_free() {
  local port="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
    print("free")
except OSError:
    print("used")
finally:
    s.close()
PY
    return
  fi
  if (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
    exec 3>&- 3<&-
    echo "used"
  else
    echo "free"
  fi
}

# Ports already handed out in this run. Nothing listens on them yet, so
# port_is_free alone would give two services the same port (e.g. backend
# bumped 8000 -> 8001 colliding with the docs default 8001).
TAKEN_PORTS=" "

next_free_port() {
  local port="$1"
  while [ "$port" -lt 65535 ]; do
    if [ "${TAKEN_PORTS#* $port }" = "$TAKEN_PORTS" ] &&
       [ "$(port_is_free "$port")" = "free" ]; then
      echo "$port"
      return
    fi
    port=$((port + 1))
  done
  echo "$port"
}

# --- .env helpers --------------------------------------------------------------

set_env_var() {
  local key="$1" val="$2" file="$3"
  if grep -qE "^${key}=" "$file"; then
    local tmp
    tmp="$(mktemp)"
    sed -E "s|^${key}=.*|${key}=${val}|" "$file" > "$tmp" && mv "$tmp" "$file"
  else
    printf '%s=%s\n' "$key" "$val" >> "$file"
  fi
}

# --- args ------------------------------------------------------------------------

FORCE=no
NO_AUTO=no
BACKEND_PORT=""
FRONTEND_PORT=""
DOCS_PORT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --backend)  BACKEND_PORT="$2"; shift 2;;
    --frontend) FRONTEND_PORT="$2"; shift 2;;
    --docs)     DOCS_PORT="$2"; shift 2;;
    --no-auto)  NO_AUTO=yes; shift;;
    --force|-f) FORCE=yes; shift;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 1;;
  esac
done

# --- create / refresh .env ------------------------------------------------------

if [ ! -f "$EXAMPLE_FILE" ]; then
  echo "==> .env.example not found at $EXAMPLE_FILE" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ] || [ "$FORCE" = "yes" ]; then
  echo "==> Copying .env.example -> .env"
  cp "$EXAMPLE_FILE" "$ENV_FILE"
fi

# --- resolve ports -----------------------------------------------------------------

if [ "$NO_AUTO" = "no" ]; then
  # Explicit ports are reserved first so auto-picks steer around them.
  TAKEN_PORTS=" $BACKEND_PORT $FRONTEND_PORT $DOCS_PORT "
  [ -z "$BACKEND_PORT" ]  && BACKEND_PORT="$(next_free_port "$DEFAULT_BACKEND")"
  TAKEN_PORTS="$TAKEN_PORTS$BACKEND_PORT "
  [ -z "$FRONTEND_PORT" ] && FRONTEND_PORT="$(next_free_port "$DEFAULT_FRONTEND")"
  TAKEN_PORTS="$TAKEN_PORTS$FRONTEND_PORT "
  [ -z "$DOCS_PORT" ]     && DOCS_PORT="$(next_free_port "$DEFAULT_DOCS")"
else
  [ -z "$BACKEND_PORT" ]  && BACKEND_PORT="$DEFAULT_BACKEND"
  [ -z "$FRONTEND_PORT" ] && FRONTEND_PORT="$DEFAULT_FRONTEND"
  [ -z "$DOCS_PORT" ]     && DOCS_PORT="$DEFAULT_DOCS"
fi

# Display name seeded from the directory name ("my-app" -> "My-app"); the
# .env knob is PROJECT_NAME — edit there to override.
APP_NAME="$(basename "$ROOT_DIR")"
APP_NAME="${APP_NAME^}"

echo "==> Resolved:"
echo "      backend  : $BACKEND_PORT"
echo "      frontend : $FRONTEND_PORT"
echo "      docs     : $DOCS_PORT"
echo "      name     : $APP_NAME"

# --- write vars -----------------------------------------------------------------

set_env_var "PROJECT_NAME"        "$APP_NAME"       "$ENV_FILE"
set_env_var "BACKEND_HOST_PORT"   "$BACKEND_PORT"   "$ENV_FILE"
set_env_var "FRONTEND_HOST_PORT"  "$FRONTEND_PORT"  "$ENV_FILE"
set_env_var "DOCS_HOST_PORT"      "$DOCS_PORT"      "$ENV_FILE"
set_env_var "VITE_API_URL"        "http://localhost:$BACKEND_PORT" "$ENV_FILE"
set_env_var "BACKEND_CORS_ORIGINS" "http://localhost:$FRONTEND_PORT" "$ENV_FILE"

echo "==> Done. Start the app with: make dev"
echo "    Set AI_API_KEY to your Meta API key (AI_BASE_URL defaults to Meta's API;"
echo "    point it at any other OpenAI-compatible endpoint to switch)."
