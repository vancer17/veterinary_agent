<!--
=============================================================================
文件: input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md
作用: 定义 V14 one-pass LLM-first Structured Claim 治理收敛轮的契约、实验矩阵、
      指标、执行顺序、准入条件和防偏移边界。
范围: 覆盖 fixed-field turn intent、approximate one-pass claim generation、
      claim-local fuzzy alignment、participant resolver、semantic proposal
      verifier、constrained canonical selector、minimal lane 成本、REP 和
      report-only shadow。
说明: V14 不引入新的候选生成范式，不恢复 GLiNER / SpanGraph / NMS 主路径；
      本轮只收敛 V13 已暴露的稳定性与治理缺陷。
维护: 当 V14 契约、prompt skill、generation option、alignment 策略、实验矩阵
      或准入条件调整时同步更新本文。
=============================================================================
-->

# Input preprocessing V14 one-pass governance convergence 实验计划

> **文档状态**：待执行；基于 V13 权威数据定义下一轮收敛实验
>
> **核心问题**：在不回到 GLiNER candidate-first / SpanGraph / candidate menu 的前提下，V13 approximate one-pass 能否通过 fixed-field intent、claim inventory、claim-local alignment 和后置治理收敛为稳定、可审计、成本可接受的 claim 生成路径？
>
> **安全边界**：report-only；不写业务状态；不触发 clinical safety evaluator / pgvector / required_context / OPA；held-out 与 DSPy 继续冻结。

## 1. V13 权威基线

V13 权威报告见：

[input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)

关键数据：

```text
V12 support-first seed:
  precision / recall = 0.0448 / 0.34

V13 approximate one-pass:
  run 1 precision / recall = 0.8125 / 0.8125
  run 2 precision / recall = 0.5625 / 0.5625

V13 claim segmentation:
  representative claim unit precision / recall = 1.0 / 1.0

V13 turn intent:
  act precision / recall = 0.375 / 0.75
  expected acts = 4
  output acts = 8
  fact_statement 按 claim 重复输出

V13 field alignment:
  field alignment rate = 0.8912～0.9056
  false alignment rate = 0.1624～0.2085

V13 participant:
  participant mention recall = 0.3333
  participant resolution accuracy = 0.3333

V13 temporal:
  parser normalized rate = 0.6667
  parser conflict rate = 0.25～0.3333

V13 canonical targeted:
  descriptor recall = 1.0
  dual query recall = 1.0
  false confirmation = 0.5

V13 representative experiment:
  model call count = 13
  p95 latency = 132s～149s
  token usage / cost unavailable
```

V14 只处理以上 blocker，不再扩大候选后处理矩阵。

## 2. 实验目标与非目标

### 2.1 目标

1. 用 fixed-field intent 契约消灭 `fact_statement` 重复输出。
2. 用 one-pass claim inventory 和 shared-scope skill 提升 claim 输出稳定性。
3. 用 claim-local field alignment 降低 false alignment。
4. 用 TurnContext candidate-only resolver 修复 participant phrase / role 绑定。
5. 保留 temporal / measurement parser verifier，区分 verified、model_proposed、parser_conflict。
6. 用 dual-query candidate recall 和 constrained selector 降低 canonical 假确认。
7. 建立最小候选路径的模型调用、延迟、token 和成本观测。
8. 对完整 representative units 执行三次冷调用 REP。
9. 保持 NEG / ASYNC / early-failure 边界。

### 2.2 非目标

1. 不重做 V8～V12 GLiNER 后处理路线。
2. 不恢复 support anchor、structural seed、role menu 或 span_id binding 主路径。
3. 不继续扩大 BGE / GLiNER / NMS / graph ranking 矩阵。
4. 不默认引入 DSPy、BAML、Instructor 或新模型。
5. 不接入 `VetOrchestrator`。
6. 不消费问诊事实。
7. 不评估 clinical safety adapter。
8. 不读取 held-out。
9. 不解除 V8 live Phase 0 gate。

