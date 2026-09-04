<!--
=============================================================================
文件: semantic-collaboration-dag-pre-m11-integration-test-design.md
作用: 定义受限语义协作 DAG 在 M10 完成后、M11 完成前的权威集成测试设计。
范围: 覆盖真实 LiteLLM、Temporal、PostgreSQL projection、M02～M10 组合链路、
      语义验证矩阵、测试替身边界、Fail Fast 负例、报告契约与 M11 升级路径。
说明: 本文是集成测试实现和 code review 的权威边界；不改变生产架构，不扩展
      M11 / M12 / M13 / M15 职责，不承载新的架构实验结论。
维护: 当模块契约、SKILL schema、review 维度、repair lane、外部依赖、测试矩阵、
      TODO 边界或 M11 实现状态调整时，必须先同步本文再修改测试。
=============================================================================
-->

# 受限语义协作 DAG Pre-M11 集成测试设计

> **文档状态**：权威集成测试设计
>
> **文档版本**：1.0.0
>
> **适用阶段**：M10 已完成，M11 Artifact Store 尚未实现
>
> **测试结论边界**：本文验证 M02～M10 的真实依赖组合与语义保真；不宣称
> verified artifact、repair_verified、claim graph ready 或领域投影可用。

## 1. 文档定位

本文是受限语义协作 DAG 进入正式生产工程实现后的第一份权威集成测试设计，
用于固定 M11 实现前的验证范围，避免测试实现发生以下漂移：

1. 为了跑通链路而伪造 M11 权威 artifact。
2. 为了判断语义而在 Python 中恢复关键词、正则或医学规则。
3. 为了让真实模型结果稳定而加入隐藏 retry、fallback 或宽松 JSON 解析。
4. 为了观察下游效果而提前调用问诊状态、临床安全或长期记忆。
5. 把 M10 的 `patch_ready` 误写成 `verified` 或 `repair_verified`。
6. 把 M04 PostgreSQL projection 误当成 M11 Artifact Store。
7. 把集成测试扩展成新的 input preprocessing 架构实验。

本文与生产文档的分工如下：

| 文档类型 | 权威职责 |
|---|---|
| 生产架构基线 | 定义系统架构与模块边界 |
| 生产实施计划 | 定义 M01～M15 交付顺序 |
| 本集测设计 | 定义测试分层、用例矩阵、测试替身、TODO、报告与准入结论 |
| 集成测试代码 | 实现本文的 `case_id` 与断言，不得自行扩展权威边界 |

后续新增或修改集成测试时，必须满足：

```text
每个测试有稳定 case_id
每个 case_id 可追溯到本文
未在本文声明的测试替身不得引入
本文未允许的真实依赖不得调用
本文禁止宣称的结果不得出现在测试名称、断言或报告中
```

## 2. 当前工程基线

### 2.1 已实现能力

| 模块 | 状态 | 集测角色 |
|---|---|---|
| M01 SkillSpec / SkillCatalog | 已实现 | 启动期契约闭合与版本冻结 |
| M02 TurnSnapshot Builder | 已实现 | 提供不可变回合上下文与 digest |
| M03 Deterministic Plan Compiler / Validator | 已实现 | 生成并校验 Root Plan IR |
| M04 Temporal workflow / activity / projection | 已实现 | 真实 Temporal 与 PostgreSQL projection 联调 |
| M05 StructuredLLMGateway | 已实现 | 真实 LiteLLM 结构化调用与调用审计 |
| M06 Turn Intent + Claim Inventory | 已实现 | 真实模型生成面 |
| M07 最小结构 verifier | 已实现 | proposal 结构门禁 |
| M08 Coverage / Faithfulness Review | 已实现 | 语义审查与确定性 outcome 派生 |
| M09 Repair Planner | 已实现 | 语义问题到通用修复 lane 的路由 |
| M10 Repair SKILL / typed patch | 已实现 | 修复 proposal、patch 校验与 preview |

### 2.2 未实现或未闭合能力

| 能力 | 当前状态 | 本集测处理 |
|---|---|---|
| Persistent TurnSnapshot reader | TODO Fail Fast | 只允许测试 fixture reader |
| M11 base artifact snapshot | TODO Fail Fast | 只允许显式 fixture snapshot |
| M11 append-only patch store | 未实现 | 测试止步 `patch_ready` / preview |
| M04 生产 SemanticTaskExecutor 组合 | 未接入 | scheduler-only executor 独立验证 |
| M10 后正式 M07 re-verify | 未编排 | 只允许 test-only probe，不产生权威终态 |
| M10 后正式 M08 re-review | 未编排 | 只允许 test-only probe，不产生权威终态 |
| M11 artifact gate state | 未实现 | 不生成 verified / repair_verified |
| M12 Claim Graph | 未实现 | 不测试 graph ready |
| M13 领域投影 | 未实现 | 不调用问诊 / 临床安全 / 长期记忆 |
| M14 完整 trace / metrics | 未完成 | 输出测试级 report，不冒充生产观测 |
| M15 VetOrchestrator 接入 | 未实现 | 不进入业务 API 主路径 |

### 2.3 本阶段可以宣称

```text
真实 LiteLLM 下的 strict schema 调用可用
M06 两个根生成 SKILL 可执行
M07 可对真实 proposal 给出 accepted / blocked
M08 可执行 Coverage / Faithfulness Review 并派生确定性 outcome
M09 可生成确定性 repair plan
M10 可生成确定性验证后的 typed patch preview
真实 Temporal workflow / activity 可调度当前 Root Plan
PostgreSQL projection 可初始化、写入和查询
所有 TODO 依赖显式 Fail Fast
语义预处理链路未调用下游领域
```

### 2.4 本阶段禁止宣称

