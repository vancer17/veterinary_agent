<!--
=============================================================================
文件: input-preprocessing-v13-llm-first-structured-claim-change-summary.md
作用: 记录第十三轮 LLM-first Structured Claim 实现、phrase-policy 修复、
      远程 quick control、approximate/literal shadow、canonical targeted shadow
      与 REP 结果。
范围: 覆盖 approximate/literal phrase policy、独立意图识别、claim unit
      segmentation、one-pass/two-stage claim generation、fuzzy alignment、
      statement semantics、participant/temporal/measurement/canonical governance、
      post-hoc claim graph、NEG/ASYNC/REP 与 held-out 防护。
说明: 本文证明 V13 范式对照可执行，并给出当前质量与稳定性 blocker；不解除
      V8 live phase gate，不构成生产消费准入。
维护: 当 V13 报告、契约、phrase policy 或后续 V14 结论变化时同步更新本文。
=============================================================================
-->

# Input preprocessing V13 LLM-first Structured Claim 实验总结

> **结论**：V13 approximate LLM-first one-pass 路线在 development representative units 上显著超过 V12 support-first seed 基线：两次完整 approximate shadow 的 one-pass claim precision / recall 分别为 `0.8125 / 0.8125` 与 `0.5625 / 0.5625`，均高于 V12 的 `0.0448 / 0.34`。这支持本轮核心判断：V8～V12 的主要问题不是后处理不足，而是 GLiNER candidate-first 表示与论元角色 / 话语功能任务错配。
>
> **当前 blocker**：V13 不是稳定 winner。完整 representative shadow 的 claim 结果在两次冷执行间从 `0.8125` 降至 `0.5625`；turn intent precision 仅 `0.375`；participant resolution 仅 `0.3333`；field false-alignment 约 `0.1624～0.2085`；canonical descriptor 在 targeted units 上 recall 为 `1.0` 但 false confirmation 为 `0.5`；p95 latency 约 `132～149s`。因此 V13 只能证明范式方向有效，不能进入 integration / adapter cold / held-out。
>
> **准入结论**：所有 V13 报告均为 `diagnostic_only=true`、`can_unblock_v8_phase=false`。V8 live Phase 0 gate 保持不变；held-out 与 DSPy 继续冻结。

## 1. 实现范围

新增：

```text
src/vet_agent/input_preprocessing/v13_contracts.py
src/vet_agent/input_preprocessing/v13_aligner.py
src/vet_agent/input_preprocessing/v13_generator.py
src/vet_agent/input_preprocessing/v13_governance.py
src/vet_agent/input_preprocessing/v13_experiments.py

tests/test_input_preprocessing_v13.py
scripts/integration/deploy-input-preprocessing-v13-remote.sh
scripts/integration/run-input-preprocessing-v13-remote-runner.sh
```

支持 suite：

```text
quick
aligner
negative
llmf
paradigm
rep
async
all
```

支持实验：

```text
ALIGNER-CONTROL
TURN-INTENT
NEG-V13
LLMF-SEG-ONLY
LLMF-ONEPASS
LLMF-TWOSTAGE
CLAIM-ALIGN
FUZZY-POLICY
STATEMENT-SEMANTICS
TEMPORAL-PROPOSAL
MEASUREMENT-PROPOSAL
PARTICIPANT-RESOLVE
CAN-DESCRIPTOR
CLAIM-GRAPH
PARADIGM-COMPARE
REP-V13
ASYNC-V13
HELD-OUT-V13
```

报告版本：

```text
v13-experiment-report-1
```

关键版本：

```text
phrase policy:
  literal control
  approximate primary

aligner:
  v13-conservative-fuzzy-aligner-20260901-2

intent prompt:
  v13-intent-dev-20260901-4

segmentation prompt:
  v13-segmentation-dev-20260901-4

claim prompt:
  v13-flat-claim-dev-20260901-4
```

## 2. Phrase policy 修复

首轮实现曾把模型 phrase 约束为“逐字来自原文连续片段”，使实验偏离 V13 的 approximate phrase 假设。修复后：

```text
literal control:
  phrase 必须逐字来自原文连续片段；

approximate primary:
  phrase 是 semantic proposal；
  不要求逐字复制原文或保持连续；
  不得引入原文没有的信息；
  不得丢失否定、时间、主体、数量或关键关系；
  能逐字复制时优先逐字复制。
```

CLI：

```text
--phrase-policy literal
--phrase-policy approximate   # 默认
```

