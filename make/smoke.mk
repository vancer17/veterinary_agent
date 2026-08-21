# =============================================================================
# 文件: make/smoke.mk
# 作用: 封装应用请求验证与外部真实依赖冒烟入口。
# 范围: 覆盖容器内开发请求样例、业务主链路样例，以及 scripts/integration 下的真实依赖冒烟。
# 说明: 开发样例仅作为手动验证入口，不作为 CI 静态资产门禁依据。
# =============================================================================

.PHONY: request-all request-curl request-health request-ready request-followup-first request-followup-second request-multitask request-safety-toxic request-idempotency request-profile-memory request-memory-read request-report-parse request-rag-stats request-rag-chunks request-business-all request-business-followup-first request-business-followup-second request-business-multitask request-business-memory request-business-safety-semantic request-business-stream smoke-clinical-safety-api smoke-clinical-safety-stage5-preprod smoke-memory-read-api smoke-task-routing-api smoke-answer-rag-api smoke-response-generation-api smoke-consultation-state-api smoke-external-api

request-all: ## 执行开发请求样例聚合验证。
	$(EXEC) python scripts/dev_request.py all

request-curl: ## 输出开发请求样例 curl 命令。
	$(EXEC) python scripts/dev_request.py print-curl --base-url "$(BASE_URL)"

request-health: ## 请求开发主应用健康检查。
	$(EXEC) python scripts/dev_request.py health

request-ready: ## 请求开发主应用就绪检查。
	$(EXEC) python scripts/dev_request.py ready

request-followup-first: ## 执行开发连续问诊首轮请求样例。
	$(EXEC) python scripts/dev_request.py followup-first

request-followup-second: ## 执行开发连续问诊次轮请求样例。
	$(EXEC) python scripts/dev_request.py followup-second

request-multitask: ## 执行开发多任务请求样例。
	$(EXEC) python scripts/dev_request.py multitask

request-safety-toxic: ## 执行开发输入安全请求样例。
	$(EXEC) python scripts/dev_request.py safety-toxic

request-idempotency: ## 执行开发幂等请求样例。
	$(EXEC) python scripts/dev_request.py idempotency

request-profile-memory: ## 执行开发画像记忆请求样例。
	$(EXEC) python scripts/dev_request.py profile-memory

request-memory-read: ## 执行开发记忆读取请求样例。
	$(EXEC) python scripts/dev_request.py memory-read

request-report-parse: ## 执行开发报告解析请求样例。
	$(EXEC) python scripts/dev_request.py report-parse

request-rag-stats: ## 查询开发 RAG 统计。
	$(EXEC) python scripts/dev_request.py rag-stats

request-rag-chunks: ## 查询开发 RAG chunk。
	$(EXEC) python scripts/dev_request.py rag-chunks

request-business-all: ## 执行业务主链路请求样例聚合验证。
	$(EXEC) python scripts/dev_request.py business-all $(BUSINESS_RUN_ARG)

request-business-followup-first: ## 执行业务连续问诊首轮请求样例。
	$(EXEC) python scripts/dev_request.py business-followup-first $(BUSINESS_RUN_ARG)

request-business-followup-second: ## 执行业务连续问诊次轮请求样例。
	$(EXEC) python scripts/dev_request.py business-followup-second $(BUSINESS_RUN_ARG)

request-business-multitask: ## 执行业务多任务请求样例。
	$(EXEC) python scripts/dev_request.py business-multitask $(BUSINESS_RUN_ARG)

request-business-memory: ## 执行业务记忆请求样例。
	$(EXEC) python scripts/dev_request.py business-memory $(BUSINESS_RUN_ARG)

request-business-safety-semantic: ## 执行业务安全语义请求样例。
	$(EXEC) python scripts/dev_request.py business-safety-semantic $(BUSINESS_RUN_ARG)

request-business-stream: ## 执行业务流式回复请求样例。
	$(EXEC) python scripts/dev_request.py business-stream $(BUSINESS_RUN_ARG)

smoke-clinical-safety-api: ## 执行临床安全真实依赖冒烟。
	bash scripts/integration/run-external-api-smoke.sh

smoke-clinical-safety-stage5-preprod: ## 执行临床安全阶段 5 预发布黑盒冒烟。
	bash scripts/integration/run-clinical-safety-stage5-preprod-smoke.sh

smoke-memory-read-api: ## 执行记忆读取真实依赖冒烟。
	bash scripts/integration/run-memory-read-api-smoke.sh

smoke-task-routing-api: ## 执行任务拆分真实依赖冒烟。
	bash scripts/integration/run-task-routing-api-smoke.sh

smoke-answer-rag-api: ## 执行回答 RAG 真实依赖冒烟。
	bash scripts/integration/run-answer-rag-api-smoke.sh

smoke-response-generation-api: ## 执行回复生成真实依赖冒烟。
	bash scripts/integration/run-response-generation-api-smoke.sh

smoke-consultation-state-api: ## 执行问诊状态真实依赖冒烟。
	bash scripts/integration/run-consultation-state-api-smoke.sh

smoke-external-api: ## 执行外部真实依赖聚合冒烟。
	CI_TRY_RUN_SCOPE=external-api bash $(CI_SCRIPT_DIR)/try-run.sh
