<!--
=============================================================================
文件: gliner-role-span-finetuning-migration-plan.md
作用: 保留 V12 后曾规划的 GLiNER / supervised role-aware span extractor 微调
      路线，作为历史架构对照材料。
范围: 覆盖中文兽医口语 role-aware span 标注、GLiNER 微调试点、SpanMarker /
      SpanCategorizer 对照、support anchor 与 shared scope 专项、fresh held-out
      确认性验证和下游 seed 消费回归。
说明: 本文只定义微调阶段边界；实验过程、报告路径和复现命令由后续 change summary
      维护。微调模型不得判断医学事实或替代临床安全链路。
维护: 仅在需要追溯 V12 后 supervised span extractor 设计时维护；该路线不再是
      当前主迁移方向。
=============================================================================
-->

# GLiNER Role-aware Span 微调迁移方案

> **文档状态**：已被 V13 LLM-first Structured Claim Generation 方案取代；仅作历史对照，不再执行
>
> **文档定位**：V12 后 supervised span extractor 路线的历史设计记录
>
> **适用范围**：GLiNER fine-tuning pilot、显式 offset 标注数据、role-specific span 标签、
> support anchor / shared scope / multi-role 专项、fresh held-out、下游 structural seed 回归
>
> **不适用范围**：医学事实分类、canonical 最终裁决、急诊风险判断、问诊槽位填写、
> required_context 判断、临床安全 evaluator / OPA 接入和生产消费准入

## 1. 背景与结论

> **Superseded**：当前权威方向见
> [agent-input-preprocessing-shadow-experiment-architecture-guidance.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-shadow-experiment-architecture-guidance.md)
> 与 [input-preprocessing-v13-llm-first-structured-claim-experiment-plan.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-experiment-plan.md)。
> 不应按本文继续实施 GLiNER 微调或 support-first graph 主路径。

V8～V12 已将输入前置预处理的问题收敛为：

```text
1. full candidate snapshot 的 available exact recall 只有 0.6585；
2. V12 support-first graph ranking 可将 gold_in_view 提升到 0.5610；
3. structural seed recall 从 0 恢复到 0.34，但 seed precision 仅 0.0448；
4. 所有当前 conflict pruning / NMS 变体均会误删必要 gold；
5. Macro 在严格 gate 下仍失败，且存在 timeout / circuit breaker；
6. relation frozen contract 已在 development 三次冷调用稳定；
7. canonical / temporal / measurement / participant resolver 在 gold 输入下可用。
```

因此当前首要 blocker 不是继续调整后处理，而是：

```text
上游 role-aware span candidate generation 质量不足。
```

后处理无法创造缺失 span，也无法修复不可靠的 role label 和 score。下一阶段应转向
**supervised role-aware span extractor**，首选试点为 GLiNER fine-tuning，同时保留
SpanMarker / SpaCy SpanCategorizer 作为对照候选。

## 2. 微调目标与非目标

### 2.1 目标

GLiNER 微调后的目标行为是：

```text
输入中文兽医口语原文和受控结构角色标签
输出 exact-offset role span candidates
```

每个候选只包含：

```text
start
end
text
label / role
score
extractor_version
```

微调目标是提升：

1. exact boundary recall；
2. boundary precision；
3. role coverage；
4. unambiguous label accuracy；
5. support anchor candidate 质量；
6. shared scope / multi-target 覆盖；
7. multi-role boundary 表达；
8. score calibration；
9. 下游 structural seed precision / recall；
10. Macro 输入规模可控性。

### 2.2 非目标

微调后的 GLiNER 不得输出或决定：

```text
canonical_id
医学事实
疾病结论
急诊风险
required_context
问诊槽位
clinical safety action
claim statement type
用户意图
```

它也不得通过医学词表、症状词典或疾病词典补造 span。

## 3. 为什么不继续优先做后处理

V10～V12 已经证明：

```text
nested candidate generation 可以提高 exact recall；
role eligibility 可以改善 role coverage；
support-first graph 可以改善 gold_in_view；
但候选 precision、label accuracy 和 seed precision 仍低。
```

V12 中所有 pruning variant 的 gold retention 都低于 no pruning：

| variant | gold retention |
|---|---:|
| no pruning | 1.0 |
| global filter_spans | 0.4208 |
| same-role NMS | 0.4100 |
| same-anchor-role NMS | 0.4100 |
| score-margin NMS | 0.4684 |

因此：

```text
NMS / conflict pruning 降级为后置对照；
不得作为 GLiNER 微调前的主路径；
任何 pruning 必须 gold retention 不低于 no pruning。
```

