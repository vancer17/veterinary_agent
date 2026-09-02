<!--
=============================================================================
文件: agent-input-preprocessing-shadow-experiment-architecture-guidance.md
作用: 将 V2～V13 输入前置预处理实验结论收敛为 V14 one-pass governance
      convergence 阶段的架构原则、实验边界、防偏移检查清单和准入条件。
范围: 覆盖 fixed-field turn intent、approximate one-pass claim generation、
      claim-local fuzzy alignment、语义 proposal verifier、TurnContext
      participant resolver、constrained canonical selector、minimal lane 成本、
      REP 和 report-only shadow。
说明: 本文只保留当前仍有效的结论与边界；历史实验细节、样本、报告与复现命令
      由对应 change summary 维护。
维护: 当 V14 契约、prompt skill、alignment 策略、语义 proposal 状态、
      canonical 边界、实验矩阵或准入条件调整时同步更新本文。
=============================================================================
-->

# Agent 输入前置预处理 Shadow 实验架构指导

> **文档状态**：V2～V13 已完成；当前进入 V14 one-pass governance convergence；未达到生产消费准入
>
> **文档定位**：后续快速验证与 shadow 实验的架构决策基线
>
> **适用范围**：TurnContext、fixed-field turn intent、approximate one-pass claim generation、claim-local fuzzy alignment、语义 proposal 治理、TurnContext 实体解析、constrained canonical linking、post-hoc claim graph、minimal lane 成本、NEG / ASYNC / REP 和 report-only 边界
>
> **不适用范围**：生产 prompt 设计、具体训练代码、临床安全医学资产、问诊状态存储实现、RAG 知识证据、自然语言回复生成、长期记忆写入和前端展示

## 1. 当前范式判断

### 1.1 已被 V2～V7 证明不可靠的路径

让 LLM 同时完成：

```text
语义理解；
精确字符切片；
quote 复制；
复杂嵌套 schema 输出；
```
会导致：

```text
复制整句 evidence；
输出省略式伪 quote；
target / relation / temporal 字段错位；
statement type 漂移；
schema 崩溃；
```

结论：

> 不应要求 LLM 成为精确字符串切片器。

但证据链不应因此放弃。正确做法是：

```text
LLM 输出 approximate phrase；
确定性 aligner 在原文中生成 aligned quote 和 offset；
gate 决定是否接受。
```

### 1.2 已被 V8～V12 证明错配的路径

GLiNER 路线的核心问题不是缺少后处理，而是任务错配：

```text
GLiNER:
  span 内容与实体 / 类别 label 的语义相似度

当前任务:
  span / phrase 在事件、论元结构、断言范围和话语结构中的功能
```

典型数据：

```text
V8 GLiNER live:
  precision = 1.0
  recall = 0.075
  label accuracy = 0

V10 calibrated pool:
  exact recall = 0.6585
  precision 约 0.124
  role coverage 约 0.512

V12 support-first graph:
  gold_in_view = 0.5610
  seed recall = 0.34
  seed precision = 0.0448
  conflict pruning 均误删必要 gold
```

因此以下不再是主路径：

```text
GLiNER candidate pool；
support anchor 前置；
role-specific candidate menu 前置；
structural seed 前置；
SpanGraph 前置；
NMS / conflict pruning 主路径；
LLM span_id binding 主路径。
```

这些路线保留为历史对照，不再继续加厚。

### 1.3 V13 已证明的新主线

V13 approximate one-pass 真实模型结果：

```text
run 1 claim precision / recall = 0.8125 / 0.8125
run 2 claim precision / recall = 0.5625 / 0.5625
claim segmentation = 1.0 / 1.0
```

即使较差 run，也明显高于 V12：

```text
seed precision / recall = 0.0448 / 0.34
```

因此当前主线为：

```text
LLM-first one-pass approximate claim generation
+ deterministic source-grounded alignment
+ field-level governance
+ targeted enrichment
```

V13 不是稳定 winner，V14 只收敛其暴露的缺陷，不引入新的候选生成范式。

## 2. V14 目标架构

