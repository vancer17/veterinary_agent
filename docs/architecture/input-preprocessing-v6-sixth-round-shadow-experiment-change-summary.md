<!--
=============================================================================
文件: input-preprocessing-v6-sixth-round-shadow-experiment-change-summary.md
作用: 记录输入前置预处理 V6 薄声明、按需批量富化与 claim graph 实验的实现与结论。
范围: 覆盖 V6 intent 契约、ThinUserClaim、quote 分层治理、candidate-only participant、
      deterministic temporal/measurement parser、Enrichment Planner、canonical
      under-confirmation 诊断、report-only 投影、持久化实验队列和远程真实模型 shadow。
说明: 本文只沉淀工程结论；不改变生产问诊、临床安全召回、required_context 或 OPA 裁决。
维护: 当 V6 契约、实验矩阵、远程报告结论或迁移准入条件变化时同步更新。
=============================================================================
-->

# 输入前置预处理 V6 第六轮 Shadow 实验变更总结

> **文档状态**：V6 实验实现、本地 ideal control 与远程 exploratory shadow 已完成；未达到生产消费准入
>
> **结论**：V6 的批量富化调度显著降低了 model call count，但真实模型仍在 intent 并行字段、quote containment、relation 分类、聚合目标拆分、canonical 召回和 gate 状态稳定性上漂移。V6 不能进入生产消费，也不能接入 `VetOrchestrator`。

## 1. 实验目标

第六轮实验验证 V6 是否修复 V5 暴露的执行与归因缺陷：

1. 拆分 intent contract 后，`answer_now` 与 `fact_statement_present` 能否并行稳定识别；
2. semantic identity 与 evidence quote 粒度能否分层评估；
3. participant enrichment 改为 TurnContext candidate-only 后能否消除发明实体；
4. canonical `not_found` 能否拆分为可归因诊断状态；
5. temporal / measurement 先走确定性 parser、再进入有界批量模型 fallback 是否有效；
6. Enrichment Planner 是否能降低逐 claim 调用爆炸；
7. per-claim graph 与 enrichment request state 是否提升失败隔离；
8. 负例是否全部阻断；
9. 异步批量 worker 是否保持 report-only 失败隔离；
10. 临床安全是否继续严格不进入 evaluator / OPA。

本轮仍为 development set exploratory 实验。held-out set 仅执行本地 ideal control，未参与远程真实模型确认性准入。

## 2. 实现范围

### 2.1 V6 契约

新增：

1. `V6TurnIntentRaw`;
2. `ThinUserClaimRaw`;
3. `V6QuoteAnchor`;
4. `V6ClaimStateVector`;
5. `V6ClaimTransition`;
6. `EnrichmentRequest`;
7. `EnrichmentPlan`;
8. `EnrichmentBatch`;
9. `V6SubjectEnrichment`;
10. `V6ParticipantEnrichment`;
11. `V6TemporalEnrichment`;
12. `V6MeasurementEnrichment`;
13. `V6AssertionVerification`;
14. `V6CanonicalMapping`;
15. `V6InputAnalysisResult`;
16. `V6QualityGateResult`。

V6 intent 契约删除：

```text
fact_path_required
```

改为并行输入属性：

```text
answer_now
wants_triage
correction
clarification_request
fact_statement_present
question_present
report_context_present
```

显式意图必须携带 evidence quote。

ThinUserClaim 增加：

```text
relation_quote
relation
subject_evidence_quote
```

初抽仍不输出：

```text
canonical_id
selected_candidate_id
canonical_surface
normalized temporal semantics
normalized measurement semantics
action_agent
action_recipient
action_object
fact_path_required
```

### 2.2 保守 quote 治理

V6 quote anchor 覆盖：

```text
evidence_quote
target_quote
temporal_quote
measurement_quote
relation_quote
subject_evidence_quote
intent evidence quote
```

仍只允许 trim、空白归一、全半角标点保守归一和重复标点保守归一。禁止同义词替换、编辑距离修复、embedding 相似修复和 LLM 重写 quote。

