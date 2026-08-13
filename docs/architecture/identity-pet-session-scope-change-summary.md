<!--
=============================================================================
文件: docs/architecture/identity-pet-session-scope-change-summary.md
作用: 总结“身份、宠物资料与会话范围”迁移后的实现边界、兼容接口、保留路径与有意 TODO。
范围: 适用于后续 Agent 中间件迁移、代码评审、接口联调、测试补充和跨服务职责对齐。
说明: 本文档不替代对外 API 字段说明；字段契约以 docs/api/external_api.md 为准，变更通知以 docs/api/agent-turn-scope-assertion-change-notice.md 为准。
维护: 当 scope_assertion 契约、范围服务、宠物画像投影、session 绑定或兼容接口策略调整时，应同步更新本文档。
=============================================================================
-->

# 身份、宠物资料与会话范围变更总结

> **文档状态**：迁移完成后的对齐基线
>
> **适用范围**：当前主业务对话入口、入口适配层、范围授权服务、宠物上下文组装、宠物画像投影与会话绑定
>
> **不适用范围**：临床语义抽取、RAG 检索、长期记忆策略、输出安全策略、报告解析、管理接口完整改造

## 1. 变更目的

本次迁移将 Agent 主业务入口中的身份、宠物、会话、授权状态、宠物基础画像和来源信息统一收束到 `scope_assertion`。

迁移前，`vet_context` 同时携带 `user_id`、`pet_id`、`session_id` 与 `pet_info`，导致访问范围字段、服务端可信画像和请求侧自报资料混放。后续临床推理、记忆写入、冷启动投影和权限判断都容易误用 `pet_info`。

迁移后，主链路采用以下边界：

| 数据 | 可信等级 | 位置 | 允许用途 |
|---|---|---|---|
| 身份范围 | 可信 | `scope_assertion.user_id`、`scope_assertion.pet_id`、`scope_assertion.session_id` | 构造 `TrustedIdentity`、会话绑定、幂等、记忆与 trace 范围 |
| 授权裁决摘要 | 可信 | `scope_assertion.authorization` | 判断是否允许进入 Agent 主链路 |
| 服务端宠物画像 | 可信 | `scope_assertion.profile` | 初始化或刷新 Agent 本地画像投影，并进入 `PetContext.verified_profile` |
| 画像来源 | 可信审计信息 | `scope_assertion.source` | 校验来源记录、记录本地投影来源 |
| 请求侧宠物信息 | 未验证 | `vet_context.pet_info` | 审计、差异提示、资料纠正候选；不得覆盖可信画像 |

## 2. 职责边界

### 2.1 BFF 或授权内部调用方职责

BFF 是用户登录、宠物归属、宠物状态和 session 发放的权威执行方。Agent 不直接面向 App、Web 或小程序暴露，不从用户侧请求中接受自造的范围声明。

BFF 调用 Agent 前必须完成：

1. 校验用户登录态并确定 `user_id`。
2. 校验 `pet_id` 属于当前 `user_id`。
3. 校验宠物未删除、未停用，且允许进入 Agent 服务。
4. 发放或复用本轮 `session_id`。
5. 从主服务 `master_pet_info` 读取宠物基础资料。
6. 将主服务字段归一为 `scope_assertion.profile`。
7. 写入 `scope_assertion.authorization`、`scope_assertion.source` 与可选 `session_policy`。
8. 移除客户端可能伪造的 `scope_assertion`、`vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id`。

### 2.2 Agent 入口层职责

Agent 入口层只消费 BFF 注入后的服务端声明，不主动查询主服务数据库。

入口层当前负责：

