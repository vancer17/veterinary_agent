<!--
=============================================================================
文件: docs/api/agent-turn-scope-assertion-change-notice.md
作用: 说明兽医 Agent 主接口身份、宠物、会话与宠物画像字段结构调整方案，用于通知 BFF、App、后端业务线和测试线进行适配。
范围: 适用于 POST /agent/turns 与 POST /openai/v1/responses 兼容入口的请求结构迁移。
说明: 本文档只描述 API 结构变更、调用方改造点、字段映射、兼容策略和验收要求；最终接口契约以 docs/api/external_api.md 为准。
维护: 本文档应随 scope_assertion 契约、BFF 字段来源、主服务宠物资料表结构或灰度策略变更同步更新。
=============================================================================
-->

# 兽医 Agent API 结构变更通知：`scope_assertion` 范围声明

## 1. 变更摘要

兽医 Agent 主接口将身份、宠物、会话和服务端已验证宠物基础画像统一收束到顶层 `scope_assertion` 字段。

旧版 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id` 不再作为正式契约字段。后续 BFF 或其他内部服务调用 Agent 时，应通过 `scope_assertion` 传递本轮服务端范围声明。

本次变更不要求 App 端直连 Agent。App 仍只调用 BFF；BFF 负责用户登录、宠物归属校验、宠物启停校验、`session_id` 发放和 `scope_assertion` 组装。

## 2. 变更原因

当前架构中，Agent 服务不直接面向 App、Web 或其他用户端暴露，而是由 BFF 或其他授权内部服务通过 HTTP 调用。因此：

1. `user_id`、`pet_id`、`session_id` 属于服务端访问范围声明，不应与请求侧宠物资料混放在 `vet_context` 中。
2. `pet_info` 历史上容易被误解为服务端可信画像，但它本质上可能来自请求侧自报或兼容字段，不应参与鉴权、冷启动画像写入或临床硬判断。
3. Agent 不应主动查询主服务数据库校验宠物归属，避免越过 BFF 职责边界并形成双可信源。
4. Agent 侧 `pet_profiles` 应作为上游已验证宠物资料的本地投影，而不是独立宠物主档权威库。
5. `scope_assertion` 可以把调用方、身份、宠物、会话、归属裁决、画像来源和画像版本放在同一结构中，降低字段分散和冲突处理成本。

## 3. 影响范围

受影响调用方：

| 业务线 | 影响 |
|---|---|
| BFF / 主服务后端 | 需要组装并传入 `scope_assertion`，不再向 Agent 传递 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id` |
| App / Web / 小程序 | 不直接调用 Agent 时无接口改造；需要确认仍只向 BFF 传用户输入、附件引用、流式选项和幂等键 |
| 测试线 | 需要更新 Agent 联调请求样例、Mock 数据、契约测试和回归用例 |
| 运维 / 网关 | 需要确认 Agent 仅允许授权内部服务访问，避免普通请求伪造 `scope_assertion` |
| Agent 线 | 需要在入口 DTO、范围服务、宠物画像投影和文档中落地新契约 |

不受影响或弱影响：

| 能力 | 说明 |
|---|---|
| `input` 用户文本 | 字段结构保持不变 |
| `attachments` 附件引用 | 字段结构保持不变 |
| `stream` SSE 开关 | 字段结构保持不变 |
| `turn_options.idempotency_key` | 字段结构保持不变 |
| 同步 / 流式响应结构 | 本次不调整响应主结构 |

## 4. 旧结构与新结构对比

### 4.1 旧请求结构

旧结构将可信身份范围和宠物资料放在 `vet_context` 中：

```json
{
  "input": "我家猫今天吐了两次，还不太吃东西，要不要紧？",
  "stream": false,
  "vet_context": {
    "user_id": "user_123",
    "pet_id": "pet_789",
    "session_id": "sess_456",
    "pet_info": {
      "species": "cat",
      "breed": "domestic_shorthair",
      "age": "3y",
      "weight_kg": 4.6
    }
  }
}
```

旧结构存在的问题：

1. `user_id`、`pet_id`、`session_id` 是访问范围字段，却放在业务上下文对象中。
2. `pet_info` 既可能被理解为 BFF 注入的资料，也可能被理解为客户端自报资料，可信边界不清晰。
3. 冷启动时难以区分“可信上游画像投影初始化”和“客户端自报资料自动注册”。
4. 字段分散后，后续接入服务端画像版本、来源表、更新时间和归属裁决会继续膨胀 `vet_context`。

### 4.2 新请求结构

新结构通过 `scope_assertion` 承载服务端范围声明：

