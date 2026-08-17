<!--
=============================================================================
文件: docs/api/external_api.md
作用: 定义兽医 Agent HTTP 入口的外部契约、请求字段、错误码、治理接口和联调语义。
范围: 面向 BFF、内部服务调用方、治理后台、测试与运维排障；当前以 scope_assertion 作为主业务入口的唯一可信范围声明入口。
维护: 当入口 DTO、范围声明字段、错误码、治理接口或 BFF 调用约定变化时，应同步更新本文档。
=============================================================================
-->

# 兽医 Agent 对外 API 文档

## 1. 文档说明

本文定义兽医 Agent 第一阶段对外 HTTP API 契约，范围仅覆盖 `ApiIngress` 暴露的入口接口。

关联文档：

- [`docs/component_catalog.md`](../component_catalog.md) §4.1 API 接入组件
- [`docs/components/l0/api-ingress/design.md`](../components/l0/api-ingress/design.md)
- [`docs/components/l2-vet-business/vet-trace-schema/design.md`](../components/l2-vet-business/vet-trace-schema/design.md)
- [`docs/interface_spec.md`](../interface_spec.md)

当前阶段服务部署在可信局域网内，Agent 服务不直接面向 App、Web 或其他用户端暴露，而是由 BFF 或其他已授权内部服务通过 HTTP 调用。用户登录、宠物归属校验、宠物启停校验和 `session_id` 发放职责由调用方承担。

Agent 请求必须通过 `scope_assertion` 显式携带本轮服务端范围声明。`scope_assertion` 是身份、宠物、会话和已验证宠物基础画像的唯一可信入口；旧版 `vet_context.user_id`、`vet_context.session_id`、`vet_context.pet_id` 不再属于正式契约。请求侧自报宠物资料不得与服务端画像混用，仅可放入 `vet_context.pet_info` 作为非可信审计信息。

## 2. 接口总览

| 方法 | 路径 | 定位 |
| --- | --- | --- |
| `POST` | `/agent/turns` | 创建一轮兽医 Agent 对话，生产主业务入口 |
| `POST` | `/openai/v1/responses` | OpenAI Responses 风格兼容入口，用于 SDK 适配、内部调试或迁移 |
| `GET` | `/admin/rag/misses` | 内部治理接口，分页查询 RAG 无命中知识缺口记录 |
| `PATCH` | `/admin/rag/misses/{miss_id}` | 内部治理接口，更新 RAG 无命中知识缺口治理状态 |
| `GET` | `/health` | 存活检查 |
| `GET` | `/ready` | 就绪检查 |

路径说明：

- 文档中的路径为服务逻辑路径；部署网关可增加环境前缀、服务名前缀或版本前缀。
- `/agent/turns` 是正式业务入口。
- `/openai/v1/responses` 是兼容入口，不作为兽医业务主契约。
- `/admin/*` 是内部治理接口，只允许治理后台、运维或授权内部服务通过受控网络访问，不面向 App、Web 或小程序直接开放。
- 同步响应与流式响应共用同一个对话接口，通过 `stream` 字段区分。

## 3. 通用协议约定

### 3.1 请求头

| Header | 必填 | 说明 |
| --- | --- | --- |
| `Content-Type: application/json` | 是，POST 接口 | 请求体格式 |
| `Accept: application/json` | 否 | 同步响应建议值 |
| `Accept: text/event-stream` | 否 | 流式响应建议值；最终是否流式以 `stream=true` 为准 |
| `Authorization: Bearer <token>` | 条件必填 | 当启用 API 鉴权或配置 `VET_AGENT_API_KEYS` 时必填 |
| `X-API-Key` | 条件必填 | 当启用 API 鉴权或配置 `VET_AGENT_API_KEYS` 时可替代 Bearer Token |
| `X-User-ID` | 否 | 内部治理接口可用于记录治理操作人；不得替代 `scope_assertion.user_id` |
| `X-Request-ID` | 否 | 上游请求 ID；不传时由服务生成 |
| `X-Trace-ID` | 否 | 上游链路 ID；不传时由服务生成 |

`request_id` 与 `trace_id` 也可在请求体中透传。若请求头和请求体同时传入同名 ID，二者必须一致；否则按 `400 INVALID_REQUEST` 处理。

鉴权说明：

- 生产环境建议配置 `VET_AGENT_API_KEYS` 并由网关或调用方注入 `Authorization` 或 `X-API-Key`。
- `scope_assertion` 仍是主业务入口的身份、宠物、会话和画像范围声明；API Key 只表示调用方服务凭据，不替代宠物归属校验。
- 内部治理接口不携带 `scope_assertion`，但仍必须通过访问控制认证，并应由网关限制来源网络和调用主体。

### 3.2 通用字段语义

| 字段 | 说明 |
| --- | --- |
| `request_id` | 单次入口请求 ID，用于访问日志、排障、幂等输入关联 |
| `trace_id` | 全链路追踪 ID，用于编排、留痕和下游排障 |
| `metadata` | 客户端透传元信息；不得承载安全绕过、工具授权、RAG 禁令豁免等控制语义 |
| `stream` | 是否启用 SSE 流式响应；未传时采用服务默认响应模式 |
| `reasoning_display` | 下游已允许展示的推理摘要文本投影；不是模型隐藏思维链，也不是完整业务逻辑链 |

### 3.3 错误响应结构

HTTP 状态码大于等于 `400` 时，响应体统一使用以下结构：

