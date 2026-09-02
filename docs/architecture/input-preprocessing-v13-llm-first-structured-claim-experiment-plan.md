<!--
=============================================================================
文件: input-preprocessing-v13-llm-first-structured-claim-experiment-plan.md
作用: 定义 V13 LLM-first Structured Claim Generation 快速验证与 shadow 实验
      的契约、矩阵、指标、执行顺序、准入条件和防偏移边界。
范围: 覆盖 claim unit segmentation、flat claim record generation、fuzzy
      evidence alignment、语义 proposal verifier、TurnContext participant
      resolver、canonical descriptor linking、post-hoc claim graph、范式对照、
      REP、NEG、ASYNC 和 held-out 防护。
说明: 本文是 V13 实验计划；实验已完成并由 V13 change summary 固化权威结论。
      本文不改变生产问诊、临床安全召回、required_context 或 OPA 裁决。
维护: 当 V13 契约、实验矩阵、runner、报告字段或准入条件变化时同步更新。
=============================================================================
-->

# Input preprocessing V13 LLM-first Structured Claim 实验计划

> **文档状态**：已完成；V13 探索结论已固化；未达到生产消费准入
>
> **核心问题**：不用 GLiNER 前置候选池，LLM 直接生成结构化 claim，再由后验工具对齐和验证，是否显著优于 V12 support-first graph 路线？
>
> **安全边界**：report-only；不写业务状态；不触发临床安全 evaluator / pgvector / required_context / OPA；held-out 与 DSPy 冻结。

> **权威结果**：approximate one-pass 在两次完整 representative shadow 中分别达到 `0.8125 / 0.8125` 与 `0.5625 / 0.5625` claim precision / recall，均高于 V12 `0.0448 / 0.34`，但结果波动、intent precision、field false alignment、participant resolution、canonical false confirmation 与 latency 仍未达标。详见：
> [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)

## 1. 实验目标

V13 只验证以下问题：

1. LLM 能否稳定切分 claim unit；
2. LLM 能否生成扁平 claim record；
3. approximate phrase 能否由 fuzzy aligner 可靠落回原文；
4. statement type、polarity、modality、epistemic status 是否可分离；
5. temporal / measurement proposal 是否可由 parser verifier 治理；
6. participant phrase 是否可由 TurnContext resolver 正确解析；
7. canonical descriptor 是否降低 under-confirmation 且不引入假确认；
8. post-hoc claim graph 是否能提供 field-level lineage；
9. V13 是否优于 V12；
10. 三次冷调用是否 stable-and-correct。

不验证：

```text
生产问诊消费；
clinical safety evaluator 接入；
required_context；
clinical safety OPA；
API metadata shadow 接入 VetOrchestrator；
DSPy 优化；
held-out 泛化。
```

## 2. 核心架构

```text
Raw Input + TurnContext
→ Independent Turn Intent Analyzer
→ LLM Claim Unit Segmentation
→ LLM Flat Claim Record Generation
→ Deterministic Fuzzy Evidence Alignment
→ Field-level Governance
→ Targeted Enrichment
   ├─ TurnContext subject / participant resolver
   ├─ temporal parser verifier
   ├─ measurement parser verifier
   ├─ relation classifier
   └─ canonical candidate linker
→ Post-hoc Claim Graph
→ Gates / Review
→ Report-only Projection
→ Trace / Metrics
```

硬性前提：

```text
主路径不调用 GLiNER；
LLM 输入不包含 span candidate menu；
不要求 support anchor / structural seed 前置；
LLM 不输出 span_id / offset / entity_id / canonical_id。
```

GLiNER 只允许在 `PARADIGM-COMPARE` 中做旁路 cross-check，并报告：

```text
agreement
missed_by_gliner
false_gliner_span
```

## 3. 契约

### 3.1 V13TurnIntentRaw

继续保留独立意图识别：

```text
answer_now
wants_triage
correction
clarification_request
fact_statement_present
question_present
report_context_present
```

