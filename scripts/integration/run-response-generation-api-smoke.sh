#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-response-generation-api-smoke.sh
# 作用: 通过真实 PostgreSQL、LiteLLM、OPA 与可选 Mem0 验证回复生成上下文编译 API 链路。
# 范围: 复用问诊状态外部测试的 SSH 隧道、数据库基线、OPA 策略同步和运行环境注入，
#       仅选择回复生成上下文编译专用测试用例。
# 说明: 本脚本不使用本地内存模型，不在远程生产环境编译镜像；默认通过 SSH 隧道访问
#       devlop@远程开发主机的本机服务端口，测试数据由既有外部测试夹具按唯一前缀清理。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

# 回复生成测试默认关闭 Mem0，以先隔离 PostgreSQL、OPA、LiteLLM 与回复生成主路径；
# 需要覆盖真实语义记忆时，可由调用方显式设置 EXTERNAL_API_TEST_ENABLE_MEM0=true。
export EXTERNAL_API_TEST_ENABLE_MEM0="${EXTERNAL_API_TEST_ENABLE_MEM0:-false}"

if [[ -n "${EXTERNAL_API_TEST_LITELLM_API_KEY:-}" && "${EXTERNAL_API_TEST_LITELLM_API_KEY}" != sk-* ]]; then
    echo "EXTERNAL_API_TEST_LITELLM_API_KEY 格式无效：LiteLLM Bearer API Key 必须以 sk- 开头。" >&2
    exit 1
fi

test_node="tests/integration/test_consultation_state_api_external.py::test_response_generation_context_compilation_api_uses_real_services"

exec bash scripts/integration/run-consultation-state-api-smoke.sh "${test_node}" "$@"
