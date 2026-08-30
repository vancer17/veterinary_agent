<!--
=============================================================================
文件: input-preprocessing-v5-fifth-round-shadow-experiment-change-summary.md
作用: 记录输入前置预处理 V5 薄声明、按需富化和 claim graph 实验的实现与结论。
范围: 覆盖 ThinUserClaim、独立意图路由、conservative quote governance、
      per-claim state vector、策略驱动富化、target quote canonical linking、
      report-only 投影、development / held-out fixture、持久化实验队列和
      远程真实模型 shadow。
说明: 本文只沉淀工程结论；不改变生产问诊、临床安全召回、required_context
      或 OPA 裁决。
维护: 当 V5 契约、实验矩阵、远程报告结论或迁移准入条件变化时同步更新。
=============================================================================
-->

# 输入前置预处理 V5 第五轮 Shadow 实验变更总结

> **文档状态**：V5 实验实现、本地 ideal control 与远程 exploratory shadow 已完成；未达到生产消费准入
>
> **结论**：`薄声明 + 原文引述锚定 + 确定性治理 + 按需富化 + per-claim graph` 已具备可执行实验形态。本地 development ideal control 23 / 23 通过，held-out ideal control 7 / 7 通过；修复后完整远程 exploratory matrix 为 2 / 23 通过。远程结果显示：言语行为和共享声明覆盖优于 V4，策略驱动富化成本低于全量富化，但意图路由、事件参与者、canonical under-confirmation、时间 / 度量富化和重复稳定性仍未达标。V5 不能进入生产消费。

## 1. 实验目标

第五轮实验验证 V5 是否优于继续加厚 V3 多级流水线或 V4 全字段扁平抽取：

1. 独立 Turn Intent Router 是否能将 `answer_now` 与 fact claim 分离；
2. `ThinUserClaim` schema 是否比 V4 全字段 observation 更容易遵循；
3. quote anchoring 是否继续阻断无证据事实；
4. 没有 expected count 时，共享断言是否能逐项输出 claim；
5. 言语行为 `denies` / `reports_normal` / `historical` / `hypothetical` 是否比医学断言 enum 更稳定；
6. 主体和参与者是否可以按 claim 局部富化；
7. 时间和度量是否可以初抽只保留 quote、策略需要时再富化；
8. canonical 是否能直接使用 target quote 召回，避免模型翻译 surface；
9. per-claim graph 是否能隔离单条 blocked / review claim；
10. 负例是否全部阻断；
11. 异步 shadow 是否保持有界队列、dead letter 和失败隔离；
12. 临床安全是否继续严格 report-only。

## 2. 实现范围

### 2.1 契约

新增：

1. `ThinUserClaimRaw`;
2. `ThinExtractionRawOutput`;
3. `V5QuoteAnchor`;
4. `V5ClaimStateVector`;
5. `V5ClaimTransition`;
6. `V5SubjectEnrichment`;
7. `V5ParticipantEnrichment`;
8. `V5TemporalEnrichment`;
9. `V5MeasurementEnrichment`;
10. `V5AssertionVerification`;
11. `V5CanonicalMapping`;
12. `V5InputAnalysisResult`;
13. `V5QualityGateResult`。

薄声明只输出：

```text
claim_id
source_id / source_block_id
evidence_quote / target_quote
user_statement_type
coarse_type
subject_role / status / candidates
temporal_quote
measurement_quote
confidence
needs_review
```

模型不输出：

```text
canonical_id
selected_candidate_id
canonical_surface
normalized temporal semantics
normalized measurement semantics
action_agent
action_recipient
action_object
```

### 2.2 独立意图路由

`V5TurnIntentRaw` 独立输出：

```text
answer_now
wants_triage
correction
clarification
fact_path_required
confidence
rationale
```

该路由不输出事实、canonical 或医学判断。

### 2.3 Conservative quote governance

新增 V5 quote anchor 解析：

```text
原文保留
→ 保守 normalization
→ exact substring / occurrence 解析
→ target containment
→ temporal / measurement quote containment
```

允许：

```text
trim / 空白移除
全半角标点保守归一
重复标点保守归一
```

