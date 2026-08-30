<!--
=============================================================================
文件: input-preprocessing-v9-attribution-experiment-plan.md
作用: 定义第八轮 blocker 后的第九轮专项归因实验矩阵与实现边界。
范围: 覆盖 gold/evaluator integrity、GLiNER label/threshold、macro ideal-pool、
      relation/canonical/participant gold injection、adapter 冷启动与重复归因。
说明: V9 只做根因归因，不恢复多级微任务流水线，不解除 V8 phase gate。
维护: 当 V9 实验矩阵、报告契约、fixture 定位规则或结论变化时更新。
=============================================================================
-->

# Input preprocessing V9 专项归因实验计划

> **文档状态**：历史实验计划；V9 已完成并由
> [input-preprocessing-v9-attribution-change-summary.md](input-preprocessing-v9-attribution-change-summary.md)
> 记录权威结果。当前执行方向见
> [input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md](input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md)。
>
> **历史定位**：V9 是 V8 blocker 的专项归因轮次，不是新的运行时架构，也不是生产消费准入。
>
> **核心纪律**：一次实验只改变一个变量；所有实验均使用 development fixture；held-out 保持冻结；gold injection 只用于归因，不得成为生产 fallback。

## 1. 问题分解

V8 快速验证显示至少三层 blocker 叠加：

```text
1. live GLiNER span pool 质量不足
2. ideal golden span pool 下 macro 输出 / 绑定质量不足
3. relation / canonical / participant 下游 0 值无法判断来自上游绑定还是 winner 本身
```

因此 V9 先拆分以下问题：

| 问题 | 归因实验 | 是否调用模型 |
|---|---|---|
| fixture 是否把重复 mention 定位到第一次出现 | `ATT-GOLD-INTEGRITY` | 否 |
| label accuracy 是否被同一 boundary 多 expected label 结构性压低 | `ATT-GOLD-INTEGRITY` | 否 |
| GLiNER 是漏边界还是标签错 | `ATT-SPAN-POOL` | 本地 GLiNER |
| threshold / label prompt 哪个是主变量 | `span-label` / `span-threshold` | 本地 GLiNER |
| macro 是否依赖 role-hinted span ID 泄漏 | `ATT-MACRO-IDEAL-POOL` | qwen-plus |
| claim / act / optional binding 哪层漂移 | `ATT-MACRO-IDEAL-POOL` | qwen-plus |
| V7 relation 是否能消费 gold relation quote | `ATT-RELATION-GOLD` | shadow 模式调用 |
| V7 relation 是否受 calibration batch 上下文影响 | `relation-calibration` | qwen-plus |
| V6 canonical 是否能召回 gold target quote | `ATT-CANONICAL-GOLD` | shadow 模式调用 |
| participant resolver 是否能消费 gold mention | `ATT-PARTICIPANT-GOLD` | 否 |
| 冷启动不稳定来自 act、claim 还是 binding | `ATT-REP-DETERMINISM` | qwen-plus |
| base / Instructor / BAML 在同一 golden 输入下是否稳定 | `ATT-ADAPTER-COLD` | qwen-plus |

## 2. V9 runner

新增：

```text
src/vet_agent/input_preprocessing/v9_attribution.py
src/vet_agent/input_preprocessing/v9_experiments.py
scripts/integration/run-input-preprocessing-v9-attribution.sh
```

报告版本：

```text
v9-attribution-report-1
```

顶层报告固定包含：

```text
diagnostic_only=true
can_unblock_v8_phase=false
suite
mode
changed_variables
matrix / matrix sha256
vocabulary version
model / prompt version / schema version
cache status
reports
safety boundary
```

runner 拒绝读取文件名包含 `held_out` 的 fixture。DSPy 仍不参与本轮。

## 3. ATT-GOLD-INTEGRITY

### 3.1 owner-scoped gold pool

V8 ideal pool 曾用全文第一次出现定位 quote。对于重复的 `"它"`，这可能把后续 claim 的 participant 绑定到第一个 claim 的 `"它"`。

V9 构建 owner-scoped pool：

1. support 先按 claim owner 定位；
2. target / relation / temporal / measurement / object 优先在 owner support 内定位；
3. participant 可在 support 外定位，但选择距离该 support 最近的 mention；
4. 所有字段保留 owner、role、offset、expected label；
5. 同一 boundary 的多 expected label 不再被隐藏。

### 3.2 指标

```text
required_field_count
unique_boundary_count
wrong_occurrence_count
support_containment_violation_count
conflicting_label_boundary_count
ambiguous_occurrence_count
label_evaluable_field_count / rate
```

该报告只证明测量口径是否可信，不改变 V8 正式 gate。

## 4. ATT-SPAN-POOL

V9 对 GLiNER 增加三种通用语言 label mode：

```text
english
bilingual
descriptive
```

示例：

```text
target_mention
目标现象或事物 target mention
被讨论的目标短语，不是完整句子 target phrase
```

约束：