```text
verified artifact 已提交
repair_verified 已完成
claim graph ready
问诊状态可消费
临床安全可消费
长期记忆可消费
语义预处理生产闭环完成
可以跳过 M11
```

## 3. 测试目标与非目标

### 3.1 目标

1. 验证 M02～M10 模块契约在真实外部依赖下可以组合。
2. 验证 LiteLLM `response_format` 与当前权威 JSON Schema 兼容。
3. 验证模型调用 metadata、finish reason、usage 与 latency 可审计。
4. 验证 Temporal workflow、activity 与 PostgreSQL projection 一致。
5. 验证 normal / denied / uncertain / unknown / corrected 等语义可保留。
6. 验证漏抽、合并、shared scope、医学越权和语义漂移可被发现。
7. 验证 M09 将已知问题路由到正确通用修复 lane。
8. 验证 M10 只生成局部 typed patch preview，不自由重写。
9. 验证修复后原问题消失且未引入新语义问题。
10. 验证 TODO、身份错配、schema 非法和依赖不可用均显式失败。
11. 输出可作为 M11 实现输入的标准化集成测试报告。

### 3.2 非目标

本文不验证：

```text
医学诊断正确性
临床安全 urgent / blocked 判断
问诊回答充分性
追问生成质量
RAG 知识质量
长期记忆写入策略
Mem0 行为
OPA 业务策略
VetOrchestrator 接入
生产部署拓扑
吞吐与大规模并发性能
```

## 4. 测试原则

### 4.1 契约先行

所有外部调用必须经过当前生产契约：

```text
LiteLLM → QwenClient.structured_once → M05 StructuredLLMGateway
Temporal → TemporalSemanticDAGScheduler / Worker
Projection → SemanticDAGProjectionRepository
```

禁止测试代码为了方便直接绕过契约拼 HTTP 请求后，把结果伪装成生产链路结果。

依赖健康检查可以直接调用只读健康接口，但不能替代业务链路 case。

### 4.2 单次模型调用与无隐藏重试

语义链路中的每个模型调用必须保持单次可见：

```text
一次 case 中的一次 SKILL 调用 = 一次底层 structured_once 调用
```

禁止：

```text
测试包装器内部静默 retry
失败后换模型
失败后解析宽松文本 JSON
把 retry 后成功伪装成首次成功
删除失败调用 metadata
```

如需重复观察，只能显式执行多个独立 case run，并在报告中分别记录。

### 4.3 Fail Fast

以下情况必须显式失败：

```text
Temporal 不可用
LiteLLM 不可用
PostgreSQL projection schema 不存在
模型输出 schema invalid
finish reason 异常
模型请求与响应身份不一致
TurnSnapshot digest 不一致
SkillSpec / prompt / task 身份不一致
TODO 依赖被调用
repair target 身份不一致
base version 不一致
patch 应用 preview 非法
```

不得：

```text
跳过失败 case
把失败写成空 claims
自动回退旧问诊语义抽取器
使用进程内调度器替代 Temporal
使用内存 artifact 伪装 M11
```

### 4.4 语义 oracle 不使用关键词规则

语义断言只能来自：

1. 人工审核 fixture 中的期望语义状态；
2. M08 Coverage / Faithfulness 固定布尔矩阵；
3. M08 deterministic outcome derivation；
4. M09 deterministic repair plan；
5. M10 deterministic patch verifier / preview；
6. test-only post-patch M07 / M08 观察器。

禁止在 Python 测试中写：

```python
assert "正常" in proposition
assert "没有呕吐" in proposition
assert "急诊" not in proposition
```

禁止以症状词、疾病名、医学资产 code、动物品种或硬编码中文短语作为医学语义判断。

如果当前 M08 维度无法表达必要语义差异，必须先修订 M01 / M08 契约和测试，再进入
集成测试；不得在测试层补造规则。

### 4.5 全局可读、局部可写

测试 harness 可以构造受限 TurnSnapshot，但不得让任何 SKILL 获得未授权上下文。

测试中必须保持：

```text
generator 只写 M06 输出
reviewer 只写 M08 布尔矩阵
repairer 只写极薄修复 proposal
系统推导 patch operation / identity / version
```

### 4.6 领域隔离

本集测不得调用：

```text
VetOrchestrator
ConsultationStateService
临床安全 semantic extractor / evaluator
临床安全 pgvector retrieval
required_context
临床安全 OPA
Mem0
长期记忆写入
旧问诊语义抽取器
input_preprocessing 实验 runner
```

OPA 与 Mem0 虽在开发环境可用，但均不属于本阶段依赖，不得纳入依赖健康检查或
业务断言。

## 5. 测试分层与执行架构

```text
Lane 0 Environment
  LiteLLM readiness / model list
  Temporal SDK / namespace
  PostgreSQL schema
        ↓
Lane 1 Contract Composition
  TurnSnapshot → Plan IR → M05/M06/M07/M08/M09/M10
  进程内 transport，不访问真实外部服务
        ↓
Lane 2 Real LiteLLM Semantic Pipeline
  M05 → M06 → M07 → M08 → M09 → M10 preview
        ↓
Lane 3 Real Temporal Scheduler
  Root Plan → workflow → activities → terminal projection
        ↓
Lane 4 PostgreSQL Projection
  initialize → record result → finish → load
        ↓
Lane 5 Semantic Verification
  generation fidelity / review calibration / repair regression
        ↓
Lane 6 Fail Fast And Isolation
  TODO ports / schema failure / identity mismatch / domain isolation
```

Lane 1 用于确认测试 harness 与模块契约本身正确；Lane 2～4 验证真实依赖；Lane 5
验证语义保真；Lane 6 防止测试为了通过而引入坏路径。

## 6. 外部依赖与配置

### 6.1 依赖矩阵

