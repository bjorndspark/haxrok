# Onboarding

From zero to a running stack, plus the commands that cover daily work.
`make help` lists every target; this is the cheat sheet.

## First run

```sh
git clone <repo> && cd haxrok
make dev
```

That's it. `make dev` generates `.env` (with non-conflicting ports), starts
the shared postgres/redis infra (a no-op if already running), creates this
copy's database, runs migrations, and starts backend + frontend with hot
reload. Open the printed frontend port (default http://localhost:5173).

- Swagger UI: `http://localhost:$BACKEND_HOST_PORT/docs`
- Chat won't work until `.env` points at an OpenAI-compatible gateway —
  set `AI_BASE_URL` + `AI_API_KEY` (Bifrost recommended; see README).

## Daily

```sh
make dev          # start everything
make down         # stop app containers
make logs         # container logs
make lint         # ruff + mypy + eslint + tsc, in parallel
make test         # pytest + vitest, in parallel
```

Each lint/test check also runs alone for faster iteration:
`make lint-ruff` / `lint-mypy` / `lint-eslint` / `lint-tsc` /
`test-backend` / `test-frontend`.

Log files on the host (current session only; rotated on restart):
`logs/backend/app.log` for the server, `logs/frontend/app.log` for browser
console/errors. Check both when debugging.

## Changing the schema

```sh
# 1. edit backend/app/features/<name>/models.py
make revision m='add widgets table'   # autogenerate migration
make migrate                          # apply it
```

Never edit an applied migration — always a new revision. Column defaults use
`server_default=func.now()`; code reads the clock via `get_clock().now()`.

## Adding a feature

- Backend: `backend/app/features/<name>/` — `models.py`, `schemas.py`,
  `repository.py` (flush only), `service.py` (owns commit), `router.py`
  (thin). Skip files the feature doesn't need. Register the router in
  `app/main.py`.
- Frontend: `frontend/src/features/<name>/` — `index.ts` is the public API;
  internals stay private. Server state in TanStack Query, UI state in Zustand.
- Features never import each other — shared code goes up a level
  (`app/core/`, `shared/`).
- All JSON endpoints return `ok(payload)` (`SuccessResponse[T]`) and raise
  `AppError` subclasses; SSE/file responses are exempt.

## Dependencies

```sh
./scripts/docker/uv.sh add <pkg>        # backend
./scripts/docker/pnpm.sh add <pkg>      # frontend
make install                            # resync after pulling lockfile changes
```

Deps are managed inside the containers against the pinned lockfiles — never
`pip install` / `pnpm add` on the host.

## Database ops

```sh
make db            # create this copy's DB (usually already done by make dev)
make db-backup     # dump to data/backups/<db>-<timestamp>.sql.gz
make db-restore f=data/backups/….sql.gz   # drop + recreate from a dump
```

Postgres is shared per machine (`docker-compose.infra.yml`); each copy gets
its own database. Reach it directly at `localhost:55432` (user/pass in
`.env`).

## Cleanup

```sh
make clean       # dev artifacts (logs, caches, build output); DB + containers survive
make purge       # clean + app containers + volumes; DB still survives in shared infra
```

The DB lives in shared infra, so even `purge` leaves it alone — drop it by
hand if needed (command in the Makefile).

## Issues

```sh
make issues                            # list open GitHub issues
make fix-issue n=<num> [agent=devin]   # hand one to an agent CLI
```

`fix-issue` (`scripts/agent/fix-issue.sh`) runs devin (default), opencode, or
claude non-interactively in this checkout: it implements the fix, runs lint +
test, commits to `main`, pushes, and closes the issue. Needs `gh` authed and
the agent CLI on PATH.

## Docs

```sh
make docs          # regenerate docs/architecture/api/routes.md
make docs-serve    # browse all of docs/ with mkdocs (live reload)
```

Generated from the FastAPI route table — no DB needed. Rerun after changing
routes. Other docs: trade-offs go in `DECISIONS.md`, runbooks here in
`docs/operations/`, plans in `docs/plans/`.

## When something's broken

1. `make logs` and `logs/backend/app.log` / `logs/frontend/app.log`.
2. "No CORS origins configured" or browser 403s → check
   `BACKEND_CORS_ORIGINS` / `FRONTEND_HOST_PORT` in `.env`.
3. Weird import errors after pulling → `make install` (stale venv).
4. Root-owned files in `logs/`/`data/` blocking cleanup →
   `docker compose run --rm backend rm -rf /app/logs /app/data`.
5. Infra down → `make infra` (it's shared; check `make infra-logs`,
   stop it with `make infra-down`).
