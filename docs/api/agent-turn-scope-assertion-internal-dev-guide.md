<!--
=============================================================================
文件: docs/api/agent-turn-scope-assertion-internal-dev-guide.md
作用: 说明 scope_assertion API 结构变更对兽医 Agent 内部数据链、职责边界和实现策略的影响。
范围: 适用于 Agent 项目内部开发、代码评审、迁移排期、测试补充和后续中间件迁移阶段。
说明: 本文档不重复维护 API 字段 schema；最终接口契约以 docs/api/external_api.md 为准，对外变更通知以 docs/api/agent-turn-scope-assertion-change-notice.md 为准。
维护: 当入口 DTO、范围授权、宠物画像投影、session 绑定或 PetContext 数据链发生变化时，应同步更新本文档中的实现策略和废弃路径说明。
=============================================================================
-->

# Agent 内部实现策略变更：`scope_assertion` 范围声明迁移

## 1. 文档定位

本文面向 Agent 项目内部开发人员，用于解释本次 API 结构变更的契机、原因、影响范围和后续实现策略。

本文不再维护完整 API 字段结构。字段定义、请求示例、字段必填规则和对外错误码以以下文档为准：

1. `docs/api/external_api.md`
2. `docs/api/agent-turn-scope-assertion-change-notice.md`

本文只回答内部开发时最容易断裂的几个问题：

1. 为什么要从 `vet_context` 迁移到 `scope_assertion`。
2. 哪些旧实现路径必须废弃。
3. Agent 内部数据链应如何重新分工。
4. 冷启动、画像投影和 session 绑定应如何处理。
5. 哪些测试必须补齐，才能避免回归。

## 2. 变更契机

当前 Agent 服务不是直接面向用户端的公共 API，而是由 BFF 或其他授权内部服务通过 HTTP 调用。

调用方已经明确承担以下职责：

1. 用户登录鉴权。
2. 宠物归属校验。
3. 宠物删除、停用、解绑和转移归属拦截。
4. `session_id` 发放和复用。
5. 从主服务 `master_pet_info` 读取宠物基础资料。

在这一前提下，Agent 继续把 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id` 当作主身份入口，会导致接口职责不清。尤其是 `vet_context.pet_info` 同时可能被理解为请求侧自报资料和服务端可信画像，后续容易造成冷启动、画像污染和事实写入边界混乱。

因此，本次迁移将身份、宠物、会话、归属裁决、服务端画像和来源版本统一收束到 `scope_assertion`。

## 3. 旧结构问题

### 3.1 `vet_context` 语义过载

旧结构中 `vet_context` 同时承载：

```text
user_id
pet_id
session_id
pet_info
```

其中前三个字段是访问范围，`pet_info` 是宠物资料。它们的可信等级不同，却被放在同一对象中，容易让调用方和 Agent 内部模块误判字段性质。

迁移后：

```text
scope_assertion
  服务端已验证范围声明，可信

vet_context.pet_info
  请求侧未验证上下文，可作为本轮问诊线索、差异检测和资料纠正候选；不可直接成为权威事实
