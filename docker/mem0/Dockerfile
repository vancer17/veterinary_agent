# =============================================================================
# 文件: docker/mem0/Dockerfile
# 作用: 基于 git submodule 中的 vendor/mem0/server 构建自托管 Mem0 REST Server 镜像。
# 范围: 仅封装 Mem0 上游服务、项目启动脚本和生产排障工具，不实现兽医 Agent 业务逻辑。
# 说明: 镜像在 CI/CD Runner 中预编译并推送；生产环境只拉取 MEM0_IMAGE，不允许现场 build。
# =============================================================================

# Python 运行时基础镜像；开发环境可通过 MEM0_PYTHON_BASE_IMAGE 覆盖，生产 CD 默认使用稳定 slim 基线。
ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm

# Mem0 子模块源码提交号；CI/CD 构建时自动注入，用于运行镜像溯源。
ARG MEM0_SOURCE_COMMIT=unknown

FROM ${PYTHON_BASE_IMAGE} AS builder

WORKDIR /build

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# 构建阶段保留编译工具与 PostgreSQL 头文件，兼容上游依赖缺少预编译 wheel 的情形；
# 工具仅存在于 builder，不进入最终运行镜像。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && python -m venv "${VIRTUAL_ENV}" \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/mem0/server/requirements.txt /build/requirements.txt
COPY vendor/mem0/pyproject.toml vendor/mem0/README.md vendor/mem0/LICENSE /build/vendor-mem0/
COPY vendor/mem0/mem0 /build/vendor-mem0/mem0

# 上游 server requirements 中的 mem0ai 范围依赖会从 PyPI 解析最新兼容版本；
# 本项目改为安装 git submodule 内的本地 mem0ai 包，确保 server 源码与核心包同源可追溯。
# psycopg[binary] 与 PyYAML 为本项目自托管封装的运行依赖：
# psycopg[binary] 避免运行环境缺少 libpq wrapper；PyYAML 用于读取 application.yml。
RUN python -m pip install --upgrade pip setuptools wheel \
    && grep -Ev '^[[:space:]]*mem0ai([<=> ].*)?$' /build/requirements.txt > /build/server-requirements.txt \
    && python -m pip install -r /build/server-requirements.txt "psycopg[binary]>=3.2,<4" "PyYAML>=6.0,<7" \
    && python -m pip install /build/vendor-mem0

FROM ${PYTHON_BASE_IMAGE} AS runtime

ARG MEM0_SOURCE_COMMIT

WORKDIR /app

LABEL org.opencontainers.image.title="veterinary-agent-mem0" \
    org.opencontainers.image.description="Self-hosted Mem0 REST Server packaged from the pinned git submodule" \
    org.opencontainers.image.vendor="vancer-saas" \
    io.vancer.mem0.source-revision="${MEM0_SOURCE_COMMIT}"

# 运行时全局变量：
# TZ/LANG/LC_ALL 固定容器时区与字符集；PYTHON* 变量确保日志实时输出且不生成 pyc；
# MEM0_CONFIG_FILE 指向 Compose 挂载的非敏感服务配置文件。
ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    MEM0_CONFIG_FILE=/app/config/application.yml

# 安装生产排障所需的最小工具集：
# curl 用于 HTTP 探活，postgresql-client 用于数据库连通性排查，
# iproute2/dnsutils/netcat-openbsd/procps/less/jq 用于网络、进程和 JSON 响应问题定位。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dnsutils \
        iproute2 \
        jq \
        less \
        netcat-openbsd \
        postgresql-client \
        procps \
        tzdata \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" >/etc/timezone \
    && groupadd --system mem0 \
    && useradd --system --gid mem0 --home-dir /app --shell /usr/sbin/nologin mem0 \
    && mkdir -p /app/config /app/history /opt/vet-agent-mem0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# 仅复制 Mem0 REST Server 后端运行文件，避免将 Dashboard 与开发编排资产写入生产镜像。
COPY vendor/mem0/server/*.py /app/
COPY vendor/mem0/server/alembic.ini /app/alembic.ini
COPY vendor/mem0/server/alembic /app/alembic
COPY vendor/mem0/server/routers /app/routers
COPY vendor/mem0/server/scripts /app/scripts
COPY docker/mem0/entrypoint.sh /opt/vet-agent-mem0/entrypoint.sh
COPY docker/mem0/render_env.py /opt/vet-agent-mem0/render_env.py
COPY docker/mem0/configure_mem0.py /opt/vet-agent-mem0/configure_mem0.py

RUN chmod 0755 /opt/vet-agent-mem0/entrypoint.sh \
    && chown -R mem0:mem0 /app /opt/vet-agent-mem0

USER mem0

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=30 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/docs' % os.getenv('MEM0_SERVER_PORT', '8000'), timeout=3).read()"

ENTRYPOINT ["/opt/vet-agent-mem0/entrypoint.sh"]
CMD ["serve"]
