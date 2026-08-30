<!--
=============================================================================
文件: input-preprocessing-v4-fourth-round-shadow-experiment-change-summary.md
作用: 记录输入前置预处理 V4 引述锚定与扁平抽取实验的实现、结果和准入结论。
范围: 覆盖 FlatObservation、保守 quote 治理、TurnContext 主体约束、
      constrained canonical linking、粗粒度类型 gate、report-only 领域投影、
      development / held-out fixture、持久化实验队列和远程真实模型 shadow。
说明: 本文只沉淀工程结论；不改变生产问诊、临床安全召回、required_context
      或 OPA 裁决。
维护: 当 V4 契约、实验矩阵、远程报告结论或迁移准入条件变化时同步更新。
=============================================================================
-->

# 输入前置预处理 V4 第四轮 Shadow 实验变更总结

> **文档状态**：第四轮实验实现、本地 ideal control 与远程 exploratory shadow 已完成；结果未达到生产消费准入
>
> **结论**：V4 的“引述锚定 + 扁平抽取 + 确定性治理”显著改善了 schema 遵循率、quote 可回指性和共享断言覆盖，但真实模型仍存在断言语义漂移、canonical surface 翻译、事件参与者缺失和 under-confirmation。V4 不能替代 V3 成为主线，也不能接入 `VetOrchestrator`。

## 1. 实验目标

第四轮实验验证 V4 是否优于继续加厚 V3 多级流水线：

1. 单层 `FlatObservation` schema 是否比 V3 嵌套契约更稳定；
2. `evidence_quote` / `target_quote` 是否能阻断幻觉事实；
3. 没有 `expected_evidence_count` 时，共享断言能否逐项输出；
4. 扁平字段能否稳定绑定主体和事件参与者；
5. normal / denied / absent / present / historical / hypothetical 是否保持分离；
6. `canonical_surface → candidates → selected_candidate` 是否能同时避免假确认和 under-confirmation；
7. 粗粒度类型矩阵能否阻止事件 / 状态错配；
8. 口语脏数据、多轮省略和长输入是否稳健；
9. 负例是否全部 blocking；
10. 异步 shadow 是否保持持久化、有界、死信和失败隔离；
11. 临床安全是否继续 report-only。

## 2. 实现范围

### 2.1 V4 契约

新增：

1. `FlatObservationRaw`;
2. `FlatExtractionRawOutput`;
3. `V4QuoteAnchor`;
4. `V4CandidateSet`;
5. `GovernedFlatObservation`;
6. `V4InputAnalysisResult`;
7. `V4QualityGateResult`;
8. `V4TurnContext`。

每条模型 observation 携带：

```text
evidence_quote
target_quote
event_or_state_text
semantic_class
assertion
certainty
subject reference / status / candidates
action_agent_reference
action_recipient_reference
experiencer_reference
object_mention
temporal_quote / semantics
measurement_quote / semantics
canonical_surface
confidence
```

模型不输出：

```text
canonical_id
selected_candidate_id
profile_expected_fact_count
expected_evidence_count
```

### 2.2 保守 quote 治理

新增确定性 quote anchor 解析：

```text
原文保留
→ 保守 normalization
→ exact substring / occurrence 解析
→ target containment
→ temporal / measurement quote containment
```

允许：

```text
trim
空白移除
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

无法回指原文的 observation 会被 blocking gate 阻断。

### 2.3 Constrained canonical linking

V4 保留候选引用边界：

```text
canonical_surface
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

本轮新增版本化词表快照：

```text
assets/evaluations/input_preprocessing_canonical_vocabulary.v4.json
```

该快照为 `bloody_stool` 补充已审核 alias `黑便`，版本为 `input-preprocessing-dev-v2`。这是版本化 alias 治理，不是关键词规则。

### 2.4 质量门禁与投影

新增或强化：