评估层将 semantic identity 与 evidence quote 宽度分离，避免 V5 中“合法窄 quote 被判为 semantic missing”的问题。

### 2.3 Candidate-only participant enrichment

V6 participant enrichment 只对 action / food / medication 类 claim 触发。

模型只能输出 TurnContext 提供的 candidate ID：

```text
action_agent_selected_candidate
action_recipient_selected_candidate
```

`object_mention` 只能作为原文 mention，不得当作实体 reference。

### 2.4 确定性 temporal / measurement parser

新增通用确定性 parser，先于模型 fallback 执行。

Temporal parser 支持：

```text
今天 / 昨天 / 前天 / 大前天
最近N天 / 这N天
N天 / N周 / N小时 / N分钟
一天N次 / 每天N次 / 每周N次 / 每月N次
```

Measurement parser 支持频率表达和数值 + 公斤、千克、克、毫克、毫升、升等通用单位。

无法保守解析时输出：

```text
unresolved + unresolved_reason + review
```

不猜测口语量词数值。

### 2.5 Enrichment Planner

V6 先产生结构化 request，再按以下结构性维度聚合：

```text
enrichment_type
coarse_type
subject_status
source_block_id
quote overlap
candidate set
risk level
```

禁止按疾病、症状域或急诊风险分支批量分组。

批量输出必须逐项绑定 `claim_id`，并校验：

```text
每个 request claim_id 都有 result；
无 unexpected claim；
无 cross-claim assignment；
batch partial failure 显式记录。
```

### 2.6 Canonical under-confirmation 诊断

V6 直接使用：

```text
target_quote
coarse_type
```

召回候选，不在召回前按 subject 过滤。

每个 `not_found / unmapped` 输出归因状态：

```text
no_candidate_recalled
candidate_below_threshold
candidate_filtered_by_coarse_type
candidate_filtered_by_subject
candidate_present_but_not_selected
top1_low_confidence
ambiguous_candidates
aggregate_target_not_decomposed
canonical_missing_in_registry
alias_missing
context_required
```

### 2.7 异步实验队列

新增 `FileAsyncShadowQueueV6`，以 enrichment batch snapshot 为队列单元，验证：

1. 有界队列；
2. 显式 queue full；
3. snapshot 持久化；
4. worker claim / complete / fail；
5. dead letter；
6. trace 持久化；
7. 不写业务状态；
8. 不触发临床安全 evaluator。

该队列仍是实验实现，不是生产 API shadow worker。

## 3. Fixture 与实验矩阵

新增：

```text
tests/fixtures/input_preprocessing/sixth_round_thin_shadow_matrix.json
tests/fixtures/input_preprocessing/sixth_round_thin_held_out_matrix.json
assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json
```

development set 覆盖：

```text
并列 denied / normal
用户动作
医疗提供者动作
护理者动作
其他宠物症状
多宠物歧义
historical
hypothetical
answer-now 与事实同轮
多轮省略
长输入时间 / 度量混合
口语脏数据
unmapped mention
无明确事实输入
吃喝没有明显变化
整体精神还行
相对基线变化
```

实验矩阵包含 24 组：

```text
INTENT-V6
THIN-QUOTE-V6
THIN-SCHEMA-V6
STATEMENT-RELATION-V6
SUBJECT-STAGE-V6
PARTICIPANT-BATCH-V6
AGGREGATE-DECOMP-V6
CAN-DIAG-V6
CAN-SELECT-V6
TEMPORAL-BATCH-V6
MEASUREMENT-BATCH-V6
ENRICH-PLANNER-V6
POLICY-ENRICH-T0
POLICY-ENRICH-T2
GRAPH-V6
DEDUP-MERGE-V6
MULTI-TURN-V6
DOMAIN-PROJECTION-V6
CS-REPORT-ONLY-V6
NEG-V6
REP-V6
ASYNC-V6
COST-QUALITY-V6
TOOL-ADAPTER-V6
```