```

### 3.2 `pet_info` 容易污染权威画像

旧实现中，`pet_info` 容易被用于上下文预填、测试画像注册或后续事实记忆候选。即使当前代码已经开始隔离它，字段命名仍会让后续开发者误以为它可以作为可信画像。

迁移后，`vet_context.pet_info` 的定位是未验证输入上下文。它可以用于本轮问诊线索、服务端画像差异检测、资料纠正候选和后续受控确认流程。

需要特别区分：用户自然语言输入仍是问诊和临床语义链路的重要来源；这里限制的是请求侧宠物画像字段不能被直接提升为服务端已验证画像。

`vet_context.pet_info` 不得直接用于：

1. 构造 `TrustedIdentity`。
2. 初始化 `pet_profiles`。
3. 覆盖 `scope_assertion.profile`。
4. 作为服务端已验证画像参与临床硬判断。
5. 未经确认写入长期权威事实。

### 3.3 Agent 不应查询主服务数据库

主服务数据库中的宠物表和归属关系属于 BFF 或主服务领域。Agent 如果直接读取主服务数据库，会产生职责越界和强耦合。

Agent 应消费 BFF 注入的服务端范围声明，而不是理解主服务表结构、状态枚举和归属规则。

### 3.4 Agent 不应成为宠物主档第二权威源

如果 Agent 自行维护“权威宠物画像”，会和主服务数据库形成双权威源。

迁移后，Agent 侧 `pet_profiles` 的定位是：

```text
上游已验证宠物基础资料在 Agent 侧的持久化投影
```

不是：

```text
Agent 独立维护的宠物归属权威库
```

## 4. 架构决策

本次迁移采用以下决策：

1. `scope_assertion` 是 Agent 请求中唯一可信的身份、宠物、会话和画像声明入口。
2. `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id` 从正式契约中移除。
3. `vet_context.pet_info` 作为请求侧未验证上下文，可参与本轮问诊线索、差异检测和资料纠正候选生成，但不可直接成为权威事实。
4. BFF 是用户身份、宠物归属、宠物启停状态和 session 发放的权威执行方。
5. Agent 不查询主服务数据库。
6. Agent 基于 `scope_assertion.profile` 初始化或刷新本地 `pet_profiles` 投影。
7. Agent 继续负责同一个 `session_id` 只能绑定同一组 `user_id + pet_id` 的一致性保护。
8. Orchestrator 不直接理解 `scope_assertion`，只消费授权后形成的 `PetContext` 和 `TrustedIdentity`。

## 5. 内部影响范围

本次迁移影响的是 Agent 的入口和范围数据链，不应扩散为全链路重写。

主要受影响模块：

| 模块 | 影响 |
|---|---|
| `src/ingress/dto.py` | 请求 DTO 需要新增 `scope_assertion`，并从 `scope_assertion` 构造身份范围；`vet_context` 仅保留 `pet_info` |
| `src/vet_agent/contracts.py` | 核心请求模型需要承载范围声明和非可信上下文 |
| `src/vet_agent/services/access_control.py` | 授权门面需要接收范围声明，不再把 `pet_info` 作为授权输入 |
| `src/vet_agent/services/scope.py` | 范围服务需要校验声明、初始化投影、执行 session 一致性策略 |
| `src/vet_agent/repositories/scope.py` | 仓储需要支持显式 upsert 宠物画像投影 |
| `src/vet_agent/services/context.py` | 宠物上下文只消费已验证画像投影，隔离 `vet_context.pet_info` |
| `src/vet_agent/ingress_adapter.py` | 入口适配需要以 `scope_assertion` 作为身份范围来源 |
| `tests/` | API、范围策略、冷启动、session 冲突和画像隔离测试需要更新 |

不应受影响或只应弱影响的模块：

| 模块 | 原则 |
|---|---|
| `src/vet_agent/orchestrator.py` | 不直接处理 `scope_assertion` 细节 |
| 临床安全链路 | 继续消费 `PetContext.summary()` 和 `PetContext.verified_profile` |
| 问诊状态链路 | 继续消费已验证画像预填槽位 |
| 记忆链路 | 不将未确认的请求侧画像写入权威事实；可在策略允许时生成待确认候选 |
| RAG 链路 | 不参与身份范围声明裁决 |

## 6. 实现策略变化

### 6.1 身份来源策略

旧策略：

```text
从 vet_context.user_id / pet_id / session_id 构造 TrustedIdentity
```

新策略：

```text
从 scope_assertion.user_id / pet_id / session_id 构造 TrustedIdentity
```

实现要求：

1. `TrustedIdentity` 仍只表达 `user_id + pet_id + session_id`。
2. `TrustedIdentity` 不直接携带画像详情。
3. 过渡期如保留旧字段，只能用于一致性检查，不得作为主路径。

### 6.2 宠物画像来源策略

旧策略：

```text
Agent 必须先命中 pet_profiles，否则认为宠物未注册
```

新策略：

```text
scope_assertion 完整且授权声明有效
  -> upsert pet_profiles 投影
  -> 使用投影构造 PetContext
