# File: Makefile
# Purpose: Standard local and production operations for Vet Agent.
# Scope: Wraps Docker Compose, Alembic, seed jobs, and smoke request helpers.
# Notes: Production and development compose files use one PostgreSQL container
#        with logical databases for app, LiteLLM, and Mem0. Compose 插值变量
#        来自 deploy/env/*/compose.env*，容器运行时变量来自 services/*.env*。

DEV_ENV_FILE ?= $(if $(wildcard deploy/env/dev/compose.env),deploy/env/dev/compose.env,deploy/env/dev/compose.env.template)
PROD_ENV_FILE ?= $(if $(wildcard deploy/env/prod/compose.env),deploy/env/prod/compose.env,deploy/env/prod/compose.env.template)
DEV_COMPOSE ?= docker compose --env-file $(DEV_ENV_FILE) -f docker-compose.dev.yml
PROD_COMPOSE ?= docker compose --env-file $(PROD_ENV_FILE) -f docker-compose.yml
COMPOSE ?= $(DEV_COMPOSE)
EXEC ?= $(COMPOSE) exec -T app
PROD_EXEC ?= $(PROD_COMPOSE) exec -T app
APP_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(APP_PORT)
BUSINESS_RUN_ID ?=
BUSINESS_RUN_ARG = $(if $(strip $(BUSINESS_RUN_ID)),--run-id "$(BUSINESS_RUN_ID)",)

.PHONY: dev-build dev-up dev-up-no-wait dev-down dev-clean dev-restart dev-ps dev-logs dev-app-logs dev-db-logs dev-litellm-logs dev-mem0-logs dev-mem0-db-logs dev-shell db-shell dev-migrate dev-seed dev-test dev-ready dev-url prod-config prod-build prod-deps prod-migrate prod-seed prod-up prod-restart prod-down prod-clean prod-ps prod-logs prod-app-logs prod-litellm-logs prod-mem0-logs prod-mem0-db-logs prod-ready prod-shell prod-db-shell request-all request-curl request-health request-ready request-followup-first request-followup-second request-multitask request-safety-toxic request-idempotency request-profile-memory request-memory-read request-report-parse request-rag-stats request-rag-chunks request-business-all request-business-followup-first request-business-followup-second request-business-multitask request-business-memory request-business-safety-semantic request-business-stream

dev-build:
	$(COMPOSE) build app

dev-up:
	$(COMPOSE) up -d --build --wait
	@echo "Vet Agent dev API: $(BASE_URL)"

dev-up-no-wait:
	$(COMPOSE) up -d --build
	@echo "Vet Agent dev API: $(BASE_URL)"

dev-down:
	$(COMPOSE) down --remove-orphans

dev-clean:
	$(COMPOSE) down -v --remove-orphans

dev-restart:
	$(COMPOSE) restart app

dev-ps:
	$(COMPOSE) ps

dev-logs:
	$(COMPOSE) logs -f

dev-app-logs:
	$(COMPOSE) logs -f app

dev-db-logs:
	$(COMPOSE) logs -f postgres

dev-litellm-logs:
	$(COMPOSE) logs -f litellm

dev-mem0-logs:
	$(COMPOSE) logs -f mem0

dev-mem0-db-logs:
	$(COMPOSE) logs -f postgres

dev-shell:
	$(COMPOSE) exec app sh

db-shell:
	$(COMPOSE) exec postgres sh -c 'psql -U "$${VET_AGENT_POSTGRES_USER:-vet_agent}" -d "$${VET_AGENT_POSTGRES_DB:-vet_agent}"'

dev-migrate:
	$(EXEC) alembic upgrade head

dev-seed:
	$(EXEC) python scripts/seed_database.py

dev-test:
	$(EXEC) pytest -q

dev-ready:
	$(EXEC) python scripts/dev_request.py ready

dev-url:
	@echo "$(BASE_URL)"

prod-config:
	$(PROD_COMPOSE) config

prod-build:
	$(PROD_COMPOSE) build app

prod-deps:
	$(PROD_COMPOSE) up -d --wait postgres litellm mem0

prod-migrate:
	$(PROD_COMPOSE) run --rm migrate

prod-seed:
	$(PROD_COMPOSE) run --rm seed

prod-up: prod-build prod-deps prod-migrate prod-seed
	$(PROD_COMPOSE) up -d --no-deps --wait app
	@echo "Vet Agent prod API: $(BASE_URL)"

prod-restart:
	$(PROD_COMPOSE) restart app

prod-down:
	$(PROD_COMPOSE) down --remove-orphans

prod-clean:
	$(PROD_COMPOSE) down -v --remove-orphans

prod-ps:
	$(PROD_COMPOSE) ps

prod-logs:
	$(PROD_COMPOSE) logs -f

prod-app-logs:
	$(PROD_COMPOSE) logs -f app

prod-litellm-logs:
	$(PROD_COMPOSE) logs -f litellm

prod-mem0-logs:
	$(PROD_COMPOSE) logs -f mem0

prod-mem0-db-logs:
	$(PROD_COMPOSE) logs -f postgres

prod-ready:
	$(PROD_EXEC) python scripts/dev_request.py ready

prod-shell:
	$(PROD_COMPOSE) exec app sh

prod-db-shell:
	$(PROD_COMPOSE) exec postgres sh -c 'psql -U "$${VET_AGENT_POSTGRES_USER:-vet_agent}" -d "$${VET_AGENT_POSTGRES_DB:-vet_agent}"'

request-all:
	$(EXEC) python scripts/dev_request.py all

request-curl:
	$(EXEC) python scripts/dev_request.py print-curl --base-url "$(BASE_URL)"

request-health:
	$(EXEC) python scripts/dev_request.py health

request-ready:
	$(EXEC) python scripts/dev_request.py ready

request-followup-first:
	$(EXEC) python scripts/dev_request.py followup-first

request-followup-second:
	$(EXEC) python scripts/dev_request.py followup-second

request-multitask:
	$(EXEC) python scripts/dev_request.py multitask

request-safety-toxic:
	$(EXEC) python scripts/dev_request.py safety-toxic

request-idempotency:
	$(EXEC) python scripts/dev_request.py idempotency

request-profile-memory:
	$(EXEC) python scripts/dev_request.py profile-memory

request-memory-read:
	$(EXEC) python scripts/dev_request.py memory-read

request-report-parse:
	$(EXEC) python scripts/dev_request.py report-parse

request-rag-stats:
	$(EXEC) python scripts/dev_request.py rag-stats

request-rag-chunks:
	$(EXEC) python scripts/dev_request.py rag-chunks

request-business-all:
	$(EXEC) python scripts/dev_request.py business-all $(BUSINESS_RUN_ARG)

request-business-followup-first:
	$(EXEC) python scripts/dev_request.py business-followup-first $(BUSINESS_RUN_ARG)

request-business-followup-second:
	$(EXEC) python scripts/dev_request.py business-followup-second $(BUSINESS_RUN_ARG)

request-business-multitask:
	$(EXEC) python scripts/dev_request.py business-multitask $(BUSINESS_RUN_ARG)

request-business-memory:
	$(EXEC) python scripts/dev_request.py business-memory $(BUSINESS_RUN_ARG)

request-business-safety-semantic:
	$(EXEC) python scripts/dev_request.py business-safety-semantic $(BUSINESS_RUN_ARG)

request-business-stream:
	$(EXEC) python scripts/dev_request.py business-stream $(BUSINESS_RUN_ARG)
