<!--
=============================================================================
文件: agent-input-preprocessing-shadow-experiment-architecture-guidance.md
作用: 收敛 V2～V9 输入前置预处理实验结论，定义 V10 修复、边界校准、
      宏观语义归因、早退纪律和 shadow 实验的当前执行边界。
范围: 覆盖 fixture / offset 口径、GLiNER 粗定位、通用边界校准、候选池预算、
      macro span-id-only 输出、relation adapter、gold winner 回归、
      早退机制、重复稳定性和 report-only 安全边界。
说明: 本文只保留当前仍有效的方向、契约边界和实验纪律；历史实验过程、模型输出、
      报告哈希和复现命令由对应实验总结维护。
维护: 当 V10 根因结论、稳定契约、中间件评估结论、早退规则或准入条件变化时同步更新。
=============================================================================
-->

# Agent 输入前置预处理 Shadow 实验架构指导

> **文档状态**：V9 专项归因已完成；当前进入 V10“测量修复 + 边界校准 + 宏观契约修复 + 早退纪律”阶段；未达到生产消费准入
>
> **文档定位**：后续输入前置预处理实验与实现的方向权威，用于防止回到全局 NLP 流水线、深层嵌套契约、无限候选池、静态词典主路径、自由 quote 输出或“工具链可用即语义可用”的误区
>
> **适用范围**：TurnContext、显式 offset fixture、coarse span extractor、boundary calibration、bounded candidate pool、宏观语义感知、span-id-only 输出、确定性 quote governance、按需富化、早退机制和 report-only 投影
>
> **不适用范围**：生产 prompt 设计、模型选型承诺、问诊状态存储实现、临床安全医学资产、线上观测平台建设

## 1. 文档定位与证据分工

本文是历史实验结论到当前架构方向的收敛层。它不保留已被后续实验证伪的实现路线，也不复制各轮实验细节。

| 材料 | 当前职责 |
|---|---|
| 本文 | 当前实验原则、边界、V10 对照矩阵、准入条件和防漂移规则 |
| [agent-input-preprocessing-domain-extraction-migration-plan.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-domain-extraction-migration-plan.md) | 当前候选目标架构、稳定契约、早退机制和迁移阶段 |
| [input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md) | V10 可执行实验矩阵、指标和退出条件 |
| [input-preprocessing-v9-attribution-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v9-attribution-change-summary.md) | V9 根因排序、接口审计、span / macro / relation 归因和权威报告 |
| [input-preprocessing-v8-shadow-runner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v8-shadow-runner-change-summary.md) | V8 全阶段 runner、live stage gate 和 phase 阻断记录 |
| [input-preprocessing-v7-core-attribution-microbench-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v7-core-attribution-microbench-change-summary.md) | relation classifier、canonical direct recall 和 participant resolver 的 golden winner 证据 |
| `docs/architecture/input-preprocessing-v2-*` 至 `input-preprocessing-v8-*` | 历史实验过程记录，不再作为当前实现方向权威 |
| `tests/fixtures/input_preprocessing/*.json` | 样本、expected signature、golden control 和 negative mutation 的工程权威 |

## 2. V9 后当前结论

### 2.1 仍然有效的结论

1. 下游问诊状态与回答充分性链路可以消费正确结构化事实；不应重写 `ConsultationStateService` 或问诊回答充分性 OPA。
2. 薄声明、言语行为建模、原文证据锚定和 per-claim 状态隔离仍是有效方向。
3. V7/V9 gold 证据支持三个局部 winner：
   ```text
   independent relation classifier
   golden target quote + coarse type 的 canonical direct recall
   owner-scoped participant mention → TurnContext resolver
   ```
4. 以下工程边界有效：
   ```text
   phase admission gate；
   span-id-only 输出；
   quote 由代码按 offset 反查；
   自由 quote 输出阻断；
   deterministic negative gate；
   held-out / DSPy 默认冻结；
   异步 queue full / dead letter / trace 隔离；
   report-only 安全边界。
   ```
5. Run cache 可以消除重复调用，但 cache replay 不能作为冷启动稳定性证据。

