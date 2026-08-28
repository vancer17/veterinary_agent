<!--
=============================================================================
文件: input-preprocessing-v7-core-attribution-microbench-change-summary.md
作用: 记录 V7 专项归因 core microbench 的实现、本地 ideal control、远程真实模型
      结果、失败归因和下一轮准入边界。
范围: 覆盖 intent 单项识别、golden quote 选择、minimal thin extraction、
      independent relation classifier、golden target canonical recall 和
      candidate-only participant selection。
说明: 本文只沉淀工程结论；不改变生产问诊、临床安全召回、required_context
      或 OPA 裁决。
维护: 当 V7 micro contract、fixture、prompt、gate 或远程报告结论变化时同步更新。
=============================================================================
-->

# 输入前置预处理 V7 Core Attribution Microbench 变更总结

> **文档状态**：V7 Phase 0 与 Phase 1 core microbench 已完成；未达到生产消费准入
>
> **结论**：专项归因实验有效地区分了组件能力。`RELATION-GOLDEN` 与
> `CAN-GOLDEN-DIRECT` 通过；intent 单项识别、quote 子引述选择、minimal thin
> extraction 和 participant selection 未通过。V7 不能进入 semantic repair 大矩阵，
> 更不能进入 integration。

## 1. 实验目标

本轮只回答 V6 后遗留的单组件问题：

1. `answer_now` 单独识别是否稳定；
2. fact statement 是否仍与澄清需求混淆；
3. question 是否污染其他 intent；
4. 给定 golden evidence 后能否选择 target / relation / subject / temporal / measurement quote；
5. 去掉 relation class 后 minimal thin extraction 是否更稳定；
6. independent relation classifier 能否区分四类 relation；
7. golden target quote 能否召回 canonical 候选；
8. candidate-only participant 能否在 golden action claim 上稳定选择。

本轮不执行：

```text
INTENT-HYBRID
RELATION-LIVE
AGGREGATE decomposition
planner / cost matrix
integration
confirmatory held-out real model
```

## 2. 实现范围

### 2.1 V7 micro contracts

新增：

1. `V7IntentBinaryRaw`;
2. `V7IntentBatchRawOutput`;
3. `V7QuoteSelectionRaw`;
4. `V7QuoteSelectionRawOutput`;
5. `V7ThinUserClaimRaw`;
6. `V7ThinExtractionRawOutput`;
7. `V7RelationClassificationRaw`;
8. `V7RelationRawOutput`;
9. `V7ParticipantSelectionRaw`;
10. `V7ParticipantRawOutput`;
11. `V7AttributionCode`;
12. `V7RunUnitKey` / `V7RunCache`。

Minimal ThinUserClaim 不输出：

```text
relation class
canonical_id
selected_candidate_id
canonical_surface
normalized temporal semantics
normalized measurement semantics
action_agent
action_recipient
action_object
aggregate child targets
```

### 2.2 Run cache 与 retry 归因

run cache key 覆盖：

```text
experiment_id
model
prompt version
schema version
input digest
TurnContext digest
response model
```

报告分别输出：

```text
cache hit
model call count
attempt count
first attempt status
first attempt error
latency
```

只允许同契约有限 retry；语义错误不重试。

### 2.3 Fixture

新增：

```text
tests/fixtures/input_preprocessing/seventh_round_attribution_matrix.json
tests/fixtures/input_preprocessing/seventh_round_attribution_held_out.json
```

development 覆盖：

```text
answer-now 简单 / 长输入 / 口语表达
fact-only、clarification、question-only、answer-now + fact 并行
golden quote 子引述
共享 denied / normal thin claims
时间 / 度量 / no-change 混合长输入
absolute / no-change / change / unclear relation
canonical golden target
user / medical actor / caregiver / other-pet action participant
```

held-out 使用同类现象但不同表达，本轮只执行本地 ideal control，不参与远程调参。

## 3. 本地 Ideal Control

Development：

```text
experiment_count = 8
passed = 8
failed = 0
```

Held-out：

```text
experiment_count = 8
passed = 8
failed = 0
```

本地确定性测试：

```text
tests/test_input_preprocessing_v7.py: 6 passed
```

新增本地测试覆盖：

1. development / held-out ideal control；
2. minimal thin contract 不回补 relation / canonical 字段；
3. run cache 防重复模型调用；
4. 非法 intent quote 被阻断并归因；
5. 发明 participant 被阻断并归因。

Ideal control 不是生产结论，也不能作为 fallback。

## 4. 远程真实模型 Core Microbench

### 4.1 权威报告

```text
.data/evaluations/input-preprocessing-v7-remote-core-final/input-preprocessing-v7-348c9e7e0a07.json
```

报告时间：

