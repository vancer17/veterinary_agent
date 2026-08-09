#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/secret-boundary-check.sh
# 作用: 校验仓库未提交真实环境文件，并确认 env 模板白名单仍然生效。
# 范围: 只检查 Git 已跟踪且当前存在的文件与 .gitignore 策略，不读取本地被忽略的真实密钥。
# 说明: 深度凭据指纹扫描可由平台级工具补充，本脚本负责业务仓库基础边界。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

tracked_env_violations="$(
    git ls-files | while IFS= read -r tracked_file; do
        if [ ! -e "$tracked_file" ]; then
            continue
        fi

        case "$tracked_file" in
            *.template)
                continue
                ;;
            *.env|*.env.*|.env|*/.env)
                printf '%s\n' "$tracked_file"
                ;;
        esac
    done
)"

if [ -n "$tracked_env_violations" ]; then
    echo "检测到已提交的真实环境文件，必须移出 Git 跟踪范围：" >&2
    printf '%s\n' "$tracked_env_violations" >&2
    exit 1
fi

if ! grep -qxF "*.env" .gitignore; then
    echo ".gitignore 缺少 *.env 忽略规则。" >&2
    exit 1
fi

if ! grep -qxF "!docker/*.env.template" .gitignore; then
    echo ".gitignore 缺少 docker env 模板白名单规则。" >&2
    exit 1
fi

if ! grep -qxF "!docker/*/template/*.env.template" .gitignore; then
    echo ".gitignore 缺少 docker 服务 env 模板白名单规则。" >&2
    exit 1
fi

# 新增敏感变量后应同步维护本白名单外扫描，避免真实密钥进入仓库正文或脚本。
secret_patterns=(
    'DASHSCOPE_API_KEY=(sk-|ak-|[A-Za-z0-9_-]{24,})'
    'LITELLM_MASTER_KEY=(sk-|[A-Za-z0-9_-]{24,})'
    'LITELLM_SALT_KEY=(sk-|[A-Za-z0-9_-]{24,})'
    'UI_PASSWORD=.{16,}'
)

for secret_pattern in "${secret_patterns[@]}"; do
    if git grep -n -E "$secret_pattern" -- \
        ':!docker/*.env.template' \
        ':!docker/*/template/*.env.template'; then
        echo "检测到疑似真实敏感配置，请改用环境密钥注入。" >&2
        exit 1
    fi
done
