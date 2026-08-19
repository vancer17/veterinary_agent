# =============================================================================
# 文件: make/ci.mk
# 作用: 封装 CI 门禁相关脚本入口。
# 范围: 覆盖全量门禁、通用门禁、仓库特色门禁、dry-run 与 try-run。
# 说明: 本文件只做 make 目标到 scripts/ci 的映射，不实现具体检查逻辑。
# =============================================================================

.PHONY: ci ci-common ci-repository ci-static ci-python ci-compose ci-secret-boundary ci-cd-layout ci-db ci-image ci-image-core ci-image-guardrails ci-image-variants ci-offline-image ci-dry-run ci-try-run ci-clinical-safety-api ci-memory-read-api ci-task-routing-api ci-answer-rag-api ci-response-generation-api ci-external-api

ci: ## 执行本地全量 CI 门禁。
	bash $(CI_SCRIPT_DIR)/run-all.sh

ci-common: ## 执行可分发到其他业务仓库的通用 CI 门禁。
	bash $(CI_SCRIPT_DIR)/common/run-common-gate.sh

ci-repository: ## 执行兽医 Agent 仓库特色 CI 门禁。
	bash $(CI_SCRIPT_DIR)/repository/run-repository-gate.sh

ci-static: ## 执行静态文件、脚本和基础仓库约束检查。
	bash $(CI_SCRIPT_DIR)/common/static-check.sh

ci-python: ## 执行 Python 快速检查。
	bash $(CI_SCRIPT_DIR)/common/python-check.sh

ci-compose: ## 执行 Docker Compose 配置解析检查。
	bash $(CI_SCRIPT_DIR)/common/compose-config-check.sh

ci-secret-boundary: ## 执行密钥边界检查。
	bash $(CI_SCRIPT_DIR)/common/secret-boundary-check.sh

ci-cd-layout: ## 执行 CD 文件树与部署约束检查。
	bash $(CI_SCRIPT_DIR)/repository/cd-layout-check.sh

ci-db: ## 执行数据库迁移链路检查。
	bash $(CI_SCRIPT_DIR)/repository/database-migration-check.sh

ci-image: ci-image-core ## 执行默认 core 应用镜像构建检查。

ci-image-core: ## 构建 core 应用镜像并执行离线启动冒烟。
	CI_BUILD_APP_IMAGE=true \
	CI_BUILD_APP_GUARDRAILS_IMAGE=false \
	CI_RUN_APP_OFFLINE_SMOKE="$(CI_RUN_APP_OFFLINE_SMOKE)" \
	bash $(CI_SCRIPT_DIR)/common/docker-build-check.sh

ci-image-guardrails: ## 构建 Guardrails 应用镜像并执行离线启动冒烟。
	CI_BUILD_APP_IMAGE=false \
	CI_BUILD_APP_GUARDRAILS_IMAGE=true \
	CI_RUN_APP_OFFLINE_SMOKE="$(CI_RUN_APP_OFFLINE_SMOKE)" \
	bash $(CI_SCRIPT_DIR)/common/docker-build-check.sh

ci-image-variants: ## 同时构建 core 与 Guardrails 应用镜像并执行离线启动冒烟。
	CI_BUILD_APP_IMAGE=true \
	CI_BUILD_APP_GUARDRAILS_IMAGE=true \
	CI_RUN_APP_OFFLINE_SMOKE="$(CI_RUN_APP_OFFLINE_SMOKE)" \
	bash $(CI_SCRIPT_DIR)/common/docker-build-check.sh

ci-offline-image: ci-image-core ## 构建 core 镜像并执行无网络命名空间冒烟。

ci-dry-run: ## 执行不启动真实依赖的 dry-run 门禁。
	CI_TRY_RUN_SCOPE=dry-run bash $(CI_SCRIPT_DIR)/try-run.sh

ci-try-run: ## 按 CI_TRY_RUN_SCOPE 执行手动 try-run 门禁。
	CI_TRY_RUN_SCOPE="$(CI_TRY_RUN_SCOPE)" bash $(CI_SCRIPT_DIR)/try-run.sh

ci-clinical-safety-api: smoke-clinical-safety-api ## 兼容入口：执行临床安全真实依赖冒烟。

ci-memory-read-api: smoke-memory-read-api ## 兼容入口：执行记忆读取真实依赖冒烟。

ci-task-routing-api: smoke-task-routing-api ## 兼容入口：执行任务拆分真实依赖冒烟。

ci-answer-rag-api: smoke-answer-rag-api ## 兼容入口：执行回答 RAG 真实依赖冒烟。

ci-response-generation-api: smoke-response-generation-api ## 兼容入口：执行回复生成真实依赖冒烟。

ci-external-api: smoke-external-api ## 兼容入口：执行外部真实依赖聚合冒烟。
