# =============================================================================
# 文件: docker/opa/Dockerfile
# 作用: 基于 Open Policy Agent 官方 Release 二进制构建本项目 OPA 策略服务镜像。
# 范围: 仅封装 OPA Server、默认策略文件、统一启动入口和生产排障工具，不实现业务策略逻辑。
# 说明: 镜像在 CI/CD Runner 中预编译并推送；生产环境只拉取 OPA_IMAGE，不允许现场 build。
# =============================================================================

# OPA 官方 Release 版本。生产发布应固定明确版本，避免 latest 标签漂移。
ARG OPA_VERSION=v1.19.0

# Runtime 基础镜像。开发环境可按需覆盖，但生产 CD 应使用已验证基线。
ARG OPA_RUNTIME_BASE_IMAGE=debian:bookworm-slim

FROM ${OPA_RUNTIME_BASE_IMAGE} AS downloader

ARG TARGETARCH=amd64
ARG OPA_VERSION

ENV DEBIAN_FRONTEND=noninteractive

# 下载阶段仅保留证书与 curl；该阶段不会进入最终运行镜像。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && case "${TARGETARCH}" in \
        amd64) opa_arch="amd64" ;; \
        arm64) opa_arch="arm64" ;; \
        *) echo "不支持的 OPA 架构: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL \
        "https://github.com/open-policy-agent/opa/releases/download/${OPA_VERSION}/opa_linux_${opa_arch}_static" \
        -o /usr/local/bin/opa \
    && chmod 0755 /usr/local/bin/opa \
    && /usr/local/bin/opa version \
    && rm -rf /var/lib/apt/lists/*

FROM ${OPA_RUNTIME_BASE_IMAGE} AS runtime

ARG OPA_VERSION

WORKDIR /opa

LABEL org.opencontainers.image.title="veterinary-agent-opa" \
    org.opencontainers.image.description="Open Policy Agent policy decision service for Veterinary Agent" \
    org.opencontainers.image.vendor="vancer-saas" \
    org.opencontainers.image.version="${OPA_VERSION}" \
    io.vancer.opa.version="${OPA_VERSION}"

# 运行时全局变量：
# TZ/LANG/LC_ALL 固定容器时区与字符集；
# OPA_CONFIG_FILE/OPA_POLICY_DIR 指向 Compose 挂载的官方配置与策略目录；
# OPA_ADDR/OPA_DIAGNOSTIC_ADDR 控制业务 API 与只读诊断 API 的监听地址。
ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    OPA_CONFIG_FILE=/opa/config/application.yml \
    OPA_POLICY_DIR=/opa/policies \
    OPA_ADDR=0.0.0.0:8181 \
    OPA_DIAGNOSTIC_ADDR=0.0.0.0:8282 \
    OPA_HEALTHCHECK_PORT=8282 \
    OPA_LOG_LEVEL=info \
    OPA_LOG_FORMAT=json \
    OPA_READY_TIMEOUT_SECONDS=0 \
    OPA_AUTHENTICATION=off \
    OPA_AUTHORIZATION=off \
    OPA_SKIP_VERSION_CHECK=true

# 安装生产排障所需的最小工具集：
# curl 用于 HTTP 探活，dnsutils/iproute2/netcat-openbsd/procps/less/jq 用于网络、进程与 JSON 响应排查。
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
        tzdata \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" >/etc/timezone \
    && groupadd --system opa \
    && useradd --system --gid opa --home-dir /opa --shell /usr/sbin/nologin opa \
    && mkdir -p /opa/config /opa/policies /opa/persistence /opt/vet-agent-opa \
    && rm -rf /var/lib/apt/lists/*

COPY --from=downloader /usr/local/bin/opa /usr/local/bin/opa
COPY docker/opa/entrypoint.sh /opt/vet-agent-opa/entrypoint.sh
COPY docker/opa/policies /opa/policies
COPY docker/opa/tests /opa/tests

RUN chmod 0755 /opt/vet-agent-opa/entrypoint.sh \
    && chown -R opa:opa /opa /opt/vet-agent-opa

USER opa

EXPOSE 8181 8282

HEALTHCHECK --interval=15s --timeout=5s --retries=30 \
    CMD curl -fsS "http://127.0.0.1:${OPA_HEALTHCHECK_PORT}/health" >/dev/null

ENTRYPOINT ["/opt/vet-agent-opa/entrypoint.sh"]
CMD ["serve"]