### 2.2 V9 后的主要 blocker

| 根因 | 当前结论 | 后续动作 |
|---|---|---|
| Measurement / fixture | 重复 mention owner 定位错误；同 boundary 多 expected label；2 条 relation expectation 缺 relation span | 先升级显式 offset / owner-relative fixture，再比较模型 |
| Live span extractor | near-or-exact 高但 exact boundary 和 ranking 不稳定；降低 threshold 导致 precision 崩溃 | GLiNER 降级为粗定位器，引入通用 boundary calibration |
| Macro semantic output | acts 全部为空；claim skeleton / granularity 不稳定；optional binding 与 support envelope invalid 多 | 在 golden pool 下分 act / skeleton / binding 归因修复 |
| Relation adapter | V7 winner 原控制组可用；V8 gold records 单独输入全 unclear；加 calibration batch 后恢复 | 建立版本化、固定输入序列化和 batch 契约，禁止动态 calibration fallback |
| Canonical / participant | gold 输入下可用，当前不是首要 blocker | 仅做回归，等待 macro target / participant span 供给 |
| 架构厚度 | 全链路执行导致延迟、成本和归因负担上升 | 引入 continuation gate 和最短充分路径 |

### 2.3 当前候选主线

```text
显式 offset / owner-relative fixture
→ coarse span extractor
→ generic boundary calibration
→ role eligibility + bounded candidate pool
→ macro perception, micro span-id output
→ deterministic quote / containment governance
→ versioned relation adapter
→ gold-passed canonical / participant regression
→ on-demand temporal / measurement parsing
→ per-claim graph
→ report-only projection
```

该主线仍是候选架构，不是生产承诺。

### 2.4 已移除的实现方向

以下路线保留在历史实验总结中，不再作为当前实现方向：

```text
六步全局 NLP 流水线；
V2 深层嵌套 Stage 1 / Stage 2 契约；
V3 继续拆分 scope / assertion / participant 的长流水线；
V4 一次全字段扁平抽取；
V5 / V6 逐 claim 微型富化；
V7 把归因 microbench 直接当 runtime 架构；
V8 在 live span 未达标时继续执行下游大矩阵；
继续手工微调 GLiNER threshold 寻找最终精确边界；
让模型自由输出 quote、canonical surface、entity ID 或 normalized semantics；
把兽医边界词典作为主路径；
让所有输入默认执行完整深链路。
```

## 3. V10 实验原则

### 3.1 Measurement before model

fixture 未满足以下条件前，不进行模型选型或 adapter winner 判断：

```text
显式 start / end offset；
owner-relative locator；
field-specific expected span；
同 boundary 多 role 显式建模；
relation span 完整；
boundary / field / label / role 指标拆分。
```

同一 boundary 可以同时服务 support、target、relation、subject 等角色；这不是 label conflict，不能用单一全局 label accuracy 混淆评估。

### 3.2 Golden 与 Live 双通道不可混淆

```text
Golden / gold injection：
  用于诊断组件能力和接口契约；
  不能解除 live phase gate；
  不能作为生产 fallback。

Live：
  用于阶段准入；
  上游未达标时后续实验保持 upstream_blocked。
```

### 3.3 GLiNER 是粗定位器，不是边界权威

GLiNER 只负责给出可能包含目标、关系、时间、度量、主体或控制意图的粗区域。

最终边界由以下组合完成：

```text
通用 tokenizer boundary alignment；
嵌套 / 重叠候选生成；
确定性时间 / 度量候选；
role-aware overlap pruning；
轻量 reranker 或 SpanMarker 类 boundary refiner；
宏观模型选择 span_id；
代码 offset / containment 校验。
```

不能通过降低 GLiNER threshold 换取 recall，也不能用 relaxed overlap 替代正式 exact boundary gate。

### 3.4 候选池必须小而充分

候选池必须有：

```text
role eligibility；
claim-local candidate view；
exact offset 去重；
extractor provenance merge；
role-aware NMS；
per-role / per-turn candidate budget；
必要时轻量 reranker。
```