1. 仍是通用语言结构标签；
2. 不引入疾病、急诊风险或治疗规则；
3. adapter 将 model-facing label 映射回稳定 `V8SpanLabel`；
4. `extractor_version` 包含 profile、threshold、label mode 和模型 revision。

### 指标拆分

```text
boundary_precision / boundary_recall
near_boundary_count / near_boundary_or_exact_rate
label_accuracy_on_exact
label_evaluable_field_count
label_conflict_field_count
span_intake_error_count
per-role recall
label confusion matrix
per-field attribution:
  correct
  span_recall_miss
  span_boundary_error
  span_label_error
```

relaxed overlap 只用于归因，不用于 V8 Phase 0 正式准入。

## 5. ATT-MACRO-IDEAL-POOL

同一 owner-scoped gold pool 提供两种 ID：

```text
role-hinted: ID 含 owner / role 信息
opaque:      仅 span-000001 形式
```

macro 仍一次阅读完整输入并输出：

```text
acts
claims
span bindings
```

不得拆分为 act / claim / binding 多个模型任务。

报告拆分：

```text
raw act count / expected act count / matched act count
evidence span valid rate
raw claim count / governed claim count / expected claim count
claim precision / recall
optional binding present / correct / missing
participant mention / reference
invalid span reference / binding
```

若 role-hinted 明显优于 opaque，说明模型依赖 ID 泄漏，live 行为不可信。

## 6. Gold injection

### ATT-RELATION-GOLD

直接使用 fixture 中 owner-scoped `target_quote` 与 `relation_quote` 调用 V7 relation classifier。

若 fixture 只有 `expected_relation` 而没有 `relation_quote`，报告为：

```text
gold_relation_span_missing
```

这属于 fixture / 契约归因，不用宽松 quote 补齐。

`relation-calibration` 会把 V7 `RELATION-GOLDEN` development units 与 V8 gold relation records 放入同一批请求，并同时报告：

```text
calibration_relation_accuracy
V8 relation_accuracy
```

该诊断只用于判断 batch / prompt 上下文敏感性，不得把 calibration units 伪装成生产 fallback。

### ATT-CANONICAL-GOLD

直接使用 fixture 中 owner-scoped `target_quote` 调用 V6 canonical direct recall。

指标：

```text
candidate_recall
canonical_accuracy
under_confirmation_count
no_candidate_count
```

### ATT-PARTICIPANT-GOLD

直接使用 owner-scoped participant mention 与 TurnContext entity candidates。

指标：

```text
participant_resolution_accuracy
resolved_empty_count
```

gold injection 只能回答下游组件是否可消费正确输入，不能替代 macro，也不能进入 projection。

## 7. ATT-REP-DETERMINISM

同一 opaque gold pool、同一 prompt、同一 schema、同一 adapter 执行冷调用。

签名拆分：

```text
raw_output_stability
act_signature_stability
claim_signature_stability
binding_signature_stability
semantic_signature_stability
```

cache 必须关闭；cache replay 不能计入冷启动证据。

## 8. ATT-ADAPTER-COLD

同一 opaque gold pool、同一 unit、同一 model、同一 prompt schema 下比较：

```text
base / response_format
instructor
baml
```

每个 adapter 至少三次冷调用，报告：

```text
raw output stability
act / claim / binding signature stability
schema first attempt status
latency
retry / cache status
```

macro golden baseline 未达标时，该实验只做工具链诊断，不选择 middleware winner。

## 9. 复现命令

远程 gold integrity：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite interface \
  --mode quick
```

远程 label mode 对照：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite span-label \
  --threshold 0.3 \
  --label-mode english \
  --label-mode bilingual \
  --label-mode descriptive
```

远程 threshold 对照：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite span-threshold \
  --label-mode bilingual \
  --threshold 0.1 \
  --threshold 0.2 \
  --threshold 0.3
```

远程 macro opaque control：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite macro \
  --mode shadow \
  --no-cache \
  --allow-llm \
  --macro-adapter base \
  --span-id-mode opaque \
  --unit macro-answer-fact \
  --unit macro-shared-scope \
  --unit macro-action-roles
```

远程 downstream gold injection：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite downstream \
  --mode shadow \
  --no-cache \
  --allow-llm
```

远程 V7 relation calibration batch 归因：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite relation-calibration \
  --mode shadow \
  --no-cache \
  --allow-llm
```

远程冷启动归因：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite rep \
  --mode shadow \
  --no-cache \
  --allow-llm \
  --rep-unit macro-answer-fact \
  --rep-runs 3
```

远程 adapter 冷启动对照：

```bash
scripts/integration/run-input-preprocessing-v9-attribution.sh \
  --suite adapter \
  --mode shadow \
  --no-cache \
  --allow-llm \
  --adapter base \
  --adapter instructor \
  --adapter baml \
  --rep-unit macro-answer-fact \
  --rep-runs 3
```

## 10. 安全边界

所有 V9 实验保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

禁止接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

禁止读取 held-out，禁止使用关键词、正则、宽松匹配或模型自由 quote 修复失败。