```json
{
  "code": "MISSING_REQUIRED_CONTEXT",
  "message": "scope_assertion.pet_id is required",
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "details": [
    {
      "field": "scope_assertion.pet_id",
      "reason": "required"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `code` | 是 | 机器可读错误码 |
| `message` | 是 | 面向研发的错误说明 |
| `request_id` | 是 | 本次请求 ID |
| `trace_id` | 是 | 本次链路 ID |
| `details` | 否 | 字段级或依赖级错误明细 |

通用错误码：

| HTTP 状态 | 错误码 | 场景 |
| --- | --- | --- |
| `400` | `INVALID_REQUEST` | 请求体结构错误、字段类型错误、请求头与请求体 ID 冲突 |
| `401` | `UNAUTHORIZED` | 缺少或提供了非法 API 凭据 |
| `403` | `FORBIDDEN` | 身份、宠物、会话范围不满足授权策略 |
| `409` | `CONFLICT` | 幂等、turn lock 或会话执行冲突 |
| `422` | `MISSING_REQUIRED_CONTEXT` | 缺少 `scope_assertion.user_id`、`scope_assertion.session_id`、`scope_assertion.pet_id` 或其他必需范围声明字段 |
| `413` | `PAYLOAD_TOO_LARGE` | 请求体或附件元信息超过入口限制 |
| `429` | `RATE_LIMITED` | 触发入口限流 |
| `503` | `SERVICE_UNAVAILABLE` | 编排层或关键依赖不可用，例如模型网关、数据库、RAG 召回、回答相关 RAG 或追问相关 RAG 无有效知识命中、治理留痕依赖未就绪 |
| `504` | `ORCHESTRATOR_TIMEOUT` | 编排层处理超时 |

客户端中断 SSE 连接时，服务端访问日志记录 `CLIENT_CANCELLED`；若连接已经断开，通常不再向客户端发送错误响应体。

## 4. `POST /agent/turns`

### 4.1 定位

创建一轮兽医 Agent 对话。

该接口采用 OpenAI Responses 风格组织 `model`、`input`、`stream`、`output`，同时显式保留兽医业务扩展字段 `scope_assertion`、`vet_context`、`attachments`、`turn_options`、`segments`、`vet_result`、`reasoning_display`。

该接口不等同于 OpenAI Responses API 原样复刻。它是兽医业务主接口，必须显式携带本轮可信身份上下文。

### 4.2 入口职责

`ApiIngress` 在该接口中只执行入口层职责：

- 接收一轮对话请求。
- 校验基础请求结构。
- 校验必需范围声明字段。
- 校验 `scope_assertion` 中身份、宠物、会话、授权状态和画像来源的结构完整性。
- 校验响应模式和附件元信息完整性。
- 生成或透传 `request_id`、`trace_id`。
- 构造内部 `AgentTurnRequest`。
- 调用 `VetOrchestrator / GraphRuntime`。
- 将同步响应、SSE 事件或错误映射为 HTTP 响应。
- 忠实承载并转发下游已经允许展示的 `reasoning_display`。

`ApiIngress` 不执行以下业务逻辑：

- 不通过 JWT、OAuth 或用户登录态自行识别 App 用户。
- 不主动查询主服务数据库校验 `pet_id` 是否属于 `user_id`；该职责由 BFF 或授权内部调用方完成。
- 不主动发放 `session_id`；同一个 `session_id` 只能绑定同一组 `user_id + pet_id` 的策略由 Agent 范围服务执行一致性保护。
- 不识别急症、毒物、意图或 `generation_profile`。
- 不执行 RAG、OCR、记忆读写、模型调用或安全审查。
- 不生成、分类、审查、重写或解释 `reasoning_display`。
- 不展示模型隐藏 chain-of-thought、完整业务逻辑链、模型草稿或安全审查三联稿。
- 不在普通访问日志中记录完整医疗对话正文。

### 4.3 请求体

```json
{
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "model": "vet-agent-default",
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "我家猫今天吐了两次，还不太吃东西，要不要紧？"
        }
      ]
    }
  ],
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

顶层字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `request_id` | string | 否 | 上游请求 ID；不传时由服务生成 |
| `trace_id` | string | 否 | 上游链路 ID；不传时由服务生成 |
| `model` | string | 否 | 模型或模型策略标识；最终模型选择仍由服务端配置和编排决定 |
| `input` | array | 条件必填 | 本轮输入；与 `attachments` 至少存在一类有效内容 |
| `stream` | boolean | 否 | 是否启用 SSE 流式响应 |
| `metadata` | object | 否 | 普通透传元信息 |
| `scope_assertion` | object | 是 | BFF 或授权内部调用方注入的服务端范围声明，是身份、宠物、会话和已验证宠物基础画像的唯一可信入口 |
| `vet_context` | object | 否 | 请求侧非可信上下文；当前仅允许 `pet_info`，不得参与鉴权、冷启动画像写入或临床硬判断 |
| `attachments` | array | 条件必填 | 附件引用元信息；与 `input` 至少存在一类有效内容 |
| `turn_options` | object | 否 | 本轮入口选项 |

`scope_assertion` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 范围声明结构版本，当前固定为 `v1` |
| `issuer` | string | 是 | 声明签发方，例如 `bff-main-service`；Agent 应结合服务级认证确认调用方身份 |
| `issued_at` | string | 是 | 声明签发时间，ISO 8601 格式 |
| `expires_at` | string | 否 | 声明过期时间，ISO 8601 格式；建议由 BFF 设置短有效期 |
| `user_id` | string | 是 | BFF 已认证用户 ID，来自主服务账号体系 |
| `pet_id` | string | 是 | BFF 已完成归属校验的宠物 ID，来源为 `master_pet_info.id` |
| `session_id` | string | 是 | BFF 发放或复用的连续问诊会话 ID |
| `authorization` | object | 是 | BFF 对归属和宠物状态的裁决摘要 |
| `profile` | object | 是 | BFF 从主服务数据库读取并归一后的服务端已验证宠物基础画像 |
| `source` | object | 是 | 画像来源、记录版本和审计信息 |
| `session_policy` | object | 否 | 本轮 session 范围策略声明 |