| 依赖 | 用途 | 必须 | 说明 |
|---|---|---:|---|
| LiteLLM | qwen-plus strict structured output | 是 | 只通过 `QwenClient.structured_once` |
| Temporal | durable workflow / activity | 是 | 不可用即失败，禁止降级 |
| PostgreSQL | M04 projection | 是 | 不作为 M11 artifact store |
| OPA | 无 | 否 | 本阶段不得调用 |
| Mem0 | 无 | 否 | 本阶段不得调用 |
| input_preprocessing 实验环境 | 无 | 否 | 本阶段不得读取 |

### 6.2 显式开关

默认测试不得访问外部服务。

```text
RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST=true
```

启用 LiteLLM、Temporal 与 PostgreSQL 集成测试。

```text
RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST=true
```

额外启用真实模型语义验证与 repair regression case。语义 case 可单独控制，便于
先收敛工程链路。

### 6.3 测试配置

推荐配置名：

```text
EXTERNAL_SEMANTIC_TEST_LITELLM_BASE_URL
EXTERNAL_SEMANTIC_TEST_LITELLM_API_KEY
EXTERNAL_SEMANTIC_TEST_MODEL
EXTERNAL_SEMANTIC_TEST_TEMPORAL_ADDRESS
EXTERNAL_SEMANTIC_TEST_TEMPORAL_NAMESPACE
EXTERNAL_SEMANTIC_TEST_TEMPORAL_TASK_QUEUE
EXTERNAL_SEMANTIC_TEST_DATABASE_URL
```

默认值：

```text
EXTERNAL_SEMANTIC_TEST_MODEL=qwen-plus
EXTERNAL_SEMANTIC_TEST_TEMPORAL_NAMESPACE=semantic-collaboration-dev
EXTERNAL_SEMANTIC_TEST_TEMPORAL_TASK_QUEUE=semantic-collaboration-dev
```

约束：

1. 配置必须显式注入，不从临时 README 或生产 env 硬编码读取。
2. 密钥不得写入测试代码、fixture、报告或日志。
3. 模型不得配置 fallback 列表。
4. Temporal 地址本地可通过 SSH tunnel，远端容器内应使用内网地址。
5. PostgreSQL 使用 `vet_agent` 逻辑库，不得访问 Temporal 内部表。

### 6.4 环境地址来源

Temporal 的正式开发环境边界以以下文档为准：

[temporal-dev-environment-baseline.md](/home/vancer17/veterinary_agent/docs/deployment/temporal-dev-environment-baseline.md)

临时开发服务文档中的密钥只允许用于人工建立隧道或导出环境变量，不得复制到正式
文档、测试代码或报告中。

## 7. 测试替身与测试-only 组件

### 7.1 StaticTurnSnapshotReader

允许：

```text
按 digest 返回 fixture 中构建的不可变 TurnSnapshot
调用 snapshot.verify_digest
```

禁止：

```text
进入生产包
按原始文本动态重建 snapshot
返回摘要替代原文
绕过 context policy
读取问诊状态、临床安全或长期记忆
```

### 7.2 FixedRepairTargetSnapshotResolver

允许：

```text
返回显式 fixture 构造的 RepairTargetArtifactSnapshot
保持 source_proposal_digest / review_bundle_digest / turn_snapshot_digest 一致
提供 base_version / repair_depth / claims / artifact_reference
```

禁止：

```text
从 M08 bundle 重建权威 claims
执行 M11 append-only commit
生成新的权威 artifact reference
把 fixture reference 写入生产投影
进入生产包
```

使用该组件的报告必须记录：

```text
m11_store_used=false
m11_commit_performed=false
artifact_reference_is_authoritative=false
```

### 7.3 Scheduler-only SemanticTaskExecutor

允许：

```text
返回确定性 DAGTaskExecutionResult
验证 Temporal activity、workflow、失败传播和 projection
使用 integration-test:// 前缀的显式测试引用
```

禁止：

```text
宣称已组合 M05～M11 生产执行链路
返回权威 verified artifact
进入生产包
在真实模型语义 case 中伪装模型输出
```

### 7.4 RecordingStructuredTransport

如需观察模型请求，只能添加透明记录代理：

```text
记录 prompt hash、模型、schema id、latency、usage
原样转发一次 QwenClient.structured_once
不修改 messages / schema / response
不 retry
不清洗字段
```

禁止记录完整 API key、完整系统 prompt 或未脱敏响应原文。

### 7.5 Test-only post-patch semantic probe

允许：

```text
对 M10 application preview claims 再次构造测试 proposal
调用 M07 structural verifier
调用 M08 Review 观察原 dimension 是否消失、是否引入新 dimension
输出 test_only_probe 状态
```

禁止：

```text
提交 M11 artifact
更新 artifact gate state
生成 repair_verified
改变生产 workflow terminal state
把 probe 结果写入业务领域
```

### 7.6 禁止引入的替身

```text
LocalKeywordSemanticJudge
RegexClaimNormalizer
InMemoryArtifactStore 伪装 M11
InMemoryDAGScheduler 替代 Temporal
RetryUntilSuccessTransport
LooseJSONTransport
ClinicalSafetyProbe
ConsultationStateProbe
Mem0Probe
```

如后续确需新测试组件，必须先在本文登记职责、存放位置、允许输入、禁止行为和
报告字段。

## 8. Fixture 设计

### 8.1 存放位置

```text
tests/fixtures/semantic-collaboration/
```

建议文件：

```text
semantic-regression-v1.json
engineering-turns-v1.json
```

这些 fixture 是生产集成测试 fixture，不是 V8～V14 实验 held-out：

```text
不得读取 input_preprocessing 实验 held-out
不得复用实验报告作为通过条件
不得使用 DSPy
```

### 8.2 Fixture 字段

