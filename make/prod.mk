# =============================================================================
# 文件: make/prod.mk
# 作用: 封装生产或预生产环境操作入口。
# 范围: 覆盖生产编排解析、镜像拉取、依赖启动、迁移、seed、服务启停、日志与就绪检查。
# 说明: 生产入口必须使用预编译镜像，禁止在生产环境现场构建镜像。
# =============================================================================

.PHONY: prod-config prod-pull prod-db-extensions prod-deps prod-migrate prod-seed prod-up prod-mem0-dashboard-up prod-opa-up prod-restart prod-down prod-clean prod-ps prod-logs prod-app-logs prod-litellm-logs prod-mem0-logs prod-mem0-dashboard-logs prod-opa-logs prod-mem0-db-logs prod-ready prod-shell prod-db-shell

prod-config: ## 渲染生产 Docker Compose 配置。
	$(PROD_COMPOSE_CMD) config

prod-pull: ## 拉取生产环境所需预编译镜像。
	$(PROD_COMPOSE_CMD) pull app worker mem0 mem0-dashboard opa

prod-db-extensions: ## 初始化生产数据库扩展。
	$(PROD_COMPOSE_CMD) up -d --no-build --pull missing --wait postgres
	$(PROD_COMPOSE_CMD) run --rm postgres-extensions

prod-deps: prod-db-extensions ## 启动生产主应用依赖服务。
	$(PROD_COMPOSE_CMD) up -d --no-build --pull missing --wait postgres litellm mem0 opa

prod-migrate: prod-db-extensions ## 执行生产 Alembic 迁移。
	$(PROD_COMPOSE_CMD) run --rm --pull never migrate

prod-seed: ## 执行生产 seed。
	$(PROD_COMPOSE_CMD) run --rm --pull never seed

prod-up: prod-pull prod-deps prod-migrate ## 启动生产 API 与后台 worker。
	$(PROD_COMPOSE_CMD) up -d --no-build --pull never --no-deps --wait app worker
	@echo "Vet Agent prod API: $(BASE_URL)"

prod-mem0-dashboard-up: ## 启动生产 Mem0 Dashboard 运维服务。
	$(PROD_COMPOSE_CMD) --profile ops up -d --no-build --pull never --wait mem0-dashboard

prod-opa-up: ## 启动生产 OPA 服务。
	$(PROD_COMPOSE_CMD) up -d --no-build --pull never --wait opa

prod-restart: ## 重启生产 API 与后台 worker。
	$(PROD_COMPOSE_CMD) restart app worker

prod-down: ## 停止生产环境容器。
	$(PROD_COMPOSE_CMD) down --remove-orphans

prod-clean: ## 停止生产环境并删除数据卷。
	$(PROD_COMPOSE_CMD) down -v --remove-orphans

prod-ps: ## 查看生产环境容器状态。
	$(PROD_COMPOSE_CMD) ps

prod-logs: ## 跟踪生产环境全部服务日志。
	$(PROD_COMPOSE_CMD) logs -f

prod-app-logs: ## 跟踪生产主应用日志。
	$(PROD_COMPOSE_CMD) logs -f app

prod-litellm-logs: ## 跟踪生产 LiteLLM 日志。
	$(PROD_COMPOSE_CMD) logs -f litellm

prod-mem0-logs: ## 跟踪生产 Mem0 日志。
	$(PROD_COMPOSE_CMD) logs -f mem0

prod-mem0-dashboard-logs: ## 跟踪生产 Mem0 Dashboard 日志。
	$(PROD_COMPOSE_CMD) logs -f mem0-dashboard

prod-opa-logs: ## 跟踪生产 OPA 日志。
	$(PROD_COMPOSE_CMD) logs -f opa

prod-mem0-db-logs: ## 兼容入口：跟踪生产 Postgres 日志。
	$(PROD_COMPOSE_CMD) logs -f postgres

prod-ready: ## 请求生产主应用就绪检查。
	$(PROD_EXEC) python scripts/dev_request.py ready

prod-shell: ## 进入生产主应用容器 shell。
	$(PROD_COMPOSE_CMD) exec app sh

prod-db-shell: ## 进入生产 Postgres psql。
	$(PROD_COMPOSE_CMD) exec postgres sh -c 'psql -U "$${VET_AGENT_POSTGRES_USER:-vet_agent}" -d "$${VET_AGENT_POSTGRES_DB:-vet_agent}"'
