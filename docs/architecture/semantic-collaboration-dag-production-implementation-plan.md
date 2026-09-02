<!--
=============================================================================
文件: semantic-collaboration-dag-production-implementation-plan.md
作用: 将受限语义协作 DAG 生产架构拆成模块级工程交付顺序、依赖边界、
      数据边界、测试门禁、生产接入顺序和回滚边界。
范围: 适用于生产代码迁移、契约实现、SKILL 目录、Plan IR、任务调度、
      生成 / 审查 / 修复、artifact 版本、claim graph、领域投影和观测治理。
说明: 本文不改变生产架构基线，不记录执行框架选型，不包含实验计划，
      不展开类、函数、提示词全文或测试替身实现。
维护: 当模块划分、阶段交付顺序、持久化契约、生产接入顺序、回滚策略或
      工程验收口径调整时，必须同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG 生产模块级实施计划

> **文档状态**：生产工程模块级实施计划
>
> **权威关系**：本文以
> [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
> 为架构基线；若本文与架构基线冲突，必须先修订架构基线，再同步本文。
>
> **适用范围**：生产工程实现顺序、模块职责、模块依赖、交付物、验收标准、
> 持久化边界、测试门禁、生产接入与回滚
>
> **不适用范围**：医学规则设计、临床安全策略、OPA 细节、问诊状态实现、
> 长期记忆写入策略、执行框架选型、实验矩阵和模型质量实验

## 1. 目标与工程定位

本文目标是将生产架构拆成可独立开发、独立 review、独立测试和独立回滚的模块，
避免一次性大提交造成以下问题：

1. 契约未稳定前先实现 prompt。
2. 生成任务先上线，verifier 后补。
3. Review 或 Repair 为降低延迟被弱化。
4. artifact 没有版本，patch 直接覆盖输出。
5. 上游修复后下游结果未标记 stale。
6. 领域投影提前承担领域业务逻辑。
7. 生产失败被静默回退或表现为空事实。
8. `input_preprocessing` 实验代码与生产主路径混在一起。

本计划描述的是生产工程交付顺序，不是架构验证实验。测试和验收是工程质量门禁，
不用于替代架构基线。

## 2. 实施原则

### 2.1 契约先行

每个阶段必须先交付稳定契约，再实现执行逻辑。

正确顺序：

```text
SkillSpec / schema / failure state
→ executor / verifier
→ integration
```

禁止顺序：

```text
prompt
→ 模型输出
→ 反推 schema
→ 兼容非法输出
```

### 2.2 验证与任务同步交付

每个生成 SKILL 交付时，对应 verifier 必须同步交付。

禁止：

```text
先生成，后统一补 verifier
先接入生产，后补 strict schema
先允许 forbidden field，后治理
```

### 2.3 显式失败优先

工程实现必须 Fail Fast 或进入显式终态。

禁止：

```text
失败转空集合
异常转 unknown
模型调用失败转 no_explicit_fact
review 失败默认原任务通过
repair 超预算静默放弃
```

### 2.4 生产包与实验代码隔离

V8～V14 实验代码只作为历史参考，不直接作为生产主路径依赖。

生产实现应建立独立生产边界，例如：

```text
src/vet_agent/semantic_collaboration/
tests/test_semantic_collaboration*.py
```

具体包名可在工程实现时调整，但必须满足：

1. 生产模块不 import 实验 runner。
2. 实验 runner 不反向调用生产 orchestrator。
3. 生产代码不读取实验 held-out fixture。
4. 生产测试不依赖实验报告作为通过条件。
5. 可复用的低层结构化调用能力应通过稳定 adapter 引入，而不是复制实验代码。

### 2.5 不做隐式生产回退

旧问诊语义链路可以保留用于部署级回滚，但不得在单次请求内静默回退。

允许：

```text
显式配置切换
可审计回滚
失败原因保留
```

禁止：

```text
新 DAG 失败后自动调用旧抽取器
把旧链路结果伪装成新 DAG 结果
吞掉失败指标
```

## 3. 模块总览

| 模块 | 名称 | 主要交付 | 所属阶段 |
|---|---|---|---|
| M01 | Skill 契约与目录 | SkillSpec、SkillCatalog、所有权校验 | Phase 1 |
| M02 | TurnSnapshot | 不可变上下文、digest、上下文预算 | Phase 1 |
| M03 | Plan IR 与校验 | PlanIR、PlanValidator、规划 LLM adapter | Phase 2 |
| M04 | DAG 调度 | 拓扑调度、并发、超时、终态 | Phase 2 |
| M05 | 结构化 LLM Gateway | SKILL 调用、schema、usage、失败状态 | Phase 3 |
| M06 | 生成 SKILL | intent、claim、语义与 phrase 生成 | Phase 3 / 4 |
| M07 | Deterministic Verifier | schema、所有权、evidence、binding 校验 | Phase 3 / 4 |
| M08 | Review SKILL | 正交审查、ReviewArtifact | Phase 5 |
| M09 | Repair Planner | failure mapping、repair budget | Phase 5 |
| M10 | Repair 与 Patch | typed patch、patch 校验与应用 | Phase 5 |
| M11 | Artifact Store | append-only artifact、版本、lineage、stale | Phase 1 契约 / Phase 5 实现 |
| M12 | Claim Graph | graph assembly、一致性门禁 | Phase 6 |
| M13 | 领域投影 Adapter | 问诊、临床安全、长期记忆投影契约 | Phase 6 / 8 |
| M14 | 可观测性 | trace、metrics、failure attribution | 全阶段 |
| M15 | 生产接入 | orchestrator 边界、配置、回滚 | Phase 7 |

模块依赖关系：

```text
M01 SkillCatalog
   ↓
M03 PlanIR ← M02 TurnSnapshot
   ↓
M04 Scheduler
   ↓
M05 Gateway → M06 Generator → M07 Verifier
                                ↓
                             M08 Review
                                ↓
                     M09 Repair Planner → M10 Repair / Patch
                                ↓
M11 Artifact Store / Version / Stale
   ↓
M12 Claim Graph
   ↓
M13 Domain Projection
   ↓
M15 Production Integration

M14 Observability 贯穿所有模块
```

## 4. 模块级交付定义

### M01：Skill 契约与目录

**目标**

建立生产 SKILL 的机器可读权威契约和全局目录。

**范围**

```text
SkillSpec
SkillCatalog
SkillRegistry
字段所有权
context policy
failure code 目录
repair mapping 目录
SKILL.md 投影校验
```

**上游依赖**

无业务上游依赖。

**下游被依赖**

```text
Plan Validator
Scheduler
Gateway
Verifier
Review
Repair Planner
Patch Applier
```

**交付物**

1. SkillSpec 契约。
2. SkillCatalog 启动校验。
3. 字段所有权矩阵。
4. context requirement 声明。
5. forbidden output 声明。
6. verifier binding 声明。
7. failure code 与 repair mapping 声明。
8. `SKILL.md` 与 manifest 的一致性检查。

**验收标准**

```text
skill_id / version 唯一
重复字段所有权启动失败
owns 与 forbidden_output 冲突启动失败
未绑定 verifier 的 SKILL 注册失败
未知 context policy 注册失败
未知 failure code 无法创建 repair task
SKILL.md 缺少必需段 blocked
```

**禁止事项**

```text
不按症状词注册 SKILL
不在 SKILL.md 中维护医学词表
不运行时解析 Markdown 正文作为字段所有权
不允许未注册 SKILL 进入 Plan IR
```

### M02：TurnSnapshot

**目标**

为所有生成、审查和修复任务提供同一不可变受限全局视图。

**范围**

```text
TurnSnapshot
SnapshotBuilder
context digest
snapshot version
有界历史上下文
可信宠物上下文
上下文预算
```

**上游依赖**

```text
会话上下文读取边界
宠物上下文读取边界
```

**禁止读取**

```text
问诊状态
临床安全召回结果
required_context 评估
临床安全 OPA 输入或输出
长期记忆
未验证同伴任务输出
```

**交付物**

1. TurnSnapshot schema。
2. `original_user_text` 保留策略。
3. `last_assistant_questions` 有界策略。
4. `verified_prior_fact_summary` 输入边界。
5. `context_digest` 计算与校验。
6. 上下文预算失败状态。

**验收标准**

```text
snapshot 创建后不可变
generator / reviewer / repairer digest 一致
原文不得被摘要替代
历史上下文超界显式失败
禁止上下文进入 snapshot
context_budget_exceeded 可测试
```

### M03：Plan IR 与 Plan Validator

**目标**

把任务规划 LLM 的输出限制为固定字段 `PlanSelection`，再由确定性编译层生成
可验证 Plan IR。

**范围**

```text
PlanIR
PlanSelection
PlanPolicySpec
DeterministicPlanCompiler
PlanValidator
任务规划 LLM adapter
Plan envelope 契约
```

**上游依赖**

```text
M01 SkillCatalog
M02 TurnSnapshot
```

**交付物**

1. Plan IR schema。
2. 固定字段 PlanSelection 契约。
3. 生产 PlanPolicy 与静态依赖规则。
4. task / dependency / envelope 契约。
5. skill 与 version 校验。
6. 依赖存在、策略一致与无环校验。
7. context policy 与 TurnSnapshot 投影预算校验。
8. expected output schema 校验。
9. turn / snapshot / catalog / policy digest 绑定校验。
10. canonical plan_id 校验。
11. 规划失败状态。

**验收标准**

```text
未知 skill blocked
非法 version blocked
task_id 重复 blocked
依赖缺失 blocked
依赖环 blocked
envelope 非法 blocked
context policy 越权 blocked
expected output schema 不一致 blocked
PlanPolicy 依赖不一致 blocked
snapshot / catalog / policy digest 不一致 blocked
plan_id 与 canonical 内容不一致 blocked
计划或上下文预算超限 blocked
规划失败不触发默认硬编码任务
```

### M04：DAG 调度器

**目标**

按 Plan IR 执行任务，并保证每个任务进入显式终态。

**范围**

```text
DAGScheduler
TaskExecutionState
并发分组
超时控制
依赖失败传播
terminal state 汇总
```

**上游依赖**

```text
M01 SkillCatalog
M03 PlanIR
```

**交付物**

1. 拓扑执行器。
2. 无依赖任务并行调度。
3. 任务超时状态。
4. 依赖失败传播。
5. cancellation / deadline 传播。
6. 任务运行状态记录。

**验收标准**

```text
依赖顺序正确
独立任务可并行
依赖失败输出 dependency_failed
超时输出 timeout
任务无悬空状态
调度器不修改 TurnSnapshot
```

**禁止事项**

```text
不在调度器中解释医学语义
不在调度器中动态发明任务
不把异常任务静默跳过
```

### M05：结构化 LLM Gateway

**目标**

为所有 SKILL 提供统一、可审计的结构化模型调用边界。

**范围**

```text
StructuredLLMGateway
Skill prompt 投影
response schema 绑定
usage / model snapshot / finish reason
模型调用失败状态
attempt 审计
```

**上游依赖**

```text
M01 SkillCatalog
现有结构化模型客户端能力
```

**交付物**

1. Gateway 接口。
2. SkillSpec 到 prompt projection 的转换。
3. strict output schema 绑定。
4. usage 和模型快照记录。
5. 调用失败与 schema 失败区分。
6. attempt metadata。

**验收标准**

```text
输出必须是 strict schema
extra field 不被清洗
schema_invalid 显式失败
模型调用失败显式失败
usage 缺失可观测但不伪装成功
attempt_count 与 failure history 完整
```

**禁止事项**

```text
不做宽松 JSON 检索
不做手工 JSON 修复
不做医学兜底
不用 retry 后结果冒充单次成功
```

### M06：生成 SKILL

**目标**

实现正交窄域语义生成任务。

**范围**

```text
TurnIntentGenerator
ClaimInventoryGenerator
ClaimStatementSemanticsGenerator
ParticipantPhraseGenerator
TemporalPhraseGenerator
MeasurementPhraseGenerator
CanonicalDescriptorGenerator
```

**上游依赖**

```text
M01 SkillCatalog
M02 TurnSnapshot
M03 PlanIR
M04 Scheduler
M05 Gateway
```

**建议实现顺序**

```text
Turn Intent
→ Claim Inventory
→ Claim Statement Semantics
→ Participant Phrase
→ Temporal Phrase
→ Measurement Phrase
→ Canonical Descriptor
```

**验收标准**

```text
fixed-field intent 无重复事实数组
shared scope 可拆分
reported_normal / denied / present / uncertain / corrected / unknown 分离
participant 只输出 phrase
temporal / measurement 只输出 phrase 与 binding
canonical 只输出 descriptor / query / binding
所有输出携带 claim_id 与 evidence binding
所有输出不包含 forbidden field
```

**禁止事项**

```text
不输出 entity_id
不输出 canonical_id
不输出诊断、临床风险或安全动作
不新增其他权威域字段
```

### M07：Deterministic Verifier

**目标**

在结构层和证据层验证生成结果，不做医学判断。

**范围**

```text
SchemaVerifier
OwnershipVerifier
EvidenceVerifier
ClaimBindingVerifier
ContextDigestVerifier
CrossFieldVerifier
EnumVerifier
```

**上游依赖**

```text
M01 SkillCatalog
M02 TurnSnapshot
M06 Generator 输出
```

**交付物**

1. strict schema 校验。
2. extra field 拒绝。
3. 字段所有权校验。
4. evidence phrase 校验。
5. parent scope 校验。
6. claim binding 校验。
7. context digest 校验。
8. 跨字段冲突状态。

**验收标准**

```text
forbidden_field_present blocked
field_ownership_violation blocked
evidence_not_found blocked
evidence_outside_parent_scope blocked
claim_binding_invalid blocked
context_digest_mismatch blocked
semantic_conflict 显式保留
```

**禁止事项**

```text
不删除 forbidden field 后放行
不做关键词医学判断
不把 proposal 标记 verified
不用模糊匹配自由接受语义
```

### M08：Review SKILL

**目标**

按任务域独立审查候选结果的语义忠实性。

**范围**

```text
ReviewRunner
TurnIntentReviewer
ClaimInventoryReviewer
StatementSemanticsReviewer
ParticipantReviewer
TemporalReviewer
MeasurementReviewer
CanonicalReviewer
GraphConsistencyReviewer
```

**上游依赖**

```text
M01 SkillCatalog
M02 TurnSnapshot
M06 Generator
M07 Verifier
```

**交付物**

1. Review SKILL 注册。
2. Review 输入 envelope。
3. ReviewArtifact schema。
4. verdict / failure_code / repair_hint 契约。
5. review 输出 verifier。
6. review terminal state。

**验收标准**

```text
review 按任务域正交
一次审查目标数量受限
review 与 generator 使用同一 context_digest
review 不直接修改 artifact
review 输出 forbidden field blocked
review_failed 不使原任务 verified
review_disagreement 显式保留
```

**建议实现顺序**

```text
Statement Semantics Review
→ Claim Inventory Review
→ Turn Intent Review
→ Participant / Temporal / Measurement / Canonical Review
→ Graph Consistency Review
```

### M09：Repair Planner

**目标**

根据注册 failure code 创建受限修复任务，并控制修复预算。

**范围**

```text
FailureCodeCatalog
RepairMapping
RepairPlanner
RepairBudget
```

**上游依赖**

```text
M01 SkillCatalog
M08 ReviewArtifact
```

**交付物**

1. failure code 目录。
2. failure code 到 Repair SKILL 的映射。
3. repair depth 控制。
4. per-field / per-claim / per-turn budget。
5. `repair_unavailable` 状态。

**验收标准**

```text
未知 failure code 不动态匹配 repair
repair_depth 不超过 1
不允许 repair of repair
同一字段最多一次修复
budget 超限输出 repair_exhausted
```

### M10：Repair SKILL 与 Patch

**目标**

用局部 typed patch 修复可恢复错误。

**范围**

```text
RepairSkill
RepairPatchProposal
PatchVerifier
PatchApplier
RepairLineage
```

**上游依赖**

```text
M08 ReviewArtifact
M09 Repair Planner
M11 ArtifactStore
```

**交付物**

1. Repair SKILL 注册。
2. patch operation 白名单。
3. base version 契约。
4. patch verifier。
5. deterministic applier。
6. repair lineage。

**验收标准**

```text
patch path 越权 blocked
base_version 冲突 blocked
forbidden field 不可修复
无证据 patch blocked
patch 应用后 artifact version + 1
repair lineage 可追溯
```

**禁止事项**

```text
不自由重写完整 artifact
不修复 schema 根本非法输出
不补造缺失事实
不确认无候选 canonical
不修改临床风险或安全动作
```

### M11：Artifact Store 与版本

**目标**

以 append-only 方式维护任务结果、版本、修复谱系和 stale 关系。

**范围**

```text
TaskArtifact
ArtifactVersion
ArtifactStore
RepairLineage
StaleMarker
TerminalStateStore
```

**实现顺序**

Phase 1 先交付契约；Phase 5 与 repair 一起交付生产持久化。

**交付物**

1. artifact schema。
2. artifact version 状态。
3. append-only 写入接口。
4. repair lineage 存储。
5. stale dependency 存储。
6. terminal state 查询。

**验收标准**

```text
artifact 不可原地覆盖
版本可追溯
失败结果不会被成功结果覆盖
repair lineage 完整
上游结构变化触发下游 stale
历史 artifact 可审计
```

### M12：Claim Graph 与一致性门禁

**目标**

将 verified artifact 组装为可投影 claim graph。

**范围**

```text
ClaimGraphBuilder
BindingMerger
GraphConsistencyGate
GraphArtifact
```

**上游依赖**

```text
M06～M11
```

**交付物**

1. claim graph schema。
2. claim / semantics / participant / temporal / measurement / canonical 合并。
3. ID 引用校验。
4. binding 唯一性校验。
5. 图级一致性门禁。
6. graph terminal state。

**验收标准**

```text
只消费 verified / repair_verified artifact
不消费 proposal
引用缺失 blocked
claim 重复 blocked
局部 gap 保留为 graph_partial_with_gaps
图级冲突保留为 graph_disagreement
```

### M13：领域投影 Adapter

**目标**

将 verified graph 转换为领域契约，不在 preprocessing 中实现领域业务。

**范围**

```text
ConsultationProjectionAdapter
ClinicalSafetyProjectionAdapter
LongTermMemoryProjectionAdapter
ProjectionContract
ProjectionResult
```

**实现顺序**

```text
ConsultationProjectionAdapter
→ ClinicalSafetyProjectionAdapter
→ LongTermMemoryProjectionAdapter
```

**问诊投影验收**

```text
reported_normal 不映射为 denied
denied 保留否定语义
unknown 不代表追问已完成
answer_now 独立传递
adapter 不做医学风险判断
```

**临床安全投影验收**

```text
只输出声明允许的结构化事实
不产生 urgent / blocked signal
不调用临床安全召回或 OPA
不做诊断或安全动作
```

**长期记忆投影验收**

```text
只输出候选
不写长期事实
不写 Mem0 投影
未实现时保留 TODO 空壳并显式失败
```

### M14：可观测性

**目标**

让每次任务、审查、修复和图级结果可定位、可归因、可统计。

**范围**

```text
TraceRecorder
MetricCollector
RunReport
FailureAttribution
RepairMetrics
ContextMetrics
```

**最小记录字段**

```text
turn_id
snapshot_digest
plan_id
task_id
skill_id
skill_version
prompt_hash
model snapshot
input envelope digest
artifact_id / version
review_verdict
failure_code
repair_lineage
latency
token usage
terminal_state
```

**验收标准**

```text
任一失败可定位到 task / skill / artifact / failure code
terminal state distribution 可统计
repair required / success / regression / exhausted 可观测
context budget failure 可观测
trace 不记录未授权下游领域状态
```

### M15：生产接入

**目标**

在工程门禁满足后，将语义协作 DAG 接入主业务链路。

**范围**

```text
VetOrchestrator 接入边界
配置项
显式路由
异常映射
回滚策略
运行开关审计
```

**验收标准**

```text
未完成 verifier / review / terminal state 前不得接入
生产失败不静默回退旧链路
失败映射为显式错误或 metadata
问诊状态只通过 adapter 消费
临床安全决策仍由既有链路负责
回滚可审计
```

## 5. 阶段化工程交付计划

### Phase 0：生产工程基线

**目标**

建立生产边界和工程门禁，不改变线上行为。

**交付**

```text
生产包边界
基础测试目录
架构文档引用
CI 纳入
旧实验代码隔离检查
```

**验收**

```text
生产模块不 import 实验 runner
实验模块不调用生产 orchestrator
默认测试不读取实验 held-out
新增空模块通过 lint / mypy / pytest
```

**禁止接入**

```text
VetOrchestrator
问诊状态
临床安全
长期记忆
```

### Phase 1：契约与目录

**目标**

交付 M01、M02、M11 契约和 M14 基础字段。

**交付**

```text
M01 SkillSpec / SkillCatalog
M02 TurnSnapshot 契约
M11 Artifact 契约
M14 trace 基础契约
核心状态枚举
契约测试
```

**验收**

```text
SkillCatalog 冲突启动失败
TurnSnapshot digest 稳定
artifact schema 可版本化
terminal state 枚举完整
核心负例测试通过
```

### Phase 2：计划与调度

**目标**

交付 M03、M04，使注册任务可以按受限 DAG 执行。

**交付**

```text
M03 PlanIR / PlanValidator / planner adapter
M04 DAGScheduler
TaskExecutionState
依赖与超时治理
```

**验收**

```text
非法 plan 全部 blocked
依赖环 blocked
独立任务并行执行
依赖失败传播
任务终态完整
```

**工程约束**

本阶段可使用测试替身验证调度，不要求接生产 orchestrator。

### Phase 3：核心生成与验证

**目标**

交付最小可闭环语义路径。

**交付**

```text
M05 StructuredLLMGateway
M06 Turn Intent
M06 Claim Inventory
M06 Claim Statement Semantics
M07 对应 verifier
M14 task trace
```

**验收**

```text
strict schema 全覆盖
forbidden field blocked
intent fixed-field 输出
claim envelope 可验证
shared scope 可拆分
normal / denied / uncertain 分离
evidence / binding 失败显式 blocked
```

**禁止接入**

```text
问诊状态
临床安全
长期记忆
```

### Phase 4：完整语义维度

**目标**

补齐语义协作 DAG 的生成与 deterministic 治理维度。

**交付**

```text
M06 Participant Phrase
M06 Temporal Phrase
M06 Measurement Phrase
M06 Canonical Descriptor
M07 对应 verifier
participant candidate-only resolver
temporal / measurement parser verifier
canonical candidate-only selector
```

**验收**

```text
participant 不发明 entity
无候选 / 多候选显式 not_found / ambiguous
temporal / measurement 归一化由 deterministic parser 权威完成
canonical 不假确认
canonical 无候选保持 not_found
```

### Phase 5：审查、修复与版本

**目标**

让错误可审查、可局部修复、可版本追溯。

**交付**

```text
M08 Review SKILL
M08 Review Verifier
M09 Repair Planner
M10 Repair SKILL / Patch
M11 ArtifactStore 生产实现
M11 stale marker
M14 repair metrics
```

**验收**

```text
review 不直接修改 artifact
review failure 不等于原任务通过
disagreement 显式保留
repair 只能输出白名单 patch
patch base version 校验有效
repair budget 有效
repair_exhausted 有效
上游修复触发下游 stale
```

### Phase 6：Claim Graph 与问诊投影

**目标**

形成可被问诊领域消费的 verified graph。

**交付**

```text
M12 ClaimGraphBuilder
M12 GraphConsistencyGate
M13 ConsultationProjectionAdapter
M14 graph metrics
```

**验收**

```text
graph 只消费 verified artifact
局部 gap 不被抹除
图级冲突显式保留
问诊 adapter 正确映射 normal / denied / uncertain / unknown
adapter 不写问诊状态
adapter 不做医学判断
```

### Phase 7：生产主路径接入

**目标**

将问诊消费链路接入生产 orchestrator，并保留显式回滚边界。

**交付**

```text
M15 VetOrchestrator 接入
显式配置路由
异常与 metadata 映射
回滚配置
生产 trace
```

**验收**

```text
DAG 失败显式暴露
失败不转为 facts=[]
旧链路仅允许部署级显式回滚
问诊状态写入只发生在既有问诊领域
生产请求可定位 plan / task / artifact
```

### Phase 8：其余领域投影与硬化

**目标**

在问诊主路径稳定后，补齐其他领域投影和生产硬化。

**交付**

```text
M13 ClinicalSafetyProjectionAdapter
M13 LongTermMemoryProjectionAdapter
M15 配置与回滚硬化
M14 完整指标
运维排障文档
```

**验收**

```text
临床安全投影不产生安全信号
临床安全既有链路职责不变
长期记忆只输出候选
未实现 adapter 显式 projection_adapter_not_implemented
所有生产失败可回滚或可止血
```

## 6. 持久化与数据边界

### 6.1 概念对象

生产实现至少需要以下持久化概念：

```text
semantic_turn_snapshots
semantic_plan_runs
semantic_task_artifacts
semantic_review_artifacts
semantic_repair_patches
semantic_claim_graphs
semantic_run_traces
```

表名可由数据库实现调整，但职责不得合并缺失。

### 6.2 存储要求

```text
TurnSnapshot 保存原文和 digest
PlanRun 保存 Plan IR 与校验状态
TaskArtifact append-only
ReviewArtifact append-only
RepairPatch append-only
ClaimGraph 保存版本和 terminal state
Trace 可按 turn / plan / task / artifact 检索
```

### 6.3 数据迁移原则

1. 先契约，后 Alembic migration。
2. 不为实验报告创建生产表。
3. 不将实验 fixture 导入生产库。
4. 生产 artifact 不得原地 update。
5. 修复必须新增版本或新增记录。
6. stale 关系必须可查询。

### 6.4 幂等与并发

生产实现必须声明：

```text
turn / plan / task 幂等键
artifact base version 冲突处理
并行 patch 冲突处理
重复提交处理
超时后的终态处理
```

不得用“最后一次写入覆盖”处理并行冲突。

## 7. 配置与生产切换

### 7.1 最小配置面

配置项应显式表达：

```text
功能是否启用
模型调用配置引用
上下文预算
并发上限
全局 deadline
repair budget
review 策略
投影 adapter 启用状态
trace 采样或保留策略
```

不得引入隐藏语义规则开关。

### 7.2 切换原则

```text
默认关闭
显式开启
配置变更可审计
开启前检查 SkillCatalog
开启后记录运行版本
失败不自动回退
```

### 7.3 回滚原则

允许：

```text
部署级回滚
配置级关闭
保留失败 trace
保留已写入 artifact
```

禁止：

```text
请求内静默回退
删除失败记录
将失败结果改写为空集合
绕过 verifier 继续投影
```

## 8. 测试与工程质量门禁

### 8.1 测试分层

```text
契约测试
单元测试
负例测试
模块集成测试
端到端工程测试
领域隔离测试
生产接入测试
```

默认 CI 应优先使用确定性测试替身。真实模型调用测试作为显式集成测试，
不作为隐式 fallback。

### 8.2 契约测试

覆盖：

```text
SkillSpec
PlanIR
TurnSnapshot
TaskArtifact
ReviewArtifact
RepairPatchProposal
ClaimGraph
ProjectionContract
```

### 8.3 负例测试

至少覆盖：

```text
未知 skill
依赖环
context policy 越权
extra field
forbidden field
field ownership conflict
evidence outside scope
claim binding invalid
context digest mismatch
review 输出 corrected final value
repair patch 越权
base version 冲突
repair budget 超限
graph 引用缺失
adapter 越权
失败转空 facts
```

### 8.4 模块集成测试

覆盖相邻模块：

```text
SkillCatalog → PlanValidator
PlanIR → Scheduler
Scheduler → Gateway → Generator → Verifier
Verifier → Review → Repair Planner
Repair → Patch → Artifact Version
Artifact → ClaimGraph
ClaimGraph → ProjectionAdapter
```

### 8.5 端到端工程测试

覆盖：

```text
TurnSnapshot
→ Plan
→ Scheduler
→ Generator
→ Verifier
→ Review
→ Repair
→ Artifact
→ Graph
→ Adapter
```

至少包含：

```text
多事实低风险输入
shared scope 输入
normal 状态输入
denied 状态输入
uncertain 输入
answer_now 输入
多轮指代输入
用户纠正输入
可修复错误
不可修复错误
上下文预算超限
依赖失败
```

### 8.6 领域隔离测试

生产接入前后均应断言：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
```

该断言只限制语义协作 DAG 内部；问诊和临床安全领域自身在后续消费投影时，
仍由其领域链路负责。

## 9. PR 切分建议

推荐按模块或阶段拆分 PR：

```text
PR 01 生产包边界与基础契约
PR 02 SkillCatalog 与契约测试
PR 03 TurnSnapshot 与 context policy
PR 04 PlanIR / PlanValidator
PR 05 DAGScheduler
PR 06 StructuredLLMGateway
PR 07 Turn Intent + verifier
PR 08 Claim Inventory + verifier
PR 09 Statement Semantics + verifier
PR 10 Participant / Temporal / Measurement
PR 11 Canonical descriptor 与 candidate selector
PR 12 Review SKILL
PR 13 Repair planner
PR 14 Repair patch 与 artifact version
PR 15 Claim graph
PR 16 问诊投影 adapter
PR 17 生产接入与配置
PR 18 临床安全 / 长期记忆投影与硬化
```

每个 PR 必须包含：

```text
契约
实现
负例测试
trace 字段
文档同步
```

不得提交只有 prompt 修改、没有 schema / verifier / 测试的生成任务 PR。

## 10. Code review 检查清单

Review 生产代码时应检查：

```text
是否读取架构基线
是否新增未注册 SKILL
是否产生字段所有权冲突
是否引入 forbidden output
是否静默清洗非法字段
是否把失败转为空结果
是否绕过 context policy
是否读取下游领域状态
是否让 reviewer 直接修改 artifact
是否让 repair 输出自由 JSON
是否允许任意 JSON Patch
是否缺少 base version 校验
是否缺少 stale 标记
是否让 graph 消费 proposal
是否让 adapter 写领域状态
是否在 preprocessing 做医学判断
是否引入关键词 / 正则医学规则
是否缺少显式终态
是否缺少 trace
```

任一项不满足应要求修改，不得以“先跑通再治理”合并。

## 11. 完成判定

生产迁移完成时，应满足：

```text
1. SkillCatalog 启动校验有效
2. Plan IR 无法引入未注册任务
3. 所有生成输出 strict schema
4. forbidden field 一律 blocked
5. normal / denied / uncertain / unknown 分离
6. shared scope 可拆分
7. evidence 越界 blocked
8. Review 按域正交且不直接修改 artifact
9. Repair 只能输出白名单 typed patch
10. patch base version 校验有效
11. repair budget 和 repair_exhausted 有效
12. artifact 版本与 lineage 可追溯
13. 上游修复触发下游 stale
14. claim graph 只消费 verified artifact
15. 领域只通过 adapter 消费
16. preprocessing 不写问诊状态
17. preprocessing 不触发临床安全 evaluator / OPA / required_context
18. 失败不会变成空 facts
19. 生产失败可显式回滚或止血
20. trace / metrics 可定位任一任务
```

## 12. 明确不做事项

本实施计划不做：

```text
医学规则设计
疾病 / 症状词表
临床安全策略调整
问诊状态合并实现
长期记忆写入实现
执行框架选型
实验矩阵设计
held-out 读取
DSPy 接入
通用自由 multi-agent 对话框架
```

如后续工程需要上述内容，必须由对应领域文档和架构基线显式立项。

## 13. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [agent-input-preprocessing-domain-extraction-migration-plan.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-domain-extraction-migration-plan.md)
3. [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)
4. [input-preprocessing-v14-onepass-governance-convergence-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-change-summary.md)
5. [consultation-semantic-extraction-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-semantic-extraction-change-summary.md)
6. [consultation-state-answerability-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-state-answerability-change-summary.md)
7. [clinical-safety-semantic-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-semantic-change-summary.md)