## 4. 本地 Ideal Control 结果

Development ideal control：

```text
experiment_count = 24
passed = 24
failed = 0
```

权威报告：

```text
.data/evaluations/input-preprocessing-v6-local-final-2/input-preprocessing-v6-b9f7f9935ede.json
```

Held-out ideal confirmatory：

```text
experiment_count = 1
passed = 1
failed = 0
```

权威报告：

```text
.data/evaluations/input-preprocessing-v6-held-out-local-final-2/input-preprocessing-v6-ee5ef5e70655.json
```

本地全量测试：

```text
262 passed
43 skipped
```

V6 scoped mypy 与 ruff 检查通过。

Ideal control 不是生产结论，也不能作为 fallback。

## 5. 远程真实模型 Shadow 结果

### 5.1 执行方式

执行方式：

1. 通过 SSH 隧道访问远程 LiteLLM；
2. 使用 `qwen-plus`；
3. 每个 case 使用独立 analyzer / Qwen client；
4. development set；
5. exploratory phase；
6. `repeat_override=1`；
7. 不进入 evaluator / pgvector / required_context / OPA。

权威报告：

```text
.data/evaluations/input-preprocessing-v6-remote-exploratory-full-1/input-preprocessing-v6-42250010882e.json
```

报告时间：

```text
2026-08-26T19:23:29.518563+08:00
```

SHA-256：

```text
df85ccb0c83d145a90b46dc5b583b467a49beec59c131af0e4e5803074460852
```

固定版本：

```text
model = qwen-plus
prompt_version = v6-thin-dev-20260826-2
schema_version = v6-thin-raw
policy_version = v6-policy-dev-20260826-1
graph_version = v6-claim-graph-dev-20260826-1
gate_version = v6-gates-dev-20260826-1
vocabulary_version = input-preprocessing-dev-v3
fixture_sha256 = e66d4136a8fee421fbbaabd8961978580d826bbc05aa13ddfd02780c8c5bab30
analyzer_isolation = per-case-fresh-qwen-client
```

在完整矩阵执行后，runner 补充了逐 claim trace 与 report-only projection report 输出。为避免重跑完整矩阵，另执行一次仅包含 `DOMAIN-PROJECTION-V6` 与 `CS-REPORT-ONLY-V6` 的 supplemental shadow。该补充报告用于验证 projection 边界，不改变完整矩阵的通过率：

```text
.data/evaluations/input-preprocessing-v6-remote-projection-supplemental/input-preprocessing-v6-6a689743e517.json
```

SHA-256：

```text
0dcc2d17c4938c24f170f434e344220959ec00a707dd1776364e6e56e3e3ee8d
```

两个 projection 实验均因上游 quote / intent / relation gate 失败而输出 `blocked_by_gate`。clinical safety projection 保持：

```text
downstream_evaluation = not_implemented
evaluator_called = false
opa_called = false
```

### 5.2 总览

```text
experiment_count = 24
passed = 2
failed = 22
runner_error_count = 0
```

通过：

```text
NEG-V6
ASYNC-V6
```

失败来自：

```text
intent 并行字段漂移
quote containment 失败
relation 分类漂移
聚合目标未拆分
canonical under-confirmation
participant / subject gate 阻断
semantic signature 不匹配
重复稳定性不足
```

延迟概览：

```text
experiment latency sum = 1,972,668 ms
median = 80,460.5 ms
p95 = 140,078 ms
max =  155,542 ms
```

结论：

> 批量富化降低了 model call count，但没有降低端到端延迟；真实模型与评估契约仍未稳定。

### 5.3 Intent 契约

简单 `answer_now` 输入和长输入仍出现：

```text
answer_now = false
fact_statement_present = false / true 漂移
report_context_present = true 漂移
question_present = true 漂移
```

部分控制意图仍被 thin extraction 输出为 fact claim，例如：

```text
先给我一句概述就好
```

结论：