每个 case 至少包含：

```text
case_id
priority
group
current_turn
bounded_history
last_assistant_questions
trusted_pet_context
verified_prior_fact_summary
expected_intent
expected_claim_semantics
expected_review_dimensions
expected_repair_route
forbidden_outcomes
```

`expected_claim_semantics` 只描述人工审核语义状态，例如：

```json
{
  "semantic_type": "denied",
  "target": "呕吐"
}
```

不要求模型输出与固定中文句子逐字一致。

### 8.3 唯一身份与重复执行

真实 Temporal workflow 使用：

```text
WorkflowIDReusePolicy.REJECT_DUPLICATE
```

因此每次测试运行必须使用唯一：

```text
user_id
pet_id
session_id
turn_id
```

用户原文与 fixture 语义保持不变，身份字段可由测试运行前派生唯一值。报告必须记录
最终 `turn_snapshot_digest`、`plan_id` 与 `workflow_id`。

## 9. 用例矩阵总览

### 9.1 case_id 规则

```text
ENV-###      环境与健康
ENG-###      工程链路
GEN-###      生成语义保真
REV-###      M08 Review 校准
PLAN-###     M09 修复路由
FIX-###      M10 修复与 patch preview
NEG-###      Fail Fast 与负例
ISO-###      领域与实验隔离
```

优先级定义：

| 优先级 | 含义 |
|---|---|
| P0 | 进入 M11 前必须通过 |
| P1 | 应通过；失败必须归因并决定是否阻断 |
| P2 | 观察型，不作为默认合并门禁 |

真实模型语义 case 可先按 P1 建立基线，但 M11 完成前必须给出明确的 P0 化计划；
不得长期让 P1 失败被忽略。

## 10. Lane 0：环境与健康矩阵

| ID | 优先级 | 依赖 | 验证内容 | 期望 |
|---|---:|---|---|---|
| ENV-001 | P0 | LiteLLM | readiness | 服务可用 |
| ENV-002 | P0 | LiteLLM | model list | 包含 `qwen-plus` |
| ENV-003 | P0 | Temporal | Python SDK connect | 连接成功 |
| ENV-004 | P0 | Temporal | namespace | `semantic-collaboration-dev` 存在 |
| ENV-005 | P0 | PostgreSQL | database select | 连接成功 |
| ENV-006 | P0 | PostgreSQL | Alembic head / schema | 当前语义 DAG projection 表存在 |
| ENV-007 | P0 | 配置 | 密钥与地址 | 必填配置存在且未硬编码 |

环境 case 只证明依赖可达，不得替代业务链路 case。

## 11. Lane 1：契约组合矩阵

| ID | 优先级 | 依赖 | 验证内容 | 期望 |
|---|---:|---|---|---|
| ENG-001 | P0 | 无 | SkillCatalog 构建与冻结 | 无所有权冲突，digest 稳定 |
| ENG-002 | P0 | 无 | TurnSnapshot 构建 | digest 稳定且不可变 |
| ENG-003 | P0 | M03 | Plan Compiler | Root Plan 只含 turn_intent / claim_inventory |
| ENG-004 | P0 | M03 | Plan Validator | plan / snapshot / catalog / policy 身份闭合 |
| ENG-005 | P0 | M05/M06 | 进程内生成 | 两个根 SKILL 可生成 proposal |
| ENG-006 | P0 | M07 | 结构验证 | accepted 或显式 blocked |
| ENG-007 | P0 | M08 | Coverage-first 路由 | coverage 失败时 faithfulness 有稳定 skip reason |
| ENG-008 | P0 | M08/M09 | Review Bundle → Repair Plan | accepted plan 身份一致 |
| ENG-009 | P0 | M10 | fixture snapshot | patch set accepted，preview 可生成 |
| ENG-010 | P0 | M10 | TODO M11 | resolver / store 调用显式失败 |

## 12. Lane 2：真实 LiteLLM 工程链路矩阵

| ID | 优先级 | 依赖 | 验证内容 | 期望 |
|---|---:|---|---|---|
| ENG-011 | P0 | LiteLLM | Turn Intent strict schema | 七个固定 boolean 字段完整 |
| ENG-012 | P0 | LiteLLM | Claim Inventory strict schema | claims 数组符合 0～8 / unique / 长度限制 |
| ENG-013 | P0 | LiteLLM | Gateway metadata | requested/response model、response id、finish reason 可审计 |
| ENG-014 | P0 | LiteLLM | usage / latency | usage 可用性与 token 数量记录，不缺失后默认为 0 |
| ENG-015 | P0 | LiteLLM | schema invalid 负例 | 显式 schema error，不宽松解析 |
| ENG-016 | P0 | M07 | real proposal verifier | accepted 或 blocked，不产生空 facts |
| ENG-017 | P0 | M08 | real coverage review | 固定矩阵字段完整且无 extra field |
| ENG-018 | P0 | M08 | real faithfulness review | claim index / digest 与 inventory 一致 |
| ENG-019 | P0 | M08 | deterministic outcome | 模型矩阵派生 outcome 与契约一致 |
| ENG-020 | P0 | M09 | repair planning | accepted / clarification / human / disagreement 路由显式 |
| ENG-021 | P0 | M10 | repair model output | proposition 或 sparse delta，不含自证字段 |
| ENG-022 | P0 | M10 | patch verifier | `patch_ready` 或 blocked，preview 不冒充提交 |

## 13. Lane 3：真实 Temporal 调度矩阵