## 3. 当前代码复用与新增范围

### 3.1 复用

```text
V13 approximate phrase policy
V13 deterministic fuzzy aligner
V13 one-pass claim generator
V13 claim governance
V11 frozen relation few-shot contract
V6 deterministic temporal / measurement parser
V6 canonical direct recall
TurnContext participant resolver
V5～V13 async file-backed experiment queue
```

当前 structured 主调用已经通过 `V8BaseStructuredClient` 使用：

```text
temperature = 0.0
response_format + Pydantic
```

因此 V14 不应假设降温本身能解决漂移。采样参数只做小规模归因，不作为主修复。

### 3.2 新增建议模块

```text
src/vet_agent/input_preprocessing/v14_contracts.py
src/vet_agent/input_preprocessing/v14_intent.py
src/vet_agent/input_preprocessing/v14_prompt_skills.py
src/vet_agent/input_preprocessing/v14_alignment.py
src/vet_agent/input_preprocessing/v14_participant_resolver.py
src/vet_agent/input_preprocessing/v14_canonical_selector.py
src/vet_agent/input_preprocessing/v14_generation_options.py
src/vet_agent/input_preprocessing/v14_experiments.py
scripts/integration/run-input-preprocessing-v14-remote-runner.sh
tests/test_input_preprocessing_v14.py
```

当前 `baml_src/v8_macro.baml` 仍是 V8 span-id 契约，不能直接复用为 V14 adapter。BAML / Instructor 对照必须等 V14 semantic winner 冻结后再执行。

## 4. Phase 0：执行与观测口径修复

V14 必须先让执行结果可信，再修改 prompt 或 schema。

### 4.1 Raw measurement 与 production candidate 分离

V13 generator 默认 `max_attempts=2`。这会把首次失败和重试后结果混入稳定性指标。

V14 必须区分：

```text
Raw measurement mode:
  max_attempts = 1
  用于评估真实模型稳定性

Production candidate mode:
  max_attempts <= 2
  用于评估有界 retry 的收益
```

两种模式不得混入同一组 REP 指标。

### 4.2 捕获 usage 与 response metadata

当前 structured client 只返回模型 content。V14 应返回：

```text
output
usage
finish_reason
response_id
provider model snapshot
latency
attempt_count
first_attempt_status
```

至少应使报告可区分：

```text
token_count_available = true / false
cost_available = true / false / unsupported
```

不得用估算 token 冒充真实 usage。

### 4.3 Generation option audit

虽然当前已使用 temperature=0，仍需记录：

```text
temperature
top_p
seed
frequency_penalty
presence_penalty
max_tokens
request timeout
model snapshot
```

如果 provider 不支持或未回传某个字段，报告必须标记：

```text
effective_parameter_status = unavailable / unverifiable
```

### 4.4 MINIMAL-LANE 延迟口径

V13 p95 132～149s 是完整实验矩阵耗时，不是生产候选路径耗时。

V14 必须单独记录：

```text
Turn Intent Analyzer
One-pass claim generation
deterministic alignment / governance
targeted verifier calls
```

完整实验矩阵耗时不得用于宣称 minimal lane 延迟。

## 5. Fixed-field Turn Intent

### 5.1 根因

V13 将 `fact_statement` 放在 acts 数组中。模型看到多个 claim 后，为每个 claim 输出一次 `fact_statement`。

这是契约层错误，不是随机漂移。

### 5.2 V14 schema

```python
class V14SignalDetection(BaseModel):
    detected: bool
    evidence_phrase: str | None
    confidence: float
    needs_review: bool


class V14TurnIntentRaw(BaseModel):
    answer_now: V14SignalDetection
    wants_triage: V14SignalDetection
    correction: V14SignalDetection
    clarification_request: V14SignalDetection

    fact_statement_present: V14SignalDetection
    question_present: V14SignalDetection
    report_context_present: V14SignalDetection

    no_signal_reason: str | None
```

