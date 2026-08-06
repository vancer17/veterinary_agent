#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/repository/health-check.sh
# 作用: 对生产部署后的兽医 Agent API 执行 HTTP 健康检查。
# 范围: 默认只检查 /health，不绑定 seed、RAG chunk 或开发样例等静态资产内容。
# 说明: 若生产网络不允许 Runner 访问，可将 CD_RUN_HEALTH_CHECK=false 暂时关闭外部检查。
# =============================================================================

set -Eeuo pipefail

case "${CD_RUN_HEALTH_CHECK:-true}" in
    1|true|TRUE|yes|YES|on|ON)
        ;;
    *)
        echo "跳过生产 HTTP 健康检查：CD_RUN_HEALTH_CHECK 未启用。"
        exit 0
        ;;
esac

base_url="${CD_PROD_BASE_URL:-${CD_HEALTHCHECK_BASE_URL:-}}"
health_path="${CD_HEALTHCHECK_PATH:-/health}"
attempts="${CD_HEALTHCHECK_ATTEMPTS:-30}"
timeout_seconds="${CD_HEALTHCHECK_TIMEOUT_SECONDS:-5}"

if [ -z "$base_url" ]; then
    echo "缺少 CD_PROD_BASE_URL 或 CD_HEALTHCHECK_BASE_URL，无法执行生产 HTTP 健康检查。" >&2
    exit 1
fi

export CD_HEALTHCHECK_URL="${base_url%/}${health_path}"
export CD_HEALTHCHECK_ATTEMPTS="$attempts"
export CD_HEALTHCHECK_TIMEOUT_SECONDS="$timeout_seconds"

python3 - <<'PY'
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request

url = os.environ["CD_HEALTHCHECK_URL"]
attempts = int(os.environ["CD_HEALTHCHECK_ATTEMPTS"])
timeout_seconds = float(os.environ["CD_HEALTHCHECK_TIMEOUT_SECONDS"])

last_error = ""
for attempt in range(1, attempts + 1):
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            if 200 <= response.status < 400:
                print(f"生产健康检查通过: {url}")
                sys.exit(0)
            last_error = f"HTTP 状态码异常: {response.status}"
    except (urllib.error.URLError, TimeoutError) as exc:
        last_error = str(exc)

    if attempt < attempts:
        time.sleep(2)

raise SystemExit(f"生产健康检查失败: {url}; 最后错误: {last_error}")
PY
