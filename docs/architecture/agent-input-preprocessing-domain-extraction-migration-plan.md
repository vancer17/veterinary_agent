<!--
=============================================================================
文件: agent-input-preprocessing-domain-extraction-migration-plan.md
作用: 定义 V9 后“宏观感知 + 微观输出 + 受控 span 候选 + 按需富化 + 早退”
      的候选架构、稳定契约、质量门禁、迁移阶段和消费准入。
范围: 覆盖 TurnContext、显式 offset fixture、coarse span extractor、boundary
      calibration、bounded candidate pool、Macro Semantic Perception、
      span-id-only 输出、确定性治理、relation / canonical / participant /
      temporal / measurement 按需富化、per-claim graph 和 report-only 投影。
说明: 本文是当前候选实现方向权威；V2～V9 历史实验细节由对应实验总结维护。
维护: 当当前主线、稳定契约、候选池预算、早退规则、质量门禁或迁移准入调整时同步更新。
=============================================================================
-->

# Agent 输入前置预处理与领域抽取层迁移方案

> **文档状态**：V9 专项归因已完成；当前候选架构进入 V10 测量修复、边界校准、宏观契约修复和早退验证阶段；尚未达到生产消费准入
>
> **适用范围**：用户原始输入、可信 TurnContext、coarse span、受控候选池、话语行为、薄声明、证据锚定、主体与参与者、时间与度量、canonical mapping、问诊 / 临床安全 report-only 投影
>
> **不适用范围**：临床安全医学准入内容、OPA 策略细节、问诊状态存储实现、RAG 知识证据、自然语言回复生成、长期记忆写入策略、前端展示编排、具体模型与 prompt 实现

## 1. 背景与当前判断

临床安全阶段 0～5 已完成语义抽取、证据充分性、候选召回、`required_context` 和 OPA 裁决的框架化迁移。预发布回归暴露的核心缺口位于：

```text
用户原始输入
→ 可审计结构化声明
→ 领域可消费事实
```

第一轮 ideal injection 已证明：如果结构化事实正确，现有问诊状态融合和回答充分性链路可以消费。因此不应重写：

```text
ConsultationStateService
问诊回答充分性 OPA
临床安全召回 / required_context / OPA
```

V2～V9 实验进一步证明：

1. 深层嵌套契约和多级 NLP 流水线会造成 schema 崩溃与误差级联；
2. 一次全字段扁平抽取会产生 canonical surface 翻译、participant 缺失和 relation 漂移；
3. 薄声明与言语行为建模有效，但过度微拆任务会让模型失去全局语境；
4. relation classifier、canonical direct recall 和 participant resolver 在 gold 输入下可用；
5. GLiNER 能命中部分语义区域，但不是最终 exact boundary 权威；
6. V9 的首要问题包括 fixture 口径、span boundary、macro 输出和 relation adapter 契约；
7. 当前架构已明显变厚，必须引入最短充分路径和 continuation gate。

因此，当前不是继续加厚链路，而是执行 V10：

```text
修复测量尺；
校准 span 候选池；
修复 macro act / skeleton / binding；
稳定 relation adapter；
回归 gold winner；
验证早退与最短路径。
```

## 2. 目标与非目标

### 2.1 目标

1. 保留原始用户文本和连续证据链。
2. 用显式 offset / owner-relative fixture 建立可信评估基准。
3. 将 coarse span extractor 定位为语义先验定位器。
4. 通过通用边界校准生成 exact-offset、受预算约束的候选池。
5. 用一次宏观语义感知保留上下文、话语行为、claim skeleton 和 span 绑定。
6. 模型只输出 `span_id`，不输出 quote 字符串。
7. 用确定性代码完成 quote 反查、containment、主体解析和 gate。
8. 将 relation、canonical、participant、temporal、measurement 作为按需富化。
9. 复用 gold winner：
   ```text
   relation classifier
   canonical direct recall
   participant mention → TurnContext resolver
   ```
