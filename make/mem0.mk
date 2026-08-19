# =============================================================================
# 文件: make/mem0.mk
# 作用: 封装自托管 Mem0 及其运维 Dashboard 的专项入口。
# 范围: 覆盖开发镜像构建、配置渲染、启动、日志和生产日志查看。
# 说明: 生产环境不提供现场构建入口，生产镜像应由 CD Runner 预编译并推送。
# =============================================================================

.PHONY: mem0-build mem0-dashboard-build mem0-config mem0-up mem0-dashboard-up mem0-logs mem0-dashboard-logs mem0-prod-logs mem0-dashboard-prod-logs

mem0-build: ## 构建开发自托管 Mem0 镜像。
	$(COMPOSE) build mem0

mem0-dashboard-build: ## 构建开发 Mem0 Dashboard 镜像。
	$(COMPOSE) build mem0-dashboard

mem0-config: ## 渲染开发 Mem0 相关 Compose 配置。
	$(COMPOSE) config mem0 mem0-dashboard

mem0-up: ## 启动开发 Mem0 服务。
	$(COMPOSE) up -d --build --wait mem0

mem0-dashboard-up: ## 启动开发 Mem0 Dashboard 服务。
	$(COMPOSE) up -d --build --wait mem0-dashboard

mem0-logs: dev-mem0-logs ## 兼容入口：跟踪开发 Mem0 日志。

mem0-dashboard-logs: dev-mem0-dashboard-logs ## 兼容入口：跟踪开发 Mem0 Dashboard 日志。

mem0-prod-logs: prod-mem0-logs ## 兼容入口：跟踪生产 Mem0 日志。

mem0-dashboard-prod-logs: prod-mem0-dashboard-logs ## 兼容入口：跟踪生产 Mem0 Dashboard 日志。