prompt version 会追加：

```text
:literal
:approximate
```

顶层报告与每个 shadow report 均记录：

```text
phrase_policy
```

### 硬边界

两种 policy 下，raw schema 均禁止：

```text
span_id
start
end
entity_id
canonical_id
selected_candidate_id
```

model phrase 不是 quote，也不是 evidence。最终 evidence 只能来自：

```text
deterministic aligner
→ raw_text[start:end]
→ aligned_quote
```

## 3. Deterministic aligner

匹配顺序：

```text
exact
exact_normalized
unique fuzzy / fuzzy verifier
fuzzy ambiguous
fuzzy not found
cross source block
empty phrase
```

允许的 normalization：

```text
trim
空白归一
全半角标点归一
重复标点归一
```

fuzzy verifier 会阻断：

```text
negation_lost
temporal_lost
subject_lost
boundary_crossing
semantic_mismatch
uncertain
```

禁止：

```text
同义词替换
编辑距离修改 quote
embedding 相似改写 quote
LLM 重写 quote
```

## 4. Field governance

每个 claim 的字段独立对齐，包括：

```text
evidence
target
subject
action_agent
action_recipient
object
temporal
measurement
relation
```

治理规则：

```text
target 必须在 evidence envelope 内；
optional field 必须在 evidence envelope 内；
fuzzy not found 不通过；
fuzzy ambiguous review；
cross source block blocked；
parser conflict review；
blocked claim 不进入 projection。
```

deterministic claim ID 由以下内容派生：

```text
source block
evidence boundary
target boundary
claim type
statement type
```

## 5. 本地与远程验证

本地：

```text
ruff check V13 modules/tests: PASS
mypy V13 modules: PASS
pytest tests/test_input_preprocessing_v13.py: 8 passed
pytest V8/V9/V10/V11/V12/V13: 47 passed
bash -n deploy/remote runner: PASS
```

远程 `.venv-v11`：

```text
compileall V13 modules: PASS
ruff check V13 modules/tests: PASS
mypy V13 modules: PASS
pytest V13: 8 passed
```

## 6. 报告权威口径

### 6.1 权威报告

后续 V14 与架构讨论默认使用以下报告：

| 用途 | 报告 | 说明 |
|---|---|---|
| Quick / contract control | `quick-final/v13-20260901-161600-3326485.json` | ideal control，只证明契约与治理链路 |
| Approximate primary authority | `shadow-approximate-final-v2/v13-20260901-164533-3350994.json` | 最新 phrase policy、aligner 与 participant 口径，完整 representative run |
| Approximate repeat sensitivity | `shadow-approximate-final/v13-20260901-162105-3327008.json` | 用于观察同版本冷执行波动，不单独宣布 winner |
| Literal control | `shadow-literal-final/v13-20260901-163000-3331978.json` | one-pass lane 有一个依赖失败，作为不完整对照 |
| Canonical targeted control | `shadow-approximate-canonical-units/v13-20260901-163822-3343625.json` | 只覆盖 `macro-other-pet` / `macro-long-input` |
| REP targeted control | `rep-approximate-answer-fact/v13-20260901-163218-3341898.json` | 只覆盖 `macro-answer-fact` 三次冷调用 |

### 6.2 非权威迭代

以下早期报告只作工程归因，不作为 V13 质量结论或 V14 baseline：

```text
shadow-answer-fact
shadow-representative-v2
shadow-approximate
shadow-approximate-v2
shadow-approximate-v3
shadow-approximate-representative
shadow-approximate-representative-v2
shadow-approximate-representative-v3
shadow-literal
shadow-literal-v2
shadow-literal-v3
rep-approximate
rep-approximate-v2
rep-approximate-v3
rep-literal
rep-literal-v2
rep-literal-v3
```

早期 report 的 runner 口径可能存在以下历史问题：

```text
segmentation 结果重复计入 one-pass / two-stage；
失败 unit 未进入部分 lane 分母；
dependency failure 混入所有 report；
participant 指标混合 one-pass 与 two-stage；
literal source-constraint prompt 被误当作 approximate 主实验。
```

这些问题已在 final / final-v2 前修复。不要将早期迭代与 final 报告做直接加权比较。

## 7. Quick control

报告：

```text
.data/evaluations/input-preprocessing-v13/quick-final/
v13-20260901-161600-3326485.json

sha256=
723b47fae2b9bab01f698baeedf4ede74ae8aeb73a958ecbb536fea9f0d627cb
```