禁止全局 NMS 删除必要嵌套 span。候选过载时先 rerank / 分区 / role pruning，仍超限则进入 complexity review，不能随意截断。

### 3.5 兽医边界词典不进入主路径

当前不引入兽医边界词典作为 default。原因：

```text
静态资产治理压力高；
容易形成第二套 canonical vocabulary；
容易退化成隐性关键词规则；
无法覆盖中文口语长尾；
会间接偏置 macro span 选择。
```

如果未来重新评估，只能作为：

```text
shadow-only；
极小规模；
从 canonical alias registry 派生；
仅用于边界候选；
版本化、负例回归并通过 held-out 验证。
```

### 3.6 模型只做宏观语义感知

宏观模型可以：

```text
理解全局语境；
选择 span_id；
识别 discourse acts；
生成 claim skeleton；
绑定 target / relation / subject / participant / temporal / measurement span。
```

模型不能：

```text
输出自由 quote 字符串；
修正 offset；
发明 entity / canonical；
输出 normalized semantics；
承担精确字符切片。
```

### 3.7 Relation adapter 必须版本化

V9 的 calibration batch 只能作为诊断证据，不能成为生产机制。

Relation adapter 必须固定：

```text
input serialization；
batch size；
prompt version；
versioned few-shot（如确实需要）；
missing relation span 的 not-evaluable 语义。
```

### 3.8 引入 Continuation Gate 与最短充分路径

每个组件执行前必须回答：

```text
谁消费输出？
是否能改变下游决策？
前置字段是否满足？
当前结果是否已足够？
预算是否允许？
失败后路径是什么？
```

允许的 early exit reason：

```text
not_required_by_policy
sufficient_confidence
no_downstream_consumer
prerequisite_missing
budget_exceeded
upstream_failed
```

成功早退、失败早退和 review 早退必须区分。失败早退不得解释为用户未提供事实，也不得绕过既有临床安全主路径。

## 4. V10 对照实验矩阵

详细实验定义见
[input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md)。

| Phase | 实验 | 目标 | 退出条件 |
|---|---|---|---|
| 0 | `FIXTURE-OFFSET` / `FIELD-ROLE-SPLIT` / `RELATION-SPAN-COMPLETE` / `INTERFACE-AUDIT` | 修复测量尺和接口契约 | fixture integrity 通过；接口审计无实现矛盾 |
| 1 | `SPAN-CALIBRATE` / `SPAN-BUDGET` / `SPAN-MODEL` / `SPANMARKER-CHINESE` | 将 GLiNER 粗命中转为受控 exact-offset 候选池 | live span pool 达到 phase gate 且候选池不超载 |
| 2 | `MACRO-ACT` / `MACRO-SKELETON` / `MACRO-BINDING` / `MACRO-FULL` / `MACRO-CANDIDATE-LOAD` | 修复 acts、claim skeleton 和 optional binding | golden macro 达标；空 acts 有显式 no_act_reason |
| 3 | `REL-SINGLE` / `REL-BATCH-FIXED` / `REL-VERSIONED-FEWSHOT` / `REL-MISSING` | 建立稳定 relation adapter | 不依赖动态 calibration；3 次冷调用稳定 |
| 4 | `CAN-REGRESSION` / `PARTICIPANT-REGRESSION` | 回归 gold winner 是否可消费 macro 输出 | target / participant span 供给可归因；无发明实体或 canonical |
| 5 | `EARLY-MINIMAL` / `EARLY-VOI` / `EARLY-BUDGET` / `EARLY-ROUTER` / `EARLY-FAILURE` | 验证最短充分路径和组件继续执行价值 | 质量不回退；model call / latency / cost 下降；安全边界保留 |
| 6 | `REP-COLD` / `HELD-OUT-V10` | 只对 finalist 做确认 | 3 次冷调用 signature 稳定；held-out 通过 |

任何 Phase 失败，都先做专项归因和最小修复，不得直接进入 integration。

## 5. 统一报告与归因

每个 V10 实验必须记录：

