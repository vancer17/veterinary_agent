---
id: vet-agent-agentline-bff-integration-receipt
version: 0.1.0
owner: vet-agent-line
last_updated: 2026-07-17
status: active
audience: 后端 BFF / App 前端 / PM / Infra
related:
  - docs/external_api.md
  - docs/兽医Agent_后端接入契约_给agent线xAppxPM_20260717.md
---

# 兽医 Agent 线给后端 BFF 的接入回执

## 1. 结论

后端 BFF 接入契约已确认，整体接入方向与 Agent 当前能力匹配：

- App 不直连 Agent，由后端 BFF 作为可信上游。
- 后端 BFF 负责 JWT 校验、宠物属主校验、session 发放、限流、附件中转和错误整形。
- Agent 保持内网 HTTP 服务，提供 `POST /agent/turns` 作为主业务入口。
- Agent 支持同步 JSON 与 SSE 两种响应模式，由请求体 `stream` 字段决定。
- `reasoning_display` 可作为面向用户展示的“思考过程/处理过程”摘要。

## 2. Agent 服务信息

| 项目 | 值 |
| --- | --- |
| 服务端口 | `18081` |
| 主入口 | `POST /agent/turns` |
| 存活检查 | `GET /health` |
| 就绪检查 | `GET /ready` |
| 同步响应 | `stream=false` |
| 流式响应 | `stream=true`，SSE |

部署要求：

- Agent 对宿主机端口 `18081` 应绑定 `0.0.0.0`，确保后端容器可访问。
- 若后端与 Agent 后续接入同一个 Docker 网络，也可以改为通过容器服务名访问。
- 当前生产联调优先使用 `http://<agent-host>:18081/agent/turns`。

## 3. 信任与鉴权边界

后端契约中“可信上游 = 后端 BFF”的前提，Agent 线确认接受。

推荐 Agent 内网联调配置：

```env
REQUIRE_API_AUTH=false
REQUIRE_AUTH_USER_MATCH=false
PET_AUTHORIZATION_MODE=permissive
SESSION_POLICY_MODE=strict
```

语义说明：

- Agent 不解析 App JWT，不做 App 登录态鉴权。
- 用户身份、宠物属主校验由后端 BFF 负责。
- `PET_AUTHORIZATION_MODE=permissive` 表示首次出现的 `pet_id` 会自动登记到当前 `user_id`。
- 后续若其他 `user_id` 使用同一个 `pet_id`，Agent 仍会拒绝，避免串宠物。
- `SESSION_POLICY_MODE=strict` 表示同一个 `session_id` 只能绑定同一组 `user_id + pet_id`。

## 4. 后端调用 Agent 的请求格式

后端 BFF 调用 Agent 时，应构造标准 `AgentTurnRequest`。

示例：

```json
{
  "request_id": "req_20260717_001",
  "trace_id": "trace_20260717_001",
  "model": "qwen-plus",
  "input": "用户本轮输入文本",
  "stream": true,
  "metadata": {
    "client": "app",
    "client_version": "1.0.0",
    "source": "backend_bff"
  },
  "vet_context": {
    "user_id": "user_123",
    "session_id": "sess_456",
    "pet_id": "pet_789",
    "pet_info": {
      "species": "cat",
      "breed": "domestic_shorthair",
      "age": "4y",
      "weight_kg": 4.6
    }
  },
  "attachments": [],
  "turn_options": {
    "idempotency_key": "idem_20260717_001",
    "max_followup_questions": 3,
    "timeout_ms": 120000
  }
}
```

字段边界：

- `user_id` 来自后端 JWT 解析结果。
- `pet_id` 来自后端路由路径，并已完成属主校验。
- `session_id` 由后端发放并管理，新对话生成，续聊复用。
- `pet_info` 由后端从业务数据库补齐，不信任客户端自报。
- `metadata` 只放普通观测信息，不承载安全绕过、RAG 控制、模型绕过或身份改写语义。

## 5. SSE 响应约定

当 `stream=true` 时，Agent 返回 SSE。

后端请求头建议：

```http
Content-Type: application/json
Accept: text/event-stream
```

Agent 可能返回的事件：

| 事件 | 说明 |
| --- | --- |
| `turn.started` | 本轮开始 |
| `reasoning_display.started` | 可展示处理过程开始 |
| `reasoning_display.delta` | 可展示处理过程增量 |
| `reasoning_display.completed` | 可展示处理过程完成 |
| `segment.started` | 业务分段开始 |
| `segment.delta` | 业务分段文本增量 |
| `segment.completed` | 业务分段完成 |
| `turn.completed` | 本轮完成 |
| `turn.failed` | 本轮失败 |

注意事项：

- 后端 BFF 应直透 SSE，不要把整轮响应缓冲完再返回 App。
- Nginx 或网关层需要关闭缓冲：`proxy_buffering off`。
- Agent 响应头会携带 `X-Accel-Buffering: no`。
- 当前版本保证 SSE 事件格式；首事件延迟仍取决于内部编排与模型响应耗时，不承诺 token 级实时首字延迟。

## 6. reasoning_display 展示边界

