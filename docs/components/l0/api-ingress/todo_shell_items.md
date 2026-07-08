# API 接入组件 TODO / 空壳能力清单

## 文件信息

- 文件：`docs/components/l0/api-ingress/todo_shell_items.md`
- 作用：记录 `API 接入组件` 领域内当前仍存在的 TODO 项、空壳能力和后续接入边界，便于后续实现排期与跨组件对齐。
- 边界：本文只记录能力层面的空壳，不重复记录配置悬空项；配置悬空项统一见 [`reserved_config_items.md`](./reserved_config_items.md)。

## 判定标准

本文所称 TODO / 空壳能力需满足以下至少一项：

- 对外契约、DTO、错误码或路由已经存在，但当前没有真实运行行为。
- 当前源码以固定 TODO 响应、`todo_placeholder` 或脱敏后的内部依赖错误表示依赖尚未接入。
- 当前只完成入口层封装、转发准备或状态容器预留，尚未调用真实下游能力。
- 当前设计文档要求存在该能力，但源码中尚未完成对应实现。

以下内容不在本文重复记录：

- 已在 `reserved_config_items.md` 中记录的单个配置字段悬空项。
- 明确属于 L1 / L2 / 业务组件的能力，例如模型调用、RAG、OCR、安全审查、任务拆解、长期记忆、业务逻辑链写入等。
- `ApiIngress` 明确非目标能力，例如用户鉴权、宠物归属授权、一 session 一宠业务一致性校验等。

## 当前已落地的入口能力

为了避免把已实现能力误判为 TODO，当前以下能力已具备实际运行行为：

- HTTP body 读取、JSON 解析、请求解析超时和请求体大小限制。
- 外部请求 DTO 结构校验。
- `vet_context.user_id`、`vet_context.session_id`、`vet_context.pet_id` 必填校验。
- `request_id` / `trace_id` 的请求头与请求体来源选择、格式校验、冲突检测和缺失生成。
- 顶层 `metadata`、`input`、文本长度、附件元信息和附件引用一致性校验。
- 响应模式默认值解析和 `allow_sync` / `allow_stream` 可用性校验。
- `AgentTurnRequestCommand` Builder，包括幂等键补齐、执行选项封装和发布能力封装。
- API 接入组件启用 / 禁用开关。
- OpenAI 兼容入口启用 / 禁用开关。
- 实例级入口限流和活跃 stream 许可。
- 编排入口并发闸门。
- 统一错误响应的 details 裁剪、隐藏内部依赖明细和详细 message 开关。
- `/health` 存活检查。
- `/ready` 的入口运行配置与必要参数检查。

## 总览

| 编号 | TODO / 空壳能力 | 当前表现 | 后续归属 | 建议接入点 |
| --- | --- | --- | --- | --- |
| AIG-TODO-001 | 真实编排调用适配 | 合法请求最终固定返回编排依赖 TODO 错误 | ApiIngress + VetOrchestrator / GraphRuntime | 替换 `_build_todo_dependency_response` |
| AIG-TODO-002 | 同步响应映射 | `AgentTurnResponseDto` 已定义，但没有成功响应路径 | ApiIngress Response Mapper | 编排同步结果到 HTTP JSON 响应 |
| AIG-TODO-003 | SSE 流式响应映射 | SSE DTO 与事件契约已定义，但没有真实 `text/event-stream` 输出 | ApiIngress SSE Mapper + 编排流接口 | 流式路由返回 `StreamingResponse` 或等价实现 |
| AIG-TODO-004 | `/ready` 真实编排探活 | `check_orchestrator=true` 时返回编排 TODO 占位 | ApiIngress readiness + 编排健康检查 | 替换 `_check_orchestrator_dependency` |
| AIG-TODO-005 | Observability 接入 | 仅有耗时响应头，没有入口访问日志、指标和错误摘要 | ApiIngress + Observability | 请求生命周期中间件 / 路由链路 |
| AIG-TODO-006 | 客户端取消与流式失败处理 | 错误码和事件 DTO 已存在，但没有断连处理与 `turn.failed` 发布 | ApiIngress SSE Mapper + 编排取消接口 | SSE 发送循环与断连捕获 |
| AIG-TODO-007 | 编排错误与超时映射 | 错误码已定义，但没有真实下游异常分类与 504 映射 | ApiIngress + 编排调用适配 | 编排调用边界异常处理 |
| AIG-TODO-008 | 下游客户端 / 适配器状态装配 | AppState 只有配置、限流器和并发闸门，没有真实下游客户端 | ASGI App 装配 + ApiIngress | `lifespan` 初始化与依赖读取 |