10. 用 per-claim graph 隔离 blocked / review / ready 状态。
11. 为每个组件建立 continuation gate 和 early exit reason。
12. 在所有质量与安全准入达成前保持 report-only。

### 2.2 非目标

1. 不重做临床安全阶段 0～5 框架。
2. 不修改临床安全资产医学准入内容。
3. 不重写问诊状态机。
4. 不恢复六步全局 NLP 流水线或深层嵌套 Stage 1 / Stage 2 schema。
5. 不继续手工拆分 intent / quote / relation 任务寻找模型认知“黄金分割点”。
6. 不在 Python、OPA 或 prompt 中实现医学关键词规则。
7. 不把前置分析层变成诊断、急诊判断或治疗建议层。
8. 不把临床安全 `observed_features` 未经契约转换写入问诊状态。
9. 不把问诊事实直接映射为临床安全动作。
10. 不引入兽医边界词典作为主路径。
11. 不让所有输入默认执行完整深链路。
12. 不因单轮 shadow 通过而接入生产。

## 3. 当前候选目标架构

### 3.1 双通道数据流

V10 必须同时维护：

```text
Golden / diagnostic 通道：
  explicit offset gold pool
  → interface audit
  → component gold injection
  → 归因报告

Live 通道：
  TurnContext
  → coarse span extractor
  → boundary calibration
  → bounded candidate pool
  → macro semantic perception
  → deterministic governance
  → on-demand enrichment
  → per-claim graph
  → report-only projection
```

Golden 通道不能解除 live phase gate，也不能作为生产 fallback。

### 3.2 目标运行链路

```text
TurnContext Assembly
→ Continuation Gate: 是否需要深链路
→ Coarse Span Extractor
→ Generic Boundary Calibration
   ├─ tokenizer boundary alignment
   ├─ nested / overlapping candidates
   ├─ deterministic temporal / measurement candidates
   └─ role-aware overlap pruning
→ Bounded Candidate Pool
→ Macro Semantic Perception
   ├─ discourse acts
   ├─ claim skeleton
   └─ optional span bindings
→ Deterministic Span Governance
→ On-demand Enrichment
   ├─ relation classifier
   ├─ canonical direct recall
   ├─ participant resolver
   ├─ temporal parser
   └─ measurement parser
→ Per-claim Graph
→ Report-only Domain Projection
```

### 3.3 最短充分路径

生产候选路径不应只有一条深链路。应按输入复杂度和下游消费价值分层：

```text
Lane 0：既有临床安全主路径
  不可被新链路早退绕过。

Lane 1：控制意图轻路径
  answer_now / correction / clarification 独立识别；
  无安全信号时可进入回答策略，不触发深链路。

Lane 2：简单问诊路径
  短输入、单宠物、低风险事实；
  只抽薄声明和基础 quote，不进入深 span / macro 链路。

Lane 3：标准富化路径
  仅在策略需要时执行 temporal / measurement / participant / canonical 富化。

Lane 4：复杂深路径
  长输入、多事实、多宠物、上传报告或多轮省略；
  可执行完整 report-only 深链路。
```

### 3.4 分层职责

| 层 | 职责 | 禁止事项 |
|---|---|---|
| Fixture / measurement | 显式 offset、owner、field role、expected signature | 不用全文第一次出现定位重复 mention |
| Coarse span extractor | 找到可能包含语义对象的粗区域 | 不输出最终 quote，不做事实判断 |
| Boundary calibration | 用通用 tokenizer / parser / 嵌套候选校准边界 | 不引入兽医词典主路径，不做模糊 quote 修复 |
| Candidate governance | role eligibility、去重、预算、role-aware pruning | 不让候选池无界增长，不用全局 NMS |
| Macro semantic perception | 全局语境、acts、claim skeleton、span 绑定 | 不输出 quote / canonical / entity ID / normalized semantics |
| Deterministic governance | offset、containment、quote 反查、状态一致性 | 不做医学判断 |
| On-demand enrichment | relation / canonical / participant / temporal / measurement | 不做无下游消费价值的富化 |
| Claim graph | per-claim blocked / review / ready 隔离 | 不做疾病或急诊分支状态机 |
| Projection | report-only 领域投影 | 不写生产状态，不触发 evaluator / OPA |

