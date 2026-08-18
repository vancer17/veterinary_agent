#!/usr/bin/env sh
# =============================================================================
# 文件: docker/app/entrypoint.sh
# 作用: 作为兽医 Agent 主应用镜像的统一启动入口。
# 范围: 在启动 uvicorn 或一次性运维命令前执行离线自检，避免导入阶段联网副作用影响生产可用性。
# 说明: 正式环境与开发环境均应通过此入口启动；core 变体默认禁用 Guardrails 增强开关，guardrails 变体用于专项验证。
# =============================================================================

set -eu

command_name="${1:-serve}"
if [ "$command_name" = "--" ]; then
    shift
    command_name="${1:-serve}"
fi

variant="${VET_AGENT_IMAGE_VARIANT:-core}"
case "$variant" in
    core|guardrails)
        ;;
    *)
        echo "不支持的 Vet Agent 镜像变体: ${variant}" >&2
        exit 1
        ;;
esac

is_truthy() {
    case "$(printf '%s' "${1:-false}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

if [ "$variant" != "guardrails" ] && {
    is_truthy "${ENABLE_INPUT_SAFETY_GUARDRAILS:-false}" || is_truthy "${ENABLE_OUTPUT_SAFETY_GUARDRAILS:-false}"
}; then
    echo "core 镜像不包含 Guardrails 增强依赖，请改用 guardrails 变体后再启用对应开关。" >&2
    exit 1
fi

case "$command_name" in
    migrate|seed)
        exec "$@"
        ;;
    serve|app)
        shift || true
        run_target="app"
        ;;
    worker)
        shift || true
        run_target="worker"
        ;;
    *)
        if [ "$#" -gt 0 ]; then
            exec "$@"
        fi
        ;;
esac

: "${NLTK_DATA:=/opt/vet-agent/nltk_data}"
: "${LITELLM_LOCAL_MODEL_COST_MAP:=true}"
export NLTK_DATA
export LITELLM_LOCAL_MODEL_COST_MAP
export VET_AGENT_IMAGE_VARIANT="$variant"

python -m vet_agent.runtime.offline_startup

if [ "${run_target:-app}" = "worker" ]; then
    exec python -m vet_agent.background_tasks.worker
fi

exec uvicorn vet_agent.main:app --host 0.0.0.0 --port 8000 --workers "${APP_WORKERS:-2}"
