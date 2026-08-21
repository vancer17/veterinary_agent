# =============================================================================
# 文件: Makefile
# 作用: 提供兽医 Agent 工程工具链的统一入口。
# 范围: 聚合 make/*.mk 中的 CI、CD、Docker、数据库、开发、生产、Mem0 与冒烟验证命令。
# 说明: Makefile 仅作为薄封装索引，具体流程逻辑由 scripts 与 docker compose 承载。
# =============================================================================

include make/common.mk
include make/ci.mk
include make/cd.mk
include make/docker.mk
include make/preprod.mk
include make/runtime.mk
include make/dev.mk
include make/prod.mk
include make/db.mk
include make/smoke.mk
include make/mem0.mk
include make/help.mk