## 4. 稳定契约

### 4.1 TurnContext

TurnContext 是可信边界，至少包含：

```text
user_id
pet_id / pet entity candidates
session_id
task context
current time / timezone
source / source_block
trusted pet profile summary
previous question target
multi-pet context
```

用户文本不能覆盖服务端可信实体身份。

### 4.2 EvaluatedSpanField

fixture 与评估层必须显式表达：

```text
claim_owner
field_role
start
end
text
expected_label_candidates
occurrence_locator
source_block_id
```

约束：

1. `start/end` 是权威定位；
2. 重复 mention 使用 owner-relative locator；
3. 同一 boundary 可服务多个 field role；
4. label conflict 必须按 field role 拆分，不得混成全局冲突；
5. relation expectation 缺 relation span 时标记 `fixture_incomplete`。

### 4.3 CalibratedSpanCandidate

每个候选必须来自原文 offset：

```text
span_id
start / end
text
parent_span_id
role_eligibility
label_candidates
score
source_extractors[]
extractor_versions
candidate_status
```

允许：

```text
重叠与嵌套候选；
同一 offset 多角色；
多 extractor provenance 合并。
```

禁止：

```text
模型自由写 quote；
编辑距离修复；
embedding 相似修复；
同义词替换；
候选池无界增长。
```

### 4.4 MacroDiscourseAct

宏观模型输出：

```text
act_type
detected
evidence_span_id
confidence
needs_review
```

显式 act 为 true 时必须有 evidence span。acts 为空时必须输出：

```text
no_act_reason
```

### 4.5 MacroThinClaim

claim 分两层输出，避免 optional binding 污染 skeleton：

#### Skeleton

```text
claim_id
support_span_id / support_anchor_span_ids
target_span_id
statement_type
coarse_type
confidence
```

#### Optional bindings

```text
relation_span_id
subject_span_id
participant_span_ids
temporal_span_id
measurement_span_id
```

评估时分别报告 skeleton 与 binding 质量。

### 4.6 GovernedUserClaim

由代码从 macro claim 反查并校验：

```text
claim_id
support_quote
target_quote
relation_quote
subject_mention
participant_mentions
temporal_quote
measurement_quote
quote_state
binding_state
claim_state
```

任何无法回指原文 offset 的候选不得进入 projection。

### 4.7 Claim Graph State

每条 claim 独立维护：

```text
quote_state
statement_state
subject_state
participant_state
temporal_state
measurement_state
relation_state
canonical_state
projection_state
```

状态包括：

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

单条 claim 的失败不得自动污染其他 claim。

### 4.8 EarlyExitDecision

每个可执行组件输出：

```text
stage_name
execution_status: executed / skipped / early_exit / blocked
exit_reason
downstream_effect
trace_id
latency
model_call_count
cost
review_required
```

允许的 exit reason：

```text
not_required_by_policy
sufficient_confidence
no_downstream_consumer
prerequisite_missing
budget_exceeded
upstream_failed
safety_boundary_preserved
```

## 5. Span candidate 治理

### 5.1 Coarse span extractor

GLiNER 或等价模型只作为 semantic prior locator。可以评估：

```text
当前 GLiNER 权重；
多语言 GLiNER 权重；
SpanMarker + 中文底座；
其他中文 span extractor。
```

但不能把其输出当最终 quote。

### 5.2 Boundary calibration

候选校准来源：

```text
通用中文 tokenizer boundary alignment；
嵌套 / overlapping candidate generation；
确定性时间 / 度量 / 频率 parser；
受控 anchor phrase candidate（仅未来 shadow 候选，非当前主路径）；
SpanMarker / boundary reranker。
```

所有候选必须保留 exact offset 和 extractor provenance。

### 5.3 Role eligibility

候选必须按角色分组：