```text
删除 fact_path_required 是正确方向；
但 intent 拆分后的并行字段仍未稳定；
answer_now 与 fact_statement_present 仍会被模型混淆。
```

### 5.4 Thin claim 与 quote 治理

D 样本仍能输出 10 条 claim，说明共享声明覆盖能力保留。

但 quote valid 在不同运行中漂移：

```text
D quote_valid_count = 2 / 10
D quote_valid_count = 3 / 10
E quote_valid_count = 8 / 8
L quote_valid_count = 5 / 5
```

D 样本主要失败来自：

```text
target_quote 不落在 evidence_quote 内
或辅助 quote containment 失败
```

这不是 quote gate 过严导致的评估问题，而是模型输出的证据锚定关系仍不稳定。

### 5.5 Statement / relation

模型经常输出：

```text
relation = unclear
```

而期望为：

```text
absolute_status
no_change
```

聚合目标输入：

```text
吃饭喝水都没有明显变化
吃喝也没有明显变化
```

未被稳定拆分为：

```text
吃饭 no_change
喝水 no_change
```

模型有时输出整段聚合表达作为单一 target。

结论：

```text
relation_quote 契约方向正确；
但模型对 relation 与聚合目标拆分仍不稳定；
不能用中文关键词硬拆。
```

### 5.6 Participant enrichment

candidate-only gate 能阻断非法 participant，但真实输出仍导致较多 blocked / review。

E 样本观察：

```text
quote_valid_count = 8 / 8
projection_ready_count = 0 或 2
blocked_count = 3
review_count = 5 或 7
```

说明：

```text
participant 未再以自由字符串实体进入投影；
但 action claim 的 coarse type、subject 与 participant 绑定仍漂移。
```

### 5.7 Canonical linking 与 under-confirmation 诊断

D 样本在一次运行中：

```text
9 个 confirmed mapping
1 个 no_candidate_recalled
```

E 样本则出现：

```text
5 个 no_candidate_recalled
3 个 confirmed
```

长输入出现：

```text
candidate_filtered_by_coarse_type
no_candidate_recalled
```

说明 direct target quote recall 对简单症状有效，但动作、聚合目标与粗类型仍导致 under-confirmation。

V6 已经能输出归因状态，而不是单一 `not_found`：

```text
no_candidate_recalled
candidate_filtered_by_coarse_type
```

这是相对 V5 的可观测性进展。

### 5.8 Temporal / measurement parser

确定性 parser 在本地可稳定解析：

```text
前天 → started_at / day-2 / day
这两天 → duration / recent-2-days-approximate / approximate_duration
一天一次 → 1/day / frequency
```

但远程 thin extraction 有时不输出独立 temporal / measurement quote，导致 parser 没有输入。

例如：

```text
前天开始换新狗粮
一天一次
```

可能被合并进整段 claim 或缺失 relation 绑定。

结论：

```text
deterministic parser first 有效；
瓶颈前移到 thin extraction 的 quote 与 relation 绑定。
```

### 5.9 T0 / T2 与批量调度收益

| 样本 | Variant | model calls | batch count | projection ready | blocked |
|---|---|---:|---:|---:|---:|
| D shared scope | T0 | 2 | 0 | 0 | 8 |
| D shared scope | T2 | 10 | 12 | 0 | 10 |
| E event roles | T0 | 2 | 0 | 0 | 0 |
| E event roles | T2 | 6 | 6 | 0 | 8 |

T1 主候选在其他实验中：

```text
D model calls ≈ 8
E model calls ≈ 6
```

相比 V5 权威观察：

```text
V5 D T1 model calls = 22
V5 E T1 model calls = 19
V6 D T1 model calls ≈ 8
V6 E T1 model calls ≈ 6
```

结论：

```text
Enrichment Planner 显著降低调用数；
但质量未提升到可消费标准；
且端到端延迟未下降。
```

原因包括：

