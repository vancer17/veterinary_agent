# =============================================================================
# 文件: Makefile
# 作用: 统一封装兽医 Agent 的本地开发、生产操作与 CI 门禁入口。
# 范围: 覆盖 Docker Compose、Alembic、seed、冒烟请求、CI 与 CD 脚本编排。
# 说明: 容器编排与 env 模板统一收束到 docker 目录；生产发布镜像由 GitHub Release 标签确定。
# =============================================================================

DEV_ENV_FILE ?= $(if $(wildcard docker/compose.dev.env),docker/compose.dev.env,docker/compose.dev.env.template)
PROD_ENV_FILE ?= $(if $(wildcard docker/compose.prod.env),docker/compose.prod.env,docker/compose.prod.env.template)
DEV_COMPOSE ?= docker compose --env-file $(DEV_ENV_FILE) -f docker/compose.dev.yml
PROD_COMPOSE ?= docker compose --env-file $(PROD_ENV_FILE) -f docker/compose.yml
PROD_IMAGE_ENV = VET_AGENT_IMAGE="$${VET_AGENT_IMAGE:?请先导出 VET_AGENT_IMAGE}" MEM0_IMAGE="$${MEM0_IMAGE:?请先导出 MEM0_IMAGE}" MEM0_DASHBOARD_IMAGE="$${MEM0_DASHBOARD_IMAGE:?请先导出 MEM0_DASHBOARD_IMAGE}" OPA_IMAGE="$${OPA_IMAGE:?请先导出 OPA_IMAGE}"
PROD_COMPOSE_CMD = $(PROD_IMAGE_ENV) $(PROD_COMPOSE)
COMPOSE ?= $(DEV_COMPOSE)
EXEC ?= $(COMPOSE) exec -T app
PROD_EXEC ?= $(PROD_COMPOSE_CMD) exec -T app
APP_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(APP_PORT)
BUSINESS_RUN_ID ?=
BUSINESS_RUN_ARG = $(if $(strip $(BUSINESS_RUN_ID)),--run-id "$(BUSINESS_RUN_ID)",)
CI_SCRIPT_DIR ?= scripts/ci
CI_TRY_RUN_SCOPE ?= full
CD_SCRIPT_DIR ?= scripts/cd
CD_RELEASE_TAG ?=

.PHONY: ci ci-common ci-repository ci-static ci-python ci-compose ci-secret-boundary ci-cd-layout ci-db ci-image ci-dry-run ci-try-run cd-verify-release cd-resolve-images cd-build-images cd-deploy-production cd-sync-production cd-health-check dev-build dev-up dev-up-no-wait dev-down dev-clean dev-restart dev-ps dev-logs dev-app-logs dev-db-logs dev-litellm-logs dev-mem0-logs dev-mem0-dashboard-logs dev-opa-logs dev-mem0-db-logs dev-shell db-shell dev-db-extensions dev-migrate dev-seed dev-test dev-ready dev-url prod-config prod-pull prod-db-extensions prod-deps prod-migrate prod-seed prod-up prod-mem0-dashboard-up prod-opa-up prod-restart prod-down prod-clean prod-ps prod-logs prod-app-logs prod-litellm-logs prod-mem0-logs prod-mem0-dashboard-logs prod-opa-logs prod-mem0-db-logs prod-ready prod-shell prod-db-shell request-all request-curl request-health request-ready request-followup-first request-followup-second request-multitask request-safety-toxic request-idempotency request-profile-memory request-memory-read request-report-parse request-rag-stats request-rag-chunks request-business-all request-business-followup-first request-business-followup-second request-business-multitask request-business-memory request-business-safety-semantic request-business-stream

ci:
	bash $(CI_SCRIPT_DIR)/run-all.sh

ci-common:
	bash $(CI_SCRIPT_DIR)/common/run-common-gate.sh

ci-repository:
	bash $(CI_SCRIPT_DIR)/repository/run-repository-gate.sh

ci-static:
	bash $(CI_SCRIPT_DIR)/common/static-check.sh

ci-python:
	bash $(CI_SCRIPT_DIR)/common/python-check.sh

ci-compose:
	bash $(CI_SCRIPT_DIR)/common/compose-config-check.sh

ci-secret-boundary:
	bash $(CI_SCRIPT_DIR)/common/secret-boundary-check.sh

ci-cd-layout:
	bash $(CI_SCRIPT_DIR)/repository/cd-layout-check.sh

