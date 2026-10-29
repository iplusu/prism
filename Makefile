# Common tasks. Requires: uv, node/npm, git, bubblewrap, TeX Live (latexmk).
.PHONY: install dev serve test test-pg frontend-check build migrate doctor backup

install:
	cd backend && uv sync
	cd frontend && npm ci
	cd collab && npm ci

dev:            ## backend + worker + collab + Vite on http://localhost:5173
	./scripts/dev.sh

serve:          ## production-style run without Docker (needs .env)
	./scripts/serve.sh

build:
	cd frontend && npm run build

frontend-check:
	cd frontend && npm run typecheck && npm run build

test:           ## backend tests on SQLite (real TeX, bwrap, collab integration)
	cd backend && uv run pytest

test-pg:        ## same suite on PostgreSQL: make test-pg PG=postgresql+psycopg://user@host/db
	cd backend && PRISM_TEST_DATABASE_URL=$(PG) uv run pytest

migrate:
	cd backend && uv run alembic upgrade head

doctor:
	cd backend && uv run python -m app.cli doctor

backup:
	./scripts/backup.sh
