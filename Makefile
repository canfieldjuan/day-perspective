SHELL := /bin/bash

PROJECT_ROOT := $(CURDIR)
API_PYTHON ?= $(PROJECT_ROOT)/.venv/bin/python
DATABASE_URL ?= postgresql+psycopg://day_perspective:day_perspective@localhost:54329/day_perspective
TEST_DATABASE_URL ?= postgresql+psycopg://day_perspective:day_perspective@localhost:54329/day_perspective_test

.PHONY: help install web-install api-install db-up db-down db-reset local-storage-reset clean-reset db-migrate seed api-migrate api-seed ingest-usgs-fixture ingest-usgs-live ingest-usgs-dry-run review-usgs-fixture ingest-un-wpp-fixture ingest-un-wpp-live ingest-un-wpp-dry-run review-un-wpp-fixture ingest-ucdp-annual-fixture review-ucdp-annual-fixture ingest-ucdp-ged-fixture review-ucdp-ged-fixture ingest-wikidata-fixture ingest-wikidata-dry-run validate-golden-set reconcile-publications reconcile-publications-repair publish-context-year publish-context-resume publish-context-retry-failed rebuild-coverage golden-canary publish-golden api api-run api-test api-lint api-typecheck contracts-test contracts-lint contracts-typecheck web web-run web-test web-lint web-typecheck web-e2e web-e2e-full-stack web-build test-integration test-e2e build check audit verify

help:
	@printf '%s\n' 'Targets: install, db-up, db-down, db-reset, clean-reset, db-migrate, seed, ingest-usgs-fixture, ingest-usgs-live, ingest-usgs-dry-run, review-usgs-fixture, ingest-un-wpp-fixture, ingest-un-wpp-live, ingest-un-wpp-dry-run, review-un-wpp-fixture, ingest-ucdp-annual-fixture, review-ucdp-annual-fixture, ingest-ucdp-ged-fixture, review-ucdp-ged-fixture, ingest-wikidata-fixture, ingest-wikidata-dry-run, publish-golden, api, web, test-integration, test-e2e, build, check, verify'

install: web-install api-install

web-install:
	corepack pnpm install --frozen-lockfile

api-install:
	python -m venv .venv
	$(API_PYTHON) -m pip install --upgrade pip
	$(API_PYTHON) -m pip install -r "$(PROJECT_ROOT)/services/api/requirements.lock"
	$(API_PYTHON) -m pip install --no-deps -e "$(PROJECT_ROOT)/services/api"

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

db-reset:
	@printf '%s\n' 'Removing the local PostgreSQL volume and all local database data.'
	docker compose down -v

local-storage-reset:
	bash scripts/reset_local_dev.sh

clean-reset: db-reset local-storage-reset

api-migrate:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m alembic upgrade head

db-migrate: api-migrate

api-seed:
	DAY_PERSPECTIVE_ALLOW_TEST_FIXTURES=1 DATABASE_URL='$(DATABASE_URL)' bash scripts/seed_test_fixtures.sh

seed: api-seed

ingest-usgs-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.usgs_cli ingest --fixture '$(PROJECT_ROOT)/data/fixtures/usgs/1964-prince-william-sound.geojson'

ingest-usgs-live:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.usgs_cli ingest

ingest-usgs-dry-run:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.usgs_cli ingest --fixture '$(PROJECT_ROOT)/data/fixtures/usgs/1964-prince-william-sound.geojson' --dry-run

review-usgs-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m app.usgs_cli review

ingest-un-wpp-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.context_cli ingest-un-wpp --fixture '$(PROJECT_ROOT)/data/fixtures/un-wpp/wpp2024-world-1950-2025.csv'

ingest-un-wpp-live:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.context_cli ingest-un-wpp --live

ingest-un-wpp-dry-run:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.context_cli ingest-un-wpp --live --dry-run

review-un-wpp-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m app.context_cli review-un-wpp

ingest-ucdp-annual-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.context_cli ingest-ucdp-annual --fixture '$(PROJECT_ROOT)/data/fixtures/ucdp/ucdp-prio-26.1-conflicts-1964.csv'

review-ucdp-annual-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m app.context_cli review-ucdp-annual

ingest-ucdp-ged-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.context_cli ingest-ucdp-ged --fixture '$(PROJECT_ROOT)/data/fixtures/ucdp/ged-26.1-event-6833.csv'

review-ucdp-ged-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m app.context_cli review-ucdp-ged

validate-golden-set:
	$(API_PYTHON) -c "from pathlib import Path; from app.golden_set import validate_golden_set; print(validate_golden_set(Path('data/golden-set/golden-dates-v1.json')))"

ingest-wikidata-fixture:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.candidate_cli --fixture '$(PROJECT_ROOT)/data/fixtures/wikidata/Q749610.json'

ingest-wikidata-dry-run:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' RAW_SOURCE_ROOT='$(PROJECT_ROOT)/.local/raw-sources' $(API_PYTHON) -m app.candidate_cli --fixture '$(PROJECT_ROOT)/data/fixtures/wikidata/Q749610.json' --dry-run

publish-context-year:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.publish_cli publish-context --year $(YEAR)

publish-context-resume:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.publish_cli publish-context --resume

publish-context-retry-failed:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.publish_cli publish-context --retry-failed

golden-canary:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.publish_cli golden-canary $(ARGS)

rebuild-coverage:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.publish_cli rebuild-coverage

reconcile-publications:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.publish_cli reconcile

reconcile-publications-repair:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.publish_cli reconcile --repair

publish-golden:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' $(API_PYTHON) -m app.usgs_cli publish

api-run:
	cd services/api && DATABASE_URL='$(DATABASE_URL)' $(API_PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

api: api-run

api-test:
	TEST_DATABASE_URL='$(TEST_DATABASE_URL)' $(API_PYTHON) -m pytest -q services/api/tests

api-lint:
	$(API_PYTHON) -m ruff check services/api

api-typecheck:
	cd services/api && $(API_PYTHON) -m mypy

contracts-test:
	corepack pnpm --filter @day-perspective/contracts test

contracts-lint:
	corepack pnpm --filter @day-perspective/contracts lint

contracts-typecheck:
	corepack pnpm --filter @day-perspective/contracts typecheck

web-run:
	corepack pnpm --filter @day-perspective/web dev

web: web-run

web-test:
	corepack pnpm --filter @day-perspective/web test

web-lint:
	corepack pnpm --filter @day-perspective/web lint

web-typecheck:
	corepack pnpm --filter @day-perspective/web typecheck

web-e2e:
	corepack pnpm --filter @day-perspective/web test:e2e

web-e2e-full-stack:
	DATABASE_URL='$(DATABASE_URL)' PUBLISHED_PROFILE_ROOT='$(PROJECT_ROOT)/.local/published-profiles' API_PYTHON='$(API_PYTHON)' bash scripts/run_full_stack_e2e.sh

web-build:
	corepack pnpm --filter @day-perspective/web build

test-integration: api-test

test-e2e: web-e2e

build: web-build

check: contracts-lint contracts-typecheck contracts-test api-lint api-typecheck api-test web-lint web-typecheck web-test

audit:
	corepack pnpm audit --prod
	$(API_PYTHON) -m pip_audit

verify: check web-build web-e2e web-e2e-full-stack validate-golden-set audit