```text
Raw Input + TurnContext
→ Fixed-field Turn Intent Analyzer
→ One-pass LLM Flat Claim Generation
   └─ 内嵌 claim inventory / shared scope 约束
→ Claim-local Deterministic Fuzzy Alignment
→ Field-level Governance
→ Targeted Enrichment
   ├─ TurnContext participant resolver
   ├─ temporal parser verifier
   ├─ measurement parser verifier
   ├─ relation classifier
   └─ canonical constrained selector
→ Post-hoc Claim Graph
→ Gates / Review
→ Report-only Projection
→ Trace / Metrics / Minimal Lane Cost
```

核心原则：

```text
判断逻辑在 LLM；
原文始终保留；
工具做后验对齐、验证、归一化与治理；
claim graph 在治理后构建；
GLiNER 只做历史对照或旁路 cross-check；
minimal lane 成本必须单独测量。
```

## 3. Fixed-field Turn Intent

V13 的系统性错误是：

```text
expected acts = 4
output acts = 8
fact_statement 按 claim 重复输出
```

根因是把 turn-level input property 放进 acts 数组。

V14 使用固定字段：

```text
answer_now
wants_triage
correction
clarification_request

fact_statement_present
question_present
report_context_present
```

其中：

```text
answer_now / wants_triage / correction / clarification_request
  是 dialogue act

fact_statement_present / question_present / report_context_present
  是 input property
```

每个信号在一个 turn 中最多出现一次。

`fact_statement_present` 只回答当前 turn 是否包含事实陈述，不枚举所有事实；完整事实由 claim records 承担。

Claim generation 后需要确定性对账：

```text
fact_statement_present 与 governed_claim_count 是否一致
```

不一致时输出：

```text
intent_claim_mismatch
review_required
```

不得自动改写 intent。

## 4. One-pass Flat Claim Generation

### 4.1 为什么保留 one-pass

V13 数据：

```text
one-pass:
  0.8125 / 0.5625

two-stage:
  0.3125 / 0.375
```

One-pass 保留下列上下文：

```text
shared denial / normal scope；
claim 之间的继承关系；
完整谓词和论元结构；
用户话语功能；
```

Two-stage 会丢失这些信息，不再作为主路径。

### 4.2 Approximate phrase policy

LLM 输出：

```text
evidence_phrase
target_phrase
subject_phrase
action_agent_phrase
action_recipient_phrase
object_phrase
temporal_phrase
measurement_phrase
relation_phrase
canonical_descriptor
```

Phrase 是 semantic proposal：

```text
不要求逐字复制原文；
不要求连续；
不得引入原文没有的信息；
不得丢失否定、时间、主体、数量或关键关系；
能逐字复制时优先逐字复制。
```

Phrase 不是 quote。最终业务 evidence 只能来自：

```text
deterministic aligner
→ raw_text[start:end]
→ aligned_quote
```

### 4.3 Claim inventory

One-pass 输出内部可包含轻量 inventory：

```text
ordinal
evidence_phrase
claim_kind
```

Claims 必须逐项对应 inventory ordinal。

目的：

```text
稳定 claim 数量；
减少漏项；
稳定输出顺序；
避免字段生成过程中改变 claim skeleton。
```

仍必须保持同一次模型调用，不得退回 two-stage。

### 4.4 Shared scope

对：

```text
没有呕吐、干呕、反流
精神、食欲、饮水都正常
```

必须：

```text
一个 relation / state 作用于多个 target；
每个 target 一条 claim；
relation / polarity / modality 继承；
不得只输出第一个 target；
不得合并为一个粗 claim。
```

## 5. Claim-local Fuzzy Alignment

### 5.1 V13 缺陷

```text
field alignment rate = 0.8912～0.9056
false alignment rate = 0.1624～0.2085
```

Phrase 能落回原文，但可能落在：

```text
错误 occurrence；
错误字段；
错误 claim region；
相邻相似文本；
```

### 5.2 V14 对齐顺序

```text
1. 对齐 claim evidence phrase；
2. 生成 claim evidence envelope；
3. 字段 phrase 默认只在所属 envelope 内搜索；
4. 对 fuzzy result 执行 field-specific verifier；
5. ambiguous / not found 显式 review 或 blocked。
```

例外：

```text
省略主体；
previous question target；
TurnContext owner occurrence；
```

必须显式记录：

```text
alignment_scope = outside_parent
resolution_method
```

### 5.3 Alignment 状态

```text
exact
exact_normalized
fuzzy_verified
fuzzy_ambiguous
fuzzy_not_found
wrong_occurrence
outside_parent
cross_source_block
semantic_mismatch
negation_lost
temporal_lost
subject_lost
empty_phrase
```