语义：

```text
answer_now / wants_triage / correction / clarification_request:
  turn-level dialogue acts

fact_statement_present / question_present / report_context_present:
  turn-level input properties
```

固定字段使模型物理上无法重复输出多个 `fact_statement` act。

### 5.3 Evidence 语义

`fact_statement_present` 只回答：

```text
当前 turn 是否包含事实陈述？
```

它不枚举所有事实。

示例：

```text
前天换粮，这两天大便软，精神还好，没有呕吐。
```

正确输出：

```text
fact_statement_present = true
evidence_phrase = 前天换粮
```

完整事实证据由 claim records 承担。

### 5.4 Intent / claim reconciliation

Claim generation 后执行确定性对账：

```text
fact_statement_present=true 且 governed_claim_count>0:
  consistent

fact_statement_present=true 且 governed_claim_count=0:
  intent_claim_mismatch / review

fact_statement_present=false 且 governed_claim_count>0:
  intent_claim_mismatch / review
```

如果 claim generation 被 gate 阻断：

```text
claim_generation_blocked
intent_value_preserved
review_required=true
```

不得自动改写 intent。

### 5.5 INTENT-SPLIT 实验

#### 对照组

```text
V13 acts-array schema
```

#### 实验组

```text
V14 fixed-field schema
```

#### 样本

```text
单事实
多事实
长输入事实 + answer_now
事实 + question + answer_now
无事实 answer_now
无事实 question
模糊分诊
纠正
报告上下文
```

#### 指标

```text
fact_statement_duplicate_count
act precision / recall
fact_statement_present accuracy
question_present accuracy
answer_now precision / recall
evidence alignment rate
intent_claim_consistency_rate
3 次冷调用稳定性
```

#### 硬性目标

```text
fact_statement_duplicate_count = 0
```

## 6. Prompt skills 与 one-pass 稳定性

V14 的 skill 是版本化 prompt policy，不是样本专用规则或无形的手工调 prompt。

### 6.1 SKILL-INTENT-TURN-LEVEL

规则：

```text
fact_statement_present 是 turn-level 属性；
不随 claim 数量重复；
answer_now、fact、question 可同时为 true；
每个信号最多出现一次。
```

### 6.2 SKILL-CLAIM-INVENTORY

One-pass 输出内部先包含轻量 inventory：

```text
claim_inventory:
  ordinal
  evidence_phrase
  claim_kind
```

`claims` 必须逐项对应 inventory ordinal。

目的：

```text
稳定 claim 数量；
减少漏项；
稳定输出顺序；
避免生成字段时改变 claim skeleton。
```

仍是同一次模型调用，不得变成 two-stage。

### 6.3 SKILL-SHARED-SCOPE

针对：

```text
没有呕吐、干呕、反流
精神、食欲、饮水都正常
```

规则：

```text
一个 relation / state 可作用于多个 target；
每个 target 生成一条 claim；
polarity / modality / relation 继承；
不得只输出第一个 target；
不得合并为一个粗 claim。
```

### 6.4 SKILL-PARTICIPANT-ROLE

规则：

```text
action claim 应输出 action_agent_phrase；
应输出 action_recipient_phrase；
应输出 object_phrase；
区分 user、medical_actor、caregiver、pet；
不得发明 entity_id；
省略主体时输出 null + omission reason。
```

### 6.5 SKILL-PHRASE-PRESERVATION

规则：

```text
approximate phrase 可以不是逐字原文；
但不得丢失否定、时间、主体、数量、比较关系；
不得把“没有呕吐”写成“呕吐”；
不得把“没有明显变化”写成“正常”。
```

### 6.6 SKILL-NULL-SEMANTICS

明确：

```text
null = 原文没有该信息
not_applicable = 当前 claim 不需要该字段
ambiguous = 有线索但无法确定
review_required = 需要 verifier
```

目标是减少 V13 blocked count 从 4/16 到 10/16 的漂移。

