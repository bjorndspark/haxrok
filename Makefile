.PHONY: help infra infra-down infra-logs db dev build install migrate revision \
	test test-backend test-frontend \
	lint lint-ruff lint-ruff-format lint-mypy lint-eslint lint-tsc lint-prettier format \
	down logs clean purge issues fix-issue docs docs-serve eval-app

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  infra      start shared PostgreSQL + Redis (once per machine)"
	@echo "  infra-down stop the shared infra containers"
	@echo "  infra-logs tail shared infra logs"
	@echo "  db         create this copy's database in shared postgres"
	@echo "  db-backup  dump the DB to data/backups/<db>-<timestamp>.sql.gz"
	@echo "  db-restore drop + recreate the DB from a dump: make db-restore f=<file>"
	@echo "  dev        start backend + frontend (hot reload); starts infra + creates the DB"
	@echo "  build      build app images without starting containers"
	@echo "  install    sync backend (uv) and frontend (pnpm) deps in containers"
	@echo "  migrate    run alembic migrations"
	@echo "  revision   autogenerate a new alembic migration: make revision m='add x'"
	@echo "  test       run backend (pytest) + frontend (vitest) tests, in parallel"
	@echo "  lint       run linters + format checks (ruff, mypy, eslint, tsc, prettier), in parallel"
	@echo "             single checks: test-backend, test-frontend, lint-ruff,"
	@echo "             lint-ruff-format, lint-mypy, lint-eslint, lint-tsc, lint-prettier"
	@echo "  format     rewrite files in place (ruff format + prettier)"
	@echo "  down       stop app containers"
	@echo "  logs       tail app logs"
	@echo "  docs       regenerate docs/architecture/ (db/schema.md, db/er-diagram.md, api/routes.md)"
	@echo "  docs-serve browse docs/ with mkdocs at http://localhost:\$$DOCS_HOST_PORT"
	@echo "  eval-app   run an eval case through the app's debug prompts: make eval-app case=rust-board-fb-swapped [mode=review]"
	@echo "  issues     list open GitHub issues"
	@echo "  fix-issue  hand an issue to an AI agent: make fix-issue n=123 [agent=devin]"
	@echo "  clean      remove dev artifacts (logs, caches, build output; DB untouched)"
	@echo "  purge      clean + remove app containers + volumes (shared-infra DB survives)"
	@echo ""
	@echo "Dependency changes go through the container wrappers so installs resolve"
	@echo "against the pinned lockfile and the container's toolchain, not the host's:"
	@echo "  ./scripts/docker/uv.sh add <pkg>     ./scripts/docker/pnpm.sh add <pkg>"

.env:
	@./scripts/setup/env.sh

infra: .env
	docker compose -f docker-compose.infra.yml up -d

infra-down:
	docker compose -f docker-compose.infra.yml down

infra-logs:
	docker compose -f docker-compose.infra.yml logs -f

db: infra
	@./scripts/db/create.sh

db-backup: infra
	@./scripts/db/backup.sh

db-restore:
	@./scripts/db/restore.sh $(f)

dev: db
	@# Pre-create log/data dirs host-owned so the container (root) doesn't
	@# leave root-owned files un-deletable from the host.
	@mkdir -p logs/backend logs/frontend data
	docker compose up

build: .env
	docker compose build

install:
	docker compose exec backend uv sync --frozen --extra dev
	docker compose exec frontend pnpm install --frozen-lockfile

migrate:
	docker compose exec backend uv run alembic upgrade head

revision:
	docker compose exec backend uv run alembic revision --autogenerate -m "$(m)"

# test/lint fan out into a recursive `make -j` so backend and frontend checks
# run in parallel; --output-sync keeps each check's output grouped.
test:
	@$(MAKE) -j --output-sync=target --no-print-directory test-backend test-frontend

# uv run --no-sync: skips uv's per-invocation env check + bytecode recompile
# (~0.4s). The venv is synced by docker-entrypoint.sh on container start and
# by `make install` / scripts/docker/uv.sh for dep changes.
test-backend:
	docker compose exec backend uv run --no-sync pytest

test-frontend:
	docker compose exec frontend pnpm test

lint:
	@$(MAKE) -j --output-sync=target --no-print-directory \
		lint-ruff lint-ruff-format lint-mypy lint-eslint lint-tsc lint-prettier

lint-ruff:
	docker compose exec backend uv run --no-sync ruff check .

lint-ruff-format:
	docker compose exec backend uv run --no-sync ruff format --check .

lint-mypy:
	docker compose exec backend uv run --no-sync mypy app

lint-eslint:
	docker compose exec frontend pnpm lint

lint-tsc:
	docker compose exec frontend pnpm exec tsc -b

lint-prettier:
	docker compose exec frontend pnpm format:check

format:
	docker compose exec backend uv run --no-sync ruff format .
	docker compose exec frontend pnpm format

# --user: files written to the mounted docs/ keep host ownership instead of
# landing root-owned. .venv/bin/python skips uv's bookkeeping entirely.
docs:
	docker compose exec --user $(shell id -u):$(shell id -g) -e LOG_FILE= \
		backend .venv/bin/python /app/scripts/docs/generate.py

# One eval case through the app's two debug prompts (evals/app_prompts.py).
# --user keeps results host-owned; LOG_FILE= leaves the server's log alone.
eval-app:
	docker compose exec --user $(shell id -u):$(shell id -g) -e LOG_FILE= \
		backend .venv/bin/python /evals/app_prompts.py --case $(or $(case),rust-board-fb-swapped) --mode $(or $(mode),debug)

# `compose run` (not exec): exec can't publish a port. ENVIRONMENT= skips the
# entrypoint's dep sync and alembic step — docs don't need the DB. The run
# container starts from the image (no dev extras), so `uv run --extra dev`
# installs mkdocs from the lockfile first (fast: the uv cache is a volume).
docs-serve: .env
	@port="$$(grep -E '^DOCS_HOST_PORT=' .env | cut -d= -f2)"; \
	port="$${port:-8001}"; \
	echo "==> mkdocs serving docs/ at http://localhost:$$port"; \
	docker compose run --rm -e ENVIRONMENT= -p "$$port:8000" backend \
		uv run --frozen --extra dev mkdocs serve -a 0.0.0.0:8000 -f /mkdocs.yml

down:
	docker compose down

logs:
	docker compose logs -f

issues:
	@gh issue list

fix-issue:
	@./scripts/agent/fix-issue.sh $(n) $(agent)

clean:
	@echo "==> Cleaning dev artifacts (DB and containers untouched)..."
	@rm -rf logs/backend/* logs/frontend/* 2>/dev/null || true
	@find backend \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -f backend/test.db 2>/dev/null || true
	@rm -rf frontend/dist frontend/node_modules/.vite 2>/dev/null || true
	@# archive/ subdirs are created by the container (root-owned) and can't be
	@# removed from the host; surface them instead of silently leaving them.
	@if find logs/backend logs/frontend -mindepth 1 ! -name '.gitkeep' -print -quit 2>/dev/null | grep -q .; then \
		echo "    Root-owned files remain in logs/ or data/."; \
		echo "    One-time cleanup: docker compose run --rm backend rm -rf /app/logs /app/data"; \
	fi
	@echo "==> Done. Use 'make purge' to also remove app containers + volumes."

# Destructive: artifacts (clean) + app containers + named volumes. The project
# database lives in shared infra and survives; drop it by hand if needed:
#   docker compose -f docker-compose.infra.yml exec postgres psql -U postgres \
#     -c "DROP DATABASE \"$$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)\""
purge: clean
	docker compose down -v