显式 true act 必须输出：

```text
evidence_phrase
confidence
```

后续仍由 intent evidence aligner 落回原文。

### 3.2 V13ClaimUnitRaw

```text
unit_id
evidence_phrase
core_phrase
claim_kind:
  action
  state
  denial
  relation
  question
  correction
  historical
  hypothetical
subject_hint
confidence
needs_review
coverage_gap_reason
```

### 3.3 V13ClaimRecordRaw

```text
unit_id
claim_type

evidence_phrase
target_phrase

subject_phrase
action_agent_phrase
action_recipient_phrase
object_phrase

user_statement_type
polarity
modality_type
modality_strength
epistemic_status

temporal_phrase
temporal_relation
temporal_value
temporal_precision

measurement_phrase
measurement_value
measurement_unit
measurement_relation

canonical_descriptor

confidence
needs_review
missing_field_reason
```

禁止字段：

```text
span_id
start
end
entity_id
canonical_id
selected_candidate_id
```

### 3.4 V13AlignedEvidence

```text
field_name
model_phrase
aligned_quote
start
end
alignment_status
similarity
best_candidate
second_best_candidate
score_margin
alignment_method
verifier_status
review_required
```

### 3.5 V13SemanticProposal

用于 temporal / measurement / canonical descriptor 等语义值：

```text
proposal_type
raw_phrase
value
unit
relation
precision
confidence
governance_status:
  verified
  model_proposed
  parser_conflict
  unresolved
governance_reason
review_required
```

## 4. Fuzzy Evidence Alignment

### 4.0 Phrase policy 对照

V13 必须区分 model phrase policy 与 source quote governance：

```text
literal control:
  模型 phrase 必须逐字来自原文连续片段；

approximate primary:
  模型 phrase 是 semantic proposal，
  不要求逐字复制原文或保持连续，
  但不得引入原文没有的信息，也不得丢失关键语义。
```

两种 policy 的 model phrase 都不是 quote、不是 evidence。最终 evidence 只能来自：

```text
deterministic aligner
→ raw_text[start:end]
→ aligned_quote
```

“禁止 LLM 重写 quote”指 aligner 和 governance 不得用模型改写文本替代原文 quote；它不表示 generator prompt 必须强制逐字输出。exploratory shadow 必须默认执行 `approximate`，另跑 `literal` control。

### 4.1 匹配顺序

```text
1. exact
2. conservative normalized exact
3. unique fuzzy
4. fuzzy ambiguous
5. fuzzy not found
```

允许 normalization：

```text
trim
空白归一
全半角标点归一
重复标点归一
```

禁止：

```text
同义词替换；
编辑距离修改 quote；
embedding 相似改写 quote；
LLM 重写 quote。
```

### 4.2 状态

```text
exact
exact_normalized
fuzzy_verified
fuzzy_ambiguous
fuzzy_not_found
cross_source_block
empty_phrase
```

### 4.3 接受规则

| 状态 | 处理 |
|---|---|
| exact / exact_normalized | 直接通过 |
| fuzzy_verified | shadow / provisional evidence 可用；高风险需额外 verifier |
| fuzzy_ambiguous | review |
| fuzzy_not_found | blocked / review |
| cross_source_block | blocked |
| empty_phrase | blocked / coverage gap |

Fuzzy verifier 输出：

```text
verified
semantic_mismatch
negation_lost
temporal_lost
subject_lost
boundary_crossing
uncertain
```

## 5. 实验矩阵

### 5.1 ALIGNER-CONTROL

不调用 LLM，用人工 phrase 变体验证 deterministic aligner。

覆盖：

```text
完全一致；
标点差异；
全半角差异；
同义改写；
近似边界；
否定丢失；
时间丢失；
多个相似候选；
无匹配；
跨 source block。
```

指标：