`reasoning_display` 可用于前端展示“思考过程/处理过程”。

它的定位是：

- 用户可见的安全摘要。
- 对本轮处理依据、追问原因、风险分层思路的简要说明。
- 非模型隐藏思维链。
- 非完整业务 trace。
- 非安全审查原始记录。

前端可优先展示：

- SSE 模式：`reasoning_display.completed.data.reasoning_display.text`
- 同步模式：响应体顶层 `reasoning_display.text` 或 `segments[].reasoning_display.text`

## 7. 附件与 OSS

后端 BFF 负责文件上传与 OSS 落盘，Agent 只接收附件引用。

`storage_ref` 支持格式建议：

```text
oss://<bucket>/<object_key>
https://<bucket>.<endpoint>/<object_key>
<object_key>
```

约束：

- bucket 需与 Agent 环境变量 `OSS_BUCKET` 一致。
- endpoint 需与 Agent 环境变量 `OSS_ENDPOINT` 一致。
- 支持图片扩展名：`.jpg`、`.jpeg`、`.png`、`.webp`、`.bmp`。
- 当前线上报告解析主要支持 OSS 图片地址。
- 放射影像类报告不做线上诊断解读，只返回安全拦截提示。

附件示例：

```json
{
  "attachments": [
    {
      "attachment_id": "att_lab_001",
      "mime_type": "image/jpeg",
      "purpose": "lab_report",
      "storage_ref": "oss://infra-prod-file-storage/uploads/lab/report-001.jpg",
      "metadata": {
        "filename": "report-001.jpg"
      }
    }
  ]
}
```

请后端联调时提供一条真实 OSS `storage_ref` 样例，Agent 线确认可读性与 RAM 权限。

## 8. 幂等、超时与错误

幂等：

- 后端可透传 `turn_options.idempotency_key`。
- 未传时建议后端使用 `request_id` 作为幂等键来源。
- Agent 编排层负责整轮幂等判断。
- 后端不建议自行重试已进入 Agent 的整轮请求，避免重复落库和重复输出。

超时：

- 建议后端到 Agent 的读取超时不低于 `120s`。
- 模型繁忙或上游不可用时，Agent 可能返回 `503` 或 `504`。

错误信封：

```json
{
  "code": "INVALID_REQUEST",
  "message": "Invalid request",
  "request_id": "req_001",
  "trace_id": "trace_001",
  "details": {}
}
```

常见错误：

| HTTP 状态 | code | 说明 |
| --- | --- | --- |
| `400` | `INVALID_REQUEST` | JSON 非法或字段冲突 |
| `401` | `UNAUTHORIZED` | Agent 侧鉴权开启但缺少凭证 |
| `403` | `FORBIDDEN` | 宠物或 session 策略拦截 |
| `429` | `RATE_LIMITED` | 触发限流 |
| `503` | `SERVICE_UNAVAILABLE` | 编排或依赖不可用 |
| `504` | `ORCHESTRATOR_TIMEOUT` | 编排超时 |

## 9. 联调命令

健康检查：

```bash
curl http://<agent-host>:18081/health
curl http://<agent-host>:18081/ready
```

同步请求：

```bash
curl -X POST "http://<agent-host>:18081/agent/turns" \
  -H "Content-Type: application/json" \
  --data-binary @payload.json
```

流式请求：

```bash
curl -N --http1.1 \
  -X POST "http://<agent-host>:18081/agent/turns" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  --connect-timeout 10 \
  --max-time 180 \
  --data-binary @payload.json
```

流式响应验收点：

```text
event: reasoning_display.completed
event: segment.delta
event: turn.completed
```

## 10. 各线 Action

Agent 线：

- 确认服务监听 `0.0.0.0:18081`。
- 确认联调环境关闭 Agent 自身 API 鉴权或与后端约定服务间固定 key。
- 确认 `PET_AUTHORIZATION_MODE=permissive`，支持首次宠物自动注册。
- 确认 `SESSION_POLICY_MODE=strict`，保持一 session 一宠。
- 配合验证后端提供的 OSS `storage_ref` 样例。

后端 BFF：

- 完成 JWT 校验与宠物属主校验。
- 由后端发放并复用 `session_id`。
- 从业务数据库补齐 `pet_info`。
- 默认 SSE 直透，同步 JSON 作为兜底。
- 透传 `request_id`、`trace_id`、`idempotency_key`。
- 不透传客户端传入的 `user_id`、`pet_id`、`session_id`、`vet_context`。

App 前端：

- 只调用后端 BFF，不直连 Agent。
- 默认使用流式体验，必要时降级同步。
- 不传 `vet_context`。

Infra：

- 确认 BFF 到 Agent 的网络可达。
- 确认网关 SSE 路由关闭缓冲。
- 确认 Agent 读取 OSS 所需 RAM 权限。

## 11. Changelog

- `0.1.0` / `2026-07-17`：首版 Agent 线回执。确认后端 BFF 可信上游模式、`18081` 服务入口、SSE 直透、首次宠物自动注册、session 严格绑定、附件 OSS 引用格式与联调命令。