1. 校验 `scope_assertion` 结构完整性。
2. 拒绝旧版 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id`。
3. 通过服务级 API Key 认证调用方。
4. 调用范围授权服务生成内部 `authorized_scope_context` 快照。
5. 将已授权快照传递给核心编排层。

入口层不负责：

1. 直接识别 App 用户登录态。
2. 直接判断宠物归属。
3. 主动查询主服务数据库。
4. 根据用户自然语言纠正 `scope_assertion.pet_id`。
5. 根据 `vet_context.pet_info` 初始化权威画像。

### 2.3 Agent 范围服务职责

`ScopeContextService` 是当前迁移的核心服务边界。它负责把 BFF 范围声明转换为 Agent 内部可使用的结构化范围事实。

范围服务当前负责：

1. 校验 `scope_assertion` 是否过期。
2. 校验 BFF 是否声明已完成宠物归属校验。
3. 校验宠物是否处于可用状态。
4. 校验会话绑定策略是否为 `single_user_pet_per_session`。
5. 基于 `scope_assertion.profile` upsert Agent 本地 `pet_profiles` 投影。
6. 首次看到 `session_id` 时绑定当前 `user_id + pet_id`。
7. 后续请求校验同一个 `session_id` 未跨用户或跨宠物复用。
8. 生成可复用的 `AuthorizedScopeContext` 快照。

范围服务不负责：

1. 医学语义判断。
2. 问诊状态迁移。
3. RAG 检索。
4. 记忆事实写入裁决。
5. 用户输入中的跨宠物语义拦截。
6. 主服务宠物主档维护。

## 3. 接口兼容边界

### 3.1 已变更的主业务入口

以下入口已迁移为 `scope_assertion` 主路径：

| 接口 | 状态 | 说明 |
|---|---|---|
| `POST /agent/turns` | 已迁移 | 主业务对话入口，必须携带 `scope_assertion` |
| `POST /openai/v1/responses` | 已迁移 | OpenAI Responses 风格兼容入口，同样必须携带 `scope_assertion` |

上述入口不再接受正式契约中的 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id`。由于 `vet_context` 使用严格字段校验，调用方继续传入旧字段会被作为非法请求拒绝。

### 3.2 保持兼容的字段

以下字段为兼容保留，不属于本次范围迁移的变更面：

| 字段 | 兼容策略 |
|---|---|
| `input` | 文本与 OpenAI 风格消息输入继续兼容 |
| `attachments` | 附件引用结构保持不变 |
| `stream` | 同步与 SSE 流式响应开关保持不变 |
| `metadata` | 继续作为非控制语义透传字段 |
| `turn_options.idempotency_key` | 幂等键语义保持不变 |
| `vet_context.pet_info` | 仅保留为请求侧未验证宠物信息 |

`vet_context.pet_info` 的保留是兼容入口，而不是权威资料入口。后续代码不得用它绕过 `scope_assertion.profile` 缺失，也不得用它覆盖 `PetContext.verified_profile`。

### 3.3 暂未纳入本次迁移的兼容接口

以下接口仍保留既有 `TrustedIdentity` 或路径参数授权方式：

| 模块 | 当前状态 | 保留原因 |
|---|---|---|
| `src/vet_agent/api/report_routes.py` | 继续使用 `authorize_identity` | 报告接口属于报告摄取和查询域，本次不跨域改造 |
| `src/vet_agent/api/memory_routes.py` | 继续使用 `authorize_identity` | 记忆管理接口后续应结合记忆写入策略统一迁移 |
| `src/vet_agent/api/admin_routes.py` | 继续使用 `authorize_identity` | 管理接口需要单独定义运维主体和管理范围 |

这些保留路径不是主业务对话入口的回退机制。它们只用于避免本次范围迁移扩散到报告、记忆管理和管理后台领域。

## 4. 数据链落点

当前实现的数据链落点如下：

