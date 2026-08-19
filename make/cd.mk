# =============================================================================
# 文件: make/cd.mk
# 作用: 封装 CD 发布与部署相关脚本入口。
# 范围: 覆盖 Release 校验、镜像解析、镜像构建推送、生产包同步、生产部署和健康检查。
# 说明: 生产环境只拉取预编译镜像；具体 SSH、登录和部署逻辑由 scripts/cd 承载。
# =============================================================================

.PHONY: cd-verify-release cd-resolve-images cd-build-images cd-build-images-guardrails cd-sync-production cd-deploy-production cd-health-check

cd-verify-release: ## 校验 GitHub Release 标签输入。
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" bash $(CD_SCRIPT_DIR)/common/verify-release-input.sh

cd-resolve-images: ## 解析 Release 标签对应的镜像地址。
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" bash $(CD_SCRIPT_DIR)/common/resolve-release-images.sh

cd-build-images: ## 构建并推送 Release 对应的预编译镜像。
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" \
	CD_BUILD_APP_GUARDRAILS_IMAGE=false \
	bash $(CD_SCRIPT_DIR)/common/build-and-push-images.sh

cd-build-images-guardrails: ## 在 Release 发布中额外构建并推送 Guardrails 应用镜像。
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" \
	CD_BUILD_APP_GUARDRAILS_IMAGE=true \
	bash $(CD_SCRIPT_DIR)/common/build-and-push-images.sh

cd-sync-production: ## 同步生产部署包到目标服务器。
	bash $(CD_SCRIPT_DIR)/repository/sync-production-bundle.sh

cd-deploy-production: ## 部署指定 Release 到生产环境。
	CD_RELEASE_TAG="$(CD_RELEASE_TAG)" bash $(CD_SCRIPT_DIR)/repository/deploy-production.sh

cd-health-check: ## 执行生产 HTTP 健康检查。
	bash $(CD_SCRIPT_DIR)/repository/health-check.sh