结果：

```text
ALIGNER-CONTROL:
  false_alignment_rate = 0
  negation_loss_detection_rate = 1.0
  temporal_loss_detection_rate = 1.0

NEG-V13:
  mutation_count = 11
  gate_blocked_as_expected = 11
  false_pass = 0

TURN-INTENT ideal:
  act precision / recall = 1.0 / 1.0
  evidence alignment = 1.0

LLMF-SEG-ONLY ideal:
  claim unit precision / recall = 1.0 / 1.0

LLMF-ONEPASS / TWOSTAGE ideal:
  claim precision / recall = 1.0 / 1.0
  statement / polarity / modality / epistemic accuracy = 1.0

PARTICIPANT-RESOLVE ideal:
  participant mention recall = 1.0
  participant resolution accuracy = 1.0
  object mention accuracy = 1.0
  resolved-empty violation = 0

CLAIM-GRAPH ideal:
  claim node count = 22
  field lineage available = 1.0
  projection consuming blocked = 0

ASYNC-V13:
  queue full / dead letter / trace completeness 均有效

HELD-OUT-V13:
  blocked
  heldout_read_count = 0
```

解释：quick control 证明契约与治理链路可执行，不代表 qwen-plus 质量。

## 8. Approximate primary shadow

### 7.1 第一次完整 representative shadow：repeat sensitivity

报告：

```text
.data/evaluations/input-preprocessing-v13/shadow-approximate-final/
v13-20260901-162105-3327008.json

sha256=
25f191baef6f936b07623ffc6ce0f2b119fcd3d17ca0c62bfada6ca166f25077
```

核心结果：

```text
TURN-INTENT:
  act precision / recall = 0.375 / 0.75
  evidence alignment = 1.0

LLMF-SEG-ONLY:
  claim unit precision / recall = 1.0 / 1.0

LLMF-ONEPASS:
  claim precision / recall = 0.8125 / 0.8125
  statement / polarity / modality / epistemic accuracy = 0.8125
  blocked count = 4 / 16

LLMF-TWOSTAGE:
  claim precision / recall = 0.3125 / 0.3125
  statement accuracy = 0.3125

CLAIM-ALIGN:
  field alignment rate = 0.9056
  false alignment rate = 0.2085
  fuzzy verified rate = 0.0556
  fuzzy ambiguous rate = 0.0583
  not found rate = 0.0361

PARTICIPANT-RESOLVE:
  participant mention recall = 0.3333
  participant resolution accuracy = 0.3333

TEMPORAL-PROPOSAL:
  parser normalized rate = 0.6667
  parser conflict rate = 0.3333

MODEL / LATENCY:
  model_call_count = 13
  p50_latency_ms = 11737
  p95_latency_ms = 132470
  token_count_available = false
  cost_available = false
```

### 7.2 第二次完整 representative shadow：primary authority

报告：

```text
.data/evaluations/input-preprocessing-v13/shadow-approximate-final-v2/
v13-20260901-164533-3350994.json

sha256=
8ce982685ebfac0f4cdf1eefc032f3ec28544ea0dec106863db33a879ab28a7f
```

核心结果：

```text
TURN-INTENT:
  act precision / recall = 0.375 / 0.75

LLMF-SEG-ONLY:
  claim unit precision / recall = 1.0 / 1.0

LLMF-ONEPASS:
  claim precision / recall = 0.5625 / 0.5625
  statement / modality / epistemic accuracy = 0.5625
  polarity accuracy = 0.375
  blocked count = 10 / 16

LLMF-TWOSTAGE:
  claim precision / recall = 0.375 / 0.375

CLAIM-ALIGN:
  field alignment rate = 0.8912
  false alignment rate = 0.1624
  fuzzy verified rate = 0.0556
  fuzzy ambiguous rate = 0.0583
  not found rate = 0.0505

PARTICIPANT-RESOLVE:
  participant mention recall = 0.3333
  participant resolution accuracy = 0.3333
  object mention accuracy = 1.0

TEMPORAL-PROPOSAL:
  parser normalized rate = 0.6667
  parser conflict rate = 0.25

MODEL / LATENCY:
  model_call_count = 13
  p50_latency_ms = 11288
  p95_latency_ms = 148638
  token_count_available = false
  cost_available = false
```

### 7.3 解释