## 逐项说明

### AIG-TODO-001：真实编排调用适配

当前状态：

- `/agent/turns` 与 `/openai/v1/responses` 已经接入同一条入口处理链路。
- 入口链路已完成解析、身份解析、校验、归一化、限流、Builder 和编排并发闸门。
- 进入应调用 `VetOrchestrator / GraphRuntime` 的阶段后，当前固定调用 `_build_todo_dependency_response`。
- 当前对外表现为 `503 SERVICE_UNAVAILABLE`；在内部依赖明细未脱敏时，可见 `orchestrator / todo_placeholder`。

缺失能力：

- 没有真实 `VetOrchestrator / GraphRuntime` client 或本地 adapter。
- 没有把 `AgentTurnRequestCommandDto` 发送给编排层。
- 没有接收编排层确认、成功响应、失败响应或流式事件。
- 没有区分编排层不可用、超时、取消、业务失败和内部异常。

后续接入建议：

- 定义 ApiIngress 侧编排调用端口，例如同步调用和流式调用两个接口。
- 在 ASGI lifespan 中初始化具体实现，并放入应用状态。
- 在 `_handle_turn_request` 中用真实编排调用替换 `_build_todo_dependency_response`。
- 保留现有解析、校验、限流、并发闸门顺序，避免下游收到未经治理的请求。

验收信号：

- 合法同步请求不再固定返回 TODO 503，而是能返回真实 `AgentTurnResponseDto` 或真实编排错误。
- 合法流式请求能进入真实 SSE 响应链路。
- 编排依赖不可用时仍返回统一 `SERVICE_UNAVAILABLE`，但错误来源来自真实调用失败。

### AIG-TODO-002：同步响应映射

当前状态：

- `AgentTurnResponseDto`、`OutputItemDto`、`SegmentDto`、`VetResultDto`、`ReasoningDisplayDto` 等对外响应 DTO 已定义。
- 外部 API 文档已描述同步响应结构。
- 当前没有任何成功响应 mapper；所有通过入口校验的主业务请求最终仍停在编排 TODO 响应。

缺失能力：

- 没有将编排层同步结果映射为 `AgentTurnResponseDto`。
- 没有生成或透传 turn id、turn status、output、segments、reasoning display、vet result 和 metadata。
- 没有同步响应成功路径的 HTTP status、headers 和错误兜底策略。

后续接入建议：

- 明确编排层同步返回契约与 `AgentTurnResponseDto` 的字段对应关系。
- ApiIngress 只做协议映射和安全透传，不生成业务 segment，不改写 `reasoning_display`。
- 对下游返回的字段进行 Pydantic DTO 校验，避免非法结构直接暴露给客户端。

验收信号：

- `stream=false` 或同步响应模式下，合法请求可返回 `200 OK` 和 `object=agent.turn` 的完整 JSON 响应。
- 下游返回的业务分段与可展示推理摘要可以被忠实透传。
- 下游字段不合法时映射为统一错误响应，而不是未捕获异常。

### AIG-TODO-003：SSE 流式响应映射

当前状态：

- `SseEventDto` 和各类 SSE event data DTO 已定义。
- 外部 API 文档已列出 `turn.started`、`reasoning_display.*`、`segment.*`、`turn.completed`、`turn.failed`、`heartbeat` 等事件。
- 当前没有真实 SSE mapper，也没有 `StreamingResponse` 或等价事件流输出。
- `stream=true` 请求仍会在编排 TODO 阶段返回 JSON 503。