`scope_assertion.authorization` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `ownership_verified` | boolean | 是 | BFF 是否已确认 `master_pet_info.owner_id` 与当前 `user_id` 一致 |
| `pet_active` | boolean | 是 | 宠物档案是否允许进入 Agent 服务；通常由 `status == "active"` 且 `deleted_at IS NULL` 推导 |
| `pet_status` | string | 是 | 主服务 `master_pet_info.status` 原始状态 |
| `pet_deleted` | boolean | 是 | 主服务 `master_pet_info.deleted_at` 是否非空 |

`scope_assertion.profile` 字段：

| 字段 | 类型 | 必填 | 来源字段 | 说明 |
| --- | --- | --- | --- | --- |
| `pet_code` | string | 否 | `master_pet_info.pet_code` | 宠物业务编码，用于审计和排障 |
| `name` | string | 否 | `master_pet_info.name` | 宠物名称 |
| `species` | string | 是 | `master_pet_info.species` | 宠物物种；建议由 BFF 归一为 `cat`、`dog` 或后续明确枚举 |
| `sex` | string | 否 | `master_pet_info.gender` | 宠物性别；建议由 BFF 归一为 `male`、`female`、`unknown` |
| `birthday` | string | 否 | `master_pet_info.birthday` | 出生日期，格式为 `YYYY-MM-DD` |
| `age_months` | integer | 否 | `master_pet_info.age_months` 或由 `birthday` 推导 | 年龄月数 |
| `age` | string | 否 | `age_months` 或 `birthday` 推导 | 供当前 Agent 上下文摘要兼容使用的年龄文本 |
| `breed` | string | 否 | `master_pet_info.variety` | 品种；由 BFF 将主服务字段 `variety` 归一为 `breed` |
| `weight_kg` | number | 否 | `master_pet_info.weight` | 体重千克值；由 BFF 将主服务字段 `weight` 归一为 `weight_kg` |
| `neutered` | boolean | 否 | `master_pet_info.sterilized` | 是否绝育；由 BFF 将主服务字段 `sterilized` 归一为 `neutered` |
| `neutered_date` | string | 否 | `master_pet_info.neutered_date` | 绝育日期，格式为 `YYYY-MM-DD` |
| `reproduction_status` | string | 否 | `master_pet_info.reproduction_status` | 繁育状态 |
| `activity_level` | integer | 否 | `master_pet_info.activity_level` | 活跃度 |
| `region` | string | 否 | `master_pet_info.region` | 宠物所在地区 |

`scope_assertion.source` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `system` | string | 是 | 来源系统，当前为 BFF 或主服务标识 |
| `database` | string | 否 | 来源数据库，例如 `app_dev` |
| `table` | string | 是 | 来源表，当前为 `master_pet_info` |
| `record_id` | string | 是 | 来源记录 ID，应与 `scope_assertion.pet_id` 对应 |
| `record_updated_at` | string | 是 | 来源记录更新时间，来自 `master_pet_info.updated_at` |
| `data_source` | string | 否 | 主服务 `master_pet_info.data_source` 原始值 |

`scope_assertion.session_policy` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `binding_mode` | string | 否 | 当前推荐值为 `single_user_pet_per_session`，含义为同一个 `session_id` 只能绑定同一组 `user_id + pet_id` |

`vet_context` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `pet_info` | object | 否 | 请求侧自报或兼容宠物资料，仅作为未验证审计信息；不得覆盖 `scope_assertion.profile` |

`input[]` 当前支持的最小形态：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 是 | 固定为 `message` |
| `role` | string | 是 | 当前外部请求仅允许 `user` |
| `content` | array | 是 | 输入内容数组 |

`content[]` 当前支持：

| `type` | 字段 | 说明 |
| --- | --- | --- |
| `input_text` | `text` | 用户文本输入 |
| `input_attachment` | `attachment_id` | 引用 `attachments[]` 中的附件 |

`attachments[]` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `attachment_id` | string | 是 | 附件 ID；在本轮请求中唯一 |
| `mime_type` | string | 是 | MIME 类型，例如 `image/jpeg`、`application/pdf` |
| `purpose` | string | 是 | 附件用途，例如 `lab_report`、`medical_record`、`general_context` |
| `storage_ref` | string | 是 | 上游文件服务或对象存储引用 |
| `metadata` | object | 否 | 附件普通元信息 |

附件约束：

- 本接口只接收附件元信息，不接收二进制文件上传，也不接收文件 base64 编码。
- 客户端应先通过文件服务、BFF 或对象存储上传文件，再将 `storage_ref` 作为附件引用传入本接口。
- 附件是否可作为医疗依据由下游业务组件判断。
- 第一阶段不接收影像判读类附件作为医学判读对象；入口层只做元信息校验，不做医学类型判定。
- 附件数量、元信息大小、允许 MIME 类型由 `RuntimeConfig` 控制。

`turn_options` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `idempotency_key` | string | 否 | 幂等键；整轮幂等判定由编排层或会话持久化层负责 |
| `response_mode` | string | 否 | 可选响应模式提示；与 `stream` 冲突时按 `stream` 处理 |

### 4.4 核心校验

入口层必须执行以下校验：

- 请求体必须是合法 JSON。
- `stream` 若传入，必须是 boolean。
- `scope_assertion.schema_version` 必填。
- `scope_assertion.issuer` 必填。
- `scope_assertion.issued_at` 必填。
- `scope_assertion.user_id` 必填。
- `scope_assertion.pet_id` 必填。
- `scope_assertion.session_id` 必填。
- `scope_assertion.authorization.ownership_verified` 必须为 `true`。
- `scope_assertion.authorization.pet_active` 必须为 `true`。
- `scope_assertion.authorization.pet_status` 必填。
- `scope_assertion.authorization.pet_deleted` 必须为 `false`。
- `scope_assertion.profile.species` 必填。
- `scope_assertion.source.table`、`scope_assertion.source.record_id`、`scope_assertion.source.record_updated_at` 必填。
- `input` 与 `attachments` 至少存在一类有效内容。
- `attachments[]` 的 `attachment_id`、`mime_type`、`purpose`、`storage_ref` 必填。
- 请求体与附件元信息不得超过入口限制。