```text
exact_rate
exact_normalized_rate
fuzzy_verified_rate
fuzzy_ambiguous_rate
not_found_rate
false_alignment_rate
negation_loss_detection_rate
temporal_loss_detection_rate
latency
```

### 5.2 LLMF-SEG-ONLY

只验证 claim unit segmentation。

指标：

```text
claim_unit_recall
claim_unit_precision
atomicity
over_merge_rate
over_split_rate
coverage_gap_explicit_rate
semantic_unit_signature_stability
```

### 5.3 LLMF-ONEPASS

一次调用直接生成 flat claim records。

指标：

```text
schema_valid_rate
claim_recall
claim_precision
statement_type_accuracy
field_missing_rate
model_call_count
latency
cost
```

### 5.4 LLMF-TWOSTAGE

两阶段：

```text
Stage 1 claim unit segmentation
Stage 2 claim record generation
```

与 ONEPASS 比较：

```text
unit quality
claim quality
field accuracy
model_call_count
latency
cost
REP stability
```

### 5.5 CLAIM-ALIGN

按字段验证 phrase alignment：

```text
evidence
target
subject
action_agent
action_recipient
object
temporal
measurement
canonical_descriptor
```

指标：

```text
field_alignment_rate
exact_rate
exact_normalized_rate
fuzzy_verified_rate
fuzzy_ambiguous_rate
not_found_rate
false_alignment_rate
review_rate
```

### 5.6 FUZZY-POLICY

比较：

```text
A. exact only
B. exact + normalized
C. + unique fuzzy
D. + fuzzy verifier
E. unrestricted fuzzy 负面对照
```

指标：

```text
usable_claim_rate
false_alignment_rate
ambiguous_rate
review_rate
latency
```

E 组只验证风险，不得进入下游。

### 5.7 STATEMENT-SEMANTICS

验证：

```text
user_statement_type
polarity
modality
epistemic_status
```

重点样本：

```text
没有呕吐
好像没有呕吐
如果呕吐怎么办
去年呕吐过
医生说没有呕吐
吃喝没有明显变化
精神还好
```

指标：

```text
statement_type_accuracy
polarity_accuracy
modality_accuracy
epistemic_accuracy
denied_as_present
normal_as_no_change
```

### 5.8 TEMPORAL-PROPOSAL

比较：

```text
A. LLM proposal only
B. parser only
C. LLM proposal + parser verifier
```

指标：

```text
proposal_accuracy
parser_agreement_rate
parser_conflict_rate
unresolved_rate
over_precision_rate
binding_accuracy
```

### 5.9 MEASUREMENT-PROPOSAL

比较：

```text
A. LLM proposal only
B. parser only
C. LLM proposal + parser verifier
```

重点：

```text
一天一次
5 公斤
半片
一小把
```

指标：

```text
value_accuracy
unit_accuracy
relation_accuracy
over_precision_rate
model_proposed_review_rate
```

### 5.10 PARTICIPANT-RESOLVE

验证 LLM phrase 到 TurnContext entity 的解析。

指标：

```text
subject_resolution_accuracy
action_agent_accuracy
action_recipient_accuracy
object_mention_accuracy
ambiguous_detection_rate
invented_entity_rate
resolved_empty_violation
```

### 5.11 CAN-DESCRIPTOR

比较：

```text
A. target_phrase direct recall
B. canonical_descriptor recall
C. target_phrase + descriptor dual query
D. constrained LLM selector
```

指标：

```text
candidate_recall
canonical_accuracy
under_confirmation_rate
false_confirmation_rate
not_found_review_rate
new_concept_request_rate
```

### 5.12 PARADIGM-COMPARE

对照组：

```text
A. V12 support-first graph + seeded Macro
B. V13 one-pass
C. V13 two-stage
D. V13 + GLiNER cross-check
```

指标：

```text
claim_recall
claim_precision
statement_type_accuracy
participant_accuracy
temporal_binding_accuracy
measurement_binding_accuracy
evidence_alignment_rate
canonical_accuracy
model_call_count
latency
cost
REP_stability
```