| 阶段 | 主要文件 | 作用 |
|---|---|---|
| 入口 DTO | `src/ingress/dto.py` | 定义入口层 `scope_assertion`、严格 `vet_context` 与内部入口请求 |
| 入口授权 | `src/ingress/routes.py` | 在创建核心回合请求前完成范围授权，并注入内部授权快照 |
| 入口适配 | `src/vet_agent/ingress_adapter.py` | 将入口 DTO 转换为核心 `AgentTurnRequest` |
| 核心契约 | `src/vet_agent/contracts.py` | 定义核心层 `ScopeAssertion`、`TrustedIdentity`、`AuthorizedScopeContext` |
| 范围服务 | `src/vet_agent/services/scope.py` | 校验声明、写入画像投影、绑定 session、输出范围上下文 |
| 访问控制 | `src/vet_agent/services/access_control.py` | 认证 API Key，并把范围授权委托给 `ScopeContextService` |
| 宠物上下文 | `src/vet_agent/services/context.py` | 使用已授权快照构造 `PetContext`，隔离未验证 `pet_info` |
| 范围仓储 | `src/vet_agent/repositories/scope.py` | 通过仓储协议访问宠物画像投影和 session 绑定 |
| 数据模型 | `src/vet_agent/db/models.py` | 保存 `pet_profiles` 与 `pet_session_bindings` |

后续模块应优先消费 `TrustedIdentity`、`PetContext.verified_profile` 和必要的审计字段，而不是重新解析 `scope_assertion`。

## 5. 本地画像投影语义

Agent 侧 `pet_profiles` 不是宠物主档的第二权威源。它的准确定位是：

```text
主服务已验证宠物基础资料在 Agent 侧的持久化投影
```

本地投影的用途：

1. 支持 Agent 冷启动时基于合法 `scope_assertion` 初始化画像。
2. 支持后续回合快速读取已验证基础资料。
3. 为 trace、记忆、报告和排障保留 Agent 侧视角的画像版本。
4. 支持 session 绑定策略在 Agent 侧执行一致性保护。

本地投影不得用于：

1. 绕过 BFF 对宠物归属、删除、停用和转移的拦截。
2. 在缺少当前有效 `scope_assertion` 时重新激活已删除或已停用宠物。
3. 替代主服务 `master_pet_info`。
4. 接收 `vet_context.pet_info` 或用户自然语言直接覆盖。

如果主服务发生宠物解绑、删除、停用或转移归属，BFF 必须在调用 Agent 前拦截。Agent 内可能遗留历史投影，这类数据可以作为审计或后续冷库治理对象，但不得作为当前请求放行依据。

## 6. 会话范围语义

当前会话策略固定为：

```text
single_user_pet_per_session
```

含义是：

```text
同一个 session_id 只能绑定同一组 user_id + pet_id
```

它不是“一只宠物全局只能有一个 session”，也不是“一个用户全局只能有一个 session”。

该策略的作用是防止调用方错误复用 `session_id`，导致不同用户或不同宠物的记忆、问诊状态、trace 和上下文相互污染。

## 7. 有意保留的 TODO

以下 TODO 是有意保留的工程边界，用于避免本次迁移退化为跨域重写。

| TODO | 边界说明 | 后续建议 |
|---|---|---|
| OPA 范围策略接入 | 当前 `DeterministicScopePolicyEvaluator` 只做结构化事实裁决，不实现 Rego 策略 | 后续接入 OPA 时替换 `ScopePolicyEvaluator` 实现，保持调用方不变 |
| 调用方身份与 `issuer` 强绑定 | 当前 API Key 认证主体与 `scope_assertion.issuer` 仅可形成审计关联 | 后续增加 API Key 与 issuer 的配置映射，再启用强一致校验 |
| 管理接口范围声明迁移 | 报告、记忆、管理接口仍使用 `authorize_identity` | 后续按各自领域定义专用 assertion，不复用主对话 assertion |
| 宠物资料纠正流程 | `vet_context.pet_info` 和用户文本中的资料差异只保留为未验证线索 | 后续由 BFF 或资料域实现确认、回写和版本审计 |
| 宠物删除与冷库治理 | Agent 当前不主动清理主服务已删除宠物的历史投影 | 后续由数据治理任务或 BFF 事件驱动冷库迁移 |
| 多数据源画像来源 | 当前 `scope_assertion.source.table` 固定为 `master_pet_info` | 后续如接入设备、服务状态或医疗档案，应扩展新字段，不复用 `pet_info` |
| 更细的 profile 字段枚举 | 当前仅强制 `species`，其他字段按主服务能力渐进补齐 | 后续结合 BFF 数据质量和临床链路需要逐步收紧枚举 |
| 审计 metadata 扩展 | 当前范围裁决结果主要在错误详情和内部上下文中保留 | 后续 trace 表应记录范围声明摘要、策略动作和投影版本 |