## 7. 生成参数小规模对照

当前 temperature 已为 0，因此只做最小参数审计：

| 实验 | 配置 |
|---|---|
| P0 | 当前 temperature=0 |
| P1 | temperature=0 + provider seed（如支持） |
| P2 | temperature=0 + top_p=1.0 显式传递 |
| P3 | temperature=0.2 对照 |

限制：

```text
每组最多 representative units；
每个配置 3 次冷调用；
max_attempts=1；
cache disabled。
```

不做大规模网格搜索。

必须报告：

```text
provider 是否支持参数；
参数是否被接受；
响应 metadata 是否回传；
同配置输出稳定性；
```

若无法验证参数效果，不得把参数配置当作结论。

## 8. Claim-local field alignment

### 8.1 V13 问题

V13 field alignment 高，但 false alignment 也高：

```text
field alignment rate = 0.8912～0.9056
false alignment rate = 0.1624～0.2085
```

说明 phrase 能落回原文，但可能落在：

```text
错误 occurrence；
错误字段；
错误 claim region；
相邻相似文本；
```

### 8.2 两阶段对齐

#### Step 1：对齐 claim evidence phrase

生成：

```text
claim evidence envelope
source block
aligned quote
```

#### Step 2：字段 phrase 只在所属 claim envelope 内搜索

适用于：

```text
target phrase
subject phrase
action agent phrase
action recipient phrase
object phrase
temporal phrase
measurement phrase
relation phrase
```

默认不得跨 claim envelope。

例外：

```text
省略主体；
previous question target；
TurnContext owner occurrence；
```

必须显式记录：

```text
alignment_scope=outside_parent
resolution_method=previous_question_target / TurnContext
```

### 8.3 Alignment status

```text
exact
exact_normalized
fuzzy_verified
fuzzy_ambiguous
wrong_occurrence
outside_parent
cross_source_block
semantic_mismatch
negation_lost
temporal_lost
subject_lost
empty_phrase
```

### 8.4 分数与 margin

Fuzzy alignment 必须保留：

```text
best candidate
best score
second candidate
second score
score margin
candidate count
alignment method
```

当分数差距不足或候选多个时：

```text
fuzzy_ambiguous
review_required=true
```

不得静默选择 top1。

### 8.5 Field-specific verifier

对 fuzzy aligned 字段做局部验证：

输入：

```text
aligned quote
model phrase
field role
claim context
```

输出：

```text
verified
semantic_mismatch
negation_lost
temporal_lost
subject_lost
uncertain
```

重点字段：

```text
target
relation
subject
action_agent
action_recipient
temporal
measurement
```

### 8.6 ALIGN-LOCAL 实验

对照组：

```text
V13 global field alignment
```

实验组：

```text
V14 claim-local field alignment
+ occurrence disambiguation
+ fuzzy verifier
```

指标按字段输出：

```text
alignment precision
false alignment rate
ambiguous rate
review rate
not found rate
wrong occurrence count
outside parent count
```

## 9. Participant resolution

### 9.1 V13 问题

```text
participant mention recall = 0.3333
participant resolution accuracy = 0.3333
object mention accuracy = 1.0
```

Object 相对可用，但 action agent / recipient / subject 失败明显。

### 9.2 LLM 只输出 phrase

字段：

```text
subject_phrase
action_agent_phrase
action_recipient_phrase
object_phrase
omission_reason
```

禁止输出：

```text
entity_id
canonical_id
```

### 9.3 TurnContext candidate-only resolver

代码将 phrase 解析为：

```text
user_001
pet_001
medical_actor_001
food_mention_001
```

规则：

```text
候选来自 TurnContext；
resolved 不能为空；
空值必须 unresolved / missing；
ambiguous 必须有候选；
不得默认 current_pet。
```

### 9.4 Role compatibility

```text
action_agent:
  user / caregiver / medical_actor

action_recipient:
  current_pet / other_pet

experiencer / subject:
  current_pet / other_pet

object:
  food / medication / sample / unresolved mention
```