```text
support
target
relation
temporal
measurement
subject / participant
intent evidence
question evidence
```

非时间候选不得进入 temporal 组；非控制意图候选不得进入 act evidence 组。

### 5.4 Candidate budget

必须有：

```text
per-role top-k；
per-claim-region limit；
per-source-block limit；
per-turn total limit。
```

超限时执行：

```text
rerank；
role-aware pruning；
分区；
complexity review。
```

不得随意截断或让 Macro LLM 面对过载候选池。

### 5.5 兽医边界词典边界

当前 default 为：

```text
no veterinary boundary dictionary in main path
```

若未来重新评估，只能来自 canonical alias registry 的派生视图，并满足：

```text
shadow-only；
极小规模；
仅生成边界候选；
版本化；
负例回归；
held-out 验证。
```

不得用于事实判断、槽位填充、canonical 判定或临床安全触发。

## 6. 确定性治理

### 6.1 Span Reference Gate

校验：

```text
span_id 存在；
offset 有效；
span text 与原文一致；
source block 正确；
role eligibility 匹配。
```

### 6.2 Quote Resolution Gate

quote 只能由代码生成：

```text
raw_text[start:end]
```

模型输出自由 quote 字符串时 blocking。

### 6.3 Containment Gate

校验：

```text
target 在 support envelope 内；
relation 在 support envelope 内；
subject / participant 与 support 关系可解释；
temporal / measurement 绑定 claim 正确。
```

### 6.4 Support Envelope

support 可以来自：

```text
显式 support candidate；
target / relation / subject anchor 的最小覆盖 envelope；
同一 source block 内的受限范围。
```

不得跨越无关句或吞并其他 claim。

### 6.5 Intent Evidence Gate

显式 intent 为 true 时：

```text
evidence_span_id 必须存在且合法。
```

acts 为空时必须显式输出 no_act_reason。

## 7. 按需富化

### 7.1 Relation classification

输入：

```text
support_quote
target_quote
relation_quote
```

输出：

```text
absolute_status
no_change
change
unclear
```

relation adapter 必须版本化，禁止动态 calibration fallback。

### 7.2 Canonical mapping

输入：

```text
target_quote
coarse_type
subject status
```

流程：

```text
candidate retriever
→ candidates[]
→ selected_candidate_id
→ canonical_id
```

硬边界：

```text
confirmed 必须来自候选；
无候选不得 confirmed；
not_found / unmapped 必须 review。
```

### 7.3 Subject / participant resolution

宏观模型只输出 mention span；代码结合 TurnContext 解析：

```text
mention span
→ entity candidate / resolved / ambiguous / unresolved
```

禁止：

```text
resolved + empty entity；
发明实体；
自由字符串 entity ID。
```

### 7.4 Temporal / measurement parsing

确定性 parser 优先，无法保守解析时输出：

```text
unresolved
unresolved_reason
review_required
```

不得猜测口语量词数值或强行精确化近似时间。

### 7.5 Aggregate target decomposition

只在策略需要具体槽位时触发。子 claim 必须继承：

```text
support quote
relation quote
subject
source block
```

拆分必须候选驱动，不得用中文关键词规则。

## 8. 中间件候选边界

| 候选 | 允许用途 | 禁止事项 |
|---|---|---|
| GLiNER / multi-language GLiNER | coarse semantic prior span | 不做最终边界或事实判断 |
| SpaCy / HanLP / LTP / Jieba | 通用 tokenizer boundary suggestion | 不做医学规则，不决定事实 |
| SpanMarker + 中文底座 | supervised boundary refinement 候选 | 需 train/dev/held-out，未验证不得生产 |
| Instructor | Pydantic schema adapter | retry 不替代 Quality Gate |
| BAML | typed schema / assert / prompt 版本治理 | 单次 quick control 不能宣布 winner |
| DSPy | 后期 prompt / few-shot 优化 | 不得读取 held-out，不得替代确定性 metric |
| FlashText / PhraseMatcher | 未来边界候选 shadow 适配器 | 当前不进入主路径，不做事实判断 |

