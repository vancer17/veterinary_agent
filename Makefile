COMPOSE ?= docker compose -f docker-compose.dev.yml
EXEC ?= $(COMPOSE) exec -T app
APP_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(APP_PORT)

.PHONY: dev-build dev-up dev-up-no-wait dev-down dev-clean dev-restart dev-ps dev-logs dev-app-logs dev-db-logs dev-shell db-shell dev-migrate dev-seed dev-test dev-ready dev-url request-all request-curl request-health request-ready request-followup-first request-followup-second request-multitask request-safety-toxic request-idempotency request-profile-memory request-memory-read

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

dev-shell:
	$(COMPOSE) exec app sh

db-shell:
	$(COMPOSE) exec postgres sh -c 'psql -U "$${POSTGRES_USER:-vet_agent}" -d "$${POSTGRES_DB:-vet_agent}"'

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