| ID | 优先级 | 依赖 | 验证内容 | 期望 |
|---|---:|---|---|---|
| ENG-023 | P0 | Temporal | workflow 启动 | `semantic-collaboration-dag.v2` 可启动 |
| ENG-024 | P0 | Temporal | worker poll | task queue 可消费 |
| ENG-025 | P0 | Temporal | 并行根任务 | turn_intent / claim_inventory 均有终态 |
| ENG-026 | P0 | Temporal | 成功终态 | verified 终态必须携带 artifact reference |
| ENG-027 | P0 | Temporal | 失败终态 | blocked 携带 failure code / message |
| ENG-028 | P0 | Temporal | run completion | 所有任务显式终态后 workflow 收敛 |
| ENG-029 | P0 | Temporal | duplicate id | 同一 workflow id 重启被拒绝 |
| ENG-030 | P1 | Temporal | cancel signal | 显式取消投影，不伪装成功 |
| ENG-031 | P1 | Temporal | task timeout | 显式 timeout，不静默跳过 |

Lane 3 默认使用 scheduler-only executor，验证调度与投影，不宣称真实 M05～M11 生产
执行器已组合。

## 14. Lane 4：PostgreSQL projection 矩阵

| ID | 优先级 | 依赖 | 验证内容 | 期望 |
|---|---:|---|---|---|
| ENG-032 | P0 | PostgreSQL | initialize_run | run / task projection 初始化 |
| ENG-033 | P0 | PostgreSQL | record_task_result | terminal state 与 artifact / failure payload 一致 |
| ENG-034 | P1 | PostgreSQL | dependency failure | 下游任务显式 dependency_failed |
| ENG-035 | P0 | PostgreSQL | finish_run | workflow 与 repository 状态一致 |
| ENG-036 | P0 | PostgreSQL | load_run | 可按 run_id 查询完整 projection |
| ENG-037 | P0 | PostgreSQL | idempotent initialize | 同一身份重复初始化不产生冲突 |
| ENG-038 | P0 | PostgreSQL | identity conflict | 身份漂移显式失败 |
| ENG-039 | P0 | PostgreSQL | storage boundary | 不读写 Temporal 内部表 |

PostgreSQL projection 是查询与审计投影，不是 M11 artifact 权威存储。

## 15. Lane 5：生成语义保真矩阵

真实模型生成 case 不要求输出逐字匹配，只验证 M06 输出经 M07 / M08 后的语义状态。

| ID | 优先级 | 输入语义 | 期望语义结果 | 禁止结果 |
|---|---:|---|---|---|
| GEN-001 | P0 | 精神正常 | normal proposition 被支持 | normal 写成 denied / unknown |
| GEN-002 | P0 | 没有呕吐 | denied proposition 被支持 | denied 写成 present / unknown |
| GEN-003 | P0 | 好像没有呕吐 | uncertain 保留 | 强化为确定否认 |
| GEN-004 | P1 | 未提及精神状态 | 不生成精神状态 claim | 补造 normal / denied |
| GEN-005 | P0 | 前天开始换粮 | temporal scope 保留 | 时间漂移为今天 / 长期 |
| GEN-006 | P0 | 大便有一点软 | 症状与程度保留 | 升级为血便 / 严重腹泻 |
| GEN-007 | P0 | 饭和水都正常 | shared scope 可拆分或被 review 标记 | 合并为不可审查笼统 claim 且未发现 |
| GEN-008 | P0 | 请先回答，不要继续追问 | `answer_now=true` 且不进入 claims | intent 被忽略或写成医学事实 |
| GEN-009 | P1 | 你觉得呢 | no explicit fact 可显式表达 | 生成默认事实 |
| GEN-010 | P0 | 复杂显式事实 + 空 claims | suspicious empty 被 M08 拦截 | 空结果被视为无事实通过 |
| GEN-011 | P1 | 用户纠正旧信息 | correction intent 保留 | 新旧事实无条件并存 |
| GEN-012 | P1 | 多轮指代 | bounded history 足以自包含 | 指代对象被猜测 |

## 16. Lane 5：M08 Review 校准矩阵

Review 校准 case 使用固定 TurnSnapshot 与固定 claims，直接校准 M08，不依赖 M06
输出稳定性。

### 16.1 Coverage Review

| ID | 优先级 | 场景 | 期望 true dimension |
|---|---:|---|---|
| REV-001 | P0 | 漏掉“没有血便” | `存在漏抽显式事实` |
| REV-002 | P0 | “进食和饮水正常”合并 | `存在多事实合并` 或 `存在shared scope拆分错误` |
| REV-003 | P1 | 重复 claim | `存在重复claim` |
| REV-004 | P0 | 添加原文不支持的就医建议 | `存在原文不支持的claim` 或 `医学推断或建议添加` |
| REV-005 | P0 | “它不舒服”非自包含 | `存在非自包含proposition` / `指代对象不明` |
| REV-006 | P0 | 复杂输入返回空 claims | `存在漏抽显式事实` |
| REV-007 | P1 | 无显式事实返回空 claims | 无覆盖问题，`no_explicit_fact` 语义保留 |
| REV-008 | P1 | 未分类覆盖问题 | `未分类覆盖问题` 并路由 human review |

### 16.2 Faithfulness Review

| ID | 优先级 | 场景 | 期望 true dimension |
|---|---:|---|---|
| REV-101 | P0 | normal 写成 unknown | `正常状态误写为否认` 或相应事实类型 / 语义漂移维度 |
| REV-102 | P0 | denied 写成 present | `否定方向改变` |
| REV-103 | P0 | “没有呕吐和干呕”范围收窄 | `否定范围改变` / `否定范围不明` |
| REV-104 | P1 | 主体从猫变成人 | `主体或指代范围改变` |
| REV-105 | P0 | “前天”写成“今天” | `时间范围改变` |
| REV-106 | P0 | “一天一次”写成“多次” | `频率或数量改变` |
| REV-107 | P0 | “有一点软”写成“严重” | `程度或强度改变` |
| REV-108 | P1 | “好像没有”写成“确定没有” | `确定性改变` |
| REV-109 | P0 | 添加换粮导致软便因果 | `因果关系改变` / `医学推断或建议添加` |
| REV-110 | P0 | 添加立即就医建议 | `医学推断或建议添加` |
| REV-111 | P0 | “它也正常”指代不明 | `指代对象不明`，进入 clarification |
| REV-112 | P1 | “和之前一样”基线不明 | `比较基线不明`，进入 clarification |
| REV-113 | P1 | 未分类语义漂移 | `未分类语义改变`，进入 human review |