禁止：

```text
同义词替换
编辑距离修复
embedding 相似修复
LLM 重写 quote
```

### 2.4 Per-claim graph

新增框架无关 `ClaimGraphBuilder`。每条 claim 使用独立状态向量：

```text
quote_state
statement_state
subject_state
participant_state
temporal_state
measurement_state
assertion_state
canonical_state
projection_state
```

状态维度允许：

```text
pending
verified
ambiguous
unresolved
failed
not_required
review_required
blocked
ready
```

这样可以表达：

```text
canonical confirmed + temporal unresolved
participant ready + canonical unmapped
quote verified + subject ambiguous
```

单条 claim 的 quote / subject / enrichment / canonical 失败不会自动污染其他 claim。

### 2.5 按需富化节点

新增：

1. Reference / Subject Enrichment;
2. Participant Enrichment;
3. Temporal Enrichment;
4. Measurement Enrichment;
5. Assertion Verifier;
6. Canonical Linker。

Policy router 消费结构化 claim state 和 coarse type，输出：

```text
reference_resolution_required
participant_enrichment_required
temporal_enrichment_required
measurement_enrichment_required
assertion_verification_required
canonical_link_required
```

其中：

1. 只有 action / food / medication claim 触发 participant enrichment;
2. 只有存在 temporal quote 且策略需要时触发 temporal enrichment;
3. 只有存在 measurement quote 且策略需要时触发 measurement enrichment;
4. 低置信、冲突或高风险消费前触发 assertion verifier;
5. canonical 仅在投影需要时触发。

### 2.6 Canonical linking

V5 不再使用模型生成的 `canonical_surface`，而是直接使用：

```text
target_quote
coarse_type
subject entity type
```

执行候选召回：

```text
target_quote
→ candidate retriever
→ candidates[]
→ deterministic top-1 selected_candidate_id
→ code resolves canonical_id
```

无候选时：

```text
mapping_status = not_found
canonical_id = null
review_required = true
```

多轮省略场景保留当前用户 quote，同时允许使用服务端上一轮问题目标作为受约束检索上下文，不把系统问题文本改写为用户 quote。

### 2.7 质量门禁与投影

新增或强化：

```text
v5_turn_context
v5_thin_schema
v5_quote_anchor
v5_statement
v5_subject_participant
v5_enrichment
v5_canonical_registry
v5_duplicate_claim
v5_suspicious_empty
v5_projection_boundary
```

新增 report-only 问诊投影和临床安全结构投影。硬边界：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

### 2.8 异步实验队列

新增 `FileAsyncShadowQueueV5`，以 claim task 为队列单元，验证：

1. 有界队列；
2. 显式 queue full；
3. snapshot 持久化；
4. worker claim / complete / fail；
5. dead letter;
6. trace 持久化；
7. 不写业务状态；
8. 不触发临床安全 evaluator。

该队列仍是实验实现，不是生产 API shadow worker。

## 3. Fixture 与实验矩阵

新增：

```text
tests/fixtures/input_preprocessing/fifth_round_thin_shadow_matrix.json
tests/fixtures/input_preprocessing/fifth_round_thin_held_out_matrix.json
```

development set 覆盖：

1. 并列 denied;
2. 并列 normal;
3. 用户动作；
4. 医疗提供者动作；
5. 护理者动作；
6. 其他宠物症状；
7. hypothetical;
8. answer-now 与事实同轮；
9. 多轮省略；
10. 长输入时间 / 度量混合；
11. 口语脏数据；
12. 多宠物歧义；
13. unmapped mention;
14. 无明确事实输入。

held-out set 覆盖不同表达、狗场景、护士动作、另一只狗症状、不同 answer-now、不同多轮省略和不同 unmapped mention。

实验矩阵包含 23 组，其中 `POLICY-ENRICH-T0 / T1 / T2` 构成按需富化 ablation：