```json
{
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "input": "我家猫今天吐了两次，还不太吃东西，要不要紧？",
  "stream": false,
  "metadata": {
    "client": "miniapp",
    "client_version": "1.0.0"
  },
  "scope_assertion": {
    "schema_version": "v1",
    "issuer": "bff-main-service",
    "issued_at": "2026-08-12T10:00:00Z",
    "expires_at": "2026-08-12T10:02:00Z",
    "user_id": "user_123",
    "pet_id": "pet_789",
    "session_id": "sess_456",
    "authorization": {
      "ownership_verified": true,
      "pet_active": true,
      "pet_status": "active",
      "pet_deleted": false
    },
    "profile": {
      "pet_code": "PET202608120001",
      "name": "团子",
      "species": "cat",
      "breed": "domestic_shorthair",
      "age": "3y",
      "age_months": 36,
      "weight_kg": 4.6,
      "sex": "female",
      "neutered": true
    },
    "source": {
      "system": "bff-main-service",
      "database": "app_dev",
      "table": "master_pet_info",
      "record_id": "pet_789",
      "record_updated_at": "2026-08-12T09:58:00Z",
      "data_source": "manual"
    },
    "session_policy": {
      "binding_mode": "single_user_pet_per_session"
    }
  },
  "vet_context": {
    "pet_info": {}
  },
  "attachments": [],
  "turn_options": {
    "idempotency_key": "idem_01HZYK8JQ7M3V9QF8Y5W0A2B3C"
  }
}
```

## 5. 字段变更清单

### 5.1 删除字段

以下字段从正式契约中移除：

| 旧字段 | 替代字段 | 说明 |
|---|---|---|
| `vet_context.user_id` | `scope_assertion.user_id` | 用户身份范围改由服务端范围声明承载 |
| `vet_context.pet_id` | `scope_assertion.pet_id` | 宠物范围改由服务端范围声明承载 |
| `vet_context.session_id` | `scope_assertion.session_id` | 会话范围改由服务端范围声明承载 |

### 5.2 语义降级字段

| 旧字段 | 新字段 | 说明 |
|---|---|---|
| `vet_context.pet_info` | 保留 `vet_context.pet_info` | 仅作为请求侧非可信审计信息，不参与鉴权、冷启动画像写入或临床硬判断 |

### 5.3 新增字段

| 新字段 | 必填 | 说明 |
|---|---:|---|
| `scope_assertion.schema_version` | 是 | 声明结构版本，当前为 `v1` |
| `scope_assertion.issuer` | 是 | 声明签发方，例如 `bff-main-service` |
| `scope_assertion.issued_at` | 是 | 声明签发时间 |
| `scope_assertion.expires_at` | 否 | 声明过期时间，建议短 TTL |
| `scope_assertion.user_id` | 是 | BFF 已认证用户 ID |
| `scope_assertion.pet_id` | 是 | BFF 已完成归属校验的宠物 ID |
| `scope_assertion.session_id` | 是 | BFF 发放或复用的连续问诊会话 ID |
| `scope_assertion.authorization` | 是 | 归属与宠物状态裁决摘要 |
| `scope_assertion.profile` | 是 | 服务端已验证宠物基础画像 |
| `scope_assertion.source` | 是 | 主服务数据来源与版本审计信息 |
| `scope_assertion.session_policy` | 否 | session 范围策略声明 |
| `vet_context` | 否 | 请求侧非可信上下文；当前仅允许 `pet_info` |

## 6. `scope_assertion` 字段定义

### 6.1 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `schema_version` | string | 是 | 固定为 `v1` |
| `issuer` | string | 是 | 签发方服务标识 |
| `issued_at` | string | 是 | ISO 8601 时间 |
| `expires_at` | string | 否 | ISO 8601 时间 |
| `user_id` | string | 是 | 主服务用户 ID |
| `pet_id` | string | 是 | 主服务宠物 ID |
| `session_id` | string | 是 | BFF 发放或复用的会话 ID |
| `authorization` | object | 是 | BFF 已完成的归属与宠物状态裁决 |
| `profile` | object | 是 | 主服务宠物基础资料归一结果 |
| `source` | object | 是 | 来源系统、来源表和更新时间 |
| `session_policy` | object | 否 | 一 session 一宠策略声明 |

### 6.2 `authorization`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `ownership_verified` | boolean | 是 | BFF 已确认 `master_pet_info.owner_id` 与当前用户一致 |
| `pet_active` | boolean | 是 | 宠物允许进入 Agent 服务，通常由 `status == "active"` 且 `deleted_at IS NULL` 推导 |
| `pet_status` | string | 是 | `master_pet_info.status` 原始值 |
| `pet_deleted` | boolean | 是 | `master_pet_info.deleted_at` 是否非空 |