### 16.3 Review 非目标

M08 case 不要求 reviewer 输出：

```text
verdict
reason
confidence
corrected proposition
repair task
medical advice
```

这些字段出现时必须 schema blocked。

## 17. Lane 5：M09 修复路由矩阵

| ID | 优先级 | M08 问题 | 期望 M09 route / lane |
|---|---:|---|---|
| PLAN-001 | P0 | 漏抽显式事实 | `repair_required` + `claim_inventory_repair` |
| PLAN-002 | P0 | 多事实合并 | `repair_required` + `claim_inventory_repair` |
| PLAN-003 | P0 | shared scope 拆分错误 | `repair_required` + `claim_inventory_repair` |
| PLAN-004 | P1 | duplicate claim | `repair_required` + `claim_inventory_repair` |
| PLAN-005 | P0 | 单 claim normal/denied 漂移 | `repair_required` + `claim_proposition_repair` |
| PLAN-006 | P0 | 单 claim 否定方向漂移 | `repair_required` + `claim_proposition_repair` |
| PLAN-007 | P0 | 单 claim 时间漂移 | `repair_required` + `claim_proposition_repair` |
| PLAN-008 | P0 | 单 claim 程度漂移 | `repair_required` + `claim_proposition_repair` |
| PLAN-009 | P0 | 指代对象不明 | `clarification_required`，无 repair task |
| PLAN-010 | P0 | 时间基准不明 | `clarification_required`，无 repair task |
| PLAN-011 | P0 | 比较基线不明 | `clarification_required`，无 repair task |
| PLAN-012 | P0 | 未分类问题 | `human_review_required`，无自动 repair |
| PLAN-013 | P1 | reviewer disagreement | `disagreement`，不自动修复 |
| PLAN-014 | P1 | review failed | `review_failed`，不进入 M10 |
| PLAN-015 | P0 | coverage repair 优先 | 下游 proposition repair 被 stale / suppressed 记录 |

所有 M09 case 都必须验证：

```text
source_proposal_digest 一致
review_bundle_digest 一致
turn_snapshot_digest 一致
review dimensions 只来自 M08 true dimensions
repair_depth = 1
不生成 corrected proposition
```

## 18. Lane 5：M10 修复语义矩阵

| ID | 优先级 | 修复场景 | 期望 patch / preview |
|---|---:|---|---|
| FIX-001 | P0 | normal 误写为 denied | replace proposition，normal 恢复 |
| FIX-002 | P0 | denied 误写为 present | replace proposition，denied 恢复 |
| FIX-003 | P0 | 漏抽“没有血便” | add claim，否定保留 |
| FIX-004 | P0 | “饭和水正常”合并 | split / replace-add 局部拆分 |
| FIX-005 | P0 | 程度夸大 | replace proposition，程度恢复 |
| FIX-006 | P0 | 时间漂移 | replace proposition，时间恢复 |
| FIX-007 | P0 | no-op replacement | blocked，不生成有效 patch |
| FIX-008 | P0 | target digest mismatch | blocked |
| FIX-009 | P0 | base version mismatch | blocked |
| FIX-010 | P0 | 两个 patch 目标冲突 | patch set blocked |
| FIX-011 | P0 | 修复后引入新语义问题 | post-patch probe 失败 |
| FIX-012 | P1 | 修复后仍存在原问题 | post-patch probe 失败，不得宣称有效 |

M10 case 必须验证模型输出不包含：

```text
operation
after_claim_index
addresses_dimensions
base_version
artifact_reference
patch_id
complete claims rewrite
```

系统必须推导：

```text
operation 类型
插入位置
patch identity
target digest
base version
application preview
next version
```

## 19. Lane 6：Fail Fast 与负例矩阵

| ID | 优先级 | 场景 | 期望 |
|---|---:|---|---|
| NEG-001 | P0 | Temporal 不可用 | 测试启动显式失败，不降级 |
| NEG-002 | P0 | LiteLLM 不可用 | 模型调用显式失败，不 fallback |
| NEG-003 | P0 | PostgreSQL 不可用 | projection 显式失败 |
| NEG-004 | P0 | model schema invalid | `schema_invalid` / response parse error |
| NEG-005 | P0 | forbidden extra field | schema blocked |
| NEG-006 | P0 | finish reason 非 stop | 模型调用失败 |
| NEG-007 | P0 | requested model 被替换 | gateway contract error |
| NEG-008 | P0 | prompt context digest mismatch | gateway contract error |
| NEG-009 | P0 | task / skill version mismatch | contract error |
| NEG-010 | P0 | TODO TurnSnapshot reader | 显式 NotImplementedError |
| NEG-011 | P0 | TODO M11 snapshot resolver | 显式 Fail Fast |
| NEG-012 | P0 | TODO patch store | 显式 Fail Fast，无 new artifact |
| NEG-013 | P0 | TODO SemanticTaskExecutor | 显式 Fail Fast |
| NEG-014 | P0 | proposal digest mismatch | M08 / M09 blocked |
| NEG-015 | P0 | review bundle digest mismatch | M10 blocked |
| NEG-016 | P0 | repair plan identity mismatch | M10 blocked |
| NEG-017 | P0 | claim index 越界 | patch blocked |
| NEG-018 | P0 | patch 超出预算 | repair exhausted / blocked |
| NEG-019 | P0 | repair of repair | 禁止，repair_depth 保持 1 |
| NEG-020 | P0 | 失败转空 facts | 必须失败 |

