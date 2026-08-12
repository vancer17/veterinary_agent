#!/usr/bin/env bash
# =============================================================================
# 文件: docker/opa/entrypoint.sh
# 作用: 作为 OPA 策略服务镜像的统一启动入口。
# 范围: 校验配置与策略目录，拼装 OPA Server 官方启动参数并启动服务。
# 说明: 本脚本在镜像构建期复制进入容器；生产 Compose 不挂载脚本，不现场编译镜像。
# =============================================================================

set -Eeuo pipefail

if [ "${1:-serve}" != "serve" ]; then
    exec "$@"
fi

config_file="${OPA_CONFIG_FILE:-/opa/config/application.yml}"
policy_dir="${OPA_POLICY_DIR:-/opa/policies}"

if [ ! -f "$config_file" ]; then
    echo "OPA 配置文件不存在: ${config_file}" >&2
    exit 1
fi

if [ ! -d "$policy_dir" ]; then
    echo "OPA 策略目录不存在: ${policy_dir}" >&2
    exit 1
fi

run_args=(
    run
    --server
    --config-file "$config_file"
    --addr "${OPA_ADDR:-0.0.0.0:8181}"
    --diagnostic-addr "${OPA_DIAGNOSTIC_ADDR:-0.0.0.0:8282}"
    --log-level "${OPA_LOG_LEVEL:-info}"
    --log-format "${OPA_LOG_FORMAT:-json}"
    --ready-timeout "${OPA_READY_TIMEOUT_SECONDS:-0}"
    --authentication "${OPA_AUTHENTICATION:-off}"
    --authorization "${OPA_AUTHORIZATION:-off}"
    --ignore ".*"
)

case "${OPA_SKIP_VERSION_CHECK:-${OPA_DISABLE_TELEMETRY:-true}}" in
    1|true|TRUE|yes|YES|on|ON)
        run_args+=(--skip-version-check)
        ;;
esac

case "${OPA_OPTIMIZE_STORE_FOR_READ_SPEED:-false}" in
    1|true|TRUE|yes|YES|on|ON)
        run_args+=(--optimize-store-for-read-speed)
        ;;
esac

exec opa "${run_args[@]}" "$policy_dir"