```text
2026-08-27T14:49:42.850902+08:00
```

SHA-256：

```text
be5e3f5a4b9be8e0441da15c5fde09db02f864b774b007070cfbbd334a02fcbf
```

固定版本：

```text
model = qwen-plus
prompt_version = v7-attribution-dev-20260827-1
schema_version = v7-attribution-1
gate_version = v7-attribution-gates-dev-20260827-1
vocabulary_version = input-preprocessing-dev-v3
fixture_sha256 = db2b661d51cf02df83181c5a86dff47b7baaf7e5067f275972b0245a1c9b8854
analyzer_isolation = per-experiment-fresh-qwen-client-shared-run-cache
```

权威报告使用 run cache：

```text
cache_hit_count = 8
cache_miss_count = 0
total_model_call_count = 0
```

这说明 intent、quote、thin、relation 和 participant 的重复输入没有再次调用模型；缓存命中后的延迟不能被解释为冷启动延迟。冷调用成本仅保留在历史开发报告中，不作为本文权威指标。

### 4.2 总览

```text
experiment_count = 8
passed = 2
failed = 6
```

| 实验 | 结果 | 主要观察 |
|---|---:|---|
| INTENT-ANSWER-NOW | failed | recall 达 1.0，但 quote valid 为 0，且两个负例误报 |
| INTENT-FACT-DETECT | failed | 4 / 5 个 unit 缺失，fact 与 question / clarification 仍污染 |
| INTENT-QUESTION | failed | accuracy 0.6，recall 0.333，question 与 answer_now / fact 并行仍不稳 |
| QUOTE-GOLDEN-SELECT | failed | quote selection accuracy 0；模型把整句 evidence 当 target |
| THIN-LIVE-MIN | failed | claim recall 0.357；quote valid 为 0，target 与辅助 quote 漂移 |
| RELATION-GOLDEN | passed | 8 / 8 relation 正确，四类 relation 完全可分 |
| CAN-GOLDEN-DIRECT | passed | 8 / 8 golden target 候选召回，无 filter miss |
| PART-GOLDEN | failed | agent / recipient 全为空但 resolution_status=resolved |

## 5. 关键归因

### 5.1 Intent 单项 classifier 仍未通过

#### INTENT-ANSWER-NOW

```text
accuracy = 0.6
recall = 1.0
false_positive_count = 2
quote_valid_rate = 0.0
long_input_pass_rate = 0.0
```

模型能识别所有 positive answer-now，但：

1. true 输出缺少可用 evidence quote；
2. fact-only 与 question-only 被误报为 answer-now。

归因：

```text
intent_model_error
quote_selector_error
```

#### INTENT-FACT-DETECT

模型只返回 1 个 result，缺少 4 个 expected unit；同时把 question-only 判为 fact。

归因：

```text
intent_contract_contamination
intent_model_error
```

#### INTENT-QUESTION

```text
accuracy = 0.6
recall = 0.333333
quote_valid_rate = 0.8
```

能正确排除 answer-now 和 fact-only，但对普通 question 的 recall 低。

结论：

> answer-now 单独分类器、fact-only detector、question-only detector 均未达到进入 hybrid 的条件。

### 5.2 Quote selector 把 evidence 当 target

`QUOTE-GOLDEN-SELECT` 中 5 个 unit 全部失败。

典型输出：

```text
target = 没有呕吐、干呕、反流、流涎或舔唇
subject = 没有呕吐、干呕、反流、流涎或舔唇
```

而期望：

```text
target = 呕吐
subject evidence = 没有呕吐、干呕、反流、流涎或舔唇
```

模型在给定 golden evidence 后仍倾向复制整句，而不是选择目标概念所在的窄 quote；时间、度量和 relation 子引述也大量缺失。

归因：

```text
quote_selector_error
```

### 5.3 Minimal thin extraction 覆盖下降

共享声明样本输出 10 条 claim，但只有 5 条与 expected semantic identity 匹配；在辅助 quote availability 纳入完整 gate 后，matched claim 的完整 quote valid rate 为 0：

```text
claim_recall = 0.357143
claim_precision = 0.454545
quote_valid_rate = 0.0
```

主要问题：

1. 把目标写成省略形式，例如 `精神...都正常`；
2. 该省略形式不是原文 quote，被 quote gate 阻断；
3. `没有呕吐` 被放入 `temporal_quote`；
4. 长输入只输出 1 条 claim，把换粮 target 绑定到软便 evidence，并丢失 relation / measurement quote。

归因：

```text
quote_extraction_error
target_containment_error
relation_quote_missing
```

### 5.4 Relation classifier 通过

```text
relation_accuracy = 1.0
```

四类均正确：