### 6.3 `profile`

`profile` 由 BFF 从主服务 `master_pet_info` 表读取并归一，不由 Agent 主动查询主服务数据库。

| 新字段 | 类型 | 必填 | 来源字段 | 说明 |
|---|---|---:|---|---|
| `pet_code` | string | 否 | `pet_code` | 宠物业务编码 |
| `name` | string | 否 | `name` | 宠物名称 |
| `species` | string | 是 | `species` | 宠物物种，建议归一为 `cat`、`dog` 或后续明确枚举 |
| `sex` | string | 否 | `gender` | 宠物性别，建议归一为 `male`、`female`、`unknown` |
| `birthday` | string | 否 | `birthday` | 出生日期，格式为 `YYYY-MM-DD` |
| `age_months` | integer | 否 | `age_months` 或由 `birthday` 推导 | 年龄月数 |
| `age` | string | 否 | 由 `age_months` 或 `birthday` 推导 | 当前 Agent 上下文摘要兼容字段 |
| `breed` | string | 否 | `variety` | 品种 |
| `weight_kg` | number | 否 | `weight` | 体重，单位为千克 |
| `neutered` | boolean | 否 | `sterilized` | 是否绝育 |
| `neutered_date` | string | 否 | `neutered_date` | 绝育日期 |
| `reproduction_status` | string | 否 | `reproduction_status` | 繁育状态 |
| `activity_level` | integer | 否 | `activity_level` | 活跃度 |
| `region` | string | 否 | `region` | 宠物所在地区 |

### 6.4 `source`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `system` | string | 是 | 来源系统，建议为 `bff-main-service` |
| `database` | string | 否 | 来源数据库，例如 `app_dev` |
| `table` | string | 是 | 固定为 `master_pet_info` |
| `record_id` | string | 是 | 来源记录 ID，应与 `scope_assertion.pet_id` 对应 |
| `record_updated_at` | string | 是 | 来源记录更新时间，来自 `master_pet_info.updated_at` |
| `data_source` | string | 否 | `master_pet_info.data_source` 原始值 |

### 6.5 `session_policy`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `binding_mode` | string | 否 | 当前推荐值为 `single_user_pet_per_session` |

`single_user_pet_per_session` 的含义是：同一个 `session_id` 只能绑定同一组 `user_id + pet_id`。它不是“一只宠物全局只能存在一个 session”。

## 7. BFF 改造要求

BFF 调用 Agent 前必须完成以下步骤：