## 8. 明确不做事项

为了保持本次迁移边界清晰，以下事项不属于当前阶段：

1. 不把 Agent 改造成用户登录系统。
2. 不让 Agent 直接查询主服务数据库。
3. 不让 Agent 独立维护宠物归属权威库。
4. 不通过 `vet_context.pet_info` 或用户文本自动注册宠物。
5. 不在主业务入口缺少 `scope_assertion` 时回退旧字段。
6. 不把用户输入中提到的另一只宠物自动切换为当前 `pet_id`。
7. 不在范围服务中实现临床状态机。
8. 不在 OPA 接入前自造复杂策略语言。
9. 不在本次迁移中重写 RAG、记忆、报告和安全链路。

## 9. Fail Fast 规则

主业务入口遇到以下情况应拒绝请求，而不是进入兼容兜底：

| 场景 | 预期行为 |
|---|---|
| 缺少 `scope_assertion` | 返回缺少必要上下文错误 |
| 缺少 `user_id`、`pet_id` 或 `session_id` | 返回缺少必要上下文错误 |
| `scope_assertion.profile.species` 缺失 | 返回请求结构错误或缺少必要画像错误 |
| `scope_assertion.source.record_id` 与 `pet_id` 不一致 | 返回请求结构错误 |
| `scope_assertion.expires_at` 已过期 | 拒绝进入主链路 |
| `ownership_verified` 为 `false` | 拒绝进入主链路 |
| `pet_deleted` 为 `true` | 拒绝进入主链路 |
| `pet_active` 为 `false` | 拒绝进入主链路 |
| `session_id` 已绑定其他 `user_id + pet_id` | 拒绝进入主链路 |
| `vet_context` 携带旧身份字段 | 返回请求结构错误 |

## 10. 后续实现对齐原则

后续迁移临床安全、记忆、RAG、报告、策略和 trace 时，应遵循以下约束：

1. 需要身份范围时，只消费 `TrustedIdentity`。
2. 需要可信宠物基础资料时，只消费 `PetContext.verified_profile`。
3. 需要请求侧宠物资料时，必须显式标记为未验证资料。
4. 需要写入权威事实时，不得直接采信 `vet_context.pet_info`。
5. 需要跨 session 记忆时，必须以 `user_id + pet_id` 为边界。
6. 需要当前问诊状态时，必须以 `session_id + user_id + pet_id` 为边界。
7. 需要策略裁决时，应通过可替换策略门面，不在业务 Agent 中散落硬编码判断。
8. 需要管理接口扩展时，应定义专用内部声明或管理范围，不复用主对话请求字段。
9. 需要新增数据库访问时，只通过仓储协议暴露，不在业务层直接操作 SQLAlchemy 模型。

## 11. 验收基线

当前阶段后续修改不应破坏以下基线：

1. 完整 `scope_assertion` 可以在本地无画像投影时完成冷启动。
2. `vet_context.pet_info` 与 `scope_assertion.profile` 冲突时，主链路使用服务端画像。
3. 缺少 `scope_assertion` 时不回退旧字段。
4. 旧版 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id` 会被拒绝。
5. 同一 `session_id` 绑定其他 `user_id + pet_id` 会被拒绝。
6. 入口授权只执行一次，核心编排层复用内部授权快照。
7. 主业务入口响应结构、流式协议和幂等键语义保持兼容。

## 12. 关联文档

1. `docs/api/external_api.md`
2. `docs/api/agent-turn-scope-assertion-change-notice.md`
3. `docs/api/agent-turn-scope-assertion-internal-dev-guide.md`
4. `docs/architecture/agent-middleware-migration-plan.md`
5. `docs/external/databases/20260812-BFF 主服务：用户宠物设备与服务状态表结构说明.md`
