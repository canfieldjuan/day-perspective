SHELL := /bin/bash

PROJECT_ROOT := $(CURDIR)
API_PYTHON ?= $(PROJECT_ROOT)/.venv/bin/python
DATABASE_URL ?= postgresql+psycopg://day_perspective:day_perspective@localhost:54329/day_perspective
TEST_DATABASE_URL ?= postgresql+psycopg://day_perspective:day_perspective@localhost:54329/day_perspective_test

.PHONY: help install web-install api-install db-up db-down db-reset api-migrate api-seed api-run api-test api-lint api-typecheck contracts-test contracts-lint contracts-typecheck web-run web-test web-lint web-typecheck web-e2e web-build check

help:
	@printf '%s\n' 'Targets: install, db-up, db-down, db-reset, api-migrate, api-seed, api-run, api-test, api-lint, api-typecheck, contracts-test, contracts-lint, contracts-typecheck, web-run, web-test, web-lint, web-typecheck, web-e2e, web-build, check'

install: web-install api-install

web-install:
	corepack pnpm install --frozen-lockfile

api-install:
	python -m venv .venv
	$(API_PYTHON) -m pip install --upgrade pip
	$(API_PYTHON) -m pip install -e "$(PROJECT_ROOT)/services/api[dev]"

db-up:
	docker compose up -d db

db-down:
	docker compose down

db-reset:
	@printf '%s\n' 'Removing the local PostgreSQL volume and all local database data.'
	docker compose down -v

api-migrate:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m alembic upgrade head

api-seed:
	DAY_PERSPECTIVE_ALLOW_TEST_FIXTURES=1 DATABASE_URL='$(DATABASE_URL)' bash scripts/seed_test_fixtures.sh

api-run:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

api-test:
	TEST_DATABASE_URL='$(TEST_DATABASE_URL)' $(API_PYTHON) -m pytest -q services/api/tests

api-lint:
	$(API_PYTHON) -m ruff check services/api

api-typecheck:
	$(API_PYTHON) -m mypy services/api/app services/api/tests

contracts-test:
	corepack pnpm --filter @day-perspective/contracts test

contracts-lint:
	corepack pnpm --filter @day-perspective/contracts lint

contracts-typecheck:
	corepack pnpm --filter @day-perspective/contracts typecheck

web-run:
	corepack pnpm --filter @day-perspective/web dev

web-test:
	corepack pnpm --filter @day-perspective/web test

web-lint:
	corepack pnpm --filter @day-perspective/web lint

web-typecheck:
	corepack pnpm --filter @day-perspective/web typecheck

web-e2e:
	corepack pnpm --filter @day-perspective/web test:e2e

web-build:
	corepack pnpm --filter @day-perspective/web build

check: contracts-lint contracts-typecheck contracts-test api-lint api-typecheck api-test web-lint web-typecheck web-test