```

这解决的是冷启动阻塞问题。它不是允许客户端自报资料自动注册，而是允许已认证 BFF 的服务端声明初始化 Agent 侧投影。

### 6.3 `pet_profiles` 定位变化

旧语义容易被理解为：

```text
Agent 自己维护宠物归属和画像权威
```

新语义必须明确为：

```text
主服务已验证宠物基础资料在 Agent 侧的投影
```

因此，宠物解绑、删除、停用、转移归属的实时拦截职责属于 BFF。Agent 内历史投影可以保留为审计和冷库数据，但不得绕过当前 `scope_assertion` 重新激活。

### 6.4 冷启动策略

旧冷启动：

```text
本地 pet_profiles 缺失
  -> 403 pet_id is not registered
```

新冷启动：

```text
scope_assertion 合法
  -> upsert 本地投影
  -> session 范围绑定
  -> 进入主链路
```

Fail Fast 场景：

1. `scope_assertion` 缺失。
2. 核心范围字段缺失。
3. 授权声明为否。
4. 宠物画像核心字段缺失。
5. 本地投影写入失败。
6. session 被跨用户或跨宠物复用。

### 6.5 未验证输入上下文策略

`vet_context.pet_info` 不应被简单理解为“只作审计”。它属于请求侧未验证上下文，可以被本轮业务链路读取，但必须保留来源和确认状态。

它可以用于：

1. 排查调用方是否仍在传旧字段。
2. 比对客户端自报资料与服务端画像差异。
3. 作为本轮问诊追问、差异提示和上下文解释的输入线索。
4. 作为资料纠正候选，进入后续人工确认、BFF 回写或受控资料修正流程。

它不能用于：

1. 覆盖 `scope_assertion.profile`。
2. 绕过 `scope_assertion.profile` 缺失。
3. 写入 `pet_profiles`。
4. 未经确认写入长期权威事实。
5. 在缺少来源标记和确认状态的情况下作为服务端已验证画像参与临床硬判断。

用户输入中的症状、时间、饮食、呕吐、排便、活动变化等内容仍应进入本轮语义抽取和问诊判断。若用户明确纠正服务端画像，例如“体重不是 4.6kg，是 4.1kg”或“系统里写未绝育，但它去年已经绝育”，Agent 应将其识别为用户陈述或资料纠正候选，而不是直接覆盖 `scope_assertion.profile`。

### 6.6 范围策略变化

旧策略围绕本地 `pet_profiles` 是否存在展开。

新策略围绕以下事实展开：

1. 调用方服务身份是否可信。
2. `scope_assertion` 是否完整。
3. BFF 是否声明已完成归属校验。
4. BFF 是否声明宠物当前可用。
5. 服务端画像核心字段是否满足主链路要求。
6. 本地投影是否写入成功。
7. session 是否与同一组 `user_id + pet_id` 保持一致。

## 7. 目标数据链

内部目标链路如下：

```text
IngressRequest.scope_assertion
  -> DTO 结构校验
  -> TrustedIdentity
  -> AccessControlService
  -> ScopeContextService
  -> ScopeRepository.upsert_pet_profile_projection
  -> ScopeRepository.get_session_binding / bind_session / touch_session
  -> PetContextProvider
  -> PetContext.verified_profile
  -> VetOrchestrator