ci-db:
	bash $(CI_SCRIPT_DIR)/repository/database-migration-check.sh

ci-image:
	bash $(CI_SCRIPT_DIR)/common/docker-build-check.sh

ci-dry-run:
	CI_TRY_RUN_SCOPE=dry-run bash $(CI_SCRIPT_DIR)/try-run.sh

ci-try-run:
	CI_TRY_RUN_SCOPE="$(CI_TRY_RUN_SCOPE)" bash $(CI_SCRIPT_DIR)/try-run.sh

cd-verify-release:
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" bash $(CD_SCRIPT_DIR)/common/verify-release-input.sh

cd-resolve-images:
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" bash $(CD_SCRIPT_DIR)/common/resolve-release-images.sh

cd-build-images:
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" bash $(CD_SCRIPT_DIR)/common/build-and-push-images.sh

cd-sync-production:
	bash $(CD_SCRIPT_DIR)/repository/sync-production-bundle.sh

cd-deploy-production:
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" bash $(CD_SCRIPT_DIR)/repository/deploy-production.sh

cd-health-check:
	bash $(CD_SCRIPT_DIR)/repository/health-check.sh

dev-build:
	$(COMPOSE) build app mem0 mem0-dashboard opa

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

dev-mem0-dashboard-logs:
	$(COMPOSE) logs -f mem0-dashboard

dev-opa-logs:
	$(COMPOSE) logs -f opa

dev-mem0-db-logs:
	$(COMPOSE) logs -f postgres

dev-shell:
	$(COMPOSE) exec app sh

db-shell:
	$(COMPOSE) exec postgres sh -c 'psql -U "$${VET_AGENT_POSTGRES_USER:-vet_agent}" -d "$${VET_AGENT_POSTGRES_DB:-vet_agent}"'

dev-db-extensions:
	$(COMPOSE) up -d --wait postgres
	$(COMPOSE) run --rm postgres-extensions

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
	$(PROD_COMPOSE_CMD) config

prod-pull:
	$(PROD_COMPOSE_CMD) pull app mem0 mem0-dashboard opa

prod-db-extensions:
	$(PROD_COMPOSE_CMD) up -d --no-build --pull missing --wait postgres
	$(PROD_COMPOSE_CMD) run --rm postgres-extensions

prod-deps: prod-db-extensions
	$(PROD_COMPOSE_CMD) up -d --no-build --pull missing --wait postgres litellm mem0 opa

prod-migrate: prod-db-extensions
	$(PROD_COMPOSE_CMD) run --rm --pull never migrate

prod-seed:
	$(PROD_COMPOSE_CMD) run --rm --pull never seed

prod-up: prod-pull prod-deps prod-migrate
	$(PROD_COMPOSE_CMD) up -d --no-build --pull never --no-deps --wait app
	@echo "Vet Agent prod API: $(BASE_URL)"

prod-mem0-dashboard-up:
	$(PROD_COMPOSE_CMD) --profile ops up -d --no-build --pull never --wait mem0-dashboard

prod-opa-up:
	$(PROD_COMPOSE_CMD) up -d --no-build --pull never --wait opa

prod-restart:
	$(PROD_COMPOSE_CMD) restart app

prod-down:
	$(PROD_COMPOSE_CMD) down --remove-orphans

prod-clean:
	$(PROD_COMPOSE_CMD) down -v --remove-orphans

prod-ps:
	$(PROD_COMPOSE_CMD) ps

prod-logs:
	$(PROD_COMPOSE_CMD) logs -f

prod-app-logs:
	$(PROD_COMPOSE_CMD) logs -f app

prod-litellm-logs:
	$(PROD_COMPOSE_CMD) logs -f litellm

prod-mem0-logs:
	$(PROD_COMPOSE_CMD) logs -f mem0

prod-mem0-dashboard-logs:
	$(PROD_COMPOSE_CMD) logs -f mem0-dashboard

prod-opa-logs:
	$(PROD_COMPOSE_CMD) logs -f opa

prod-mem0-db-logs:
	$(PROD_COMPOSE_CMD) logs -f postgres

prod-ready:
	$(PROD_EXEC) python scripts/dev_request.py ready

prod-shell:
	$(PROD_COMPOSE_CMD) exec app sh

prod-db-shell:
	$(PROD_COMPOSE_CMD) exec postgres sh -c 'psql -U "$${VET_AGENT_POSTGRES_USER:-vet_agent}" -d "$${VET_AGENT_POSTGRES_DB:-vet_agent}"'

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