入口层不得执行以下校验或判决：

- 不主动查询主服务数据库判断 `pet_id` 是否属于 `user_id`。
- 不根据 `vet_context.pet_info` 创建或覆盖服务端已验证宠物画像。
- 不根据用户文本改写、纠错或推断 `scope_assertion.pet_id`。
- 不判断附件是否为化验单、病历或其他医学资料。
- 不判断用户意图、急症、毒物、非医疗跨域级别。

Agent 范围服务必须执行以下一致性保护：

- 通过服务级认证确认调用方为可信内部服务，并保留认证主体与 `scope_assertion.issuer` 的审计关联。
- 基于 `scope_assertion` 初始化或刷新 Agent 侧 `pet_profiles` 投影。
- 首次看到 `scope_assertion.session_id` 时绑定当前 `user_id + pet_id`。
- 后续看到同一 `session_id` 时，必须仍然对应同一组 `user_id + pet_id`；否则返回 `403 FORBIDDEN`。
- 当声明缺失、声明过期、授权状态为否、画像核心字段缺失或本地投影写入失败时，应 Fail Fast。

### 4.5 同步响应

当 `stream=false` 或服务默认采用同步模式时，返回 `application/json`。

示例：

```json
{
  "id": "turn_01HZYK9G4DX8S7RC7J2MTNQ9V1",
  "object": "agent.turn",
  "created_at": "2026-07-05T15:04:05Z",
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "status": "completed",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "猫一天内反复呕吐并伴随食欲下降，建议先观察精神、饮水、排尿排便和是否继续呕吐。如果精神差、持续呕吐、吐血、腹痛或无法进水，应尽快就医。"
        }
      ]
    }
  ],
  "segments": [
    {
      "segment_id": "seg_001",
      "type": "medical_consultation",
      "title": "症状判断与下一步",
      "status": "completed",
      "output_text": "猫一天内反复呕吐并伴随食欲下降，需要结合精神、饮水、排便和是否持续呕吐判断紧急程度。",
      "references": [],
      "reasoning_display": {
        "projection_id": "rdp_seg_001",
        "segment_id": "seg_001",
        "title": "处理过程",
        "text": "我先根据你提供的呕吐次数和食欲变化检查是否存在需要立即就医的信号，再整理观察要点和需要线下就诊的触发条件。",
        "metadata": {}
      }
    }
  ],
  "reasoning_display": {
    "projection_id": "rdp_turn_001",
    "segment_id": null,
    "title": "本轮处理过程",
    "text": "我围绕猫今天呕吐和食欲下降的问题，优先检查急症风险，再组织护理观察和就医触发条件。",
    "metadata": {}
  },
  "vet_result": {
    "generation_profile": "standard",
    "route": "standard_consultation",
    "audit_tier": "A"
  },
  "metadata": {}
}
```

响应字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 本轮 turn ID |
| `object` | 固定资源类型，当前为 `agent.turn` |
| `created_at` | 服务端创建时间，ISO 8601 格式 |
| `request_id` | 请求 ID |
| `trace_id` | 链路 ID |
| `status` | `completed`、`requires_followup`、`safety_escalated`、`blocked` 等业务状态 |
| `output` | OpenAI Responses 风格输出内容 |
| `segments` | 兽医业务分段结果，由下游回复合成组件产生 |
| `reasoning_display` | 整轮可展示推理摘要；由下游产出并确认可展示，`ApiIngress` 仅透传 |
| `vet_result` | 面向客户端的兽医业务结构化摘要 |
| `metadata` | 普通元信息 |

回答相关 RAG 响应语义：

- 当本轮进入回答充分性 `answer` 分支并成功召回合格知识证据时，响应 `metadata` 可包含 `answer_rag`。
- `metadata.answer_rag` 用于审计和排障，表示本轮回答证据召回策略、召回后端、命中数量、召回阈值和证据摘要。
- `evidence` 或内部证据列表中的回答 RAG 证据只应包含可进入回复生成与审计的摘要，不应暴露完整 RAG chunk、prompt、模型草稿或隐藏推理链。
- 客户端不得依赖 `metadata.answer_rag` 强制驱动前端诊疗流程；正式展示仍以 `output`、`segments`、`vet_result` 和已允许展示的 `reasoning_display` 为准。

`metadata.answer_rag` 的稳定语义包括：

| 字段 | 说明 |
| --- | --- |
| `strategy` | 回答相关 RAG 策略标识 |
| `retrieval.backend` | 检索后端标识 |
| `retrieval.hit_count` | 归一化后的命中数量 |
| `retrieval.top_k` | 本轮召回数量上限 |
| `retrieval.min_score` | 本轮最低召回分数阈值 |
| `retrieval.hits` | 命中摘要列表；仅用于审计和排障，不作为完整知识原文 |

回答 RAG Fail Fast 语义：

- 当回答分支无法召回已启用、已审核、具备向量且达到阈值的知识资产时，服务返回 `503 SERVICE_UNAVAILABLE`。
- 服务不会返回空证据回答，不会构造默认回答模板，也不会继续进入自然语言回复生成。
- 若 RAG 无命中治理记录器可用，服务会在后台记录治理事件；该记录不改变当前 HTTP 响应结果。
- 调用方可根据错误 `details.reason` 判断排障方向，但不应把该错误转换为客户端侧的医学默认回答。

示例：

```json
{
  "code": "SERVICE_UNAVAILABLE",
  "message": "answer RAG retrieval returned no approved vector hits",
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "details": {
    "reason": "no_approved_vector_hits",
    "top_k": 5,
    "min_score": 0.35,
    "allowed_chunk_types": [
      "condition_overview",
      "triage",
      "red_flags",
      "home_advice"
    ],
    "domain": null
  }
}
```

追问相关 RAG 响应语义：

