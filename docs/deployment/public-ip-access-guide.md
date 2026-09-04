<!--
=============================================================================
文件: docs/deployment/public-ip-access-guide.md
作用: 记录远程开发环境通过公网 IPv4 与 Nginx 路径路由访问外部依赖服务的方式。
范围: 覆盖 Mem0 Dashboard、Mem0 REST API、LiteLLM、OPA REST API、OPA 诊断 API，
      以及 Temporal 开发服务的有意非公网暴露边界。
说明: 本文档只记录非敏感访问路径、配置位置和排障方式；真实密钥不得写入正式 docs 目录。
=============================================================================
-->

# 公网 IPv4 访问指南

## 1. 服务器信息

| 项目 | 值 |
| --- | --- |
| 公网 IP | `47.97.19.58` |
| HTTP 端口 | `80` |
| SSH 用户 | `devlop` |
| 业务部署目录 | `/home/devlop/veterinary_agent` |
| Nginx 配置文件 | `/www/server/panel/vhost/nginx/vet-agent.conf` |
| Nginx 托管方式 | `systemctl` |

## 2. 服务入口

| 服务 | 公网入口 | 上游地址 | 说明 |
| --- | --- | --- | --- |
| Mem0 Dashboard | `http://47.97.19.58/` | `127.0.0.1:3001` | 记忆服务运维前端，作为默认首页 |
| Mem0 REST API | `http://47.97.19.58/mem0/` | `127.0.0.1:8001` | 记忆写入、检索、配置和 OpenAPI |
| Dashboard 同源 Mem0 API | `http://47.97.19.58/api/mem0/` | `127.0.0.1:8001` | 仅供 Dashboard 浏览器侧同源访问 |
| LiteLLM Admin UI | `http://47.97.19.58/litellm/ui/` | `127.0.0.1:4000` | LiteLLM 管理界面 |
| LiteLLM OpenAI 兼容 API | `http://47.97.19.58/litellm/v1/` | `127.0.0.1:4000` | 模型网关 API |
| OPA REST API | `http://47.97.19.58/opa/` | `127.0.0.1:8181` | 策略裁决、Data API、Policy API 与 Config API |
| OPA 诊断 API | `http://47.97.19.58/opa-diagnostics/` | `127.0.0.1:8282` | 只读健康检查与 Prometheus metrics |
| Temporal Frontend | 无；SSH tunnel 后 `127.0.0.1:7233` | `127.0.0.1:7233` | 受限语义协作 DAG 集成测试 gRPC 入口 |
| Temporal Web UI | 无；SSH tunnel 后 `http://127.0.0.1:8080` | `127.0.0.1:8080` | Temporal workflow / namespace 运维界面 |

说明：

- OPA 当前没有独立前端页面，因此只配置 REST API 与诊断 API 路由。
- 外部访问应优先使用 80 端口下的 Nginx 路径路由。
- 直接暴露端口只用于临时开发排障，不作为长期公网契约。
- Temporal Frontend 与 Web UI 有意不配置 Nginx 公网路由；本地访问必须通过 SSH tunnel，详见 [temporal-dev-environment-baseline.md](/home/vancer17/veterinary_agent/docs/deployment/temporal-dev-environment-baseline.md)。

## 3. Nginx 路由规则

```text
/                               -> http://127.0.0.1:3001
/api/mem0/                      -> http://127.0.0.1:8001/
/mem0/                          -> http://127.0.0.1:8001/
/litellm/                       -> http://127.0.0.1:4000
/litellm/_next/                 -> http://127.0.0.1:4000
/litellm-asset-prefix/          -> http://127.0.0.1:4000/litellm/litellm-asset-prefix/
/opa/                           -> http://127.0.0.1:8181/
/opa-diagnostics/               -> http://127.0.0.1:8282/
```

关键约束：

- LiteLLM 已配置 `SERVER_ROOT_PATH=/litellm`，`/litellm/` 路由必须保留 `/litellm` 前缀转发。
- Mem0 的 `/mem0/` 路由会在 Nginx 层剥离 `/mem0` 前缀后转发给上游。
- Dashboard 浏览器侧使用 `/api/mem0/` 访问 Mem0，因此该路由必须优先于 Dashboard 根路由匹配。
- OPA 的 `/opa/` 路由会在 Nginx 层剥离 `/opa` 前缀后转发给 OPA REST API。
- OPA 的 `/opa-diagnostics/` 路由会在 Nginx 层剥离 `/opa-diagnostics` 前缀后转发给 OPA 诊断端口。

## 4. 验证命令

检查 Nginx 配置：

```bash
ssh -i /home/vancer17/.ssh/AlibabaCloudLinux devlop@47.97.19.58 \
  "sudo nginx -t && systemctl is-active nginx"
```

检查容器状态：

```bash
ssh -i /home/vancer17/.ssh/AlibabaCloudLinux devlop@47.97.19.58 \
  "sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'"
```

检查 LiteLLM readiness：

```bash
curl -sS -o /dev/null -w "%{http_code}\n" \
  "http://47.97.19.58/litellm/health/readiness"
```

检查 Mem0 OpenAPI：

```bash
curl -sS "http://47.97.19.58/mem0/openapi.json" | jq ".info"
```

检查 OPA 健康状态：

```bash
curl -sS "http://47.97.19.58/opa-diagnostics/health"
```

检查 OPA bootstrap 裁决：

```bash
curl -sS -X POST "http://47.97.19.58/opa/v1/data/vet_agent/bootstrap/decision" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "action": "healthcheck"
    }
  }' | jq ".result"
```

检查 OPA metrics：

```bash
curl -sS "http://47.97.19.58/opa-diagnostics/metrics" \
  | awk '/^opa_info/ { print; exit }'
```

## 5. 运维注意事项

1. 修改 Nginx 配置后必须先执行 `sudo nginx -t`，通过后再执行 `sudo systemctl reload nginx`。
2. OPA REST API 当前在开发环境未启用鉴权，正式环境应通过 Nginx、内网隔离或 OPA 自身认证授权能力限制访问。
3. 生产部署不得在服务器现场构建业务镜像，应只拉取 GitHub Release 标签对应的预编译镜像。
4. 临时明文密钥只允许记录在 `tmp/` 下的开发参考文档中，不得写入正式 `docs/`。
