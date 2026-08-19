# =============================================================================
# 文件: make/runtime.mk
# 作用: 封装运行时离线启动检查入口。
# 范围: 在已经启动的 app 容器中执行 offline_startup 检查，不连接数据库、模型网关或其他外部服务。
# 说明: 镜像构建阶段的无网络冒烟由 scripts/ci/common/docker-build-check.sh 负责，本文件不重复实现。
# =============================================================================

.PHONY: runtime-offline-check dev-offline-check

runtime-offline-check: ## 在当前 app 容器内执行离线启动检查。
	$(EXEC) python -m vet_agent.runtime.offline_startup

dev-offline-check: runtime-offline-check ## 兼容入口：在开发 app 容器内执行离线启动检查。