类型不匹配输出：

```text
participant_type_mismatch
```

### 9.5 PARTICIPANT-V14 实验

样本：

```text
我给它换新猫粮
医生给它开了药
主人喂了罐头
护士给它打针
另一只猫也在呕吐
它没有呕吐
```

指标：

```text
agent phrase recall
recipient phrase recall
subject phrase recall
role assignment accuracy
entity resolution accuracy
invented entity rate
resolved-empty violation
ambiguous detection rate
```

## 10. Temporal / measurement proposal 治理

### 10.1 状态

LLM proposal 只能处于：

```text
model_proposed
verified
parser_conflict
unresolved
```

### 10.2 Parser verifier 是权威归一化来源

Parser 成功：

```text
status = verified
```

Parser 与 LLM 冲突：

```text
status = parser_conflict
review_required = true
```

Parser 无法解析：

```text
status = model_proposed
review_required = true
```

不得把 model proposal 标记为 verified。

### 10.3 Claim-local binding

示例：

```text
前天开始换新狗粮，这两天大便偏软，一天一次。
```

必须区分：

```text
diet_change.started_at = 前天开始
soft_stool.duration = 这两天
soft_stool.frequency = 一天一次
```

## 11. Canonical constrained selector

### 11.1 V13 数据

```text
target direct recall = 0.5
descriptor recall = 1.0
dual query recall = 1.0
false confirmation = 0.5
```

结论：

```text
descriptor 有召回价值；
不能直接确认。
```

### 11.2 Dual query recall

使用：

```text
target_phrase
canonical_descriptor
```

生成候选。

Descriptor 只用于 candidate recall，不用于最终确认。

### 11.3 Candidate-only selector

输入：

```text
candidate_id
canonical_id
canonical_type
score
alias
subject compatibility
```

模型只能输出：

```text
selected_candidate_id
ambiguous
not_found
```

不能输出自由 canonical_id。

### 11.4 无候选

```text
mapping_status = not_found
canonical_id = null
review_required = true
new_concept_request = true
```

### 11.5 CAN-SELECT-V14 实验

对照组：

```text
target direct recall
descriptor direct confirmation
```

实验组：

```text
dual query recall
+ candidate-only selector
+ type / subject constraints
```

硬性指标：

```text
confirmed_without_candidates = 0
invented_canonical = 0
false confirmation 显著下降
```

## 12. Minimal candidate lane

### 12.1 目标

建立最小生产候选路径，而不是继续测量完整实验矩阵。

默认模型调用：

```text
1. Turn Intent Analyzer
2. One-pass Flat Claim Generation
```

可选第三次：

```text
targeted verifier
```

仅当触发：

```text
fuzzy conflict
participant ambiguity
canonical ambiguity
semantic conflict
```

### 12.2 Early exit

```text
无 fact statement 且无活跃问诊状态:
  不执行 claim generation

claim generation 后无 governed claim:
  不执行 enrichment

无 participant phrase:
  不调用 participant verifier

无 temporal phrase:
  不执行 temporal parser

无 measurement phrase:
  不执行 measurement parser

canonical 仅在投影需要时触发

relation 仅在 relation phrase 存在且策略需要时触发
```

### 12.3 MINIMAL-LANE 实验

比较：

```text
A. V13 full experiment matrix
B. V14 minimal lane
C. V14 minimal lane + targeted verifier
```

指标：

```text
model_call_count
p50 latency
p95 latency
token usage
cost
claim precision / recall
field alignment precision
review rate
blocked rate
```

## 13. 实验矩阵

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
| HELD-OUT-V14 | 默认 blocked，待冻结后评估 |

## 14. 执行顺序

### Phase 0

```text
EXEC-OBS
```

### Phase 1

```text
INTENT-SPLIT
```

### Phase 2

```text
GEN-OPTION
SKILL-INVENTORY
SKILL-SHARED
SKILL-NULL
```