缺失能力：

- 没有建立 `text/event-stream` 响应。
- 没有把编排层事件转换为外部 SSE `event:` / `data:` 帧。
- 没有事件序列化、事件顺序保持、心跳发送、首事件等待、空闲超时、总时长控制和单事件大小检查。
- 没有在流中发布 `turn.failed`。

后续接入建议：

- 将 SSE Mapper 设计为 ApiIngress 内部协议映射层，输入为编排层流式事件，输出为外部 SSE 帧。
- ApiIngress 不解释业务 segment 类型，不排序、不总结、不裁剪下游已允许展示的 `reasoning_display`。
- 流式事件 DTO 校验应在写出前完成；非法事件应进入统一流式失败路径。

验收信号：

- `stream=true` 且流式响应模式允许时，HTTP 响应为 `text/event-stream`。
- 编排层产生的事件能够按顺序写出。
- 心跳、失败事件和正常完成事件均有可测试路径。

### AIG-TODO-004：`/ready` 真实编排探活

当前状态：

- `/ready` 已经消费 readiness 开关。
- 入口运行配置和必要参数检查已经存在。
- 当 `check_orchestrator=true` 时，当前 `_check_orchestrator_dependency` 直接返回 `orchestrator / todo_placeholder`。

缺失能力：

- 没有真实编排健康检查客户端。
- 没有区分编排层健康、不可达、超时、降级或未初始化。
- 没有将编排健康检查结果纳入 `/ready` 的可诊断明细。

后续接入建议：

- 复用 AIG-TODO-001 中的编排 client 或单独 health probe 端口。
- `/ready` 只检查“能否接收正式流量”，不要执行完整业务 turn。
- 探活失败应返回 `503 SERVICE_UNAVAILABLE`，并保留足够但不过度泄露内部细节的错误明细。

验收信号：

- 编排健康时 `/ready` 可返回 `200 OK`。
- 编排不可用时 `/ready` 返回 `503`，且错误不是 TODO placeholder。
- 编排探活异常不会导致 `/ready` 未捕获 500。

### AIG-TODO-005：Observability 接入

当前状态：

- 设计文档要求入口访问日志、基础指标与错误摘要。
- 当前框架层只添加 `X-Process-Time-Ms` 响应头。
- 没有真实 access log、metrics collector、错误摘要聚合或 Observability client。
- `/ready` 中当不允许 Observability 降级时，当前返回 `observability / todo_placeholder`。

缺失能力：

- 没有记录入口请求总数、耗时、状态码、错误码、响应模式、附件数量等指标。
- 没有记录脱敏后的访问日志。
- 没有记录编排接收耗时、流式首事件耗时、流式总时长、客户端取消数等指标。
- 没有 Observability 降级状态，也没有告警触发点。

后续接入建议：

- 在请求生命周期边界采集通用指标，在 ApiIngress 路由链路采集入口语义字段。
- 日志必须遵守隐私约束，不记录完整医疗输入、完整模型回复、OCR 原文、安全审查三联稿、完整 RAG 片段或完整逻辑链。
- Observability 不应成为普通请求的强阻塞依赖；但 `/ready` 可按配置决定是否允许降级。

验收信号：

- 每个入口请求产生脱敏访问日志或等价结构化事件。
- 关键 golden signals 可被测试或本地观测到。
- Observability 不可用时的降级策略与 `/ready` 行为一致。

### AIG-TODO-006：客户端取消与流式失败处理

当前状态：

- `CLIENT_CANCELLED` 错误码已经定义。
- `TurnFailedEventDataDto` 已定义。
- 外部 API 文档要求客户端断开时通知下游并记录取消事件。
- 当前没有真实 SSE 发送循环，因此没有断连捕获、取消通知或流式失败事件。

缺失能力：

- 没有捕获客户端 SSE disconnect。
- 没有调用编排层取消接口。
- 没有取消通知超时处理。
- 没有记录 `CLIENT_CANCELLED` 相关日志或指标。
- 没有在流式过程中将下游失败映射为 `turn.failed`。

