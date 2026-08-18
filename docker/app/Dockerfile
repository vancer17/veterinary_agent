# =============================================================================
# 文件: docker/app/Dockerfile
# 作用: 基于 uv.lock 构建兽医 Agent 生产应用镜像。
# 范围: 仅负责主应用运行时镜像，不承载数据库、LiteLLM 或 Mem0 运行逻辑。
# 说明: uv 依赖源由 pyproject.toml 与 UV_DEFAULT_INDEX 统一约束。
# =============================================================================

ARG PYTHON_BASE_IMAGE=ghcr.io/astral-sh/uv:python3.12-bookworm
FROM ${PYTHON_BASE_IMAGE}

ARG VET_AGENT_IMAGE_VARIANT=core
ARG NLTK_PACKAGES="punkt punkt_tab"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    VET_AGENT_IMAGE_VARIANT=${VET_AGENT_IMAGE_VARIANT} \
    NLTK_DATA=/opt/vet-agent/nltk_data \
    LITELLM_LOCAL_MODEL_COST_MAP=True

LABEL io.vancer.vet-agent.image-variant="${VET_AGENT_IMAGE_VARIANT}"

COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY assets ./assets
COPY scripts ./scripts
COPY src ./src
COPY docker/app/entrypoint.sh /opt/vet-agent-app/entrypoint.sh

# core 变体仅安装主链路依赖；guardrails 变体额外安装 Guardrails Hub 校验器与 NLTK。
# NLTK 数据只在 guardrails 变体中固化，生产启动阶段不得在线下载外部资源。
RUN case "${VET_AGENT_IMAGE_VARIANT}" in \
        core) uv sync --frozen --no-dev ;; \
        guardrails) uv sync --frozen --no-dev --extra guardrails ;; \
        *) echo "不支持的 Vet Agent 镜像变体: ${VET_AGENT_IMAGE_VARIANT}" >&2; exit 1 ;; \
    esac \
    && mkdir -p "${NLTK_DATA}" /app/.data/nltk_data \
    && if [ "${VET_AGENT_IMAGE_VARIANT}" = "guardrails" ]; then \
        python scripts/runtime/install_nltk_data.py --target-dir "${NLTK_DATA}" --packages ${NLTK_PACKAGES}; \
    fi \
    && python -m vet_agent.runtime.offline_startup \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chmod -R a+rX "${NLTK_DATA}" \
    && chmod 0755 /opt/vet-agent-app/entrypoint.sh \
    && chown -R app:app /app /opt/venv

USER app

EXPOSE 8000

ENTRYPOINT ["/opt/vet-agent-app/entrypoint.sh"]
CMD ["serve"]
