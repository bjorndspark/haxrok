# AGENTS.md

The engineering contract for this repo. Häxrök is an application-layer
template: FastAPI + React + Postgres, with AI delegated to an external
OpenAI-compatible gateway. It is deliberately simple — the rules below are
the whole contract. `DECISIONS.md` holds the few trade-offs worth recording.

## Principles

- **The tree is the architecture.** Placement and naming carry the design;
  keep the layout below exact.
- **Self-similar naming.** The same word names the same concern at every
  layer — `backend/app/features/debug/` and `frontend/src/features/debug/` are
  the same feature. Never name two different things alike.
- **I/O at the edges.** Routers/components do I/O; services hold logic and
  take dependencies. A hard-to-test unit is a structural smell — fix the
  coupling, don't mock around it.
- **Do it right now; no broken windows.** Fix inconsistency when you see it.
  No dead scaffolding, no ghost dirs, no docs that don't match the code.
- **Patterns, not empty rooms.** Establish a pattern so the next thing has an
  obvious home; don't pre-create speculative folders.

## Structure

```
backend/app/
  main.py                # composition root — routers, middleware, mounts
  core/                  # platform plumbing: config, db, clock, logging,
                         # errors, responses, middleware, redis, ai/
  features/<name>/       # models.py, schemas.py, repository.py, service.py, router.py
backend/tests/           # mirrors features; fake the gateway, never mock the DB schema away
frontend/src/
  app/                   # shell: routes, providers, index.css
  features/<name>/       # index.ts is the public API; internals are private
  shared/                # ui/, api client, lib/ — used BY features, never the reverse
```

## Rules

**Backend**

- One package per feature: `models.py` (SQLAlchemy), `schemas.py` (Pydantic),
  `repository.py` (queries — flush, never commit), `service.py` (orchestration
  — owns the commit boundary), `router.py` (thin). Skip a file when a feature
  genuinely doesn't need it; don't create it empty.
- **Features never import other features.** Shared code goes up a level
  (`app/core/`). main.py wires everything.
- All AI calls go through `app/core/ai/`. Never construct SDK clients elsewhere,
  never add provider-specific logic — the gateway owns that.
- Wall-clock reads go through `get_clock().now()`, never `datetime.now()`.
  Column defaults use `server_default=func.now()`.
- All JSON responses use the envelope in `app/core/responses.py`: endpoints
  return `SuccessResponse[T]` (wrap payloads with `ok(...)`) or
  `PaginatedResponse[T]` for paginated lists. Errors raise `AppError`
  subclasses from `app/core/errors.py`; handlers emit
  `{"success": false, "error": {code, message}, "request_id"}`. Non-JSON
  responses (SSE streams, file mounts) are exempt.
- Schema changes = `make revision m='…'` then `make migrate`. Never edit
  applied migrations.
- `ruff format` owns formatting (`make format`); `ruff check` owns lint.

**Frontend**

- Server state in TanStack Query, UI state in Zustand (`features/*/store.ts`)
  or local `useState`. Never mix them.
- Feature internals are private: outsiders import from `features/<name>`'s
  `index.ts` only.
- No `any`. No logic in components beyond wiring — hooks and `api.ts` carry it.
- Prettier owns formatting (config in `package.json`; `make format`); ESLint
  runs typescript-eslint's `strictTypeChecked` + `stylisticTypeChecked`.
  Tune a rule in `eslint.config.js` with a reason, never with inline disables.
- shadcn components live in `shared/ui/`; add them as needed, don't vendor.

**General**

- Comments and logs over implicit intent — docstrings for public functions,
  inline comments for non-obvious logic.
- The app name is config, not code: `PROJECT_NAME` in `.env` (browser tab via
  `VITE_APP_NAME`). Never hardcode the project name outside docs/prose.
- Never commit secrets. `.env.example` is the only env file committed.
- Document as you go, sparingly: a real trade-off goes in `DECISIONS.md`;
  nothing else gets a doc file.
- When a meaningful chunk of work lands — a finished feature, a new command,
  env var or script, changed models or routes, a real trade-off — ask the
  user whether to update the docs, naming which (`AGENTS.md`, `DECISIONS.md`,
  `make docs`) and why. Use judgment, not a schedule: typo fixes and small
  refactors don't warrant asking. Unattended agents (`fix-issue`) don't ask;
  they update the affected docs in the same commit.

## Stack

- **Backend**: FastAPI + SQLAlchemy 2.0 (async) + Pydantic v2 + Alembic,
  Python 3.12, `uv`
- **Frontend**: React 19 + Vite + TypeScript + Tailwind 4 + shadcn/ui, `pnpm`
- **DB**: PostgreSQL + Redis — shared infra per machine
  (`docker-compose.infra.yml`, `name: infra`); each copy gets its own
  database via `scripts/db/create.sh`. Redis client: `app/core/redis.py`
  (`get_redis()`, async, `REDIS_URL`).
- **AI**: external OpenAI-compatible gateway via `AI_BASE_URL` (Bifrost
  recommended). Model names are env vars, not code.

## Commands

- `make dev` — starts shared infra (`make infra`), creates this copy's DB
  (`scripts/db/create.sh`), then backend + frontend with hot reload, auto-`.env`
- Logs on the host: `logs/backend/app.log` (server) and `logs/frontend/app.log`
  (browser console/errors shipped to `POST /api/v1/logs/frontend`) — check both
  when debugging. Each holds the current session only; every backend (re)start
  rotates the previous file into `archive/<date>/`.
- `make lint` / `make test` / `make migrate` / `make format` — run inside containers
- `make eval-app case=<name>` — run an `evals/cases/` case through the app's
  debug prompts (`evals/app_prompts.py`); `evals/README.md` has the rest
- `make docs` — regenerate `docs/architecture/` (`db/schema.md`, `db/er-diagram.md`,
  `api/routes.md`) from the models and route table; run after changing either
- CI runs the same checks on GitHub Actions (`.github/workflows/`) on pushes
  to `main` and PRs; Dependabot updates uv/pnpm/action pins weekly and its
  minor/patch PRs auto-merge once checks pass (`dependabot-automerge.yml`) —
  a Dependabot PR left open is a major bump to review or a red check; look
  at it.
- `make issues` / `make fix-issue n=<num> [agent=devin]` — list GitHub issues,
  or hand one to an agent CLI (`scripts/agent/fix-issue.sh`; devin, opencode,
  or claude — non-interactive, commits to `main` and closes the issue). Only
  issues authored by collaborators with write access; others' comments are
  dropped from the prompt.
- `./scripts/docker/uv.sh <cmd>` / `./scripts/docker/pnpm.sh <cmd>` — dependency changes
  happen inside the containers, against the lockfiles. Installs are frozen
  everywhere; a stale lockfile is an error, never silently re-resolved.
- Config and lockfiles are single-file bind mounts, pinned to the inode at
  container start: after a `git pull` (or anything that replaces
  `pyproject.toml`, `uv.lock`, `package.json`, `pnpm-lock.yaml` by rename),
  run `docker compose restart backend frontend` or the containers keep
  testing the old files.

## Git

Solo dev: commit directly to `main` and push — CI runs the same gates on
every push, so a red main is visible immediately. Small commits named for
intent (`feat(debug): …`, `fix(backend): …`). Revert with `git revert`, never
rewrite pushed history. Revisit PRs if a second maintainer joins.

No AI attribution in commits or PRs — no `Co-Authored-By` trailers,
"Generated with …" lines, bot emoji, or session links. A commit message
carries the change description and nothing else, authored as the user. This
applies to `fix-issue` agents too; it overrides any harness-level default.