```

关键边界：

1. `scope_assertion` 的解析和裁决应止于入口、访问控制和范围服务层。
2. `PetContextProvider` 之后的业务链路只消费 `PetContext`。
3. Orchestrator 不应直接判断 `scope_assertion.authorization`。
4. 临床安全、问诊状态、记忆和 RAG 不应知道 BFF 的主服务表结构。

## 8. 旧路径废弃清单

以下路径应逐步替换并最终移除：

1. 从 `vet_context.user_id`、`vet_context.pet_id`、`vet_context.session_id` 构造主身份范围。
2. 缺少 `pet_profiles` 时直接将新宠物判定为未注册。
3. 从 `vet_context.pet_info` 自动注册宠物画像。
4. 将 `pet_info` 提升为服务端已验证画像，并据此覆盖临床硬判断所需的权威画像字段。
5. 从 `metadata`、query 参数或用户文本中兜底身份范围。
6. Agent 主动查询主服务数据库获取宠物归属或基础资料。
7. 在 Orchestrator 内部处理 BFF 范围声明细节。
8. 业务模块直接访问 SQLAlchemy 表模型读取或写入范围数据。

## 9. 模块实现方向

### 9.1 入口 DTO

入口 DTO 应完成结构校验和基础类型校验。

需要保证：

1. `scope_assertion` 为必填。
2. 核心范围字段非空。
3. 授权声明使用严格布尔值。
4. 宠物画像核心字段满足主链路最低要求。
5. `vet_context.pet_info` 不参与 `TrustedIdentity` 构造，但可以保留为本轮未验证输入上下文。

### 9.2 访问控制服务

访问控制服务应继续承担服务级认证和范围授权调度。

需要保证：

1. 调用方认证结果可以与 `scope_assertion.issuer` 形成审计关联。
2. 未认证或未授权调用方不能只靠 JSON 字段进入主链路。
3. 不在访问控制层查询主服务数据库。

### 9.3 范围服务

范围服务是本次迁移的核心。

需要保证：

1. 基于结构化声明执行确定性裁决。
2. 在授权声明有效时 upsert 本地画像投影。
3. 在投影写入成功后再进入宠物上下文组装。
4. 继续执行同一个 `session_id` 只能绑定同一组 `user_id + pet_id`。
5. 所有拒绝结果可审计。

后续接入 OPA 时，应替换策略裁决器实现，不应改变调用方数据链。

### 9.4 范围仓储

范围仓储应是唯一直接访问 `pet_profiles` 和 `pet_session_bindings` 表的层。

需要补齐：

1. `upsert_pet_profile_projection` 能力。
2. 并发安全的 session 绑定能力。
3. 投影写入失败时的明确异常或失败结果。
4. 来源系统、来源版本和更新时间的审计信息。

### 9.5 宠物上下文

宠物上下文层应只消费授权后的已验证画像。

需要保证：

1. `verified_profile` 只来自 `pet_profiles` 投影。
2. `reported_profile` 作为用户陈述或未验证提示，可用于本轮问诊线索、差异提示和资料纠正候选。
3. `summary()` 中缺失字段以未知表达，但 `species` 等核心字段缺失应在上游范围层处理。
4. 不从 `vet_context.pet_info` 补齐或覆盖已验证画像字段；如在本轮语义判断中引用用户陈述，必须保留来源和确认状态。

### 9.6 Orchestrator 与业务 Agent

Orchestrator 和业务 Agent 不应承担范围声明解析职责。

需要保证：

1. Orchestrator 接收到请求时，身份范围已经归一。
2. 宠物上下文已经通过范围服务授权。
3. 临床安全和问诊状态只消费 `PetContext`。
4. 记忆写入策略继续禁止未确认的请求侧宠物资料写入权威事实；可在策略允许时产生待确认资料纠正候选。

## 10. 测试策略

### 10.1 必测主路径

1. 完整 `scope_assertion` 请求成功进入主链路。
2. 冷启动时本地没有 `pet_profiles`，但声明合法，能够创建投影并继续执行。
3. 已有投影时，新的服务端画像声明可以刷新投影。
4. 同一 `session_id` 继续绑定同一组 `user_id + pet_id` 时成功。

### 10.2 必测拒绝路径

1. 缺少 `scope_assertion`。
2. 缺少 `user_id`、`pet_id` 或 `session_id`。
3. `ownership_verified=false`。
4. `pet_active=false`。
5. `pet_deleted=true`。
6. `profile.species` 缺失。
7. 同一 `session_id` 复用到其他 `user_id` 或 `pet_id`。
8. 投影写入失败。

### 10.3 必测隔离路径

1. `vet_context.pet_info` 与 `scope_assertion.profile` 冲突时，不覆盖 `scope_assertion.profile`。
2. `vet_context.pet_info` 存在时，不直接写入长期权威事实。
3. `metadata` 中出现 `user_id`、`pet_id` 或 `session_id` 时，不影响 `TrustedIdentity`。
4. 用户文本中提到另一只宠物时，不改写当前 `scope_assertion.pet_id`。
5. 用户明确纠正画像字段时，生成用户陈述或资料纠正候选，不直接刷新 `pet_profiles`。

### 10.4 回归关注点

1. 临床安全仍能读取物种、年龄、性别等画像摘要。
2. 问诊状态仍能预填物种、年龄和体重。
3. 体重缺失时仍能触发追问。
4. trace 和访问日志仍保留必要范围字段。
5. 响应 metadata 不泄露完整画像和敏感资料。

## 11. 迁移顺序

建议按以下顺序推进：

1. 更新文档和测试样例，统一 `scope_assertion` 术语。
2. 新增入口 DTO，不改变旧主路径。
3. 核心请求模型携带 `scope_assertion` 和仅含 `pet_info` 的 `vet_context`。
4. `TrustedIdentity` 改为从 `scope_assertion` 构造。
5. 范围仓储增加画像投影 upsert 能力。
6. 范围服务改为基于 `scope_assertion` 执行授权、投影和 session 绑定。
7. 宠物上下文改为只消费授权后的画像投影。
8. API 测试切换到新请求结构。
9. 灰度期保留旧字段一致性校验。
10. BFF 切换完成后移除旧字段兼容逻辑。

每个阶段都应保持主链路可运行，并补齐对应测试。

## 12. 风险与处理

### 12.1 字段结构多源漂移

风险：

```text
external_api.md、change_notice 和 internal guide 同时维护 schema，后续出现不一致。
```

处理：

```text
内部指南不维护完整 schema，只引用 external_api.md 和 change_notice。
```

### 12.2 冷启动误退化为客户端自报注册

风险：

```text
开发者把 vet_context.pet_info 当作冷启动画像来源或权威画像覆盖来源。
```

处理：

```text
投影写入只能来自 scope_assertion.profile；vet_context.pet_info 只能作为未验证输入、差异检测和纠正候选来源。
```

### 12.3 Agent 越界查询主服务数据库

风险：

```text
为了补画像缺失，在 Agent 中新增主服务数据库读取逻辑。
```

处理：

```text
缺少 scope_assertion 或核心字段时 Fail Fast，由 BFF 修复声明组装。
```

### 12.4 业务模块绕过范围服务

风险：

```text
业务 Agent 直接解析 scope_assertion 或直接访问 pet_profiles 表。
```

处理：

```text
业务模块只消费 PetContext 和仓储协议公开能力。
```

## 13. 禁止事项

以下实现不得进入主分支：

1. 从 `vet_context.pet_info` 构造 `TrustedIdentity`。
2. 从 `vet_context.pet_info` 初始化或刷新 `pet_profiles`。
3. Agent 主动查询主服务数据库。
4. 在 Orchestrator 中处理 `scope_assertion` 鉴权细节。
5. 业务 Agent 直接读取 SQLAlchemy 表模型。
6. 在 `metadata` 中传递或覆盖 `user_id`、`pet_id`、`session_id`。
7. 缺失 `scope_assertion` 时回退旧 `vet_context` 主路径。
8. `profile.species` 缺失时静默进入完整临床链路。
9. 同一个 `session_id` 跨 `user_id + pet_id` 复用时继续放行。
10. 将 Agent 本地 `pet_profiles` 定义为宠物归属最终权威。

## 14. 评审检查清单

代码评审时应检查：

1. 是否仍有新代码从 `vet_context.user_id/pet_id/session_id` 构造身份范围。
2. 是否仍有新代码从 `vet_context.pet_info` 直接写入权威画像或长期事实。
3. 是否新增了 Agent 查询主服务数据库的路径。
4. 是否只有仓储层直接访问范围数据表模型。
5. 是否 Orchestrator 和业务 Agent 没有直接处理 `scope_assertion` 策略细节。
6. 是否冷启动、画像刷新、session 冲突和授权拒绝均有测试。
7. 是否新增函数、闭包、枚举具备中文新版 ReST 风格说明和严格类型提示。
8. 是否新增文件具备文件头注释块。
9. 是否通过包 `__init__.py` 暴露跨包公共能力。
10. 是否同步更新 API 契约、变更通知和联调样例。

## 15. 关联文档

1. `docs/api/external_api.md`
2. `docs/api/agent-turn-scope-assertion-change-notice.md`
3. `docs/architecture/agent-middleware-migration-plan.md`
4. `docs/external/databases/20260812-BFF 主服务：用户宠物设备与服务状态表结构说明.md`