```text
INTENT-V5
THIN-SCHEMA
QUOTE-V5
CLAIM-COVERAGE
STATEMENT-TYPE
SUBJECT-REF
PARTICIPANT-ENRICH
TARGET-CAN
CAN-SELECT
POLICY-ENRICH
POLICY-ENRICH-T0
POLICY-ENRICH-T2
TEMPORAL-ENRICH
MEASUREMENT-ENRICH
GRAPH-STATE
DEDUP-MERGE
MULTI-TURN
DOMAIN-PROJECTION
CS-REPORT-ONLY
NEG-V5
REP-V5
ASYNC-V5
COST-QUALITY
```

## 4. 本地 Ideal Control 结果

Development ideal control：

```text
experiment_count = 23
passed = 23
failed = 0
```

Held-out ideal confirmatory：

```text
experiment_count = 7
passed = 7
failed = 0
```

权威本地报告：

```text
.data/evaluations/input-preprocessing-v5-local-final-3/input-preprocessing-v5-4e440834a4f5.json
.data/evaluations/input-preprocessing-v5-local-held-out-final-3/input-preprocessing-v5-fa0d66aa1063.json
```

SHA-256：

```text
24957640d8671a95d09bfaa2cc8cda66a5c4125813b34092dfaf190eeb881939
dedcfc781a93c6bbdfcfa5ee70024544e20b45c9416bf9e828d416679723d851
```

V5 确定性单元测试覆盖：

1. development / held-out ideal control；
2. negative mutation blocking;
3. Thin schema 不包含 canonical / participant / normalized 字段；
4. conservative quote normalization；
5. target quote canonical 链接；
6. 无候选不得 confirmed;
7. 有界队列与 dead letter。

本地结果：

```text
tests/test_input_preprocessing_v5.py: 7 passed
```

Ideal control 不是生产结论，也不能作为 fallback。

## 5. 远程真实模型 Shadow 结果

本节仅采用修复后完整远程 exploratory matrix 作为 V5 架构评估依据。早期一次远程运行因实验 runner 契约与计时缺陷被废弃，不参与本节结论；最小审计说明见附录 A。

### 5.1 权威报告与固定版本

报告：

```text
.data/evaluations/input-preprocessing-v5-remote-exploratory-repaired-full/input-preprocessing-v5-bf9fce680eb5.json
```

报告时间：

```text
2026-08-26T15:41:22.086229+08:00
```

SHA-256：

```text
da3475196c2ba04cc793bd89eb06487a56a6dd78f0337118c02a18d5c9573446
```

固定版本：

```text
model = qwen-plus
prompt_version = v5-thin-dev-20260826-1
schema_version = v5-thin-raw
policy_version = v5-policy-dev-20260826-1
graph_version = v5-claim-graph-dev-20260826-1
gate_version = v5-gates-dev-20260826-1
vocabulary_version = input-preprocessing-dev-v2
fixture_version = 4131ea45e525f8c9a14bc6596f7530a1538c30bb59b83d72f072ff6db6cbe17f
analyzer_isolation = per-case-fresh-qwen-client
```

### 5.2 总体结果与架构指标

总览：

```text
experiment_count = 23
passed = 2
failed = 21
runner_error_count = 0
```

通过：

```text
NEG-V5
ASYNC-V5
```

失败均来自真实模型输出、语义签名或行为期望不匹配。失败类型汇总：

```text
intent mismatch = 37
missing claim signature = 35
unexpected claim signature = 35
claim count mismatch = 6
```

其中大量 missing / unexpected claim signature 来自 evidence quote 宽度漂移：claim 语义可对应，但 quote 与 golden quote 不完全一致。该问题需要改进评估口径，不应通过放宽 quote gate 解决。

延迟概览：

```text
experiment latency sum = 1,520,737 ms
median = 62,919 ms
p95 = 100,776 ms
max = 167,263 ms
```

说明当前 V5-T1 微型富化链路在真实模型下仍显著偏慢；即使按需富化降低了 T2 成本，也没有达到可用延迟目标。

跨实验重复观察：

```text
D 样本 claim_count 始终为 10；
但 projection_ready_count 在 2 / 9 / 10 之间漂移；
blocked_count 在 0 / 1 / 8 之间漂移。

E 样本 claim_count 始终为 6；
projection_ready_count 在 0 / 1 之间漂移；
blocked_count 在 3 / 4 之间漂移。
```

