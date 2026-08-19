# =============================================================================
# 文件: make/dev.mk
# 作用: 封装本地开发环境操作入口。
# 范围: 覆盖开发镜像构建、容器启停、日志、交互 shell、迁移、seed、测试与就绪检查。
# 说明: 开发环境允许按 compose.dev.yml 现场构建镜像；生产环境入口请使用 make/prod.mk。
# =============================================================================

.PHONY: dev-build dev-build-core dev-build-guardrails dev-up dev-up-core dev-up-guardrails dev-up-no-wait dev-down dev-clean dev-restart dev-ps dev-logs dev-app-logs dev-db-logs dev-litellm-logs dev-mem0-logs dev-mem0-dashboard-logs dev-opa-logs dev-mem0-db-logs dev-shell dev-db-extensions dev-migrate dev-seed dev-test dev-ready dev-url

dev-build: ## 构建开发环境主应用、Mem0、Dashboard 与 OPA 镜像。
	VET_AGENT_IMAGE_VARIANT="$(VET_AGENT_IMAGE_VARIANT)" $(COMPOSE) build app mem0 mem0-dashboard opa

dev-build-core: ## 使用 core 变体构建开发环境镜像。
	@$(MAKE) --no-print-directory VET_AGENT_IMAGE_VARIANT=core dev-build

dev-build-guardrails: ## 使用 Guardrails 变体构建开发主应用及配套镜像。
	@$(MAKE) --no-print-directory VET_AGENT_IMAGE_VARIANT=guardrails dev-build

dev-up: ## 启动完整开发环境并等待服务就绪。
	VET_AGENT_IMAGE_VARIANT="$(VET_AGENT_IMAGE_VARIANT)" $(COMPOSE) up -d --build --wait
	@echo "Vet Agent dev API: $(BASE_URL)"

dev-up-core: ## 使用 core 变体启动完整开发环境。
	@$(MAKE) --no-print-directory VET_AGENT_IMAGE_VARIANT=core dev-up

dev-up-guardrails: ## 使用 Guardrails 变体启动完整开发环境。
	@$(MAKE) --no-print-directory VET_AGENT_IMAGE_VARIANT=guardrails dev-up

dev-up-no-wait: ## 启动完整开发环境但不等待服务就绪。
	VET_AGENT_IMAGE_VARIANT="$(VET_AGENT_IMAGE_VARIANT)" $(COMPOSE) up -d --build
	@echo "Vet Agent dev API: $(BASE_URL)"

dev-down: ## 停止开发环境容器。
	$(COMPOSE) down --remove-orphans

dev-clean: ## 停止开发环境并删除数据卷。
	$(COMPOSE) down -v --remove-orphans

dev-restart: ## 重启开发环境主应用容器。
	$(COMPOSE) restart app

dev-ps: ## 查看开发环境容器状态。
	$(COMPOSE) ps

dev-logs: ## 跟踪开发环境全部服务日志。
	$(COMPOSE) logs -f

dev-app-logs: ## 跟踪开发主应用日志。
	$(COMPOSE) logs -f app

dev-db-logs: ## 跟踪开发 Postgres 日志。
	$(COMPOSE) logs -f postgres

dev-litellm-logs: ## 跟踪开发 LiteLLM 日志。
	$(COMPOSE) logs -f litellm

dev-mem0-logs: ## 跟踪开发 Mem0 日志。
	$(COMPOSE) logs -f mem0

dev-mem0-dashboard-logs: ## 跟踪开发 Mem0 Dashboard 日志。
	$(COMPOSE) logs -f mem0-dashboard

dev-opa-logs: ## 跟踪开发 OPA 日志。
	$(COMPOSE) logs -f opa

dev-mem0-db-logs: ## 兼容入口：跟踪开发 Postgres 日志。
	$(COMPOSE) logs -f postgres

dev-shell: ## 进入开发主应用容器 shell。
	$(COMPOSE) exec app sh

dev-db-extensions: ## 初始化开发数据库扩展。
	$(COMPOSE) up -d --wait postgres
	$(COMPOSE) run --rm postgres-extensions

dev-migrate: ## 在开发主应用容器内执行 Alembic 迁移。
	$(EXEC) alembic upgrade head

dev-seed: ## 在开发主应用容器内执行默认 seed。
	$(EXEC) python scripts/seed_database.py

dev-test: ## 在开发主应用容器内执行 pytest。
	$(EXEC) pytest -q

dev-ready: ## 请求开发主应用就绪检查。
	$(EXEC) python scripts/dev_request.py ready

dev-url: ## 输出开发主应用访问地址。
	@echo "$(BASE_URL)"