1. `v4_turn_context`;
2. `v4_flat_schema`;
3. `v4_quote_anchor`;
4. `v4_subject_participant`;
5. `v4_canonical_registry`;
6. `v4_type_compatibility`;
7. `v4_assertion_consistency`;
8. `v4_duplicate_observation`;
9. `v4_suspicious_empty`;
10. `v4_projection_boundary`。

新增 report-only 问诊投影和临床安全结构投影。硬边界：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

### 2.5 异步实验队列

新增 `FileAsyncShadowQueueV4`，验证有界队列、显式 queue full、snapshot 持久化、worker claim / complete / fail、dead letter、trace 持久化、不写业务状态和不触发临床安全 evaluator。

该队列仍是实验实现，不是生产 API shadow worker。

## 3. Fixture 与实验矩阵

新增：

```text
tests/fixtures/input_preprocessing/fourth_round_flat_shadow_matrix.json
tests/fixtures/input_preprocessing/fourth_round_flat_held_out_matrix.json
```

development set 覆盖并列 denied / normal、长输入、用户动作、医疗提供者动作、其他宠物症状、hypothetical、unmapped mention、answer-now、口语脏数据、多轮省略和无明确事实输入。

held-out set 覆盖顺序变化的共享断言、狗粮场景、护士 / 家人动作、另一只狗症状、不同口语脏数据和不同多轮省略。

development 矩阵包含 17 组实验：

| 实验 | 目标 |
|---|---|
| FLAT-SCHEMA | 单层扁平 schema 稳定性 |
| QUOTE-GATE | 引述锚定与幻觉阻断 |
| SHARED-COVERAGE | 无 expected count 的逐项覆盖 |
| SUBJECT-ROLE | 主体与参与者绑定 |
| ASSERT-TEMPORAL | 断言、时间与度量保留 |
| CAN-RECALL | canonical 候选召回 |
| CAN-SELECT | selected candidate 与 under-confirmation |
| CAN-TYPE | 粗粒度类型兼容 |
| DIRTY-INPUT | 口语脏数据 |
| MULTI-TURN | 多轮省略 |
| ANSWER-NOW | 控制意图 |
| EMPTY-BASE | 空基线 |
| NEG | 确定性负例阻断 |
| REP | 重复稳定性 |
| ASYNC | 持久化队列与失败隔离 |
| DOMAIN-PROJECTION | 问诊 report-only 投影 |
| CS | 临床安全 report-only 对比 |

## 4. 本地 Ideal Control 结果

Development ideal control：

```text
experiment_count = 17
passed = 17
failed = 0
```

Held-out ideal confirmatory：

```text
experiment_count = 15
passed = 15
failed = 0
```

本地全量测试：

```text
248 passed
43 skipped
```

`src/vet_agent/input_preprocessing` 全量 mypy 通过。

Ideal control 不是生产结论，也不能作为 fallback。

## 5. 远程真实模型 Shadow 结果

### 5.1 执行方式

1. 通过 SSH 隧道访问远程 LiteLLM；
2. 使用 `qwen-plus`；
3. 每个 case 使用独立 analyzer / Qwen client；
4. 共享版本化 candidate retriever；
5. development set；
6. exploratory phase；
7. `repeat_override=1`；
8. clinical baseline 仅做结构化对比；
9. 不进入 evaluator / pgvector / required_context / OPA。

权威报告：

```text
.data/evaluations/input-preprocessing-v4-experiments-remote-isolated-authoritative/input-preprocessing-v4-daa53eaf9fa2.json
```

报告时间：

```text
2026-08-25T17:18:47.050620+08:00
```

SHA-256：

```text
20acf5604d29353bebd384e79bd35713b445a28b8f05fc56de17314a9e9270cb
```

固定版本：