## 4. 为什么不让 LLM 直接完成 span 抽取

多轮实验证明，LLM 可以参与语义判断，但不应作为主路径上的字符级 span 边界权威。

### 4.1 LLM span 抽取的失败模式

#### 1. 把整句 evidence 当 target

V7 `QUOTE-GOLDEN-SELECT` 中，模型倾向输出：

```text
target = 没有呕吐、干呕、反流、流涎或舔唇
```

而期望是：

```text
support = 没有呕吐、干呕、反流、流涎或舔唇
relation = 没有
target = 呕吐 / 干呕 / 反流 / 流涎 / 舔唇
```

#### 2. 输出省略式伪 quote

例如：

```text
精神...都正常
```

这不是原文 substring，而是模型生成的省略表达，会被 quote gate 阻断。

#### 3. 多 quote 字段错位

当 schema 同时包含：

```text
target_quote
relation_quote
subject_quote
temporal_quote
measurement_quote
```

模型曾把 `没有呕吐` 填入 temporal 字段，而不是拆成：

```text
relation = 没有
target = 呕吐
```

#### 4. quote 有效但语义漂移

V4 曾出现：

```text
没有呕吐 → absent
精神正常 → present
```

说明 quote anchoring 是必要条件，不是充分条件。

#### 5. canonical surface 被翻译

V4 中出现过：

```text
血便 → hematochezia
精神还行 → normal energy level
换粮 → diet change
```

这会导致 canonical 候选召回失败和 under-confirmation。

#### 6. 输出不稳定且延迟高

Macro 多轮 REP 中 claim / binding signature 未达到 3/3 一致；V12 还出现
structured request timeout 和 circuit breaker。

### 4.2 当前 LLM 的正确位置

LLM 可以继续用于：

```text
macro semantic perception；
discourse act 判断；
claim skeleton 判断；
statement type / relation verifier；
participant role 语义绑定；
低置信样本 review。
```

但只能在 role-specific candidate menu 中选择 `span_id`，不得自由输出 quote、
start/end 或 canonical ID。

核心分工是：

```text
span extractor 负责“原文哪里说了”；
LLM 负责“这是什么意思”；
code gate 负责“证据是否可信”。
```

## 5. 标签体系

### 5.1 结构角色标签

微调标签必须是通用语言 / 结构角色，而不是医学本体：

```text
support_region
target_mention
relation_expression
temporal_expression
measurement_expression
subject_mention
agent_mention
recipient_mention
object_mention
action_event
state_mention
control_intent_expression
question_expression
```

### 5.2 禁止医学标签

禁止出现：

```text
vomiting
vomiting_symptom
猫瘟
急诊症状
疾病
症状词典标签
```

例如：

```text
呕吐
```

只能标注为：

```text
target_mention
```

不能标注为医学症状类别。

### 5.3 Multi-role boundary

同一 boundary 可以服务多个 role：

```text
target_mention + measurement_expression
subject_mention + recipient_mention
support_region + action_event
state_mention + relation_expression
```

训练和评估不得要求一个 boundary 只有一个全局 label。

可选训练方式：

| 方式 | 说明 | 适用 |
|---|---|---|
| multi-label head | 同一 span 同时输出多个 role | 契约最自然，但需模型适配 |
| role-specific instances | 同一 span 按不同 role 重复训练 | 更贴近现有 GLiNER 训练方式 |
| support / micro 拆模型 | 一个模型找 support，一个模型找子 span | 符合 support-first，但成本更高 |

第一阶段建议使用 role-specific instances，评估时还原为 multi-role candidate。

## 6. 标注数据契约

每个 expected field 必须显式标注：

```text
turn_id
source_id
source_block_id
claim_owner
field_role
label
start
end
text
occurrence_locator
ambiguity_status
annotator_id
review_status
```

规则：

1. `start/end` 是唯一权威定位；
2. `text` 仅用于校验；
3. 重复 mention 使用 owner-relative occurrence；
4. 不允许全文第一次出现作为默认定位；
5. 同一 boundary 可服务多个 field role；
6. omitted participant 可以显式指向 support 外的 owner occurrence；
7. ambiguous span 必须有状态和 review 结果。

## 7. 数据治理

### 7.1 数据划分

必须建立：

```text
train
dev
fresh blind held-out
```

现有 82-field development fixture 只能用于：

```text
契约回归；
pilot cross-validation；
历史 baseline 复现。
```

不得作为生产训练结论依据。

Held-out 必须新标注，且不得从现有 development 样本拆出，因为现有样本已参与
多轮 prompt / threshold / schema / ranking 调整，存在泄漏风险。

