# Häxrök

A lean full-stack template: FastAPI + React + Postgres, with all AI concerns
delegated to an external OpenAI-compatible gateway (e.g.
[Bifrost](https://github.com/maximhq/bifrost)). The repo builds the application
layer — features, not infrastructure.

Extracted from Fulcrum's structure with the ceremony removed. The rules that
survived live in `AGENTS.md`; the reasoning lives in `DECISIONS.md`.

## Quick start

```sh
make dev        # generates .env, starts shared infra, creates this copy's DB,
                # then starts backend + frontend
```

Then open the printed frontend port (default http://localhost:5173).

PostgreSQL (and Redis) are shared per machine via `docker-compose.infra.yml`
(`make infra` is a no-op once running); each copy gets its own database.

### Pointing at a gateway

The app talks to any OpenAI-compatible endpoint via `AI_BASE_URL` in `.env`:

- **Bifrost** (recommended): run it separately, then set
  `AI_BASE_URL=http://host.docker.internal:8080/v1` and `AI_API_KEY` to a
  Bifrost virtual key. Provider keys, routing, budgets, and observability
  are configured in Bifrost — not here.
- **Direct**: leave `AI_BASE_URL` empty and set `AI_API_KEY` to an OpenAI key.

## Layout

```
backend/app/
  main.py            # composition root
  core/              # config, db, clock, logging, errors, responses,
                     # middleware, storage, ai/ (the ONLY place AI is touched)
  features/chat/     # feature: models, schemas, repository, service, router
  features/speech/   # feature: push-to-talk transcribe + read-aloud synthesize
frontend/src/
  app/               # shell: main.tsx, App.tsx (routes), index.css
  features/chat/     # index.ts is the public API
  shared/            # ui/, api client (envelope unwrap), lib/
```

## Commands

`make dev` · `make lint` · `make test` · `make migrate` · `make revision m='…'` ·
`make docs` · `make down` · `make purge`

New here? `docs/operations/onboarding.md` is the cheat sheet. `make docs`
regenerates `docs/architecture/` (schema, ER diagram, route inventory).

`make issues` lists open GitHub issues; `make fix-issue n=<num> [agent=devin]`
hands one to an agent CLI (`scripts/agent/fix-issue.sh`; devin, opencode, or
claude — non-interactive, commits to `main` and closes the issue).

Dependencies: `./scripts/docker/uv.sh add <pkg>` / `./scripts/docker/pnpm.sh add <pkg>`
(run inside the containers, against the pinned lockfiles).

## Seed feature

Chat with voice input (mic → transcribe), voice output (read-aloud on
assistant replies), and image generation (image-mode toggle in the composer).
It exists to establish the patterns — replace it with whatever you're building.

## Using it as a template

The app name is config, not code:

- `PROJECT_NAME` in `.env` is the display name — API title, startup log,
  browser tab, docs site. `make dev` seeds it from the directory name, so
  renaming the clone's directory does most of the work. The DB name
  (`POSTGRES_DB`) and compose project name follow the directory the same way.
- What's left is prose: this README, `AGENTS.md`, `docs/`.
