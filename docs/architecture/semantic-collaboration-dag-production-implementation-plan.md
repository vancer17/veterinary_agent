<!--
=============================================================================
文件: semantic-collaboration-dag-production-implementation-plan.md
作用: 将受限语义协作 DAG 生产架构拆成模块级工程交付顺序、依赖边界、
      数据边界、测试门禁、生产接入顺序和回滚边界。
范围: 适用于生产代码迁移、契约实现、SKILL 目录、Plan IR、任务调度、
      生成 / 审查 / 修复、artifact 版本、claim graph、领域投影和观测治理。
说明: 本文不改变生产架构基线，固定使用架构基线声明的 Temporal durable
      execution 边界，不包含实验计划，不展开类、函数、提示词全文或测试替身实现。
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
> **当前边界修订**：M06 / M08 按
> [semantic-collaboration-dag-m06-production-boundary-revision.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-production-boundary-revision.md)
> 收敛为自然语言 proposition 生成与固定布尔审查矩阵。
>
> **适用范围**：生产工程实现顺序、模块职责、模块依赖、交付物、验收标准、
> 持久化边界、测试门禁、生产接入与回滚
>
> **不适用范围**：医学规则设计、临床安全策略、OPA 细节、问诊状态实现、
> 长期记忆写入策略、第二执行框架选型、实验矩阵和模型质量实验

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
| M03 | Root Plan IR 与校验 | PlanIR、PlanValidator、确定性编译器 | Phase 2 |
| M04 | Temporal-first DAG 调度 | 确定性 frontier、workflow / activity、终态投影 | Phase 2 |
| M05 | 结构化 LLM Gateway | SKILL 调用、schema、usage、失败状态 | Phase 3 |
| M06 | 生成 SKILL | prompt renderer、Turn Intent、Claim Proposition Inventory | Phase 3 |
| M07 | Deterministic Verifier | strict schema、所有权、claim 数量与身份校验 | Phase 3 |
| M08 | Review SKILL | Coverage Review、Faithfulness Review、固定布尔矩阵 | Phase 3 |
| M09 | Repair Planner | review dimension mapping、repair budget | Phase 4 |
| M10 | Repair 与 Patch | typed patch、patch 校验与应用 | Phase 4 |
| M11 | Artifact Store | append-only artifact、版本、lineage、stale | Phase 1 契约 / Phase 4 实现 |
| M12 | Claim Graph | proposition graph assembly、一致性门禁 | Phase 5 |
| M13 | 领域投影 Adapter | 问诊、临床安全、长期记忆投影契约 | Phase 5 / 7 |
| M14 | 可观测性 | trace、metrics、failure attribution | 全阶段 |
| M15 | 生产接入 | orchestrator 边界、配置、回滚 | Phase 6 |

模块依赖关系：

