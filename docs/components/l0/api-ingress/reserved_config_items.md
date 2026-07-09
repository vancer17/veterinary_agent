# API 接入组件预留配置项清单

## 文件信息

- 文件：`docs/components/l0/api-ingress/reserved_config_items.md`
- 作用：仅记录 `API 接入组件` 配置模型中仍明确预留、且需要后续组件接入后才能完整生效的配置项。
- 边界：本文只讨论 `configs/api_ingress.yaml` 与 `ApiIngressSettings` 中属于 ApiIngress 领域的现存配置项；不记录已实现、已删除或已经正常生效的字段。

## 判定标准

本文所称“预留配置项”需同时满足以下条件：

- 字段仍存在于配置模型和默认 YAML 中。
- 当前源码尚未让该字段产生完整运行效果，或仅完成 DTO 转发、配置合理性检查。
- 字段完整生效依赖后续 `VetOrchestrator / GraphRuntime`、`Response / SSE Mapper` 或编排健康探测能力接入。

已实现字段不再列入本文，例如入口限流、错误响应策略、OpenAI 兼容入口开关、组件启用开关、请求解析超时、响应模式可用性校验等。

## 总览

| 配置路径 | 当前状态 | 后续归属 | 建议接入点 |
| --- | --- | --- | --- |
| `response_mode.sync_timeout_seconds` | 明确预留 | ApiIngress + 编排调用适配 | 同步响应等待与超时映射 |
| `sse.first_event_timeout_seconds` | 部分预留 | Response / SSE Mapper | 首个可发布 SSE 事件等待 |
| `sse.heartbeat_enabled` | 已转发但未控制真实流 | Response / SSE Mapper | SSE 心跳发送器 |
| `sse.heartbeat_interval_seconds` | 已转发但未控制真实流 | Response / SSE Mapper | SSE 心跳发送器 |
| `sse.idle_timeout_seconds` | 已转发但未控制真实流 | Response / SSE Mapper | SSE 空闲超时 |
| `sse.max_stream_duration_seconds` | 已转发但未控制真实流 | Response / SSE Mapper | SSE 总时长控制 |
| `sse.max_event_bytes` | 已转发但未控制真实流 | Response / SSE Mapper | SSE 事件序列化前检查 |
| `sse.client_cancel_notify_timeout_seconds` | 已转发但未控制真实流 | Response / SSE Mapper + 编排取消 | 客户端断开后的取消通知 |
| `readiness.orchestrator_check_timeout_seconds` | 明确预留 | readiness + 编排客户端 | `/ready` 编排探活预算 |

## 逐项说明

### `response_mode.sync_timeout_seconds`

当前配置含义是同步响应模式下，API 接入层等待编排层完成的最大时间。

当前状态：

- 字段仍未参与真实等待、取消或超时错误映射。
- 当前编排层仍为 TODO 空壳，因此不存在真实同步等待过程。

后续接入方式：

- 当 `VetOrchestrator / GraphRuntime` 同步调用接入后，该字段应作为 API 接入层等待最终响应的超时预算。
- 超时后应映射为统一错误响应，通常可使用 `504` 或项目内统一的超时错误码。
- 不建议当前阶段只为了消费字段而在 TODO 响应中读取它；那不会产生真实行为。

需要对齐的问题：

- 它与 `orchestrator.request_timeout_seconds` 的关系需要明确。
- 推荐语义是：`response_mode.sync_timeout_seconds` 面向 HTTP 同步响应等待预算，`orchestrator.request_timeout_seconds` 面向下游编排调用预算。

### `sse.*`

当前 `sse` 配置整体描述 SSE 长连接行为，但 `Response / SSE Mapper` 尚未实现。

当前状态：

- `heartbeat_enabled`、`heartbeat_interval_seconds`、`idle_timeout_seconds`、`max_stream_duration_seconds`、`max_event_bytes`、`client_cancel_notify_timeout_seconds` 已进入 `AgentTurnRequestCommand` 的 execution options。
- 由于当前没有真实 SSE Mapper，这些字段尚未控制实际 HTTP 事件流。
- `first_event_timeout_seconds` 当前只参与配置合理性检查，尚未进入真实首事件等待逻辑。

后续接入方式：

- SSE Mapper 负责将上游编排事件转换为外部 SSE 事件。
- 心跳、空闲超时、总时长、单事件大小和客户端断开通知均应在 SSE Mapper 或其调用边界中生效。
- `first_event_timeout_seconds` 应用于“HTTP SSE 连接建立后等待首个可发布事件”的预算。

需要对齐的问题：

- `sse.first_event_timeout_seconds` 与 `orchestrator.stream_first_event_timeout_seconds` 语义相近，后续需要确认是否保留两层预算。
- 推荐语义是：`orchestrator.stream_first_event_timeout_seconds` 约束编排层首个原始事件，`sse.first_event_timeout_seconds` 约束 API 层首个可对外发布事件。

### `readiness.orchestrator_check_timeout_seconds`

当前配置含义是 `/ready` 中编排入口探测的超时预算。

当前状态：

- `/ready` 已经消费 readiness 相关开关，但真实编排探活尚未实现。
- 当前编排依赖为空时返回 TODO 占位，因此该 timeout 暂未生效。

后续接入方式：

- 当编排健康检查接入后，该字段应约束 `/ready` 侧等待编排探活完成的外层预算。
- 当前不在 `orchestrator` 配置块中保留编排健康检查超时，避免 `/ready` 编排检查出现双 timeout 来源。

## 后续处理顺序建议

1. `readiness.orchestrator_check_timeout_seconds`：等待编排客户端或编排健康检查接口接入后实现。
2. `response_mode.sync_timeout_seconds`：等待真实同步编排调用接入后实现。
3. `sse.*`：等待 `Response / SSE Mapper` 与真实编排流接入后实现。

## 当前不建议做的事情

- 不建议为了消除悬空而在 TODO 下游占位里读取配置；这只会制造“形式上消费、行为上无效”的假象。
- 不建议让 ApiIngress 跨领域实现编排探活、业务安全审查、模型调用或完整 Observability 平台。
- 不建议静默降级响应协议，例如把被禁用的 stream 自动改成 sync，或把被禁用的 sync 自动改成 stream。