- 当本轮进入回答充分性 `ask` 分支并成功生成追问计划时，响应 `status` 为 `requires_followup`。
- 追问场景下 `segments[].type` 通常为 `followup_consultation`，客户端应优先展示 `segments[].output_text`。
- `metadata.followup_question_plan` 用于审计和前端展示对齐，表示本轮追问策略、召回摘要和实际生成的问题集合。
- `metadata.missing_slots` 表示本轮 OPA 认可的完整缺失槽位集合；受单轮最大追问数、问题优先级和知识命中约束，`followup_question_plan.questions` 可以是其子集。
- 客户端不得基于 `missing_slots` 自行生成追问问题，不得在 `followup_question_plan` 缺失时使用本地模板补齐追问。

`metadata.followup_question_plan` 的稳定语义包括：

| 字段 | 说明 |
| --- | --- |
| `strategy` | 追问相关 RAG 策略标识 |
| `rationale` | 结构化追问计划摘要；只用于展示辅助和审计，不作为隐藏推理链 |
| `retrieval.backend` | 检索后端标识 |
| `retrieval.hit_count` | 归一化后的命中数量 |
| `retrieval.top_k` | 本轮召回数量上限 |
| `retrieval.min_score` | 本轮最低召回分数阈值 |
| `retrieval.hits` | 命中摘要列表；仅用于审计和排障，不作为完整知识原文 |
| `questions[].slot` | 本问题对应的缺失槽位 |
| `questions[].question` | 面向用户展示的追问文本 |
| `questions[].reason` | 为什么该追问影响分诊或下一步建议 |
| `questions[].evidence_chunk_ids` | 本轮追问引用的证据 chunk 标识 |
| `questions[].evidence_titles` | 本轮追问引用的证据标题 |
| `questions[].priority` | 问题优先级，数值越小优先级越高 |

追问 RAG Fail Fast 语义：

- 当追问分支无法召回已启用、已审核、具备向量且达到阈值的追问知识资产时，服务返回 `503 SERVICE_UNAVAILABLE`。
- 服务不会生成默认追问，不会恢复关键词、正则、模板或 seed 回退，也不会把空追问计划返回给客户端。
- 若 RAG 无命中治理记录器可用，服务会记录 `rag_scope=followup_rag` 的治理事件；该记录不改变当前 HTTP 响应结果。
- 调用方可根据错误 `details.reason` 判断排障方向，但不应把该错误转换为客户端侧的医学默认追问。
- `details.query` 属于内部排障材料，可能包含结构化用户输入、问诊状态和宠物摘要，不应直接展示给普通用户。

示例：

```json
{
  "code": "SERVICE_UNAVAILABLE",
  "message": "followup RAG retrieval returned no approved vector hits",
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "details": {
    "reason": "no_approved_vector_hits",
    "query": "{\"domain\":\"gastrointestinal\",\"missing_slots\":[\"mental_status\",\"appetite\",\"vomiting\"]}",
    "top_k": 4,
    "min_score": 0.35,
    "allowed_chunk_types": [
      "followup_questions"
    ]
  }
}
```

`segments[]` 是客户端展示分段的推荐来源。急症段优先、医疗段优先于非医疗段、独立 OCR 段位置等顺序由 `VetResponseComposer` 与编排层保证，`ApiIngress` 只负责承载。

`segments[].reasoning_display` 是与单个业务分段关联的可展示推理摘要，优先用于多任务和多分段展示。`AgentTurnResponse.reasoning_display` 是整轮汇总摘要，适用于单任务或顶部折叠展示。

`reasoning_display` 字段结构：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `projection_id` | 是 | 可展示推理摘要投影 ID，用于前端定位和内部排障关联 |
| `segment_id` | 否 | 关联业务分段 ID；整轮摘要可为 `null` |
| `title` | 否 | 前端折叠区标题 |
| `text` | 是 | 已经由下游生成、裁剪并允许展示的推理摘要文本 |
| `metadata` | 否 | 普通扩展信息；不得包含隐藏思维链、完整 trace、审查三联稿或受限原文 |

`reasoning_display` 只表达用户可见的安全文本投影，不包含完整证据结构、guard action、裁剪原因、降级标记、prompt、模型草稿、隐藏 chain-of-thought、完整 OCR 原文或完整 RAG 片段。审查 Agent 的作用可以由下游写入 `text` 的安全摘要中体现，但不得暴露原始审查记录。

`vet_result` 只包含可对外暴露的业务摘要，不包含完整 prompt、模型草稿、安全审查三联稿、RAG 原文片段或内部逻辑链。

### 4.6 流式响应

当 `stream=true` 时，返回 SSE：

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Connection: keep-alive
```

事件格式：

```text
event: turn.started
data: {"id":"turn_01HZYK9G4DX8S7RC7J2MTNQ9V1","request_id":"req_01HZYK8JQ7M3V9QF8Y5W0A2B3C","trace_id":"trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C"}

event: reasoning_display.started
data: {"projection_id":"rdp_seg_001","segment_id":"seg_001","title":"处理过程"}

event: reasoning_display.delta
data: {"projection_id":"rdp_seg_001","text_delta":"我先根据你提供的呕吐次数和食欲变化检查"}

event: reasoning_display.completed
data: {"reasoning_display":{"projection_id":"rdp_seg_001","segment_id":"seg_001","title":"处理过程","text":"我先根据你提供的呕吐次数和食欲变化检查是否存在需要立即就医的信号，再整理观察要点和需要线下就诊的触发条件。","metadata":{}}}

event: segment.started
data: {"segment_id":"seg_001","index":0,"type":"medical_consultation","title":"症状判断与下一步"}

event: segment.delta
data: {"segment_id":"seg_001","delta":{"type":"output_text_delta","text":"猫一天内反复呕吐"}}

event: segment.completed
data: {"segment_id":"seg_001","status":"completed"}