```text
experiment_id
phase
lane: deterministic / golden / live / regression / early-exit
model / prompt / schema / policy / gate version
span extractor / tokenizer / calibration version
candidate budget
fixture version
cache status
cold run status

boundary / field coverage
unambiguous label accuracy
role binding
candidate pool metrics
macro act / skeleton / binding metrics
relation adapter metrics
canonical / participant regression metrics
early exit reason and downstream decision impact

first attempt status
retry count
model call count
latency
token count / cost availability
failure attribution
safety boundary status
```

推荐归因码：

```text
fixture_offset_invalid
fixture_role_conflict_unresolved
field_coverage_missing
boundary_near_but_not_exact
tokenizer_boundary_error
candidate_pool_overload
role_eligibility_error
candidate_budget_exceeded

macro_act_schema_error
macro_act_prompt_error
macro_claim_skeleton_error
macro_optional_binding_error
macro_support_envelope_error

relation_adapter_format_error
relation_batch_context_sensitivity
relation_missing_span
relation_classifier_error

canonical_target_binding_missing
participant_span_binding_missing

early_exit_sufficient
early_exit_not_required
early_exit_upstream_failed
early_exit_false_positive
upstream_blocked
gate_blocked_expected
```

## 6. 硬性指标与安全边界

### 6.1 Integration 硬边界

任何进入 integration 的结果必须满足：

```text
invalid span reference = 0
model free quote output = 0
accepted claim quote resolution rate = 100%
true intent without evidence span = 0
resolved participant with empty entity = 0
invented entity = 0
selected canonical outside candidates = 0
confirmed canonical without candidates = 0
projection consuming blocked claim = 0
```

### 6.2 Report-only 安全边界

所有 V10 实验必须保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

不得接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

## 7. 明确不做事项

1. 不在 fixture 未修复前比较模型或宣布 adapter winner；
2. 不恢复六步全局 NLP 流水线或深层嵌套 schema；
3. 不继续手工拆分 intent / quote / relation 寻找所谓“黄金分割点”；
4. 不让模型自由输出 quote、canonical surface、entity ID 或 normalized semantics；
5. 不用关键词、正则、短语匹配或兽医边界词典补事实、span 或 canonical；
6. 不把 GLiNER 输出当事实或最终 quote；
7. 不用 relaxed overlap 替代 exact boundary gate；
8. 不让候选池无界增长；
9. 不用全局 NMS 删除必要嵌套 span；
10. 不把 tokenizer 或词典结果当医学事实；
11. 不把 calibration batch 当生产 fallback；
12. 不因 golden / gold injection 通过而解除 live phase gate；
13. 不让 DSPy 接触 held-out；
14. 不用 LLM judge 替代确定性 metric；
15. 不把 runner 可用当语义可用；
16. 不让所有输入默认执行完整深链路；
17. 不用早退掩盖上游失败或绕过既有临床安全主路径；
18. 不把 model call count 下降等同整体成本下降；
19. 不把 cache replay 当冷启动稳定性；
20. 不因单次实验通过接入生产主链路。

## 8. 执行顺序

```text
1. fixture offset / role / relation span 修复
2. deterministic interface audit
3. generic boundary calibration + bounded candidate pool
4. macro act / skeleton / binding golden 归因
5. versioned relation adapter
6. canonical / participant regression
7. early exit 与最短路径对照
8. 有限 live retest
9. REP-COLD
10. HELD-OUT-V10
11. 仅在 baseline 稳定且 train / dev 明确后评估 DSPy
```

## 9. 维护规则

本文应在以下情况更新：

1. V10 fixture、span calibration、macro、relation adapter 或 early exit 结论变化；
2. span-id-only 契约或 Quality Gate 调整；
3. 候选池预算或 role eligibility 规则调整；
4. 兽医边界词典从“非主路径”变为受控 shadow 候选；
5. held-out、冷启动或重复稳定性规则调整；
6. report-only 或 clinical safety 边界调整。

本文不应记录：

1. 具体 prompt 原文；
2. 内部函数实现细节；
3. 远程服务密钥；
4. 样本专用修复逻辑；
5. 完整模型输出和临时报告路径；
6. 已被后续实验证伪的历史架构实现细节。