```text
model = qwen-plus
prompt_version = v4-flat-dev-20260825-2
schema_version = v4-flat-raw
vocabulary_version = input-preprocessing-dev-v2
candidate_recall_version = input-preprocessing-dev-v2:top-8:min-0.72
quote_normalization_version = v4-conservative-20260825-1
fixture_version = 787ab1b124af782d58d2f6091112ff4b8ebee2fe97e18d8a312695dda5272448
gate_version = v4-gates-20260825-1
analyzer isolation = per-case-fresh-qwen-client-shared-candidate-retriever
```

### 5.2 总览

```text
experiment_count = 17
passed = 3
failed = 14
```

| 实验 | 结果 | 主要观察 |
|---|---:|---|
| FLAT_SCHEMA_D4 | failed | schema valid 和 quote valid 达到 1.0，但断言语义 mismatch |
| QUOTE_GATE_D4 | failed | quote 有效，但语义结果未达 exact match |
| SHARED_COVERAGE_D4 | failed | D 覆盖 10/10；长输入覆盖 15/15，但语义与类型 gate 失败 |
| SUBJECT_ROLE_E4 | failed | action participants 缺失，事件 / 状态角色不完整 |
| ASSERT_TEMPORAL_D4 | failed | denied 被输出为 absent，normal 被输出为 present |
| CAN_RECALL_V4 | failed | D 覆盖可用，但 dirty / action 表面形式漂移导致 under-confirmation |
| CAN_SELECT_D4 | failed | 候选引用安全，但语义 mismatch |
| CAN_TYPE_E4 | failed | action 缺 agent / recipient / object，被 gate 阻断 |
| DIRTY_INPUT_X4 | failed | quote 有效，但 canonical surface 翻译与断言漂移 |
| MULTI_TURN_M4 | failed | “没有”可继承上一轮目标，但“精神正常”被 present 化 |
| ANSWER_NOW_C4 | failed | 模型将控制意图混入 fact observation 并被 gate 阻断 |
| EMPTY_BASE_B4 | passed | 无明确事实输入未产生伪事实 |
| NEG_D4 | passed | 10 / 10 负例全部按预期 blocking |
| ASYNC_D4 | passed | queue full、dead letter、trace 和失败隔离有效 |
| REP_D4 | failed | 存在 gate blocked 或 semantic mismatch，无法进入稳定性准入 |
| DOMAIN_PROJECTION_D4 | failed | 上游语义未达标，投影只能报告阻断或低质量 |
| CS_D4 | failed | 上游 gate 阻断；evaluator / OPA 未调用 |

## 6. 关键观察

### 6.1 V4 已证明有效

D 样本真实模型输出：

```text
schema_valid_count = 1
observation_count = 10
expected_observation_count = 10
observation_recall = 1.0
observation_precision = 1.0
quote_valid_rate = 1.0
subject_wrong_binding_count = 0
normal_as_denied_count = 0
denied_as_present_count = 0
confirmed_without_candidates_count = 0
invented_canonical_count = 0
```

这说明：

1. 单层扁平 schema 更容易遵循；
2. quote 能回指原文；
3. 并列断言可以不依赖 expected count 展开；
4. TurnContext 主体约束有效；
5. selected candidate 能防止假确认；
6. unmapped 可以进入 review；
7. 负例 gate 有效；
8. 异步实验队列具备失败隔离语义。

`ZZZ表现` 正确输出：

```text
mapping_status = not_found
canonical_id = null
review_required = true
```

### 6.2 V4 仍未解决

#### 1. 断言语义漂移

D 样本中：

```text
没有呕吐 → absent
精神正常 → present
食欲正常 → present
```

期望是：

```text
没有呕吐 → denied
精神正常 → normal
```

#### 2. canonical surface 翻译导致 under-confirmation

脏输入中出现：

```text
血便 → hematochezia
精神还行 → normal energy level
换粮 → diet change
```

导致候选召回缺失或语义签名不匹配。

#### 3. action participants 缺失

模型输出动作事件，但没有稳定填写：

```text
action_agent_reference
action_recipient_reference
object_mention
```