先跑 targeted representative units，再跑完整 representative shadow。

### Phase 3

```text
ALIGN-LOCAL
```

### Phase 4

```text
SKILL-PARTICIPANT
PARTICIPANT-V14
```

### Phase 5

```text
TEMPORAL-V14
MEASUREMENT-V14
CAN-SELECT-V14
```

### Phase 6

```text
MINIMAL-LANE
```

### Phase 7

```text
REP-V14
NEG-V14
ASYNC-V14
```

### Phase 8

只有 development winner 冻结后才允许评估：

```text
adapter cold
API async shadow design
fresh held-out
```

## 15. REP-V14

### 输入

完整 representative units：

```text
macro-answer-fact
macro-shared-scope
macro-action-roles
macro-long-input
```

后续可扩展 development set，但不得读取 held-out。

### 执行

```text
3 次冷调用
cache disabled
max_attempts=1
```

### 指标

```text
raw output stability
semantic claim stability
field binding stability
intent stability
participant stability
canonical stability
blocked count stability
review count stability
stable-and-correct rate
stable-but-wrong rate
unstable rate
```

必须报告：

```text
best run
worst run
median run
```

不能只报告平均分。

## 16. NEG-V14

必须覆盖：

```text
fact_statement duplicate
model 自由输出 canonical_id
model 自由输出 entity_id
fuzzy not found 直接通过
fuzzy ambiguous 直接通过
model_proposed 伪装 verified
parser_conflict 未 review
participant resolved + null
canonical confirmed without candidates
projection 消费 blocked claim
claim evidence phrase 为空
true intent 无 evidence phrase
retry 后语义结果伪装单次结果
```

硬性目标：

```text
gate_blocked_as_expected_rate = 1.0
false_pass = 0
```

## 17. 报告要求

每个实验报告：

```text
experiment_id
mode: quick / shadow / cold
model
model snapshot / response metadata
prompt version
skill version
schema version
phrase policy
generation options
attempt policy
cache status

claim inventory metrics
one-pass claim metrics
intent fixed-field metrics
field alignment metrics
participant metrics
temporal metrics
measurement metrics
canonical metrics
claim graph metrics
minimal lane metrics

model_call_count
latency per stage
p50 / p95
token usage
cost availability

stable-and-correct
stable-but-wrong
unstable
failure attribution
safety boundary
```

## 18. 准入条件

### 18.1 硬性边界

```text
fact_statement_duplicate_count = 0
invented_entity = 0
invented_canonical = 0
confirmed_without_candidates = 0
projection_consuming_blocked_claim = 0
```

### 18.2 质量目标

相较 V13：

```text
one-pass claim quality 下限高于 0.5625
blocked count 波动收窄
field false alignment 低于 0.1624
participant resolution 高于 0.3333
canonical false confirmation 低于 0.5
temporal parser conflict 全部显式 review
```

这些是 V14 探索目标，不是生产承诺。

### 18.3 稳定性目标

```text
全 representative units 3 次冷调用
semantic claim signature 稳定
stable-and-correct 占比提升
不得由 stable-but-wrong 主导
```

### 18.4 成本目标

```text
minimal lane model calls <= 2～3
真实 minimal lane p95 显著低于 V13 full matrix
token usage / cost 可观测
```

## 19. 安全边界

V14 全程保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
held_out_read_count = 0
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

## 20. 明确不做

1. 不恢复 GLiNER 主路径。
2. 不恢复 support anchor / structural seed 前置。
3. 不恢复 role-specific candidate menu 前置。
4. 不使用 NMS 作为主路径。
5. 不继续扩大 BGE reranker。
6. 不读取 held-out。
7. 不启用 DSPy。
8. 不引入无界修复循环。
9. 不让 LLM 输出 entity_id / canonical_id。
10. 不把 fuzzy not found / ambiguous 静默通过。
11. 不把 model_proposed 伪装 verified。
12. 不因实验变绿解除 V8 live gate。