1. intent / thin extraction schema retry；
2. 批量输出 coverage mismatch；
3. 不必要 enrichment 类型被触发；
4. quote / relation 失败导致重复实验；
5. 实验矩阵多次重复同一远程 case。

### 5.10 安全边界

权威远程矩阵中保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

未接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

## 6. 架构判断

### 6.1 已证明

1. V6 契约可表达 thin claim、intent、relation、批量富化和 claim graph 状态；
2. deterministic negative gate 有效；
3. batched file queue 的 overflow / dead letter / trace 隔离语义有效；
4. candidate-only participant 能阻断自由字符串实体；
5. canonical not_found 可以输出可归因诊断状态；
6. deterministic temporal / measurement parser 能保守解析常见表达；
7. Enrichment Planner 显著降低 model call count；
8. clinical safety report-only 边界可执行。

### 6.2 未证明

1. intent 并行字段稳定；
2. answer_now 长输入稳定；
3. quote containment 稳定；
4. relation 与聚合目标拆分稳定；
5. participant / subject 富化质量达到消费标准；
6. canonical under-confirmation 已修复；
7. V6 端到端延迟优于 V5；
8. development set 3 次输出 signature 稳定；
9. held-out 真实模型确认性通过；
10. V6 质量优于 V3 / V4 / V5。

## 7. 准入结论

当前结果不允许：

1. 生产消费问诊事实；
2. 生产消费 clinical safety projection；
3. 接入 clinical safety evaluator；
4. 接入 `VetOrchestrator`；
5. 将 file-backed experiment queue 用于生产；
6. 将 ideal control 作为 fallback；
7. 直接灰度 `answer_now`；
8. 修改回答充分性策略掩盖上游失败；
9. 放宽 quote gate、candidate audit 或 claim graph 状态边界。

允许继续：

1. report-only shadow；
2. deterministic gate 回归；
3. intent contract 专项拆分实验；
4. quote containment / relation 专项实验；
5. candidate-only participant 专项实验；
6. canonical diagnostic 归因分析；
7. V6-T0 / T1 / T2 成本与质量对照；
8. held-out 真实模型验证；
9. 生产级异步 worker 设计。

## 8. 下一轮修复优先级

1. **Intent 专项路由**：将 answer-now / fact-statement / question / report-context 拆成更小任务，继续要求 evidence quote。
2. **Quote containment 诊断**：区分 evidence not found、target not contained、relation quote not contained，不放宽 quote gate。
3. **Relation classifier**：独立区分 absolute status 与 no change，不与 thin extraction 混合。
4. **Aggregate decomposition**：只在投影需要具体槽位时拆分，子 claim 共享 relation quote，不得关键词硬拆。
5. **Canonical 诊断归因**：针对 `no_candidate_recalled` 与 `filtered_by_coarse_type` 修正词表、类型或 selector。
6. **Enrichment Planner 触发率**：减少不必要 temporal / measurement / assertion batch，同时保证 request coverage。
7. **重复稳定性**：development 单次通过后，每样本至少 3 次 signature 一致。

## 9. 复现命令

本地 development ideal control：

```bash
uv run python -m vet_agent.input_preprocessing.v6_experiments \
  --mode ideal \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v6-local
```

本地 held-out ideal confirmatory：

```bash
uv run python -m vet_agent.input_preprocessing.v6_experiments \
  --matrix tests/fixtures/input_preprocessing/sixth_round_thin_held_out_matrix.json \
  --mode ideal
```

远程 exploratory shadow：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
INPUT_PREPROCESSING_V6_PROMPT_VERSION=v6-thin-dev-20260826-2 \
scripts/integration/run-input-preprocessing-v6-experiment-smoke.sh \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v6-remote-exploratory
```

远程 held-out confirmatory 必须冻结模型、prompt、schema、policy、vocabulary、parser、planner、graph、gate 和 fixture 版本后执行。

真实模型实验依赖远程 LiteLLM。依赖不可用时必须失败，不得回退关键词、宽松 JSON 或本地规则。
