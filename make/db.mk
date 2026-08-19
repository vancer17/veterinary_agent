# =============================================================================
# 文件: make/db.mk
# 作用: 封装数据库相关的稳定入口。
# 范围: 提供开发数据库 shell，以及开发和生产迁移、扩展、seed 的别名索引。
# 说明: 具体执行仍复用 dev/prod 分类目标，避免维护第二套数据库流程。
# =============================================================================

.PHONY: db-shell dev-db-shell db-dev-extensions db-prod-extensions db-dev-migrate db-prod-migrate db-dev-seed db-prod-seed

db-shell: ## 进入开发 Postgres psql。
	$(COMPOSE) exec postgres sh -c 'psql -U "$${VET_AGENT_POSTGRES_USER:-vet_agent}" -d "$${VET_AGENT_POSTGRES_DB:-vet_agent}"'

dev-db-shell: db-shell ## 兼容入口：进入开发 Postgres psql。

db-dev-extensions: dev-db-extensions ## 兼容入口：初始化开发数据库扩展。

db-prod-extensions: prod-db-extensions ## 兼容入口：初始化生产数据库扩展。

db-dev-migrate: dev-migrate ## 兼容入口：执行开发 Alembic 迁移。

db-prod-migrate: prod-migrate ## 兼容入口：执行生产 Alembic 迁移。

db-dev-seed: dev-seed ## 兼容入口：执行开发 seed。

db-prod-seed: prod-seed ## 兼容入口：执行生产 seed。
