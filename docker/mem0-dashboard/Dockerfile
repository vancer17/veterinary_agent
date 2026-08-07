# =============================================================================
# 文件: docker/mem0-dashboard/Dockerfile
# 作用: 基于 git submodule 中的 vendor/mem0/server/dashboard 构建 Mem0 运维 Dashboard 镜像。
# 范围: 仅封装 Mem0 Dashboard、项目配置渲染脚本和生产排障工具，不实现兽医 Agent 业务逻辑。
# 说明: 镜像在 CI/CD Runner 中预编译并推送；生产环境只拉取 MEM0_DASHBOARD_IMAGE，不允许现场 build。
# =============================================================================

# Node.js 运行时基础镜像；开发环境可通过 MEM0_DASHBOARD_NODE_BASE_IMAGE 覆盖。
ARG NODE_BASE_IMAGE=node:20-bookworm-slim

# Mem0 子模块源码提交号；CI/CD 构建时自动注入，用于运行镜像溯源。
ARG MEM0_SOURCE_COMMIT=unknown

FROM ${NODE_BASE_IMAGE} AS base

WORKDIR /app

ENV PNPM_HOME=/pnpm \
    PATH="/pnpm:${PATH}" \
    NEXT_TELEMETRY_DISABLED=1

RUN corepack enable \
    && corepack prepare pnpm@10.34.2 --activate

FROM base AS deps

# 构建阶段安装 native module 编译工具；这些工具不会进入最终运行镜像。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/mem0/server/dashboard/package.json vendor/mem0/server/dashboard/pnpm-lock.yaml vendor/mem0/server/dashboard/pnpm-workspace.yaml ./

RUN pnpm install --frozen-lockfile

FROM base AS builder

COPY --from=deps /app/node_modules ./node_modules
COPY vendor/mem0/server/dashboard ./

# NEXT_PUBLIC_* 会在 Next.js build 阶段内联。这里写入同名占位符，
# 运行时由 entrypoint 根据 application.yml 渲染后的变量做安全替换。
ENV NEXT_PUBLIC_API_URL=NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_INSTANCE_NAME=NEXT_PUBLIC_INSTANCE_NAME

RUN pnpm run build

FROM ${NODE_BASE_IMAGE} AS runtime

ARG MEM0_SOURCE_COMMIT

WORKDIR /app

LABEL org.opencontainers.image.title="veterinary-agent-mem0-dashboard" \
    org.opencontainers.image.description="Mem0 operations dashboard packaged from the pinned git submodule" \
    org.opencontainers.image.vendor="vancer-saas" \
    io.vancer.mem0.source-revision="${MEM0_SOURCE_COMMIT}"

# 运行时全局变量：
# TZ/LANG/LC_ALL 固定容器时区与字符集；PORT/HOSTNAME 提供默认监听值；
# MEM0_DASHBOARD_CONFIG_FILE 指向 Compose 挂载的非敏感服务配置文件。
ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    MEM0_DASHBOARD_CONFIG_FILE=/app/config/application.yml

# 安装生产排障所需的最小工具集：
# curl 用于 HTTP 探活，python3-yaml 用于读取 application.yml，
# dnsutils/iproute2/netcat-openbsd/procps/less/jq 用于网络、进程和 JSON 响应问题定位。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        dnsutils \
        iproute2 \
        jq \
        less \
        netcat-openbsd \
        procps \
        python3 \
        python3-yaml \
        tzdata \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" >/etc/timezone \
    && groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid nodejs --home-dir /app --shell /usr/sbin/nologin nextjs \
    && mkdir -p /app/config /opt/vet-agent-mem0-dashboard \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY docker/mem0-dashboard/entrypoint.sh /opt/vet-agent-mem0-dashboard/entrypoint.sh
COPY docker/mem0-dashboard/render_env.py /opt/vet-agent-mem0-dashboard/render_env.py

RUN chmod 0755 /opt/vet-agent-mem0-dashboard/entrypoint.sh \
    && chown -R nextjs:nodejs /app /opt/vet-agent-mem0-dashboard

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --retries=30 \
    CMD bash -c 'eval "$(python3 /opt/vet-agent-mem0-dashboard/render_env.py "${MEM0_DASHBOARD_CONFIG_FILE}")"; python3 -c "import os, urllib.request; urllib.request.urlopen(\"http://127.0.0.1:%s%s\" % (os.getenv(\"PORT\", \"3000\"), os.getenv(\"MEM0_DASHBOARD_HEALTH_PATH\", \"/api/health\")), timeout=3).read()"'

ENTRYPOINT ["/opt/vet-agent-mem0-dashboard/entrypoint.sh"]
CMD ["serve"]