因此被 `v4_type_compatibility` blocking。

#### 4. answer_now 与 fact observation 混淆

本轮 answer-now 分支失败，模型将控制意图或相关请求变成 fact observation。

结合第三轮结果，`answer_now` 仍应保留独立 Turn Intent Analyzer，而不是依赖完整 flat extraction。

#### 5. 长输入 event / measurement 绑定不足

长输入最后一段：

```text
前天开始换新狗粮，这两天大便偏软，一天一次。
```

模型用同一 quote 输出换粮、软便和频率，但事件主体与 measurement 关联不完整。

## 7. 架构判断

### 7.1 已证明

1. FlatObservation 能降低 schema 崩溃率；
2. evidence / target quote 可以维系原文证据链；
3. 无 expected count 时，共享断言可以逐项输出；
4. selected candidate 可以防止假确认；
5. unmapped 可以进入 review；
6. 负例 gate 有效；
7. 异步实验队列具备失败隔离语义；
8. clinical safety report-only 边界可执行。

### 7.2 未证明

1. 真实模型能稳定区分 denied / absent / normal / present；
2. canonical surface 能保持原文语言并稳定召回；
3. action participants 能稳定输出；
4. answer_now 能在 flat extraction 中独立；
5. 长输入 event / temporal / measurement 绑定可靠；
6. development set 3 次稳定；
7. held-out 真实模型确认性通过；
8. V4 语义质量和成本优于 V3；
9. V4 可接入 `VetOrchestrator`。

## 8. 准入结论

当前结果不允许：

1. 生产消费问诊事实；
2. 生产消费 clinical safety projection；
3. 接入 clinical safety evaluator；
4. 接入 `VetOrchestrator`；
5. 将 file-backed experiment queue 用于生产；
6. 将 ideal control 作为 fallback；
7. 直接灰度 `answer_now`；
8. 修改回答充分性策略掩盖上游失败；
9. 放宽 quote gate、candidate audit 或类型兼容 gate。

允许继续：

1. report-only shadow；
2. deterministic gate 回归；
3. assertion classifier / verifier 专项实验；
4. canonical surface 与 target quote 对照实验；
5. participant binding 专项实验；
6. 独立 Turn Intent Analyzer 路径；
7. 生产级异步 worker 设计。

## 9. 下一轮修复优先级

1. **断言与存在性分离**：显式区分 assertion direction 与 existence polarity，确保 `没有呕吐 → denied`、`精神正常 → normal`。
2. **canonical surface 对照**：比较 model surface、target quote 直接召回、双查询和 targeted verifier，降低 under-confirmation。
3. **participant binding 对照**：比较 flat 直接输出、专项 action analyzer 和 targeted verifier。
4. **answer_now 独立路径**：保留独立 Turn Intent Analyzer，等待 API shadow 准入后再评估灰度。
5. **重复稳定性**：development set 单次通过后，每样本至少 3 次 signature 一致；held-out 不参与探索调参。

## 10. 复现命令

本地 ideal control：

```bash
uv run python -m vet_agent.input_preprocessing.v4_experiments \
  --mode ideal
```

远程 exploratory shadow：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
INPUT_PREPROCESSING_WITH_CLINICAL_BASELINE=true \
INPUT_PREPROCESSING_V4_PROMPT_VERSION=v4-flat-dev-20260825-2 \
scripts/integration/run-input-preprocessing-v4-experiment-smoke.sh \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v4-experiments-remote-isolated
```

held-out confirmatory：

```bash
uv run python -m vet_agent.input_preprocessing.v4_experiments \
  --matrix tests/fixtures/input_preprocessing/fourth_round_flat_held_out_matrix.json \
  --mode shadow \
  --phase confirmatory
```

真实模型实验依赖远程 LiteLLM。依赖不可用时必须失败，不得回退关键词、宽松 JSON 或本地规则。
