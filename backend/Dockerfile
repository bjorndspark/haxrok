# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/uv-cache \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# kicad-cli for the debug feature (netlist export + ERC). Debian's kicad
# package without recommends: no symbol/footprint libraries, which the
# schematics don't need since they embed their symbols. It adds ~800 MB.
# It must be at least as new as the KiCad that saved the user's files.
# ngspice (~30 MB) draws a flagged I²C line's rise (spice.py); without it the
# card just has no plot.
RUN apt-get update \
    && apt-get install -y --no-install-recommends kicad ngspice \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps from the committed lockfile only — never re-resolve at build
# time. Source is bind-mounted at runtime in dev. UV_EXTRAS opts into
# optional extras (`--build-arg UV_EXTRAS="--extra <name>"`).
ARG UV_EXTRAS=""
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/uv-cache \
    uv sync --frozen --no-install-project $UV_EXTRAS

# Copy app source (ignored in local dev — bind-mounts override at runtime).
COPY docker-entrypoint.sh ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