### 5.13 REP-V13

三次冷调用，禁用 cache。

分别报告：

```text
claim segmentation
claim record generation
field semantics
```

指标：

```text
unique_output_count
raw_output_stability
semantic_claim_stability
field_stability
statement_stability
stable_and_correct_rate
stable_but_wrong_rate
unstable_rate
```

### 5.14 NEG-V13

负例覆盖：

```text
fuzzy not found 直接通过；
fuzzy ambiguous 静默通过；
LLM 发明 entity_id；
LLM 发明 canonical_id；
claim 无 evidence phrase；
true act 无 evidence phrase；
resolved participant 为空；
model_proposed 伪装 verified；
parser_conflict 未 review；
projection 消费 blocked claim；
retry 无界或改变契约。
```

指标：

```text
gate_blocked_as_expected_rate
false_pass_rate
gate_reason_correct_rate
```

### 5.15 ASYNC-V13

验证异步 report-only worker：

```text
queue full
dead letter
worker failure isolation
trace completeness
main path latency delta
main path error delta
```

## 6. 报告要求

每个实验报告：

```text
experiment_id
mode: quick / shadow
model
prompt_version
schema_version
aligner_version
parser_version
vocabulary_version
gate_version
fixture_version
cache_status
cold_run_status

claim_segmentation_metrics
claim_record_metrics
alignment_metrics
semantic_metrics
participant_metrics
canonical_metrics
comparison_metrics

model_call_count
latency
token_usage
cost_availability
failure_attribution
safety_boundary
```

## 7. 执行顺序

```text
1. ALIGNER-CONTROL
2. NEG-V13
3. LLMF-SEG-ONLY
4. LLMF-ONEPASS
5. LLMF-TWOSTAGE
6. CLAIM-ALIGN
7. FUZZY-POLICY
8. STATEMENT-SEMANTICS
9. TEMPORAL-PROPOSAL
10. MEASUREMENT-PROPOSAL
11. PARTICIPANT-RESOLVE
12. CAN-DESCRIPTOR
13. PARADIGM-COMPARE
14. REP-V13
15. ASYNC-V13
```

禁止顺序倒置：

```text
alignment 未验证时，不进入 claim integration；
claim segmentation 未验证时，不评估 field semantics；
participant / temporal / measurement 未治理时，不进入 claim graph；
claim graph 未达标时，不做 projection；
REP 未稳定时，不接触 held-out。
```

## 8. V13 继续条件

相比 V12：

```text
claim recall > 0.34
claim precision > 0.0448
statement type accuracy > 0.32
participant role accuracy 有可解释改善
fuzzy false alignment 可控
latency / cost 可接受
输出不是稳定错误
```

这些不是生产阈值，只是判断 V13 是否值得继续的探索条件。

## 9. 硬边界

所有结果均不得：

```text
fuzzy not found 通过；
fuzzy ambiguous 静默通过；
model_proposed 伪装 verified；
LLM 发明 entity / canonical；
临床安全接入；
写业务状态；
接入 VetOrchestrator；
解除 V8 live gate；
读取 held-out；
启用 DSPy。
```

## 10. 安全边界

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

## 11. 防偏移检查清单

实现和 PR review 必须确认：

```text
1. V13 主路径无 GLiNER 调用；
2. LLM 输入无 precomputed candidate menu；
3. 无 support anchor 前置依赖；
4. 无 structural seed 前置依赖；
5. LLM 不输出 span_id；
6. LLM 不输出 offset；
7. LLM 不输出 entity_id；
8. LLM 不输出 canonical_id；
9. aligned quote 均来自原文；
10. fuzzy acceptance 有分级和 verifier；
11. semantic proposal 有 governance status；
12. canonical selected id 来自候选；
13. retry 有界且同契约；
14. no / empty act / claim 有显式 reason；
15. projection 不消费 blocked claim；
16. held-out 与 DSPy 保持冻结。
```