### 7.2 人工审核

允许模型辅助预标注，但必须：

```text
逐条人工审核；
保留 reviewer；
保留 review status；
保留原始模型输出；
记录修改原因；
未审核 weak label 不得作为 gold。
```

### 7.3 标注一致性

至少报告：

```text
inter-annotator agreement；
boundary conflict rate；
role conflict rate；
ambiguous rate；
owner occurrence consistency；
review 后变更率。
```

标注规范冻结前不得开始规模化训练。

### 7.4 版本化

每次训练必须记录：

```text
dataset version
annotation guideline version
label schema version
base model / revision
training environment
hyperparameters
evaluation report
rollback path
```

## 8. 候选模型与对照

### 8.1 首选试点

```text
GLiNER small fine-tuning
```

理由：

```text
当前链路已适配 GLiNER；
切换成本低于引入新范式；
可按 role-specific 标签训练；
便于与 V8～V12 baseline 直接比较。
```

### 8.2 对照组

每次核心实验至少包含：

```text
GLiNER small raw；
GLiNER multi-v2.1；
fine-tuned GLiNER small；
可选 SpanMarker + 中文底座；
可选 SpaCy SpanCategorizer。
```

SpanMarker / SpanCategorizer 不因环境可用而宣布 winner，必须有显式训练和评估。

### 8.3 medspaCy 定位

medspaCy 后置，不作为当前主路径。

原因：

```text
中文兽医口语适配成本高；
标签与规则扩展成本高；
容易滑向医学词典 / 关键词规则；
当前瓶颈是结构 role span，不是医学实体归一。
```

只有在以下条件满足时重新评估：

```text
supervised span extractor 仍不达标；
输入转向结构化临床文档；
已有稳定中文兽医标签体系；
需要与受控医学实体体系对接；
规则扩展成本可接受。
```

## 9. 实验矩阵

### 9.1 DATA-QUALITY

目标：

```text
验证训练数据是否足以进入微调。
```

指标：

```text
field count；
unique boundary count；
role distribution；
multi-role boundary count；
offset valid rate；
text match rate；
owner occurrence valid rate；
label agreement；
ambiguous rate；
review completion rate。
```

准入：

```text
offset / text / owner occurrence 全部有效；
label schema 冻结；
标注一致性可接受；
fresh held-out 已准备但保持冻结。
```

### 9.2 GLINER-FINETUNE-PILOT

对照：

```text
GLiNER small raw
GLiNER multi-v2.1
fine-tuned GLiNER small
可选 SpanMarker / SpanCategorizer
```

指标：

```text
exact boundary recall；
boundary precision；
role coverage；
unambiguous label accuracy；
near-or-exact；
candidate count；
score calibration；
latency；
model size。
```

初步成功信号：

```text
exact recall 高于 V12 snapshot 的 0.6585；
role coverage 高于 0.5122；
label accuracy 显著提升；
candidate count 不爆炸；
没有通过放宽 exact match 变绿。
```

### 9.3 SUPPORT-ANCHOR-FINETUNE

目标：

```text
提升 support anchor 候选质量。
```

指标：

```text
support anchor recall@1/2/3；
anchor precision；
over-broad anchor rate；
anchor without valid child rate；
anchor role diversity。
```

对照 V12：

```text
anchor top3 recall = 0.5。
```

### 9.4 SHARED-SCOPE-FINETUNE

目标：

```text
修复共享断言范围和多 target 展开。
```

样本覆盖：

```text
没有呕吐、干呕、反流、流涎或舔唇；
精神、食欲和饮水都正常；
无血便和黑便；
吃喝没有明显变化。
```

指标：

```text
support region recall；
relation span recall；
target span recall；
multi-target coverage；
shared seed downstream recall。
```

对照 V12：

```text
shared seed recall = 0.04。
```

### 9.5 MULTI-ROLE-FINETUNE

目标：

```text
验证同一 boundary 多角色能力。
```

样本覆盖：

```text
target + measurement；
subject + recipient；
support + action event；
state + relation。
```

指标：

```text
multi-role boundary recall；
per-role precision；
role conflict rate；
false exclusive label rate。
```

### 9.6 DOWNSTREAM-SEED-RECOVERY

目标：

```text
验证微调后的候选是否改善 V12 下游。
```

链路：

```text
fine-tuned span extractor
→ candidate snapshot
→ support-first graph
→ role-specific view
→ structural seed
```

指标：

```text
gold_in_view；
seed recall；
seed precision；
seed count；
coverage gap rate；
Macro input size；
Macro unit failure rate。
```