结论：

```text
ThinUserClaim 覆盖率较稳定；
富化结果和 gate 状态不稳定；
V5 尚未达到重复稳定性准入。
```

### 5.3 关键远程观察

#### 1. Thin schema 能覆盖共享断言

D 样本可输出 10 条 thin claim：

```text
claim_count = 10
expected_claim_count = 10
```

说明薄声明可以逐项展开并列 denied / normal，不依赖 expected count。

但模型有时将 evidence quote 切成更窄片段，例如：

```text
没有呕吐
没有干呕
没有反流
```

而非使用完整共享断言范围。quote 本身可回指原文，但与 golden quote 不一致，导致 semantic signature mismatch。

#### 2. 言语行为明显优于 V4 医学断言 enum

D 样本稳定保持：

```text
没有呕吐 → denies
精神正常 → reports_normal
```

未再出现 V4 的：

```text
没有呕吐 → absent
精神正常 → present
```

这是 V5 的重要语义进展。

#### 3. 独立意图路由仍不稳定

`answer_now` 在简单控制组可通过，但在长输入中可能输出：

```text
answer_now = false
fact_path_required = false
```

而原文包含：

```text
请先根据现有信息给阶段建议
```

同时，`fact_path_required` 被模型解释为“是否需要继续澄清”，而非“当前输入是否包含事实声明”。因此 D / E / M 等事实样本也可能输出 false。

结论：

```text
intent 独立路由方向正确；
当前 fact_path_required 语义定义不足；
answer_now 仍未达到准入。
```

一次 `v5-thin-dev-20260826-2` prompt 诊断尝试未能改善，报告位于：

```text
.data/evaluations/input-preprocessing-v5-remote-intent-contract-v2/input-preprocessing-v5-aba113d3ba09.json
```

SHA-256：

```text
b7208538faf4f9ac0e69bde584156004c99e81a06db590c0742e3241b82a2e75
```

该诊断版本未采用为默认 prompt，当前默认仍为 `v5-thin-dev-20260826-1`。

#### 4. 事件参与者严重漂移，但 gate 能阻断

E 样本中模型曾输出词表外引用：

```text
new_cat_food
it
医生
主人
罐头
```

并出现角色颠倒：

```text
罐头 → action_agent
主人 → action_recipient / experiencer
```

`v5_subject_participant` gate 能将这些 claim 阻断或送 review，但事件角色质量未达消费标准。

#### 5. canonical target quote 能确认部分概念，但 under-confirmation 仍明显

有效确认包括：

```text
呕吐 → vomiting
干呕 → retching
反流 → regurgitation
流涎 → drooling
舔唇 → lip_smacking
精神 → mental_status
食欲 → appetite
饮水 → water_intake
血便 / 黑便 → bloody_stool
软便 → soft_stool
```

但长输入和动作场景仍出现：

```text
整体精神还行 → not_found
吃喝没有明显变化 → not_found
换新狗粮 → not_found
另一只猫呕吐 → not_found
```

说明 direct target quote recall 有改善，但词表、subject/type 约束和候选选择仍不足。

#### 6. temporal / measurement enrichment 保守但过度 unresolved

典型输出：

```text
temporal_quote = 前天开始
relation = unstructured
value = 前天开始
precision = unresolved
```

以及：

```text
measurement_quote = 一天一次
relation = unstructured
value = 一天一次
precision = unresolved
```

这避免了过度精确化，但导致大量 projection review。时间与度量富化质量仍未达到生产准入。

### 5.4 T0 / T1 / T2 成本与质量对照

完整远程矩阵中 `POLICY-ENRICH-T0 / T1 / T2` 的核心数据：

| 样本 | Variant | model calls | extraction latency | enrichment latency | projection ready | blocked |
|---|---|---:|---:|---:|---:|---:|
| D shared scope | T0 | 2 | 20078 ms | 0 ms | 0 | 8 |
| D shared scope | T1 | 22 | 19970 ms | 33319 ms | 2 | 8 |
| D shared scope | T2 | 52 | 19907 ms | 80397 ms | 0 | 9 |
| E event roles | T0 | 2 | 14432 ms | 0 ms | 0 | 0 |
| E event roles | T1 | 19 | 13646 ms | 33853 ms | 1 | 3 |
| E event roles | T2 | 32 | 14143 ms | 52808 ms | 0 | 4 |