1. 校验用户登录态，取得当前 `user_id`。
2. 校验路由或业务入参中的 `pet_id` 属于当前 `user_id`。
3. 校验宠物未删除、未停用，并满足进入 Agent 服务的业务条件。
4. 发放新 `session_id` 或复用连续问诊已有 `session_id`。
5. 从 `master_pet_info` 读取宠物基础资料。
6. 将 `master_pet_info` 字段归一为 `scope_assertion.profile`。
7. 填充 `scope_assertion.authorization` 和 `scope_assertion.source`。
8. 调用 Agent 时不再传递 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id`。
9. 不透传客户端伪造的 `scope_assertion`、`user_id`、`pet_id`、`session_id` 或 `vet_context`。

## 8. Agent 侧处理要求

Agent 入口和范围服务应按以下规则处理：

1. `scope_assertion` 缺失时拒绝请求。
2. `scope_assertion.user_id`、`scope_assertion.pet_id`、`scope_assertion.session_id` 缺失时拒绝请求。
3. `authorization.ownership_verified != true` 时拒绝请求。
4. `authorization.pet_active != true` 或 `authorization.pet_deleted == true` 时拒绝请求。
5. `profile.species` 缺失时拒绝请求或进入明确的 Fail Fast 错误路径。
6. 基于 `scope_assertion.profile` 初始化或刷新 Agent 侧 `pet_profiles` 投影。
7. 首次看到 `session_id` 时绑定当前 `user_id + pet_id`。
8. 后续看到同一 `session_id` 时，必须仍然对应同一组 `user_id + pet_id`，否则拒绝请求。
9. 不使用 `vet_context.pet_info` 覆盖 `scope_assertion.profile`。
10. 不主动查询主服务数据库获取宠物归属或宠物基础资料。

## 9. 错误码调整

新增或调整后的典型错误：

| HTTP 状态 | 错误码 | 场景 | 调用方处理 |
|---|---|---|---|
| `400` | `INVALID_REQUEST` | 请求体结构错误、字段类型错误、时间格式错误 | 修正请求结构 |
| `401` | `UNAUTHORIZED` | 内部服务凭据缺失或无效 | 检查服务级认证配置 |
| `403` | `FORBIDDEN` | 调用方未授权、归属声明为否、宠物停用、session 范围不一致 | BFF 检查用户、宠物、session 和服务权限 |
| `422` | `MISSING_REQUIRED_CONTEXT` | `scope_assertion` 或核心字段缺失 | BFF 补齐声明字段 |
| `503` | `SERVICE_UNAVAILABLE` | Agent 依赖未就绪或本地投影写入不可用 | 稍后重试或切换降级策略 |

错误示例：

```json
{
  "code": "MISSING_REQUIRED_CONTEXT",
  "message": "scope_assertion.profile.species is required",
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "details": [
    {
      "field": "scope_assertion.profile.species",
      "reason": "required"
    }
  ]
}
```

## 10. 兼容与迁移计划

建议按以下阶段迁移：

| 阶段 | 策略 | 说明 |
|---|---|---|
| 阶段 1 | 双写请求字段 | BFF 同时传新 `scope_assertion` 和仅含 `pet_info` 的 `vet_context`，Agent 只从 `scope_assertion` 读取身份范围 |
| 阶段 2 | 新字段主路径 | BFF 只传 `scope_assertion` 与可选 `vet_context.pet_info`，旧 `vet_context.user_id/pet_id/session_id` 不再进入正式联调样例 |
| 阶段 3 | 移除旧字段 | Agent DTO、测试、文档移除旧身份范围字段 |

正式生产建议至少完成阶段 2 后再切换主流量。

## 11. 联调验收项

BFF 与 Agent 联调时至少覆盖以下用例：

1. `scope_assertion` 完整且宠物为 `active`，请求成功。
2. 缺少 `scope_assertion.user_id`，返回 `422`。
3. 缺少 `scope_assertion.pet_id`，返回 `422`。
4. 缺少 `scope_assertion.session_id`，返回 `422`。
5. `authorization.ownership_verified=false`，返回 `403`。
6. `authorization.pet_active=false`，返回 `403`。
7. `authorization.pet_deleted=true`，返回 `403`。
8. 缺少 `profile.species`，返回 `422` 或约定的 Fail Fast 错误。
9. 同一个 `session_id` 首次绑定 `user_id + pet_id` 后，下一轮继续使用同一组范围，请求成功。
10. 同一个 `session_id` 复用到其他 `user_id` 或 `pet_id`，返回 `403`。
11. `vet_context.pet_info` 与 `scope_assertion.profile` 冲突时，Agent 不使用 `vet_context.pet_info` 覆盖服务端画像。
12. BFF 不传旧版 `vet_context.user_id/pet_id/session_id` 时，请求仍可成功。

## 12. 代码改造清单

BFF 建议改造点：

1. 新增 `ScopeAssertion` DTO。
2. 在调用 Agent 前由主服务宠物资料表构造 `ScopeAssertion.profile`。
3. 在调用 Agent 前由归属校验结果构造 `ScopeAssertion.authorization`。
4. 在调用 Agent 前填充 `ScopeAssertion.source`。
5. 移除对 Agent 请求体中旧版 `vet_context.user_id/pet_id/session_id` 的写入。
6. 阻断客户端透传 `scope_assertion` 或 `vet_context`。
7. 更新契约测试、联调样例和 Mock 数据。

Agent 建议改造点：

1. 入口 DTO 新增 `scope_assertion`，并将 `vet_context` 收束为仅含 `pet_info`。
2. `TrustedIdentity` 从 `scope_assertion` 构造。
3. 范围服务基于 `scope_assertion.profile` upsert `pet_profiles` 投影。
4. `PetContextProvider` 只消费服务端已验证画像投影。
5. 移除旧版 `vet_context.user_id/pet_id/session_id` 主路径。
6. 保留必要灰度期一致性校验后，再移除旧字段兼容逻辑。

## 13. 注意事项

1. `scope_assertion` 不是客户端字段，客户端不得直接构造或覆盖。
2. `scope_assertion.profile` 来自主服务 `master_pet_info`，由 BFF 归一后传入 Agent。
3. Agent 不主动查询主服务数据库，避免职责越界。
4. Agent 侧本地 `pet_profiles` 是上游已验证宠物资料投影，不是独立宠物主档权威库。
5. `vet_context.pet_info` 只作审计，不参与鉴权和临床硬判断。
6. 一 session 一宠语义为同一个 `session_id` 只能绑定同一组 `user_id + pet_id`。
7. 主服务中宠物解绑、删除、停用和转移归属问题应由 BFF 拦截；Agent 侧历史投影可按审计和冷库策略处理。
