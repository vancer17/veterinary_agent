# =============================================================================
# 文件: make/common.mk
# 作用: 定义 Make 工具链入口共用变量。
# 范围: 覆盖 Docker Compose、CI/CD 脚本目录、应用访问地址与业务冒烟参数。
# 说明: 本文件不承载具体流程逻辑，仅为各分类入口提供稳定变量。
# =============================================================================

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# 通用执行上下文
# ---------------------------------------------------------------------------
# 使用 bash 作为 recipe shell，确保变量展开与脚本调用行为在本地和 CI 中保持一致。
# ---------------------------------------------------------------------------
SHELL := /bin/bash

# ---------------------------------------------------------------------------
# Docker Compose 编排入口
# ---------------------------------------------------------------------------
# env 文件优先使用本地真实文件；缺失时回落到模板文件，便于 dry-run 与配置解析。
# 生产编排显式要求 Release 解析后的镜像变量，避免误用本地构建镜像。
# ---------------------------------------------------------------------------
DEV_ENV_FILE ?= $(if $(wildcard docker/compose.dev.env),docker/compose.dev.env,docker/compose.dev.env.template)
PROD_ENV_FILE ?= $(if $(wildcard docker/compose.prod.env),docker/compose.prod.env,docker/compose.prod.env.template)
DEV_COMPOSE ?= docker compose --env-file $(DEV_ENV_FILE) -f docker/compose.dev.yml
PROD_COMPOSE ?= docker compose --env-file $(PROD_ENV_FILE) -f docker/compose.yml
PROD_IMAGE_ENV = VET_AGENT_IMAGE="$${VET_AGENT_IMAGE:?请先导出 VET_AGENT_IMAGE}" MEM0_IMAGE="$${MEM0_IMAGE:?请先导出 MEM0_IMAGE}" MEM0_DASHBOARD_IMAGE="$${MEM0_DASHBOARD_IMAGE:?请先导出 MEM0_DASHBOARD_IMAGE}" OPA_IMAGE="$${OPA_IMAGE:?请先导出 OPA_IMAGE}"
PROD_COMPOSE_CMD = $(PROD_IMAGE_ENV) $(PROD_COMPOSE)
COMPOSE ?= $(DEV_COMPOSE)
EXEC ?= $(COMPOSE) exec -T app
PROD_EXEC ?= $(PROD_COMPOSE_CMD) exec -T app

# ---------------------------------------------------------------------------
# 常用参数
# ---------------------------------------------------------------------------
# CI_TRY_RUN_SCOPE、CD_RELEASE_TAG 和 BUSINESS_RUN_ID 可由命令行覆盖。
# 示例: make ci-try-run CI_TRY_RUN_SCOPE=dry-run
# 示例: make cd-build-images CD_RELEASE_TAG=v0.1.0-rc.3
# ---------------------------------------------------------------------------
APP_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(APP_PORT)
BUSINESS_RUN_ID ?=
BUSINESS_RUN_ARG = $(if $(strip $(BUSINESS_RUN_ID)),--run-id "$(BUSINESS_RUN_ID)",)
CI_SCRIPT_DIR ?= scripts/ci
CI_TRY_RUN_SCOPE ?= full
CI_RUN_APP_OFFLINE_SMOKE ?= true
CD_SCRIPT_DIR ?= scripts/cd
CD_RELEASE_TAG ?=
VET_AGENT_IMAGE_VARIANT ?= core
