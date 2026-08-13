#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/repository/opa-policy-check.sh
# 作用: 使用 OPA CLI 执行仓库内 Rego 策略的格式、编译与单元测试门禁。
# 范围: 覆盖 docker/opa/policies 与 docker/opa/tests，不启动外部服务，不调用模型。
# 说明: OPA CLI 版本应由 CI Runner 或开发环境显式安装；策略门禁失败时按 Fail Fast 退出。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

opa_command="${OPA_BIN:-opa}"
if ! command -v "$opa_command" >/dev/null 2>&1; then
    echo "缺少 OPA CLI: ${opa_command}。请先安装固定版本 OPA CLI。" >&2
    exit 1
fi

policy_dir="${CI_OPA_POLICY_DIR:-docker/opa/policies}"
test_dir="${CI_OPA_TEST_DIR:-docker/opa/tests}"

if [ ! -d "$policy_dir" ]; then
    echo "OPA 策略目录不存在: ${policy_dir}" >&2
    exit 1
fi

if [ ! -d "$test_dir" ]; then
    echo "OPA 测试目录不存在: ${test_dir}" >&2
    exit 1
fi

# 先校验格式，再执行编译检查，最后执行策略单元测试，保证失败定位保持清晰。
"$opa_command" fmt --diff --fail "$policy_dir" "$test_dir"
"$opa_command" check --strict "$policy_dir" "$test_dir"
"$opa_command" test "$policy_dir" "$test_dir" --fail-on-empty