```text
M01 SkillCatalog
   ↓
M03 PlanIR ← M02 TurnSnapshot
   ↓
M04 Temporal Workflow
   ↓
M05 Gateway → M06 Generator → M07 Verifier → M08 Review
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

### M03：Root Plan IR 与 Plan Validator

**目标**

由确定性编译层根据生产 PlanPolicy 直接生成可验证初始 Root Plan IR，不调用
任务规划 LLM，不预估 claim 数量，不预分配 claim envelope。

**范围**

```text
PlanIR
PlanPolicySpec
DeterministicPlanCompiler
PlanValidator
Plan envelope 契约
```

**上游依赖**

```text
M01 SkillCatalog
M02 TurnSnapshot
```

**交付物**

1. Plan IR schema。
2. 生产 PlanPolicy 与并行根任务规则。
3. turn_root envelope 契约。
4. task / dependency / envelope 契约。
5. skill 与 version 校验。
6. 依赖存在、策略一致与无环校验。
7. context policy 与 TurnSnapshot 投影预算校验。
8. expected output schema 校验。
9. turn / snapshot / catalog / policy digest 绑定校验。
10. canonical plan_id 校验。
11. 初始计划预分配 claim envelope 的阻断状态。

**验收标准**

```text
未知 skill blocked
非法 version blocked
task_id 重复 blocked
依赖缺失 blocked
依赖环 blocked
envelope 非法 blocked
初始计划包含 claim envelope blocked
context policy 越权 blocked
expected output schema 不一致 blocked
PlanPolicy 依赖不一致 blocked
snapshot / catalog / policy digest 不一致 blocked
plan_id 与 canonical 内容不一致 blocked
计划或上下文预算超限 blocked
不得调用规划 LLM
不得输出或消费 claim 数量预估值
根任务不得互相依赖，应可并行调度
```

### M04：DAG 调度器

**目标**

按 Plan IR 执行任务，并保证每个任务进入显式终态。

**范围**

```text
Temporal SemanticDAGWorkflow
确定性 DAG frontier
任务 activity 边界
语义有界重试策略投影
依赖失败传播
terminal state 汇总
PostgreSQL 只读投影
```

**上游依赖**

```text
M01 SkillCatalog
M03 PlanIR
```

**交付物**

1. Temporal workflow 与任务 activity。
2. 无依赖任务并发调度。
3. Temporal activity / workflow 超时。
4. 依赖失败传播。
5. cancellation / deadline 传播。
6. 任务终态投影记录。
7. `clarification_required` 终态与 clarification gap artifact 引用投影契约。
8. 任务队列、语义 / 基础设施重试、worker 租约与恢复由 Temporal 负责。

**验收标准**

```text
依赖顺序正确
独立任务可并行
依赖失败输出 dependency_failed
超时输出 timeout
来源绑定缺失输出 clarification_required，而不是 blocked 或 verified
任务无悬空状态
调度器不修改 TurnSnapshot
数据库不保存 worker_id / lease / ready / running / attempt 调度状态
```

**禁止事项**

```text
不在调度器中解释医学语义
不在调度器中动态发明任务
不把异常任务静默跳过
不实现数据库任务队列、租约或自研 worker 恢复协议
```

### M05：结构化 LLM Gateway

> **实施状态**：M05 生产契约与实现已完成；尚未接入任务执行器。实现边界、
> 有意 TODO 与验证状态见
> [semantic-collaboration-dag-m05-structured-llm-gateway-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m05-structured-llm-gateway-change-summary.md)。

**目标**

为所有 SKILL 提供统一、可审计的结构化模型调用边界。

**范围**

```text
StructuredLLMGateway
SkillPromptProjection 身份与 digest 校验
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
2. SkillPromptProjection 到模型消息的确定性投影与哈希。
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
不在 Gateway 内生成 SKILL 语义提示词
```

SKILL 语义 prompt renderer 属于 M06 生成 SKILL 交付物。M05 只接收、校验、
序列化和哈希上游传入的 `SkillPromptProjection`，不得解析 `SKILL.md` 正文、
按症状词发明提示词或读取未授权上下文。

### M06：生成 SKILL

> **实施状态**：M06 生产契约与实现已完成；待 M08 Review、M11 Artifact Store、
> `SemanticTaskExecutor` 组合与真实外部联调后关闭。实现边界、有意 TODO 与
> 验证状态见
> [semantic-collaboration-dag-m06-generation-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-generation-skill-change-summary.md)。

**目标**

实现当前生产面所需的正交窄域自然语言 proposition 生成任务。

**范围**

```text
SemanticSkillDocument / SKILL.md
RestrictedSkillTemplate
SkillPromptRenderer
RendererRegistry
GenerationModelPolicy
StructuredGenerationSkillRunner
TurnIntentGenerator
ClaimPropositionInventoryGenerator
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
标准化 SKILL.md / 受限模板
→ Skill prompt renderer / renderer registry
→ Turn Intent
→ Claim Proposition Inventory
→ Generation Runner 接入 M05
```

**稳定输出**

Turn Intent 输出 fixed-field boolean：

```json
{
  "answer_now": true,
  "wants_triage": false,
  "correction": false,
  "clarification_request": false,
  "fact_statement_present": true,
  "question_present": true,
  "report_context_present": false
}
```

Claim Proposition Inventory 输出自然语言 proposition：

```json
{
  "claims": [
    "英短前天开始更换新猫粮",
    "英短这两天大便偏软",
    "英短精神状态良好",
    "英短进食正常",
    "英短饮水正常",
    "英短没有呕吐",
    "英短大便没有血"
  ]
}
```

Claim proposition 必须使用对象层主语义：当前宠物、宠物状态、宠物行为或宠物
相关事件。不得把 `用户报告`、`用户认为`、`用户询问` 作为 proposition 主语义；
来源与观察方式由系统 metadata 和审查状态承载。

**验收标准**

```text
每个生成 SKILL 携带标准化 SKILL.md 和版本化 prompt renderer
SKILL.md 文件头部元数据仅确定性代码可见
SKILL.md 身份、schema、context、verifier 与 SkillSpec 闭合
model-visible sections 与标准章节白名单闭合
受限 Jinja 只允许顶层白名单字符串变量
模板 AST 不含条件、循环、过滤器、属性访问或表达式
prompt projection 与 SkillSpec / envelope / snapshot digest 身份闭合
prompt user message 使用结构化 tag 或极浅文本，不使用深层 JSON
prompt 不暴露 task_id / run_id / digest / 完整 schema / front matter
prompt 不提示 estimated claim envelope count
Turn Intent fixed-field 无重复数组
Claim Inventory 输出自包含 proposition，不输出主题词
shared scope 可拆分
normal / denied / unobserved / uncertain / corrected 在自然语言中保留
proposition 主语义是宠物或宠物相关事件，不是用户报告行为
模型不输出 evidence、reason、confidence 或自证字段
模型不输出 claim_id、target、unit_type、shared_parent
所有输出不包含 forbidden field
tag delimiter collision 可测试
```

**禁止事项**

```text
不输出 entity_id
不输出 canonical_id
不输出 assertion_state / certainty / scope enum
不输出 participant / temporal / measurement / canonical 结构化字段
不输出诊断、临床风险或安全动作
不做 evidence 字面锚定
不新增其他权威域字段
```

**Deferred lane**

以下任务不进入当前 M06 生产实现：

```text
ClaimStatementSemanticsGenerator
ParticipantPhraseGenerator
TemporalPhraseGenerator
MeasurementPhraseGenerator
CanonicalDescriptorGenerator
```

后续启用必须同时具备：

```text
明确下游消费者
领域投影或 resolver / parser 契约
strict schema
verifier
负例测试
成本与延迟预算
```

### M07：Deterministic Verifier

> **实施状态**：M06 配套的最小结构 verifier 已实现；完整 M07 所有权、
> target envelope 与跨字段治理仍待后续硬化。

**目标**

在结构层和任务身份层验证生成结果，不做医学语义判断。

**范围**

```text
SchemaVerifier
OwnershipVerifier
ClaimCountVerifier
ClaimTextShapeVerifier
ContextDigestVerifier
CrossFieldVerifier
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
4. boolean / string 类型校验。
5. claim 字符串非空、长度和换行校验。
6. claim 数量上限与重复检测。
7. claim 数量由 `claims.length` 确定性派生。
8. context digest 与任务身份校验。
9. 显式跨字段冲突状态。