1. approximate one-pass 在两次冷执行中均显著超过 V12 seed 基线；
2. 但 claim quality 波动明显，不能宣布稳定 winner；
3. one-pass 优于 two-stage，说明完整原文上下文对 claim 语义和 shared scope 更重要；
4. two-stage 会在 shared denial / normal scope 上丢失 statement type 或 polarity；
5. field alignment 高不代表字段正确，false-alignment 仍是核心 blocker；
6. participant phrase 经常缺失或错绑；
7. temporal parser verifier 必须保留，不能直接采信 LLM proposal。

## 9. Literal control shadow

报告：

```text
.data/evaluations/input-preprocessing-v13/shadow-literal-final/
v13-20260901-163000-3331978.json

sha256=
241ae5dee81cf21d65c5dc5176d20c27877ee765990c50e1b0de0c2bbbb89d81
```

核心结果：

```text
LLMF-ONEPASS:
  claim precision = 1.0
  claim recall = 0.8125
  output count = 13 / 16
  statement accuracy = 0.8125
  polarity accuracy = 0.625

LLMF-TWOSTAGE:
  claim precision / recall = 0.3125 / 0.3125

TURN-INTENT:
  act precision / recall = 0.1667 / 0.25
```

该报告存在一个依赖失败：

```text
unit = macro-action-roles
lane = onepass
failure_attribution = dependency_failed
```

由于该报告生成时的 runner status 逻辑仍将含失败 lane 的顶层 report 标为 `completed`，读取时应以：

```text
dependency_failure_count = 1
lane_failure_count = 1
claim_output_count = 13 / 16
```

作为完整性判断，而不能只看 report status。

结论：

1. literal constraint 可以提高已输出 claim 的 precision；
2. 但它不能解决 intent、participant、canonical 或稳定性问题；
3. literal one-pass 存在依赖失败，不能与 approximate 完整 run 直接宣布 winner；
4. literal 保留为保守对照，不作为主路线。

## 10. Canonical targeted shadow

报告：

```text
.data/evaluations/input-preprocessing-v13/shadow-approximate-canonical-units/
v13-20260901-163822-3343625.json

sha256=
447716604e70e18f735c8b69cfb916749deea1abbcd245ebb40fbec8074700c2
```

使用：

```text
macro-other-pet
macro-long-input
```

结果：

```text
CAN-DESCRIPTOR:
  record count = 2
  target direct recall = 0.5
  descriptor recall = 1.0
  dual query recall = 1.0
  false confirmation rate = 0.5
  not_found_review_rate = 0

LLMF-ONEPASS:
  claim precision / recall = 0.6667 / 0.6667

LLMF-TWOSTAGE:
  claim precision / recall = 0.25 / 0.1667
```

结论：

1. canonical descriptor 确实能提升 recall；
2. 直接扩大查询会引入较高兴假确认；
3. descriptor 只能用于 candidate recall，不能直接确认；
4. selected canonical 必须来自 constrained candidate set，否则不得 confirmed。

## 11. REP

报告：

```text
.data/evaluations/input-preprocessing-v13/rep-approximate-answer-fact/
v13-20260901-163218-3341898.json

sha256=
0c8193cb6a2224609bcd082ca350a297ac875b5228c5f77e49c1898202864d2a
```

结果：

```text
cold_run_count = 3
cache_hit_count = 0
unique_output_count = 1
semantic_claim_stability = 1.0
stable_and_correct_rate = 1.0
stable_but_wrong_rate = 0
unstable_rate = 0
```

解释：

1. `macro-answer-fact` 单单元 REP 表现优秀；
2. 但两次完整 representative shadow 的 claim 结果差异证明全矩阵稳定性仍未达标；
3. REP 必须扩展到全部 representative units，不能以单单元结果宣布 finalist。

## 12. 范式结论

V13 已足以回答核心问题：

```text
LLM-first Structured Claim Generation 是否显著优于
V12 support-first graph + structural seed？
```

当前答案是：

```text
方向上显著优于，但尚未稳定到 finalist。
```

对比：

```text
V12:
  seed precision / recall = 0.0448 / 0.34

V13 approximate one-pass run 1:
  claim precision / recall = 0.8125 / 0.8125

V13 approximate one-pass run 2:
  claim precision / recall = 0.5625 / 0.5625
```

这说明：

1. GLiNER candidate-first 与后处理补偿路线不再是当前主线；
2. approximate phrase + deterministic source-grounded alignment 是新的主线候选；
3. one-pass flat claim generation 优于 two-stage；
4. 后续应收敛治理与稳定性，而不是继续扩大 candidate 后处理矩阵。

