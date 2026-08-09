# =============================================================================
# 文件: docker/app/Dockerfile
# 作用: 基于 uv.lock 构建兽医 Agent 生产应用镜像。
# 范围: 仅负责主应用运行时镜像，不承载数据库、LiteLLM 或 Mem0 运行逻辑。
# 说明: uv 依赖源由 pyproject.toml 与 UV_DEFAULT_INDEX 统一约束。
# =============================================================================

ARG PYTHON_BASE_IMAGE=ghcr.io/astral-sh/uv:python3.12-bookworm
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/src

COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY assets ./assets
COPY scripts ./scripts
COPY src ./src

RUN uv sync --frozen --no-dev \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/.data \
    && chown -R app:app /app /opt/venv

USER app

EXPOSE 8000

CMD ["uvicorn", "vet_agent.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