**验收标准**

```text
forbidden_field_present blocked
field_ownership_violation blocked
schema_invalid blocked
duplicate_claim blocked
empty_claim_proposition blocked
context_digest_mismatch blocked
semantic_conflict 显式保留
```

**禁止事项**

```text
不删除 forbidden field 后放行
不验证或生成 evidence phrase
不做关键词医学判断
不把 proposal 标记 verified
不截断或合并 claim 以匹配 Plan envelope
```

### M08：Review SKILL

**当前实现状态**

Coverage Review、Faithfulness Review、固定布尔矩阵、M05 Gateway 接入、review verifier、
deterministic outcome derivation、clarification gap proposal 和 M11 TODO 空壳已实现。

当前仍未完成：

```text
M04 SemanticTaskExecutor 组合
M09 Repair Planner
M10 typed patch / applier
M11 Review Artifact Store
真实 LiteLLM 冒烟
真实 Temporal workflow 联调
```

因此 M08 当前只能返回可审计 review bundle，不能返回权威 artifact reference，也不能把
`semantic_review_supported` 伪装成 `verified`。

实现边界与后续对接契约见
[semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)。

**目标**

独立发现 Claim Inventory 的覆盖问题和单条 proposition 的语义漂移问题。

**范围**

```text
ReviewRunner
CoverageReviewer
FaithfulnessReviewer
ReviewOutcomeDeriver
ReviewArtifact schema
Review output verifier
Review terminal state
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
3. Coverage Review 固定布尔矩阵。
4. Faithfulness Review 固定中文布尔矩阵。
5. deterministic outcome derivation。
6. review 输出 verifier。
7. review terminal state。
8. `clarification_required` 与 `repair_then_clarification_required` 派生契约。
9. 人工审查过渡状态契约。

**Coverage Review 输出**

```json
{
  "coverage_matrix": {
    "存在漏抽显式事实": false,
    "存在多事实合并": false,
    "存在重复claim": false,
    "存在原文不支持的claim": false,
    "存在非自包含proposition": false,
    "存在shared scope拆分错误": false,
    "未分类覆盖问题": false
  },
  "missing_claim_candidates": []
}
```

可附带 bounded `missing_claim_candidates` 作为 repair hint，不得直接追加 claim。

**Faithfulness Review 输出**

```json
{
  "faithfulness_matrix": {
    "主体或指代范围改变": false,
    "否定方向改变": false,
    "否定范围改变": false,
    "正常状态误写为否认": false,
    "事实类型改变": false,
    "时间范围改变": false,
    "频率或数量改变": false,
    "程度或强度改变": false,
    "确定性改变": false,
    "因果关系改变": false,
    "医学推断或建议添加": false,
    "命题不自包含": false,
    "指代对象不明": false,
    "时间基准不明": false,
    "否定范围不明": false,
    "比较基线不明": false,
    "未分类语义改变": false
  }
}
```

**验收标准**

```text
Coverage Review 为 turn 级任务
Faithfulness Review 一次只审查一个 claim
review 与 generator 使用同一 context_digest
review 不直接修改 artifact
review 不输出 verdict / reason / confidence / corrected value
review 输出 forbidden field blocked
review_failed 不使原任务 verified
disagreement 显式保留
全部 false 派生 semantic_review_supported
来源绑定缺失维度派生 clarification_required
模型漂移 / 模型越权维度派生 repair_required
模型漂移与来源绑定缺失同时存在派生 repair_then_clarification_required
医学推断或建议添加派生删除式局部修复
未分类维度派生 human_review_required
可修复维度 true 数量超过上限派生 human_review_required
claims=[] 区分 no_explicit_fact 与 suspicious_empty
skipped Faithfulness Review 不伪装 supported
Review schema / claim 身份失败显式 review_failed
```

**当前实现顺序结论**

```text
Faithfulness Review 已实现
→ Coverage Review 已实现
→ deterministic outcome derivation 已实现
→ repair / clarification router 待 M09
→ 人工审查 artifact 状态待 M11
```

### M09：Repair Planner

**目标**

根据 Faithfulness / Coverage 布尔矩阵中的具体 true 维度创建受限修复任务，并控制修复预算。

**范围**

```text
ReviewDimensionCatalog
RepairMapping
RepairPlanner
RepairBudget
ClarificationGapRouter
HumanReviewRouter
```

**上游依赖**

```text
M01 SkillCatalog
M08 ReviewArtifact
```

**交付物**

1. 固定 review dimension 目录。
2. review dimension 到 Repair SKILL 的白名单映射。
3. repair depth 控制。
4. per-proposition / per-turn budget。
5. repair dimension 到删除式修复 / 措辞修复的白名单映射。
6. `clarification_required` gap 路由。
7. `repair_unavailable` 与 `human_review_required` 状态。

**验收标准**

```text
未知 review dimension 不动态匹配 repair
来源绑定缺失维度输出 clarification_required，不进入 repair
医学推断或建议添加进入删除式局部 repair
未分类维度输出 human_review_required
repair_depth 不超过 1
不允许 repair of repair
同一 proposition 最多一次修复
可修复 true 维度超过上限输出 human_review_required
budget 超限输出 repair_exhausted
```

### M10：Repair SKILL 与 Patch

**目标**

用局部 typed patch 修复可恢复的自然语言 proposition 漂移，并删除模型引入的越权生成内容。

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
2. review dimension 到 patch operation 的白名单。
3. `remove_external_medical_inference` 删除式 patch 契约。
4. base version 契约。
5. patch verifier。
6. deterministic applier。
7. repair lineage。

**验收标准**

```text
patch path 越权 blocked
未申报 review dimension 的 patch blocked
医学推断 / 风险 / 建议 patch 只能删除或还原用户表述，不得生成新医学结论
base_version 冲突 blocked
forbidden field 不可修复
schema 根本非法输出不可修复
patch 应用后 artifact version + 1
repair lineage 可追溯
```

**禁止事项**

```text
不自由重写完整 artifact
不整轮重写 claim list
不补造无证据事实
不自动消解指代、时间基准、否定范围或比较基线
不判断医学推断是否正确
不生成新的诊断、风险、就医或治疗建议
```

### M11：Artifact Store 与版本

**目标**

以 append-only 方式维护任务结果、版本、修复谱系、证据门禁状态和 stale 关系。

**范围**

```text
TaskArtifact
ArtifactVersion
EvidenceBindingStatus
HumanReviewRecord
ClarificationGapRecord
ArtifactStore
RepairLineage
StaleMarker
TerminalStateStore
```

**实现顺序**

Phase 1 先交付契约；Phase 4 与 repair 一起交付生产持久化。

**交付物**

1. artifact schema。
2. artifact version 状态。
3. append-only 写入接口。
4. repair lineage 存储。
5. evidence binding / clarification gap / human review 状态存储。
6. stale dependency 存储。
7. terminal state 查询。

**验收标准**

```text
artifact 不可原地覆盖
版本可追溯
失败结果不会被成功结果覆盖
repair lineage 完整
semantic_review_supported 不被写成 verified
evidence_binding_pending / clarification_required / human_review_required 可查询
上游结构变化触发下游 stale
历史 artifact 可审计
```

### M12：Claim Graph 与一致性门禁

**目标**

将已通过语义审查和证据门禁的 proposition artifact，以及显式 clarification gap artifact，组装为可投影 proposition graph。

**范围**

```text
ClaimGraphBuilder
IntentClaimConsistencyGate
ReviewStatusMerger
ClarificationGapMerger
EvidenceGateMerger
GraphConsistencyGate
GraphArtifact
```

**上游依赖**

```text
M06～M11
```

**交付物**

1. proposition graph schema。
2. turn intent 与 claim proposition 合并。
3. coverage / faithfulness / repair 状态合并。
4. clarification gap 状态合并。
5. evidence binding 状态合并。
6. ID 引用校验。
7. 图级一致性门禁。
8. graph terminal state。

**验收标准**

```text
只消费完整门禁后的 verified / repair_verified artifact
不消费 proposal
引用缺失 blocked
claim proposition 重复 blocked
证据门禁或 clarification gap 未按契约处理时不得 graph_verified
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
输入是 reviewed 自包含自然语言 proposition 或显式 clarification gap
reported normal 不映射为 denied
denied 保留否定语义
unobserved 与绝对否定分离
unknown 不代表追问已完成
answer_now 独立传递
clarification gap 交给问诊回答充分性 / followup 策略
clarification_required 不等于强制追问
adapter 不做医学风险判断
不得用关键词 / 正则代替领域投影契约
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
ClarificationGapMetrics
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
review_outcome
review_matrix_digest
clarification_gap_status
evidence_binding_status
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
clarification required / repair_then_clarification 可观测
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
M04 Temporal workflow / activity / projection
依赖与超时治理
```

**验收**

```text
非法 plan 全部 blocked
依赖环 blocked
独立任务并行执行
依赖失败传播
Temporal 负责队列、retry、timeout 与 worker 恢复
数据库不保存 worker / lease / ready / running / attempt 调度状态
任务终态完整
```

**工程约束**

本阶段可使用测试替身验证调度，不要求接生产 orchestrator。

### Phase 3：核心生成与验证

**目标**

交付最小可闭环的 proposal 生成、结构验证与语义审查路径。

**交付**

```text
M05 StructuredLLMGateway
M06 Turn Intent
M06 Claim Proposition Inventory
M07 对应 deterministic verifier
M08 Faithfulness Review
M08 Coverage Review
M08 deterministic outcome derivation
M14 task trace
```

**当前实现状态**

```text
M05 StructuredLLMGateway: 已实现
M06 Turn Intent / Claim Inventory: 已实现
M07 最小结构 verifier: 已实现
M08 Coverage / Faithfulness Review: 已实现
M08 deterministic outcome derivation: 已实现
M14 完整 task trace / metrics: 未完成
真实 LiteLLM / Temporal 联调: 未执行
```

本阶段尚不能因 M08 已实现而关闭；关闭前还需补齐 M14 观测闭环，并通过显式外部服务
集成验证。当前全部链路验证均使用进程内测试替身。

**验收**

```text
strict schema 全覆盖
forbidden field blocked
intent fixed-field 输出
claim proposition 自包含
shared scope 可拆分
normal / denied / unobserved / uncertain 语义在自然语言 proposition 中保留
claim 数量超过 schema 上限 blocked
coverage 漏抽和合并问题可发现
faithfulness 语义漂移维度可定位
review 失败不等于原任务通过
```

**禁止接入**

```text
问诊状态
临床安全
长期记忆
```

### Phase 4：修复、版本与证据门禁

**目标**

交付局部修复、artifact 版本、证据门禁状态和人工审查过渡机制。

**交付**

```text
M09 Repair Planner
M10 Repair SKILL / typed patch
M11 ArtifactStore 生产实现
M08/M09 clarification_required 与 repair_then_clarification_required 状态
M11 evidence_binding_pending / human_review_required 状态
M11 stale marker
M14 repair metrics
```

**验收**

```text
repair 只针对具体 true review dimension
来源绑定缺失维度输出 clarification_required，不被 repair 猜测
医学推断 / 风险 / 建议添加可被删除式局部修复
未分类维度进入 human_review_required
repair 不自由重写完整 artifact
patch base version 校验有效
repair budget 有效
repair_exhausted 有效
semantic_review_supported 不等于 verified
人工审查显式记录 review_mode=human
上游修复触发下游 stale
```

### Phase 5：Claim Graph 与问诊投影

**目标**

形成可被问诊领域消费的 verified proposition graph。

**交付**

```text
M12 ClaimGraphBuilder
M12 GraphConsistencyGate
M13 ConsultationProjectionAdapter
M14 graph metrics
```

**验收**

```text
graph 只消费完整门禁后的 verified artifact
局部 gap 不被抹除
图级冲突显式保留
问诊 adapter 不把 reported normal 映射为 denied
unknown 不代表追问已完成
adapter 不写问诊状态
adapter 不做医学判断
```

### Phase 6：生产主路径接入

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

### Phase 7：其余领域投影与硬化

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
claim 数量超过 schema 上限
空 claim proposition
重复 claim proposition
主题词 claim
context digest mismatch
review 布尔字段缺失或非 boolean
review 输出 verdict / reason / confidence
review 输出 corrected final value
来源绑定缺失维度进入自动修复
医学推断添加未被删除式修复而直接放行
clarification_required 被当成 verified
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
unobserved 输入
uncertain 输入
可能因果归因输入
answer_now 输入
多轮指代输入
指代不明进入 clarification gap
医学推断添加进入删除式修复
用户纠正输入
claim 数量超过 schema 上限输入
空集合但原文事实丰富输入
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
PR 02 SkillCatalog 自然语言 proposition 契约收敛与契约测试
PR 03 TurnSnapshot 与 context policy
PR 04 PlanIR / PlanValidator
PR 05 Temporal-first DAG workflow
PR 06 StructuredLLMGateway
PR 07 Turn Intent + verifier
PR 08 Claim Proposition Inventory + verifier
PR 09 Prompt renderer 与 generation runner
PR 10 Faithfulness Review 固定布尔矩阵
PR 11 Coverage Review 与 missing hint 治理
PR 12 deterministic review outcome derivation
PR 13 Repair planner
PR 14 Repair patch、artifact version 与证据门禁状态
PR 15 proposition claim graph
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
是否让生成器输出 evidence / reason / confidence 自证字段
是否让 claim inventory 输出主题词而非 proposition
是否让 review 输出 verdict / corrected value
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
5. normal / denied / unobserved / uncertain / corrected 语义在 proposition 中保留
6. shared scope 可拆分
7. 生成器不做 evidence 自证
8. Coverage Review 可发现漏抽、合并和非自包含 proposition
9. Faithfulness Review 使用固定布尔矩阵定位语义漂移维度
10. Review 按域正交且不直接修改 artifact
11. 来源绑定缺失输出 clarification_required，不被 repair 补造
12. Repair 只能针对具体 true 维度输出白名单 typed patch
13. 医学推断 / 风险 / 建议添加只能被删除或还原为用户表述
14. patch base version 校验有效
15. repair budget 和 repair_exhausted 有效
16. artifact 版本、lineage、clarification gap 与证据门禁状态可追溯
17. 上游修复触发下游 stale
18. claim graph 只消费完整门禁后的 verified artifact
19. 领域只通过 adapter 消费
20. preprocessing 不写问诊状态
21. preprocessing 不触发临床安全 evaluator / OPA / required_context
22. 失败不会变成空 facts
23. 生产失败可显式回滚或止血
24. trace / metrics 可定位任一任务
```

## 12. 明确不做事项

本实施计划不做：

```text
医学规则设计
疾病 / 症状词表
临床安全策略调整
问诊状态合并实现
长期记忆写入实现
第二执行框架选型
实验矩阵设计
held-out 读取
DSPy 接入
通用自由 multi-agent 对话框架
```

如后续工程需要上述内容，必须由对应领域文档和架构基线显式立项。

## 13. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-m06-production-boundary-revision.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-production-boundary-revision.md)
3. [agent-input-preprocessing-domain-extraction-migration-plan.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-domain-extraction-migration-plan.md)
4. [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)
5. [input-preprocessing-v14-onepass-governance-convergence-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-change-summary.md)
6. [consultation-semantic-extraction-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-semantic-extraction-change-summary.md)
7. [consultation-state-answerability-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-state-answerability-change-summary.md)
8. [clinical-safety-semantic-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-semantic-change-summary.md)
9. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)
10. [semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)
