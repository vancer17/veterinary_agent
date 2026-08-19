# =============================================================================
# 文件: make/docker.mk
# 作用: 提供 Docker Compose 原生命令的稳定索引入口。
# 范围: 覆盖开发与生产编排的配置渲染和容器状态查询。
# 说明: 本文件不替代 dev/prod 分类目标，只提供跨环境的低层 Compose 检查入口。
# =============================================================================

.PHONY: docker-dev-config docker-prod-config docker-dev-ps docker-prod-ps

docker-dev-config: ## 渲染开发 Docker Compose 配置。
	VET_AGENT_IMAGE_VARIANT="$(VET_AGENT_IMAGE_VARIANT)" $(COMPOSE) config

docker-prod-config: prod-config ## 兼容入口：渲染生产 Docker Compose 配置。

docker-dev-ps: dev-ps ## 兼容入口：查看开发容器状态。

docker-prod-ps: prod-ps ## 兼容入口：查看生产容器状态。