## 20. Lane 6：领域与实验隔离矩阵

| ID | 优先级 | 检查项 | 期望 |
|---|---:|---|---|
| ISO-001 | P0 | ConsultationStateService | 未调用 |
| ISO-002 | P0 | 临床安全 semantic extractor | 未调用 |
| ISO-003 | P0 | 临床安全 evaluator / retrieval | 未调用 |
| ISO-004 | P0 | required_context | 未调用 |
| ISO-005 | P0 | 临床安全 OPA | 未调用 |
| ISO-006 | P0 | Mem0 | 未调用 |
| ISO-007 | P0 | 长期记忆写入 | 未发生 |
| ISO-008 | P0 | 旧问诊语义抽取器 | 未 fallback |
| ISO-009 | P0 | input_preprocessing 实验 runner | 未调用 |
| ISO-010 | P0 | 实验 held-out fixture | 读取数为 0 |
| ISO-011 | P0 | DSPy | 未使用 |
| ISO-012 | P0 | VetOrchestrator | 未接入 |

隔离 case 可通过 dependency injection 观测、导入检查或专用 guard test 实现，但不得
为了“证明未调用”而在生产模块中新增 Hook。

## 21. 报告契约

### 21.1 存放路径

```text
.data/evaluations/semantic-collaboration-integration/
```

文件名建议：

```text
semantic-pre-m11-<UTC timestamp>-<short run id>.json
```

报告是测试级观测产物，不是 M14 生产 trace。

### 21.2 顶层字段

```json
{
  "report_version": "semantic-pre-m11-integration-report-v1",
  "run_id": "...",
  "test_design_revision": "...",
  "code_revision": "...",
  "started_at": "...",
  "finished_at": "...",
  "execution_mode": "external|semantic|full-pre-m11",
  "environment": {
    "litellm_ready": true,
    "temporal_ready": true,
    "postgres_ready": true,
    "model": "qwen-plus",
    "temporal_namespace": "semantic-collaboration-dev",
    "task_queue": "semantic-collaboration-dev"
  },
  "summary": {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0
  },
  "case_results": []
}
```

### 21.3 case 结果字段

```json
{
  "case_id": "<case_id>",
  "lane": "semantic_generation",
  "priority": "P0",
  "status": "passed|failed|skipped",
  "duration_ms": 0,
  "failure_code": null,
  "failure_message": null,
  "turn_snapshot_digest": null,
  "plan_id": null,
  "task_id": null,
  "skill_id": null,
  "skill_version": null,
  "prompt_hash": null,
  "proposal_digest": null,
  "m07_state": null,
  "review_outcome": null,
  "true_dimensions": [],
  "repair_route": null,
  "repair_lane": null,
  "patch_state": null,
  "preview_claim_count": null,
  "post_patch_probe_state": null,
  "original_dimension_resolved": null,
  "new_dimension_introduced": null
}
```

### 21.4 模型调用审计字段

```json
{
  "requested_model": "qwen-plus",
  "response_model": null,
  "response_id": null,
  "finish_reason": null,
  "latency_ms": 0,
  "prompt_tokens": null,
  "completion_tokens": null,
  "total_tokens": null,
  "usage_available": false
}
```

token 字段缺失时保持 `null`，不得改写为 `0`。

### 21.5 安全与边界字段

每份报告必须包含：

```json
{
  "consultation_state_written": false,
  "clinical_safety_evaluator_called": false,
  "clinical_safety_retrieval_called": false,
  "clinical_safety_opa_called": false,
  "required_context_called": false,
  "long_term_memory_written": false,
  "mem0_called": false,
  "old_semantic_extractor_called": false,
  "input_preprocessing_experiment_called": false,
  "heldout_read_count": 0,
  "dspy_used": false,
  "m11_store_used": false,
  "m11_commit_performed": false,
  "artifact_reference_is_authoritative": false
}
```

### 21.6 报告脱敏

报告不得包含：

```text
API key
JWT
数据库密码
Temporal 内部连接串
完整系统 prompt
未脱敏外部服务配置
用户真实身份信息
```

fixture 用户原文属于测试数据，可进入测试内部排障日志；公开归档报告应优先记录
digest 与 case_id。

## 22. 执行模式

### 22.1 默认单元 / 契约测试

```text
不访问 LiteLLM
不访问 Temporal
不访问 PostgreSQL
不进入 external integration marker
```

### 22.2 工程外部集成

```bash
RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST=true
```

执行：

```text
Lane 0
Lane 1
Lane 2
Lane 3
Lane 4
Lane 6
```

### 22.3 语义验证

```bash
RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST=true
RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST=true
```

额外执行：

```text
Lane 5
```

### 22.4 Full pre-M11 gate

进入 M11 实现前执行：

```text
Lane 0～Lane 6 全部 P0 case
已定义 P1 case 的显式结果与归因
报告归档
```

### 22.5 重复执行

如需观察真实模型稳定性，可显式执行多次完整 run：

```text
run 1
run 2
run 3
```

要求：

1. 每个 run 独立生成 workflow 身份；
2. 每个 case result 独立记录；
3. 不在单次 case 内隐藏 retry；
4. 不用最后一次成功覆盖失败；
5. 汇总指标单独输出。

## 23. 建议测试交付物

```text
tests/integration/test_semantic_collaboration_pre_m11_external.py
tests/integration/semantic_collaboration_integration_helpers.py
tests/fixtures/semantic-collaboration/engineering-turns-v1.json
tests/fixtures/semantic-collaboration/semantic-regression-v1.json
scripts/integration/run-semantic-collaboration-pre-m11-smoke.sh
.data/evaluations/semantic-collaboration-integration/
```