### 5.4 接受策略

#### Tier 0：exact / exact_normalized

直接通过。

#### Tier 1：unique fuzzy aligned

需要：

```text
最佳候选唯一；
相似度高；
与第二候选 score margin 明显；
不跨 source block / claim envelope；
fuzzy verifier 未发现否定、时间、主体丢失；
```

可用于 report-only provisional evidence。

#### Tier 2：ambiguous

```text
review_required = true
```

不得静默选择。

#### Tier 3：not found

```text
blocked / review
```

不得补造 quote。

## 6. 语义 Proposal 治理

LLM 可生成语义 proposal，但必须带状态：

```text
verified
model_proposed
parser_conflict
unresolved
```

### 6.1 Temporal

LLM 可输出：

```text
temporal_relation
temporal_value
temporal_precision
```

Parser verifier 决定：

```text
verified
model_proposed
parser_conflict
unresolved
```

V13 parser conflict 为 0.25～0.3333，因此 proposal 不能直接采信。

### 6.2 Measurement

LLM 可输出 value / unit / relation proposal。

对模糊表达：

```text
一小把
```

不得伪造成精确 verified 数值。

### 6.3 Statement semantics

继续分离：

```text
user_statement_type
polarity
modality
epistemic_status
```

尤其区分：

```text
normal
no_change
denies
historical
hypothetical
```

## 7. Subject / Participant / Canonical

### 7.1 Subject / participant

LLM 只输出 phrase。

代码通过 TurnContext candidate-only resolver 解析：

```text
我 → user_001
它 → pet_001 或 ambiguous
医生 → medical_actor_001
新猫粮 → unresolved food mention
```

禁止：

```text
LLM 发明 entity_id；
resolved + null；
ambiguous 缺 candidates；
多宠物默认 current_pet；
```

### 7.2 Canonical

LLM 可输出：

```text
canonical_descriptor
```

不能输出最终 canonical_id。

V13 targeted data：

```text
descriptor recall = 1.0
dual query recall = 1.0
false confirmation = 0.5
```

因此 V14 使用：

```text
target_phrase + canonical_descriptor
→ candidate retriever
→ candidates[]
→ constrained selector
→ selected_candidate_id
→ code resolves canonical_id
```

无候选：

```text
not_found
review_required
new_concept_request
```

## 8. Post-hoc Claim Graph

Claim graph 在以下治理完成后构建：

```text
evidence alignment；
subject resolution；
semantic proposal governance；
canonical linking；
```

节点：

```text
UserClaim
AlignedEvidence
Subject
Participant
TemporalProposal
MeasurementProposal
CanonicalMapping
DiscourseAct
```

用途：

```text
field lineage；
冲突解释；
review routing；
claim state governance；
投影准备。
```

Claim graph 不作为 LLM 前置候选笼子。

## 9. Minimal lane 与成本

V13 的 p95 132～149s 是完整实验矩阵耗时，不是最小候选路径耗时。

V14 minimal lane 默认只包含：

```text
1. Turn Intent Analyzer
2. One-pass claim generation
```

可选 targeted verifier 仅在以下情况触发：

```text
fuzzy conflict；
participant ambiguity；
canonical ambiguity；
semantic conflict；
```

必须记录：

```text
model_call_count；
stage latency；
p50 / p95；
token usage；
cost；
```

Early exit 规则：

```text
无 fact statement 且无活跃问诊状态 → 不执行 claim generation；
无 governed claim → 不执行 enrichment；
无 participant phrase → 不调用 participant verifier；
无 temporal phrase → 不执行 temporal parser；
无 measurement phrase → 不执行 measurement parser；
canonical 仅在投影需要时触发；
relation 仅在 relation phrase 存在且策略需要时触发。
```

## 10. V14 实验矩阵