## 9. V10 迁移阶段

### 阶段 0：fixture 与接口审计

交付：

```text
显式 offset；
owner-relative locator；
field role 拆分；
relation span 补齐；
deterministic interface audit。
```

退出：

```text
fixture integrity 通过；
V7/V9 gold winner 可复现；
无接口转换矛盾。
```

### 阶段 1：boundary calibration 与候选池治理

交付：

```text
coarse span → calibrated candidate pipeline；
role eligibility；
candidate budget；
role-aware pruning；
candidate pool metrics。
```

退出：

```text
live span pool 达到 phase gate；
候选池不超载；
macro binding 不因候选数量退化。
```

### 阶段 2：macro golden 修复

交付：

```text
acts 输出契约；
claim skeleton 契约；
optional binding 分层评估；
support envelope 规则。
```

退出：

```text
acts 不再默认为空；
skeleton 稳定；
binding 失败可独立归因。
```

### 阶段 3：relation adapter 契约

交付：

```text
固定序列化格式；
固定 batch size；
版本化 prompt / few-shot；
missing span 的 not-evaluable 语义。
```

退出：

```text
不依赖动态 calibration；
development 3 次冷调用稳定。
```

### 阶段 4：gold winner 回归

交付：

```text
canonical target regression；
participant mention regression；
temporal / measurement parser regression。
```

退出：

```text
macro span 供给可归因；
无发明 canonical / entity；
gold winner 质量保持。
```

### 阶段 5：早退与最短路径

交付：

```text
continuation gate；
early exit reason；
simple / standard / deep lane；
组件 value-of-information 对照。
```

退出：

```text
质量不回退；
model call / latency / cost 降低；
安全主路径保留。
```

### 阶段 6：有限 live retest

交付：

```text
calibrated live span + macro + relation + winner regression。
```

退出：

```text
invalid span reference / binding = 0；
projection 只消费 ready claim。
```

### 阶段 7：重复与确认性验证

交付：

```text
REP-COLD；
HELD-OUT-V10；
token / cost / latency report。
```

退出：

```text
3 次冷调用 signature 稳定；
held-out 通过；
所有版本冻结。
```

## 10. Trace 与指标

### 10.1 Evidence lineage

```text
turn
→ source block
→ coarse span
→ calibrated candidate
→ macro act / claim
→ governed quote
→ enrichment result
→ claim graph state
→ report-only projection
```

### 10.2 核心指标

```text
boundary coverage
field coverage
unambiguous label accuracy
role binding accuracy
candidate count per role / turn
false candidate rate
macro act precision / recall
claim skeleton precision / recall
optional binding accuracy
invalid span reference / binding
relation adapter accuracy
canonical recall / selection accuracy
participant resolution accuracy
early exit reason distribution
model call count
latency p50 / p95
token / cost availability
```

## 11. 渐进消费顺序

1. report-only shadow；
2. 独立 `answer_now` API shadow；
3. 简单问诊薄声明消费评估；
4. 标准按需富化消费评估；
5. 复杂深链路 report-only 对照；
6. 跨 Agent 一致性评估；
7. 临床安全 adapter 评估最后进行。

在任何阶段，`answer_now` 都不得绕过 urgent / blocked 安全信号。

## 12. 异步 API shadow 边界

API shadow 必须异步化，并具备：

```text
采样；
限流；
阶段超时；
熔断；
有界队列；
queue full 显式记录；
幂等 snapshot；
worker lease；
retry / dead letter；
trace 持久化；
失败隔离。
```

主请求只允许采样和入队，不得等待模型链路完成。

## 13. 迁移完成判定

当以下条件同时满足时，才可认为候选架构完成：