## 13. 当前根因排序

1. **输出稳定性**
   - one-pass claim quality 在 `0.5625～0.8125` 间波动；
   - 单单元 REP 3/3，但全 representative 矩阵不稳定。

2. **Turn intent act 重复**
   - expected acts = 4；
   - output acts = 8；
   - precision = 0.375；
   - `fact_statement` 被按 claim 重复输出。

3. **Field false alignment**
   - false alignment rate `0.1624～0.2085`；
   - phrase 可落回原文，但可能落在错误字段或错误 claim 范围。

4. **Participant binding**
   - mention / resolution accuracy 仅 `0.3333`；
   - phrase 缺失或角色漂移仍明显。

5. **Canonical false confirmation**
   - descriptor recall = 1.0；
   - false confirmation = 0.5；
   - 必须使用 constrained selector。

6. **Temporal proposal conflict**
   - parser conflict rate `0.25～0.3333`；
   - parser verifier 必须保留。

7. **Cost / latency**
   - experiment p95 latency `132～149s`；
   - token usage 与 cost unavailable；
   - one-pass production lane 需要重新测量最小调用路径。

## 14. V14 收敛方向

后续不应继续扩大 V13。下一轮应只收敛以下问题：

```text
1. one-pass approximate finalist 冻结；
2. turn intent act 去重；
3. field false alignment 专项治理；
4. participant phrase 契约与 TurnContext resolver；
5. canonical constrained descriptor selector；
6. temporal parser conflict review；
7. 全 representative units 三次冷调用 REP；
8. 只保留 intent + one-pass claim generation 的最小成本路径。
```

详细后续计划见：

[input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md)

## 15. 复现命令

部署与基础验证：

```bash
scripts/integration/deploy-input-preprocessing-v13-remote.sh
```

Quick control：

```bash
scripts/integration/run-input-preprocessing-v13-remote-runner.sh \
  --suite quick \
  --mode quick \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v13/quick-final
```

Approximate primary shadow：

```bash
INPUT_PREPROCESSING_V13_REMOTE_TIMEOUT_SECONDS=1800 \
INPUT_PREPROCESSING_V13_REQUEST_TIMEOUT_SECONDS=150 \
scripts/integration/run-input-preprocessing-v13-remote-runner.sh \
  --suite llmf \
  --mode shadow \
  --phrase-policy approximate \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v13/shadow-approximate-final-v2
```

Literal control：

```bash
INPUT_PREPROCESSING_V13_REMOTE_TIMEOUT_SECONDS=1800 \
INPUT_PREPROCESSING_V13_REQUEST_TIMEOUT_SECONDS=150 \
scripts/integration/run-input-preprocessing-v13-remote-runner.sh \
  --suite llmf \
  --mode shadow \
  --phrase-policy literal \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v13/shadow-literal-final
```

Canonical targeted units：

```bash
INPUT_PREPROCESSING_V13_REMOTE_TIMEOUT_SECONDS=1500 \
INPUT_PREPROCESSING_V13_REQUEST_TIMEOUT_SECONDS=150 \
scripts/integration/run-input-preprocessing-v13-remote-runner.sh \
  --suite llmf \
  --mode shadow \
  --phrase-policy approximate \
  --no-cache \
  --unit macro-other-pet \
  --unit macro-long-input \
  --output-dir .data/evaluations/input-preprocessing-v13/shadow-approximate-canonical-units
```

REP targeted control：

```bash
INPUT_PREPROCESSING_V13_REMOTE_TIMEOUT_SECONDS=1200 \
INPUT_PREPROCESSING_V13_REQUEST_TIMEOUT_SECONDS=150 \
scripts/integration/run-input-preprocessing-v13-remote-runner.sh \
  --suite rep \
  --mode cold \
  --phrase-policy approximate \
  --rep-unit macro-answer-fact \
  --rep-runs 3 \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v13/rep-approximate-answer-fact
```

注意：重新执行真实 shadow 会生成新报告。本文的结论绑定上方 SHA256；新报告必须重新评估，不得自动继承本文结论。

## 16. 安全边界

本轮所有实现与远程命令保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
held_out_read = false
dspy_used = false
gliner_called_on_main_path = false
```

未接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

所有 V13 ideal / fake shadow / real shadow / phrase proposal 结果不得：

```text
进入 production projection；
作为生产 fallback；
解除 V8 live phase admission；
替代 live span gate；
接触 held-out；
触发 DSPy。
```
