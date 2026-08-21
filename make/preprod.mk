# =============================================================================
# 文件: make/preprod.mk
# 作用: 封装临床安全预发布环境部署与回滚入口。
# 范围: 只调用 scripts/preprod 下的显式流程，不在 Makefile 中实现部署逻辑。
# 说明: 预发布只消费 Release core 镜像与 OPA 镜像，不在服务器现场构建。
# =============================================================================

.PHONY: preprod-deploy-clinical-safety-stage5 preprod-rollback-clinical-safety-stage5

preprod-deploy-clinical-safety-stage5: ## 部署临床安全阶段 5 Release 到预发布环境。
	CLINICAL_SAFETY_STAGE5_RELEASE_TAG="$(CLINICAL_SAFETY_STAGE5_RELEASE_TAG)" \
	bash scripts/preprod/deploy-clinical-safety-stage5.sh deploy

preprod-rollback-clinical-safety-stage5: ## 回滚阶段 5 预发布 app、worker 与 OPA 运行时镜像。
	CLINICAL_SAFETY_STAGE5_ROLLBACK_RUN_ID="$(CLINICAL_SAFETY_STAGE5_ROLLBACK_RUN_ID)" \
	bash scripts/preprod/deploy-clinical-safety-stage5.sh rollback-runtime