后续接入建议：

- 在 SSE Mapper 中统一处理客户端断开、发送异常和下游事件流异常。
- 客户端断开后不应尝试继续向客户端发送错误响应体。
- 已发布内容不回滚；只能通知下游取消并记录状态。

验收信号：

- 流式客户端断开后，编排取消通知被调用或被明确跳过并记录原因。
- 断连不会导致未处理异常污染服务日志。
- 下游流式失败可映射为 `turn.failed` 或统一流式终止策略。

### AIG-TODO-007：编排错误与超时映射

当前状态：

- `ORCHESTRATOR_TIMEOUT`、`SERVICE_UNAVAILABLE`、`INTERNAL_ERROR` 等错误码已定义。
- 外部 API 文档要求编排不可用映射为 `503`，编排超时映射为 `504`。
- 当前没有真实下游调用，因此也没有真实错误分类。

缺失能力：

- 没有将连接失败、请求超时、流式首事件超时、下游 5xx、协议错误等映射为稳定错误码。
- 没有统一处理同步和流式两类编排失败。
- 没有保留 request_id / trace_id / route_kind / response_mode 等排障字段的错误上下文。

后续接入建议：

- 在编排调用适配层定义异常类型或结果枚举。
- ApiIngress 根据异常类型映射 HTTP status、`IngressErrorCode` 和脱敏 details。
- 依赖错误默认应走内部依赖明细隐藏策略，避免泄露内部地址、堆栈或供应商细节。

验收信号：

- 编排不可达、超时、返回非法结构和内部异常均有独立测试。
- 对外错误码稳定，不随底层 client 异常类型变化。
- 详细诊断信息只在配置允许时暴露。

### AIG-TODO-008：下游客户端 / 适配器状态装配

当前状态：

- ASGI lifespan 当前装配了配置、`ready` 标记、入口限流器和编排并发闸门。
- AppState 尚未保存真实编排 client、SSE mapper、Observability client 或健康检查器。

缺失能力：

- 没有运行期依赖对象的统一初始化和关闭流程。
- 没有下游 client 的生命周期管理。
- 没有启动期初始化失败与 `/ready` 状态之间的明确关系。

后续接入建议：

- 在 `VeterinaryAgentAppState` 中增加明确的 ApiIngress 下游依赖字段，避免路由层临时构造 client。
- 在 lifespan 中完成初始化、关闭和降级状态标记。
- 下游依赖初始化失败时，应明确决定是启动失败、启动但 not ready，还是按配置降级。

验收信号：

- 应用启动后可从 app state 读取真实编排 client / health checker。
- 应用关闭时下游 client 被正常关闭。
- 初始化失败路径有明确错误响应或 readiness 表达。

## 当前不建议做的事情

- 不建议为了消除 TODO，把固定成功响应、假 SSE 事件或假编排结果塞进 ApiIngress。
- 不建议让 ApiIngress 自行生成医疗业务 segment、`vet_result` 或 reasoning display。
- 不建议在 ApiIngress 内实现模型调用、RAG、OCR、安全审查、任务拆解或记忆读写。
- 不建议绕过编排层直接写 ConversationStore、CheckpointStore 或 LogicTraceStore。
- 不建议把配置字段的“形式读取”当作能力落地；配置悬空项应继续在 `reserved_config_items.md` 中跟踪。

## 建议处理顺序

1. AIG-TODO-008：先补齐下游 client / adapter 的状态装配方式。
2. AIG-TODO-001：接入真实编排调用，替换固定 TODO 503。
3. AIG-TODO-002：实现同步响应 mapper，打通第一条成功请求路径。
4. AIG-TODO-007：补齐编排错误与超时映射。
5. AIG-TODO-004：接入 `/ready` 真实编排探活。
6. AIG-TODO-003：实现 SSE Mapper 和真实流式响应。
7. AIG-TODO-006：补齐客户端取消和流式失败处理。
8. AIG-TODO-005：接入 Observability，并对齐日志、指标和降级策略。