测试实现不得修改：

```text
src/vet_agent/semantic_collaboration/**
src/vet_agent/orchestrator.py
问诊状态仓储
临床安全链路
长期记忆链路
```

如果发现生产契约缺陷，应停止集成测试实现，先修复生产模块并同步架构文档与测试
设计。

## 24. 脚本设计要求

建议脚本：

```text
scripts/integration/run-semantic-collaboration-pre-m11-smoke.sh
```

脚本必须：

1. 校验显式开关与必填环境变量；
2. 校验 API key 格式但不打印明文；
3. 可选建立 LiteLLM、Temporal、PostgreSQL SSH tunnel；
4. 执行依赖健康检查；
5. 以 integration marker 运行测试；
6. 支持 `--semantic` 启用语义矩阵；
7. 支持 `--full-pre-m11` 执行全部 P0；
8. 保留 pytest 退出码；
9. 输出报告路径；
10. 不写任何密钥到报告。

禁止脚本：

```text
在服务不可用时自动改用 mock
自动重跑失败测试
自动清理失败报告
从临时 README 读取生产密钥
修改远端服务配置
```

## 25. 通过、失败与准入

### 25.1 P0 通过条件

```text
环境依赖全部可用
契约组合全部通过
真实 LiteLLM strict schema case 通过
M07 / M08 / M09 / M10 状态显式且身份一致
真实 Temporal workflow 全部任务终态完整
PostgreSQL projection 与 workflow 结果一致
所有 TODO 负例显式 Fail Fast
语义 P0 case 达到期望状态
无下游领域调用
无隐藏 fallback / retry / 宽松解析
报告完整归档
```

### 25.2 不能作为通过条件

```text
模型输出与固定中文句子逐字一致
绕过 M11 生成 artifact
测试代码直接标记 verified
问诊状态被成功更新
临床安全链路被调用
旧抽取器 fallback 成功
失败 case 被跳过
```

### 25.3 失败处理

失败后允许：

```text
修复生产契约 / prompt / schema / orchestration
修复测试 harness
修正 fixture 标注错误
更新本测试设计并重新评审
记录 P1 观察结论
```

禁止：

```text
放宽 schema
删除断言
加入关键词规则
隐藏 retry
把失败写成空结果
为了通过伪造 M11
```

### 25.4 进入 M11 的工程准入

建议 M11 开工前满足：

1. 全部 P0 工程 case 通过；
2. 语义 P0 case 通过或已有明确生产修复计划；
3. P1 case 均有结果与归因；
4. 真实 LiteLLM、Temporal、PostgreSQL 版本与环境记录完整；
5. 报告可复现；
6. TODO 边界未被测试绕过；
7. M10 的失败形态足以推导 M11 snapshot / commit / gate 需求。

## 26. M11 完成后的测试升级

M11 实现后，本文必须升级或拆分为 closed-loop integration test design，并新增：

```text
base artifact snapshot 身份校验
append-only commit
version + 1
repair lineage
stale marker
幂等提交
并行 patch 冲突
M11 commit 后 M07 re-verify
M07 accepted 后 M08 re-review
M08 结果进入 artifact gate state
repair_depth = 1 后禁止继续 repair
verified 只能由完整门禁产生
```

本文中的以下 test-only 组件应删除或降级：

```text
FixedRepairTargetSnapshotResolver
test-only post-patch semantic probe 的权威化用法
scheduler-only executor 对生产链路的替代表述
```

## 27. Code review 检查清单

集成测试 PR 评审时必须逐项检查：

1. 每个 test 有 `case_id`。
2. 每个 `case_id` 来自本文。
3. 未新增未声明测试替身。
4. 测试替身只存在于 integration 测试目录。
5. 测试 fixture 不读取实验 held-out。
6. 未绕过 M05 直接调用 LiteLLM 业务链路。
7. 未绕过 M04 直接操作调度状态。
8. 未隐藏 retry。
9. 未解析宽松 JSON。
10. 未用关键词判断医学语义。
11. 未调用 VetOrchestrator。
12. 未读写问诊状态。
13. 未调用临床安全链路。
14. 未调用 Mem0 或长期记忆。
15. TODO 依赖均显式失败。
16. `patch_ready` 未被写成 `verified`。
17. Temporal 不可用未降级。
18. PostgreSQL projection 未被当作 M11。
19. 报告包含安全与边界字段。
20. 报告不包含密钥。
21. 失败路径有稳定 failure code。
22. 文档矩阵与代码同步。

任一项不满足，不得以“先跑通”为由合并。

## 28. 文档同步触发条件

出现以下变化必须先更新本文：

```text
SkillSpec / schema version 调整
M06 输出契约调整
M08 review dimension 调整
M09 repair routing 调整
M10 patch contract 调整
M04 workflow / projection 契约调整
外部服务环境边界调整
测试矩阵新增或删除
测试替身新增或删除
M11 实现状态变化
M12 / M13 / M14 / M15 开始接入
```

## 29. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)
4. [semantic-collaboration-dag-m05-structured-llm-gateway-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m05-structured-llm-gateway-change-summary.md)
5. [semantic-collaboration-dag-m06-generation-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-generation-skill-change-summary.md)
6. [semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)
7. [semantic-collaboration-dag-m09-repair-planner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m09-repair-planner-change-summary.md)
8. [semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md)
9. [semantic-collaboration-dag-pre-m11-semantic-validation-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-semantic-validation-summary.md)
10. [temporal-dev-environment-baseline.md](/home/vancer17/veterinary_agent/docs/deployment/temporal-dev-environment-baseline.md)