event: turn.completed
data: {"id":"turn_01HZYK9G4DX8S7RC7J2MTNQ9V1","status":"completed"}
```

事件类型：

| 事件 | 说明 |
| --- | --- |
| `turn.started` | 本轮编排已开始 |
| `reasoning_display.started` | 一个可展示推理摘要开始发布 |
| `reasoning_display.delta` | 可展示推理摘要文本增量 |
| `reasoning_display.completed` | 一个可展示推理摘要发布完成 |
| `segment.started` | 一个业务分段开始发布 |
| `segment.delta` | 分段文本或内容增量 |
| `segment.completed` | 一个业务分段发布完成 |
| `turn.completed` | 本轮完成 |
| `turn.failed` | 本轮失败 |
| `heartbeat` | 入口层心跳事件，不代表业务进展 |

流式约束：

- `ApiIngress` 不缓存完整响应后再统一发送；收到编排事件后应尽快写出。
- `ApiIngress` 不伪造业务 segment。
- `ApiIngress` 不判断 `reasoning_display` 是进度、结论、审查说明或证据摘要；事件生成、排序、审查和发布时间由下游编排与业务组件负责。
- `ApiIngress` 收到下游已经允许展示的 `reasoning_display.*` 事件后，应按事件顺序忠实转发，不改写、不总结、不裁剪。
- 当回答相关 RAG 或追问相关 RAG 无有效知识命中，或关键依赖不可用时，流式响应以 `turn.failed` 表达失败，错误结构与同步响应保持一致。
- 心跳事件仅用于维持连接，不得承载医疗建议。
- 客户端断开时，入口层通知下游并记录取消事件；已发布内容不由入口层回滚。

### 4.7 可展示 reasoning display

`reasoning_display` 是由 `VetTraceSchema`、安全审查链路、`VetResponseComposer` 或编排层产出的用户可见推理摘要投影。它用于前端折叠展示 Agent 的处理过程或解释摘要，但不等同于模型隐藏 chain-of-thought，也不等同于完整业务逻辑链。

发布语义：

- 未启用 SSE 时，`reasoning_display` 随最终 JSON 响应一次性返回。
- 启用 SSE 时，下游一旦产出已经允许展示的 `reasoning_display.*` 事件，`ApiIngress` 应按收到顺序立即转发，不等待整轮 turn 完成。
- 是否生成、何时生成、归属哪个 segment、是否体现审查 Agent 的输出，均由下游组件决定。
- 若某段推理摘要不可展示，下游可以不发送对应 `reasoning_display`；第一阶段普通客户端不依赖 blocked 事件。

安全边界：

- `reasoning_display.text` 必须是下游已经允许展示的文本。
- 不得通过 `reasoning_display` 暴露隐藏 chain-of-thought、完整 trace patch、prompt、模型草稿、安全审查三联稿、完整 OCR 原文、完整 RAG chunk 或被删除的危险内容。
- API 层只承载和传输，不生成、不审查、不分类、不排序 `reasoning_display`。

### 4.8 幂等与重试

- 客户端可传入 `turn_options.idempotency_key`。
- 未传幂等键时，服务可使用 `request_id` 作为幂等输入。
- 整轮幂等判定由编排层或会话持久化层负责。
- 编排层确认接收后，`ApiIngress` 不自行重试整轮请求，避免重复发布和重复落库。
- 客户端在网络失败后是否重试，应复用同一个 `idempotency_key` 或 `request_id`。

## 5. `POST /openai/v1/responses`

### 5.1 定位

创建一轮 Agent 对话的 OpenAI Responses 风格兼容入口。

该接口用于：

- OpenAI SDK 风格客户端适配。
- 内部调试。
- 迁移期协议兼容。

该接口不作为兽医业务主契约。正式业务客户端和 BFF 应优先使用 `/agent/turns`。

### 5.2 契约约束

兼容入口允许使用接近 OpenAI Responses 的字段形态，例如：

- `model`
- `input`
- `stream`
- `metadata`

但本系统仍要求显式提供兽医业务上下文：

- `scope_assertion.user_id`
- `scope_assertion.session_id`
- `scope_assertion.pet_id`
- `scope_assertion.authorization`
- `scope_assertion.profile`

兼容入口的请求最终会被标准化为内部 `AgentTurnRequest`，并进入与 `/agent/turns` 相同的编排、护栏、留痕和响应流程。

### 5.3 不支持的兼容行为

外部请求不得通过 OpenAI 兼容字段绕过系统规则：

- 不得通过 `instructions` 放宽急症、毒物、用药或安全护栏。
- 不得通过 `tools`、`tool_choice` 或类似字段授予额外工具权限。
- 不得通过 `metadata`、`vet_context.pet_info` 或兼容字段改写 `scope_assertion.pet_id`、`scope_assertion.session_id` 或安全判决。
- 不得通过模型选择绕过服务端模型策略。
- 不得通过兼容入口放宽一 session 一宠约束。
- 不得通过兼容入口绕过 RAG 禁令或 SAF 规则。

服务端可以忽略或拒绝未纳入本系统契约的 OpenAI 原生字段。若字段可能造成安全边界误解，应返回 `400 INVALID_REQUEST`。

### 5.4 响应

同步和流式响应语义与 `/agent/turns` 一致。

为了兼容 SDK，响应可保留 OpenAI Responses 风格的 `output` 与流式事件结构；但 `segments`、`vet_result` 仍是本系统业务扩展字段。

## 6. 内部 Admin RAG 治理接口

### 6.1 定位

Admin RAG 治理接口用于内部治理后台、运维和授权研发排查知识资产状态。该类接口不面向 App、Web、小程序或普通 BFF 用户流程开放。

RAG 无命中治理接口只记录和更新知识缺口治理状态，不触发以下动作：

- 不重新执行当前 Agent 回合。
- 不生成默认回答。
- 不生成默认追问。
- 不生成知识 chunk。
- 不生成 embedding。
- 不审核或发布知识资产。
- 不改变已完成或已失败回合的 HTTP 响应结果。

### 6.2 鉴权与访问边界

Admin 接口使用与主入口相同的 API 凭据认证机制：

| Header | 必填 | 说明 |
| --- | --- | --- |
| `Authorization: Bearer <token>` | 条件必填 | 当启用 API 鉴权或配置 `VET_AGENT_API_KEYS` 时必填 |
| `X-API-Key` | 条件必填 | 可替代 Bearer Token |
| `X-User-ID` | 否 | 治理后台可传入操作人标识，用于治理记录审计 |

生产环境应通过网关限制 `/admin/*` 访问来源。API Key 认证只证明调用方服务身份，不代表普通用户授权。

### 6.3 `GET /admin/rag/misses`

分页查询 RAG 无命中知识缺口治理记录。

查询参数：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `rag_scope` | string | 否 | 无 | RAG 数据链范围；当前支持 `answer_rag`、`followup_rag` |
| `status` | string | 否 | 无 | 治理状态，可选 `open`、`triaged`、`asset_drafted`、`published`、`dismissed` |
| `task_domain` | string | 否 | 无 | 任务域过滤，例如 `gastrointestinal`、`urinary` |
| `limit` | integer | 否 | `50` | 返回数量上限，取值范围 `1..200` |
| `offset` | integer | 否 | `0` | 分页偏移量，必须大于等于 `0` |

成功响应：

```json
{
  "items": [
    {
      "miss_id": "rag_miss_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
      "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
      "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
      "user_id": "user_123",
      "pet_id": "pet_789",
      "session_id": "sess_456",
      "rag_scope": "followup_rag",
      "task_id": "task_1",
      "task_key": "task_key_1",
      "task_domain": "gastrointestinal",
      "task_title": "消化道咨询",
      "user_text_excerpt": "我家 3 岁、12 公斤的柯基犬饭后总是缩成一团趴着，看起来不太舒服。",
      "user_text_digest": "sha256_hex_digest",
      "structured_query": {
        "domain": "gastrointestinal",
        "missing_slots": [
          "mental_status",
          "appetite",
          "vomiting"
        ],
        "answerability": {
          "decision": "ask",
          "reason": "仍缺少会明显影响分诊建议的高价值信息。"
        }
      },
      "consultation_state": {},
      "answerability": {
        "decision": "ask"
      },
      "semantic_extraction": {},
      "retrieval_parameters": {
        "allowed_chunk_types": [
          "followup_questions"
        ],
        "top_k": 4,
        "min_score": 0.35,
        "domain_filter": "gastrointestinal",
        "missing_slots": [
          "mental_status",
          "appetite",
          "vomiting"
        ]
      },
      "failure_reason": "no_approved_vector_hits",
      "error_type": "FollowupRagDependencyError",
      "error_message": "followup RAG retrieval returned no approved vector hits",
      "error_details": {
        "reason": "no_approved_vector_hits"
      },
      "dedupe_key": "stable_sha256_group_key",
      "status": "open",
      "review_notes": null,
      "linked_ingestion_batch": null,
      "linked_chunk_ids": [],
      "metadata": {
        "governance_role": "knowledge_gap_record",
        "runtime_effect": "none",
        "agent_path_node": "FollowupRagService",
        "task_state_key": "task_key_1",
        "missing_slots": [
          "mental_status",
          "appetite",
          "vomiting"
        ],
        "planner_called": false,
        "runtime_action": "fail_fast"
      },
      "created_at": "2026-08-17T10:00:00Z",
      "updated_at": "2026-08-17T10:00:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "backend": "rag_miss_governance"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `miss_id` | RAG 无命中治理记录稳定标识 |
| `request_id` / `trace_id` | 触发无命中的入口请求与链路标识 |
| `user_id` / `pet_id` / `session_id` | 触发无命中的可信范围标识 |
| `rag_scope` | RAG 数据链范围，当前支持 `answer_rag`、`followup_rag` |
| `task_domain` | 触发无命中的任务域 |
| `user_text_excerpt` | 经裁剪后的用户任务文本片段，用于人工排障 |
| `user_text_digest` | 用户任务文本摘要，用于隐私友好的去重与追踪 |
| `structured_query` | 当前 RAG 实际使用的结构化 query |
| `retrieval_parameters` | 本轮召回参数摘要；追问场景可包含 `missing_slots` |
| `failure_reason` | 无命中或依赖失败原因 |
| `error_type` | 原始异常类型，例如 `AnswerRagDependencyError`、`FollowupRagDependencyError` |
| `dedupe_key` | 用于后台聚合同类知识缺口的稳定键 |
| `status` | 当前治理状态 |
| `linked_ingestion_batch` | 关联的知识导入批次标识 |
| `linked_chunk_ids` | 关联的正式知识 chunk 内部主键集合 |
| `metadata.runtime_effect` | 当前记录对运行时的影响；应为 `none` |
| `metadata.missing_slots` | 追问场景下触发无命中的缺失槽位集合 |
| `metadata.planner_called` | 追问场景下结构化追问规划器是否已被调用；无命中通常为 `false` |
| `metadata.runtime_action` | 当前回合运行时动作；无命中治理场景应为 `fail_fast` |

隐私约束：

- `user_text_excerpt` 仅用于治理排障，不应直接展示给无权限人员。
- `user_text_digest` 可用于聚合与去重，但不能反推出原始用户文本。
- `structured_query`、`consultation_state`、`answerability`、`semantic_extraction` 属于治理材料，不应作为运行时规则来源。
- `structured_query.missing_slots` 与 `metadata.missing_slots` 只表示本轮追问知识缺口排障上下文，不应由治理后台或 BFF 转换为客户端侧默认追问。

### 6.4 `PATCH /admin/rag/misses/{miss_id}`

更新单条 RAG 无命中治理记录的人工治理字段。

路径参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `miss_id` | string | RAG 无命中治理记录稳定标识 |

请求体：

```json
{
  "status": "triaged",
  "review_notes": "确认需要补充消化道阶段性建议知识资产。",
  "linked_ingestion_batch": "batch_digestive_20260817",
  "linked_chunk_ids": [
    101,
    102
  ],
  "reason": "knowledge_gap_triage"
}
```

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `status` | string | 否 | 新治理状态，可选 `open`、`triaged`、`asset_drafted`、`published`、`dismissed` |
| `review_notes` | string | 否 | 治理人员处理备注 |
| `linked_ingestion_batch` | string | 否 | 关联知识导入批次标识 |
| `linked_chunk_ids` | array[integer] | 否 | 关联正式知识 chunk 内部主键集合 |
| `reason` | string | 否 | 本次治理操作原因 |

成功响应为更新后的治理记录详情，字段结构与 `GET /admin/rag/misses` 中的 `items[]` 一致。

治理状态语义：

| 状态 | 含义 |
| --- | --- |
| `open` | 新记录，尚未处理 |
| `triaged` | 已确认并完成初步分流 |
| `asset_drafted` | 已准备或导入候选知识资产 |
| `published` | 已有关联知识资产通过审核并可进入正式召回 |
| `dismissed` | 经确认不是知识缺口或无需处理 |

约束：

- 更新治理状态不会让当前或历史失败回合重新生成回答或追问。
- `published` 只表示治理记录已关联发布结果；正式召回仍要求知识资产本身满足启用、审核通过和 embedding 非空等条件。
- 如果 `miss_id` 不存在或状态值非法，返回 `400 INVALID_REQUEST`。

## 7. `GET /health`

### 7.1 定位

进程存活检查。

该接口只判断 HTTP 进程是否存活，不检查编排层、模型、存储或外部依赖。

### 7.2 响应

成功：

```json
{
  "status": "ok"
}
```

语义：

- 返回 `200 OK` 表示进程存活。
- `/health` 失败通常意味着实例应被重启。
- 下游依赖异常不应导致 `/health` 失败。

## 8. `GET /ready`

### 8.1 定位

服务就绪检查。

该接口判断 `ApiIngress` 是否具备接收正式流量的条件。

### 8.2 检查项

`/ready` 至少检查：

- 入口配置已加载。
- `RuntimeConfig` 可用。
- 编排入口 `VetOrchestrator / GraphRuntime` 可用。
- 入口限制、超时、流式心跳等必要参数有效。
- 回答相关 RAG 服务就绪，包括数据库、embedding 和已审核向量知识召回条件。
- 追问相关 RAG 服务就绪，包括数据库、embedding、已审核向量知识召回条件和结构化追问规划依赖。
- RAG 无命中治理记录器就绪；生产环境配置数据库时，应能访问治理记录表。
- 服务处于可接收请求状态。

### 8.3 响应

就绪：

```json
{
  "status": "ready"
}
```

未就绪：

```json
{
  "code": "SERVICE_UNAVAILABLE",
  "message": "service is not ready: orchestrator is unavailable",
  "request_id": "req_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "trace_id": "trace_01HZYK8JQ7M3V9QF8Y5W0A2B3C",
  "details": [
    {
      "field": "orchestrator",
      "reason": "unavailable"
    }
  ]
}
```

语义：

- 返回 `200 OK` 表示实例可接收正式流量。
- 返回 `503 SERVICE_UNAVAILABLE` 表示实例不应接收正式流量。
- 当编排入口不可用时，`/ready` 应返回不可就绪；`/agent/turns` 应返回 `503 SERVICE_UNAVAILABLE`。
- 当回答相关 RAG、追问相关 RAG 或 RAG 无命中治理记录器未就绪时，生产实例不应接收正式医疗问诊流量。

## 9. 不属于本对外 API 的能力

以下能力不由 `ApiIngress` 直接对外暴露：

| 能力 | 归属 |
| --- | --- |
| 创建或查询 session | `ConversationStore` 或上游 BFF |
| 查询历史消息 | `ConversationStore` 或业务后台 |
| 宠物绑定、切宠、授权校验 | 上游客户端 / BFF / 数据层 |
| 文件二进制上传 | 文件服务或上游 BFF |
| 化验 OCR 独立调用 | `LabOcrService` |
| RAG 检索独立调用 | `RagPlatform` |
| RAG 无命中自动聚合任务 | 治理后台或离线知识生产流程 |
| 知识 chunk 自动生成、embedding 和发布 | 知识治理链路 |
| 安全审查独立调用 | `GuardrailFramework` / `VetOutputSafetyReviewer` |
| 逻辑链查询 | `LogicTraceStore` 或治理后台 |
| 验收集运行 | `VetEvaluationSuites` |

## 10. 日志与隐私约束

入口访问日志可记录：

- `request_id`
- `trace_id`
- `scope_assertion.user_id`
- `scope_assertion.session_id`
- `scope_assertion.pet_id`
- `scope_assertion.issuer`
- `scope_assertion.source.record_updated_at`
- `path`
- `response_mode`
- `status_code`
- `error_code`
- `duration_ms`
- `attachment_count`
- `rag_miss_id`
- `rag_miss_dedupe_key`

普通访问日志不得记录：

- 完整用户医疗输入。
- 完整模型回复。
- OCR 原文。
- 安全审查三联稿。
- 完整 RAG 片段。
- 完整 RAG 无命中结构化 query。
- 完整 RAG 无命中用户原文。
- 完整业务逻辑链。

业务内容与逻辑链由 `LogicTraceStore` 与业务留痕分级策略管理。

## 11. 版本与兼容性

- 当前文档为第一阶段外部 API 契约草案。
- 已发布字段不得做破坏性变更。
- 新增字段应保持向前兼容，客户端必须忽略未知字段。
- 若未来引入正式鉴权、API 版本路径或公网访问控制，应单独升版并更新本文档。