该实验是微调阶段的核心验收，不得只看离线 span 指标。

### 9.7 MODEL-CALIBRATION

目标：

```text
确保 score 可用于 threshold / ranking / future pruning。
```

指标：

```text
per-role PR curve；
score distribution；
threshold stability；
calibration error；
ranking discrimination；
latency。
```

不得使用固定默认阈值如 `0.8`。

### 9.8 ROBUSTNESS

覆盖：

```text
口语脏数据；
长输入；
多轮省略；
多宠物歧义；
用户动作；
医疗提供者动作；
其他宠物症状；
historical / hypothetical / uncertain；
answer_now 与事实同轮。
```

指标：

```text
role coverage；
boundary precision；
seed precision；
false span rate；
failure attribution。
```

### 9.9 FRESH-HELD-OUT

进入条件：

```text
development 微调结果达标；
model / data / schema / threshold 版本冻结。
```

要求：

```text
新标注；
从未参与训练或调参；
不用于 prompt / threshold / schema 调整；
一次性确认性评估；
报告 development vs held-out delta。
```

### 9.10 NEG / SAFETY

负例覆盖：

```text
offset / text mismatch；
owner occurrence mismatch；
role-ineligible output；
multi-role 被错误抑制；
模型输出医学事实；
模型输出 canonical_id；
candidate count 超预算；
fresh held-out 被读取。
```

所有结果继续保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

## 10. 阶段执行顺序

```text
1. DATA-QUALITY
2. GLINER-FINETUNE-PILOT
3. SUPPORT-ANCHOR-FINETUNE
4. SHARED-SCOPE-FINETUNE
5. MULTI-ROLE-FINETUNE
6. DOWNSTREAM-SEED-RECOVERY
7. MODEL-CALIBRATION
8. ROBUSTNESS
9. FRESH-HELD-OUT
10. 重跑 support-first view / structural seed / Macro
11. Macro golden 达标后 REP
12. 最后评估 adapter cold / integration / API shadow
```

在 `DOWNSTREAM-SEED-RECOVERY` 未达标前，不得继续 Macro 大矩阵。

## 11. 准入条件

### 11.1 数据准入

```text
标注规范冻结；
offset / text / owner occurrence 有效；
multi-role 契约可表达；
train / dev 分离；
fresh held-out 冻结；
未审核样本不进入 gold。
```

### 11.2 模型准入

```text
exact boundary recall 显著高于 raw baseline；
boundary precision 不以牺牲必要 gold 为代价；
role coverage 和 label accuracy 达标；
support anchor / shared scope / multi-role 专项改善；
downstream seed recall 与 precision 改善；
latency / model size 可接受。
```

### 11.3 下游准入

只有微调后的 candidate pool 改善：

```text
support-first gold_in_view；
seed recall；
seed precision；
Macro 输入规模；
```

后才允许重跑 Macro 语义修复。

### 11.4 确认性准入

```text
fresh held-out 通过；
development / held-out delta 可解释；
NEG 全部阻断；
三次冷调用 stable-and-correct；
版本冻结并可回滚。
```

## 12. 明确不做事项

1. 不用医学词典 / 症状词表作为主训练标签。
2. 不把 GLiNER 输出直接映射为 canonical_id。
3. 不把 GLiNER 命中当作医学事实。
4. 不用未审核模型输出自动训练。
5. 不在被污染 development set 上宣布泛化。
6. 不用 held-out 调参。
7. 不放宽 exact offset。
8. 不用 overlap 代替 exact。
9. 不通过后处理补造缺失 span。
10. 不继续把 NMS 作为主修复路径。
11. 不在 span 未达标时接入 Macro integration。
12. 不解除 V8 live phase gate。
13. 不触发 clinical safety evaluator / OPA。
14. 不接入 VetOrchestrator。

## 13. 完成判定

本阶段完成时，应形成：

```text
1. 冻结的 role label schema；
2. 已审核显式 offset 数据集；
3. train / dev / fresh held-out 分离；
4. fine-tuned GLiNER 或等价 supervised span extractor；
5. 与 raw / multi / SpanMarker / SpanCategorizer 的对照报告；
6. support anchor、shared scope、multi-role 专项报告；
7. downstream seed recovery 报告；
8. calibration 与 robustness 报告；
9. fresh held-out confirmatory 报告；
10. 可回滚模型版本和训练数据版本。
```

只有在以上条件满足后，才允许进入：

```text
Macro 语义修复；
adapter cold；
integration；
API metadata shadow；
生产消费评估。
```