同一 D 样本在其他 T1 实验中的 `projection_ready_count` 曾达到 9 或 10，说明富化输出本身漂移，而不是稳定质量上限。

T1 的调用数和延迟显著低于 T2，但语义质量没有达到可消费标准。

结论：

```text
策略驱动富化在成本上有效；
V5-T1 优于 V5-T2 的成本；
V5-T1 质量未证明优于 V4 / V3；
当前主要瓶颈是意图路由、事件角色、canonical under-confirmation 和时间 / 度量富化。
```

### 5.5 安全边界

权威远程完整矩阵中保持：

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

## 6. 准入结论

当前结果不允许：

1. 生产消费问诊事实；
2. 生产消费 clinical safety projection;
3. 接入 clinical safety evaluator;
4. 接入 `VetOrchestrator`;
5. 将 file-backed experiment queue 用于生产；
6. 将 ideal control 作为 fallback;
7. 直接灰度 `answer_now`;
8. 修改回答充分性策略掩盖上游失败；
9. 放宽 quote gate、candidate audit 或 claim graph 状态边界。

允许继续：

1. report-only shadow;
2. deterministic gate 回归；
3. 远程真实模型 exploratory / confirmatory 实验；
4. V5-T0 / T1 / T2 成本与质量对照；
5. held-out 真实模型验证；
6. 生产级异步 worker 设计。

## 7. 复现命令

本地 development ideal control：

```bash
uv run python -m vet_agent.input_preprocessing.v5_experiments \
  --mode ideal \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v5-local
```

本地 held-out ideal confirmatory：

```bash
uv run python -m vet_agent.input_preprocessing.v5_experiments \
  --matrix tests/fixtures/input_preprocessing/fifth_round_thin_held_out_matrix.json \
  --mode ideal
```

远程 exploratory shadow：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
scripts/integration/run-input-preprocessing-v5-experiment-smoke.sh \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v5-experiments-remote-isolated
```

远程 held-out confirmatory 必须冻结模型、prompt、schema、policy、vocabulary、gate 和 fixture 版本后执行。

## 附录 A：历史远程运行与 Runner 有效性说明

本附录只保留最小审计信息，用于说明历史报告为何不作为架构结论权威；不参与第 5 章的架构判断。

| 已修复问题 | 对旧运行的影响 | 处理结果 |
|---|---|---|
| temporal payload 直接序列化 `tzinfo` | 部分 temporal 实验 runner error | 改为时区字符串后重跑完整矩阵 |
| unresolved quote anchor `occurrence=0` 与契约冲突 | 非法 quote 可能中断 runner | 契约允许 unresolved occurrence 为 0 |
| shadow mode 下 NEG 走真实模型 | 负例实验口径无效 | NEG 固定走 deterministic mutation |
| 阶段耗时重复统计 | 延迟拆分不可靠 | 拆分计时边界，以权威报告为准 |

被 superseded 的历史报告：

```text
.data/evaluations/input-preprocessing-v5-remote-exploratory/input-preprocessing-v5-7208310a48c9.json
sha256 = 634f5771d4ab6f7f654440fa41879c98635b20c1d566326952c5938b62132096

.data/evaluations/input-preprocessing-v5-remote-exploratory-post-fix/input-preprocessing-v5-31b70b50c87b.json
sha256 = 7bb9966ae33aecf17c0620b3ca707f5c6ace82a7ca0865bae4076f98aa3789d5
```

另有一次 intent contract prompt 诊断：

```text
.data/evaluations/input-preprocessing-v5-remote-intent-contract-v2/input-preprocessing-v5-aba113d3ba09.json
sha256 = b7208538faf4f9ac0e69bde584156004c99e81a06db590c0742e3241b82a2e75
```

该诊断用于追溯 `fact_path_required` 语义漂移，不是默认 prompt 版本，也不改变权威矩阵结论。