1. deterministic interface audit 长期通过；
2. calibrated live span pool 达标且候选池受控；
3. macro act / skeleton / binding 均达标；
4. relation adapter 稳定且无动态 calibration 依赖；
5. canonical / participant / temporal / measurement regression 保持 gold 质量；
6. early exit 不造成质量或安全回退；
7. report-only projection 只消费 ready claims；
8. held-out 确认性实验通过；
9. token、成本、延迟和错误率满足预算；
10. 人工 review 闭环可运行；
11. 无临床安全误升级、denied 变 present 或 urgent / blocked 回退；
12. 未接入任何生产状态或临床安全 evaluator / OPA。

## 14. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| Fixture offset / role 不干净 | 模型比较失真 | Phase 0 measurement before model |
| GLiNER boundary 不准 | exact quote 失败 | coarse定位 + boundary calibration |
| 候选池过载 | Macro 注意力发散、成本上升 | role eligibility、预算、rerank |
| 中文 tokenizer 过拟合 | 边界错误或隐性规则 | tokenizer 只做建议，多方案对照 |
| Macro schema 过载 | acts / claim 漂移 | skeleton 与 binding 分层 |
| Relation batch 敏感 | winner 无法集成 | 版本化 adapter 契约 |
| 架构过厚 | 延迟和归因成本上升 | continuation gate、最短充分路径 |
| Golden 冒充 live | 虚假准入 | golden/live 双通道标记 |
| Cache replay 冒充重复 | 稳定性误判 | REP-COLD 禁用 cache |
| 阶段越界 | 归因混乱 | live phase gate 保持不变 |

回滚策略：

```text
shadow 可直接停止；
中间件 adapter 可独立替换；
span extractor / tokenizer / prompt / schema 版本化回滚；
clinical safety 与问诊生产路径始终未被接入。
```

## 15. 有意 TODO

1. Span extractor / boundary calibration 最终选型；
2. Candidate budget 与 role eligibility 参数；
3. Macro act / skeleton / binding prompt 契约；
4. Relation adapter 版本化契约；
5. BAML / Instructor / response_format winner；
6. DSPy train / dev / held-out 防泄漏流程；
7. token / cost usage 采集；
8. 持久化异步 API shadow worker；
9. report-only projection 后续消费评估；
10. 临床安全 adapter 空壳契约。

这些 TODO 未完成时，应保留显式 `not_implemented` 或 report-only 状态，不允许由前置分析层或其他领域代为实现。

## 16. 关联材料

1. [agent-input-preprocessing-shadow-experiment-architecture-guidance.md](agent-input-preprocessing-shadow-experiment-architecture-guidance.md)
2. [input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md](input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md)
3. [input-preprocessing-v9-attribution-change-summary.md](input-preprocessing-v9-attribution-change-summary.md)
4. [input-preprocessing-v8-shadow-runner-change-summary.md](input-preprocessing-v8-shadow-runner-change-summary.md)
5. [input-preprocessing-v7-core-attribution-microbench-change-summary.md](input-preprocessing-v7-core-attribution-microbench-change-summary.md)
6. [input-preprocessing-v6-sixth-round-shadow-experiment-change-summary.md](input-preprocessing-v6-sixth-round-shadow-experiment-change-summary.md)
7. [input-preprocessing-v5-fifth-round-shadow-experiment-change-summary.md](input-preprocessing-v5-fifth-round-shadow-experiment-change-summary.md)
8. [input-preprocessing-v4-fourth-round-shadow-experiment-change-summary.md](input-preprocessing-v4-fourth-round-shadow-experiment-change-summary.md)
9. [input-preprocessing-v3-third-round-shadow-experiment-change-summary.md](input-preprocessing-v3-third-round-shadow-experiment-change-summary.md)
10. [input-preprocessing-v2-second-round-shadow-experiment-change-summary.md](input-preprocessing-v2-second-round-shadow-experiment-change-summary.md)
11. [consultation-semantic-extraction-change-summary.md](consultation-semantic-extraction-change-summary.md)
12. [consultation-state-answerability-change-summary.md](consultation-state-answerability-change-summary.md)
13. [clinical-safety-stage5-preprod-verification-change-summary.md](clinical-safety-stage5-preprod-verification-change-summary.md)