```text
absolute_status
no_change
change
unclear
```

结论：

> relation 应保持独立 classifier；这是本轮可进入后续组合的 winner。

但尚未执行 `RELATION-LIVE`，不能证明它能消费真实 thin claim 的 relation quote。

### 5.5 Canonical direct recall 通过

```text
candidate_recall = 1.0
filtered_miss_count = 0
no_candidate_count = 0
```

覆盖：

```text
vomiting
bloody_stool
soft_stool
mental_status
appetite
water_intake
diet_change
stool_frequency
```

结论：

> golden target quote + coarse type 的直接召回能力有效；这是本轮第二个 winner。

但尚未验证 live target quote、candidate selector 和 projection-stage subject exclusion。

### 5.6 Candidate-only participant 未通过

四个 action claim 均输出：

```text
action_agent_selected_candidate = null
action_recipient_selected_candidate = null
object_mention = ""
resolution_status = resolved
```

没有发明实体，说明 candidate-only gate 有效；但模型在 golden action claim 上仍不能选择角色。

归因：

```text
participant_role_error
```

`resolution_status=resolved` 与空 participant 冲突，应在后续 gate 中优先阻断。

## 6. 架构判断

### 6.1 已证明

1. V7 Attribution Suite 可以逐组件归因，而不是只给整体 pass/fail；
2. run cache 能消除重复模型调用，并显式区分 cache hit 与 model call；
3. independent relation classifier 是有效方向；
4. golden target quote 的 canonical direct recall 是有效方向；
5. candidate-only participant 能阻断自由字符串实体；
6. quote gate 能阻断省略式伪 quote；
7. 本地 ideal control 与远程失败可以明确区分。

### 6.2 未证明

1. answer-now singleton 可稳定输出 evidence quote；
2. fact detector 能稳定批量覆盖所有 unit；
3. question detector 与 answer-now / fact 并行稳定；
4. golden quote selector 能选择窄 target；
5. minimal thin extraction 在去掉 relation class 后稳定；
6. candidate-only participant 能在 golden claim 上选择角色；
7. winner relation / canonical 能与 live claim 集成；
8. V7 质量优于 V6；
9. 可进入 semantic repair 或 integration。

## 7. 准入结论

当前不允许：

1. 执行 `INTENT-HYBRID`；
2. 执行 `RELATION-LIVE`；
3. 执行 aggregate decomposition；
4. 执行 planner / cost matrix；
5. 执行 V7 integration；
6. 执行 held-out 远程确认性实验；
7. 接入 `VetOrchestrator`；
8. 写任何业务状态；
9. 触发 clinical safety evaluator / pgvector / required_context / OPA。

允许继续：

```text
answer-now evidence quote 专项修复
fact detector batch coverage 专项修复
question detector 专项修复
quote target selector 专项修复
minimal thin quote containment 专项修复
participant resolved-non-empty contract 专项修复
```

只有对应 core microbench 通过后，才允许进入对应 semantic repair 实验。

## 8. 下一轮最小修复

1. **Intent quote 输出**：answer-now true 时必须携带 quote；可用更小 schema 或 span selector，但不得由 Python 关键词补 quote。
2. **Fact detector coverage**：批量输出必须覆盖全部 unit；否则逐 unit singleton 对照，先定位 batch schema 与模型能力。
3. **Question detector**：独立验证普通 question、answer-now、fact-only 并行状态。
4. **Quote target selector**：明确 target 是 evidence 内的窄概念 quote，而不是整句 evidence；失败时输出 unresolved。
5. **Thin claim target 禁止省略号**：target / evidence / temporal / relation quote 必须逐字回指原文。
6. **Participant resolved contract**：`resolution_status=resolved` 时 selected candidate 不得为空；空值应输出 unresolved。
7. **Relation winner 保持冻结**：不得因其他组件失败继续调整 relation classifier。
8. **Canonical winner 保持冻结**：不得盲目调整词表或阈值。

## 9. 复现命令

本地 development ideal control：

```bash
uv run python -m vet_agent.input_preprocessing.v7_experiments \
  --mode ideal \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v7-local
```

本地 held-out ideal control：

```bash
uv run python -m vet_agent.input_preprocessing.v7_experiments \
  --matrix tests/fixtures/input_preprocessing/seventh_round_attribution_held_out.json \
  --mode ideal \
  --no-cache
```

远程 core microbench：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
scripts/integration/run-input-preprocessing-v7-core-microbench.sh \
  --cache-path .data/cache/input-preprocessing-v7/core-run-cache.json \
  --output-dir .data/evaluations/input-preprocessing-v7-remote-core
```

真实模型依赖不可用时必须失败，不得回退关键词、宽松 JSON 或本地医学规则。