| 实验 | 目标 |
|---|---|
| EXEC-OBS | usage、attempt、model snapshot、minimal lane 延迟口径 |
| INTENT-SPLIT | fixed-field intent，消灭 fact_statement 重复 |
| GEN-OPTION | temperature=0 下 seed / top_p / 低温度小规模归因 |
| SKILL-INVENTORY | one-pass claim inventory 稳定性 |
| SKILL-SHARED | shared denial / normal scope 继承 |
| SKILL-NULL | null / not_applicable / ambiguous / review 分离 |
| SKILL-PARTICIPANT | action role phrase 契约 |
| ALIGN-LOCAL | claim-local field alignment 与 occurrence 消歧 |
| PARTICIPANT-V14 | TurnContext candidate-only resolver |
| TEMPORAL-V14 | temporal proposal + parser verifier |
| MEASUREMENT-V14 | measurement proposal + parser verifier |
| CAN-SELECT-V14 | dual query recall + constrained selector |
| MINIMAL-LANE | 最小路径调用、延迟、token、成本 |
| REP-V14 | 全 representative units 三次冷调用 |
| NEG-V14 | 契约与安全负例 |
| ASYNC-V14 | 异步失败隔离 |
| HELD-OUT-V14 | 默认 blocked |

详细执行设计见：

[input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md)

## 11. 防偏移检查清单

V14 实现和 PR review 必须确认：

```text
1. 主路径不调用 GLiNER；
2. LLM 输入不包含 precomputed span candidate menu；
3. 不要求 support anchor / structural seed 前置；
4. 不要求 LLM 输出 span_id / offset；
5. one-pass 不退回 two-stage；
6. fact_statement 不作为重复 act 输出；
7. LLM phrase 是 approximate proposal；
8. 最终 quote 由 aligner 从原文生成；
9. fuzzy not found 不通过；
10. fuzzy ambiguous 不静默选择；
11. 字段对齐默认限制在 claim envelope 内；
12. LLM 不输出 entity_id / canonical_id；
13. canonical 只能来自候选或 review；
14. model_proposed 不伪装 verified；
15. temporal / measurement proposal 有 parser verifier 状态；
16. retry 有界、同契约、可审计；
17. raw measurement 与 production retry 分离；
18. minimal lane 延迟不混入完整实验矩阵；
19. 临床安全保持 report-only；
20. 不写业务状态；
21. held-out 与 DSPy 继续冻结。
```

## 12. 准入与退出

### 12.1 硬性边界

```text
fact_statement_duplicate_count = 0
invented_entity = 0
invented_canonical = 0
confirmed_without_candidates = 0
projection_consuming_blocked_claim = 0
```

### 12.2 V14 探索目标

相较 V13：

```text
one-pass claim quality 下限高于 0.5625；
blocked count 波动收窄；
field false alignment 低于 0.1624；
participant resolution 高于 0.3333；
canonical false confirmation 低于 0.5；
temporal parser conflict 全部显式 review；
```

这些是实验目标，不是生产承诺。

### 12.3 稳定性目标

```text
全 representative units 3 次冷调用；
semantic claim signature 稳定；
stable-and-correct 占比提升；
不得由 stable-but-wrong 主导。
```

### 12.4 成本目标

```text
minimal lane model calls <= 2～3；
真实 minimal lane p95 显著低于 V13 full matrix；
token usage / cost 可观测。
```

## 13. 安全边界

所有 V14 实验保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
held_out_read = false
dspy_used = false
```

不接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

## 14. 证据索引

历史权威记录：

1. [input-preprocessing-v8-shadow-runner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v8-shadow-runner-change-summary.md)
2. [input-preprocessing-v9-attribution-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v9-attribution-change-summary.md)
3. [input-preprocessing-v10-shadow-runner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v10-shadow-runner-change-summary.md)
4. [input-preprocessing-v11-candidate-view-reranking-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v11-candidate-view-reranking-change-summary.md)
5. [input-preprocessing-v12-support-first-graph-ranking-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v12-support-first-graph-ranking-change-summary.md)
6. [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)

当前实验计划：

7. [input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md)

长期迁移方案：

8. [agent-input-preprocessing-domain-extraction-migration-plan.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-domain-extraction-migration-plan.md)

## 15. 维护规则

本文应在以下情况更新：

1. V14 fixed-field intent 契约调整；
2. one-pass claim inventory / shared scope 策略调整；
3. claim-local alignment 策略调整；
4. semantic proposal 状态调整；
5. participant / canonical 边界调整；
6. minimal lane 成本口径调整；
7. 实验矩阵或准入条件调整；
8. held-out / DSPy 解冻条件变化。

本文不应记录：

1. 具体 prompt 原文；
2. 每轮远程报告全文；
3. held-out 样本；
4. 生产阈值承诺；
5. 未经验证的实现细节。

