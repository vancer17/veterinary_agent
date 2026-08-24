<!--
=============================================================================
文件: docs/architecture/agent-input-preprocessing-domain-extraction-migration-plan.md
作用: 定义 Agent 输入前置预处理层、断言与主体绑定、实体归一化、领域抽取投影与质量门禁的迁移边界和快速验证路线。
范围: 适用于主业务对话入口、Agentic 结构化输入分析、normal/denied/unknown 等断言语义、时间与度量表达、canonical mapping、问诊与临床安全领域投影、跨 Agent 一致性检查和回归观察。
说明: 本文档只描述领域职责、稳定契约、数据流、Fail Fast 语义、质量门禁、分阶段迁移和有意 TODO；不把未经验证的模型、库、提示词或阈值写成生产承诺。
维护: 当前置分析契约、断言与主体语义、canonical mapping 输入输出、领域投影方式、质量门禁或快速验证口径调整时，应同步更新本文档。
=============================================================================
-->

# Agent 输入前置预处理与领域抽取层迁移方案

> **文档状态**：待执行的架构边界与快速验证方案
>
> **适用范围**：用户原始输入到问诊事实 / 临床安全结构化事实之间的前置分析链路，包括输入组织、断言与主体绑定、时间与度量归一化、实体归一化、领域投影、跨 Agent 一致性检查和质量门禁
>
> **不适用范围**：临床安全医学准入内容、OPA 策略细节、问诊状态存储实现、RAG 知识证据、自然语言最终回复生成、长期记忆写入策略、前端展示编排、具体模型与提示词实现

## 1. 背景

临床安全链路的阶段 0 至阶段 5 已把语义抽取、证据边界、候选召回、`required_context`、OPA 裁决和响应投影收敛为框架化路径。预发布回归暴露的新问题显示，当前缺口不再只是单个问诊语义抽取器的鲁棒性，而是用户原始表达到可裁决结构化事实之间缺少一条完整、可审计、可治理的输入工程链路。

当前可观察到的问题包括：

1. 问诊语义抽取在用户已提供多个显式事实时返回空集合，且空结果无法区分“用户未提供”与“模型漏抽”。
2. `精神正常`、`食欲正常`、`饮水正常` 等正常状态与 `没有呕吐`、`没有血便` 等否定状态被混淆表示为 denied。
3. 用户明确表达 `answer_now` 时，控制意图没有被稳定传递或消费。
4. 第二轮用户逐项回答追问后，新增事实没有进入问诊状态，未知槽位没有下降。
5. 问诊语义与临床安全语义对同一原文的事实覆盖结果不一致，但没有冲突或覆盖率告警。
6. 相似低风险输入在 `risk_evidence_state`、召回路径和 `required_context` 前提评估路径上漂移。
7. OPA 收到大量 unknown 后，回答准入依赖追问轮数而非事实完成度，用户表现为过度追问。
8. 上游模型输出漂移缺少阶段化 trace、质量门禁和离线回归信号。

这些问题的共同根因是：不同领域抽取器直接消费原始用户文本，缺少统一但受约束的输入证据视图；断言、主体、时间和表达归一化没有被显式建模；跨领域结果缺少一致性检查；失败与空结果没有独立状态。

## 2. 目标与非目标

### 2.1 目标

1. 建立用户原始输入与领域抽取之间的统一前置分析层。
2. 保留原始用户文本和每个结构化结果的证据锚点。
3. 将 normal、denied、unknown、uncertain、historical、hypothetical 等断言语义显式分离。
4. 对每个候选事实绑定可信主体，避免用户行为、其他宠物或环境对象被误写成当前宠物事实。
5. 将时间、频率、数量和单位表达显式归一化，并保留原文、精度和不确定状态。
6. 将中文口语表达映射到受控 canonical ID，同时区分 confirmed、ambiguous、not_found 和 conflict。
7. 将统一证据图分别投影为问诊事实和临床安全事实，领域间不隐式复制字段。
8. 建立跨 Agent 一致性检查与质量门禁，使空抽取、覆盖率缺口、主体缺失、断言冲突和领域漂移可见。
9. 建立 shadow mode 快速验证路径，先观测新链路，再逐步允许下游消费。
10. 建立在线指标与离线回归基线，防止修复引入安全回退或体验回退。

### 2.2 非目标

1. 不重做临床安全阶段 0 至阶段 5 的已迁移框架。
2. 不修改临床安全资产的医学准入内容。
3. 不通过 Python、OPA 或提示词重新实现医学关键词规则。
4. 不把前置分析层变成诊断、急诊判断或治疗建议层。
5. 不把临床安全 `observed_features` 未经契约转换写入问诊状态。
6. 不把问诊事实直接映射为临床安全升级动作。
7. 不简单放宽回答充分性策略来掩盖上游漏抽。
8. 不在本文档阶段承诺具体模型、库、提示词、阈值或观测平台选型。

## 3. 当前基线

当前相关实现和文档基线如下：

| 能力 | 当前位置 | 当前状态 |
|---|---|---|
| 问诊语义抽取 | [src/vet_agent/agents/semantic_extractor.py](/home/vancer17/veterinary_agent/src/vet_agent/agents/semantic_extractor.py) | 已有结构化输出和显式失败状态，但缺少前置证据视图和断言质量边界 |
| 问诊状态与回答充分性 | [src/vet_agent/consultation_state](/home/vancer17/veterinary_agent/src/vet_agent/consultation_state) | 已有状态融合和 OPA 主路径，但 normal / denied / answer_now / 多轮收敛仍未闭环 |
| 临床安全语义抽取 | [src/vet_agent/clinical_safety/semantic_extractor.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/semantic_extractor.py) | 已有证据充分性边界，但与问诊语义缺少统一证据来源 |
| 临床安全召回与裁决 | [src/vet_agent/clinical_safety](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety) | 已有 pgvector、`required_context` 和 OPA 裁决框架 |
| 主编排入口 | [src/vet_agent/orchestrator.py](/home/vancer17/veterinary_agent/src/vet_agent/orchestrator.py) | 当前缺少阶段化输入分析 trace 和统一质量门禁 |
| 问题工单 | [docs/tickets/ongoing/20260821-兽医 Agent 修复工单：问诊语义抽取空结果与过度追问.md](/home/vancer17/veterinary_agent/docs/tickets/ongoing/20260821-兽医 Agent 修复工单：问诊语义抽取空结果与过度追问.md) | 定义 A/B/C/D 快速回归样本和验收口径 |

本方案定位为在既有问诊语义抽取和临床安全语义抽取之前补充统一输入分析层，而不是替换这两个领域已经建立的框架。

## 4. 目标架构

### 4.1 总体数据流

目标链路如下：

```text
TurnContext 装配
→ 原文保留与输入切分
→ Agentic 结构化重写 / 指代与省略消解
→ 信息源与话语角色标记
→ 断言与主体绑定
→ 时间与度量归一化
→ canonical mapping
→ 统一证据图
→ 领域抽取投影
   ├─ 问诊事实投影
   └─ 临床安全事实投影
→ 跨 Agent 一致性检查
→ 状态融合与回答准入
→ 临床安全召回与裁决
→ 响应投影
```

旁路能力如下：

```text
Trace / Evidence Lineage
Quality Gates
Online Metrics
Offline Evaluation
Human Review
```

其中，统一证据图是本方案的核心中间产物。问诊抽取和临床安全抽取应消费同一批带证据锚点的 segment、主体、断言、时间和归一化表达，而不是各自从原始文本重新开始解释。

### 4.2 分层职责

| 层 | 主要职责 | 禁止事项 |
|---|---|---|
| TurnContext 装配 | 提供可信用户、宠物、会话、任务、当前时间、时区和多轮上下文 | 不允许模型发明实体 ID，不允许用户文本覆盖服务端可信画像 |
| 输入切分与结构化重写 | 切分 claim-level 输入片段，做指代与省略补全，标记信息源和话语角色 | 不做医学判断，不删除否定和时间，不引入用户未说事实，不用重写文本替代原文 |
| 断言与主体绑定 | 输出 present / denied / normal / unknown 等断言语义，并绑定事实主体 | 不用关键词组合补事实，不把主体未解析默认为当前宠物 |
| 时间与度量归一化 | 归一化日期、时长、频率、数量和单位 | 不把近似表达强行精确化，不猜测无法解析的值 |
| Canonical Mapping | 将用户表达映射到受控 canonical ID，并保留候选和歧义状态 | 不把向量最近邻直接当事实，不输出自由 predicate 给策略层 |
| 领域抽取投影 | 将统一证据图投影为问诊事实或临床安全事实 | 不隐式复制跨领域字段，不直接写状态，不做医学动作裁决 |
| 跨 Agent 一致性检查 | 比较两个领域对同一证据的覆盖、断言、主体和 canonical 结果 | 不做医学裁决，不静默选择某一边 |
| 质量门禁 | 校验契约、覆盖率、证据链、主体、断言和一致性 | 不用医学关键词规则兜底，不吞掉失败 |
| 观测与回归 | 记录阶段状态、指标、失败样本和人工审核结果 | 不把观测平台状态当作业务权威，不隐藏模型漂移 |

## 5. 前置预处理层设计边界

### 5.1 TurnContext 装配

TurnContext 是所有前置分析的可信边界，至少表达：

1. 当前用户、宠物、会话和任务范围。
2. 当前回合时间和时区。
3. 服务端可信宠物画像。
4. 家庭其他宠物或多个可能主体。
5. 上一轮追问对象和多轮历史摘要。
6. 输入来源，例如 API 用户消息、上传文档或服务端上下文。

服务端提供的 source、pet_id、user_id 和当前时间属于可信 provenance，不能由模型推断覆盖。模型只能对文本内容的话语角色和内容语义进行分类。

### 5.2 原文保留与输入切分

所有后续结构化结果必须能回指原始用户输入。输入切分应输出 claim-level segment，例如将一段口语化输入拆为：

```text
前天开始换新猫粮
这两天大便有一点软
精神挺好
饭和水都正常
没有呕吐
也没有血便
我要不要先把粮换回去
```

Segment 不做医学归类，不判断疾病或风险，只表达：

1. 原文片段；
2. 所属回合；
3. 来源类型；
4. 话语角色，例如事实陈述、用户问题、控制意图或历史补充；
5. 证据 span；
6. 置信度和状态。

对于聊天输入，应使用 claim-level 切分；对于后续上传病历或检验报告，可使用文档结构适配器，但最终仍输出同一 Segment 契约。

### 5.3 Agentic 结构化重写

Agentic 结构化重写层用于解决中文对话中的指代、省略和多轮上下文继承，例如：

```text
它没有呕吐
```

应形成带映射的分析视图：

```text
当前宠物没有呕吐
```

同时保留：

1. 原文；
2. 重写后的分析文本；
3. 指代链；
4. 主体解析方式；
5. 置信度；
6. 原文证据 span。

该层不得把重写文本作为唯一事实来源。重写只允许做指代与省略补全，不允许做摘要、诊断扩展、否定弱化、时间模糊化或医学改写。

### 5.4 信息源与话语角色

信息源需要区分两层：

| 维度 | 语义 | 来源 |
|---|---|---|
| channel source | 输入来自 API 用户消息、上传文档、宠物画像、历史记录或系统上下文 | 服务端可信上下文 |
| information role | 当前陈述、历史陈述、用户问题、控制意图、假设表达或不确定表达 | 前置分析层分类 |

例如：

```text
去年医生说它有肾病
```

可标记为用户口头转述的历史医学信息，但不能把 channel source 改成原始医生记录。

## 6. 断言、主体、时间与度量契约

### 6.1 AssertionObservation

断言语义必须显式区分：

| 用户表达 | 目标语义 |
|---|---|
| 精神正常 | normal |
| 没有精神异常 | denied_abnormal |
| 没有呕吐 | denied |
| 未提及干呕 | unknown |
| 好像没有呕吐 | denied + uncertain |
| 如果它呕吐了怎么办 | hypothetical |
| 去年呕吐过 | historical |

建议的稳定状态至少包括：

```text
present
absent
denied
normal
abnormal
uncertain
possible
hypothetical
historical
resolved
not_applicable
unknown
```

断言状态必须与 canonical concept 分离。例如：

```text
没有呕吐
```

应表达为：

```text
concept = vomiting
assertion = denied
```

而不是把 `vomiting_denied` 作为被向量归一的单一实体。

### 6.2 SubjectBinding

每个候选事实必须显式携带主体。建议主体类型至少包括：

```text
current_pet
other_pet
user
caregiver
food
environment
medical_actor
sample
unknown
```

主体绑定规则：

1. subject 必须来自可信 TurnContext 或显式指代链。
2. 模型可以输出 `current_pet`、`other_pet`、`user` 等引用，但不能发明实体 ID。
3. 多宠物上下文中主体无法消歧时，应输出 `subject_ambiguous`，不得默认当前宠物。
4. 事实类型与主体类型必须匹配，例如临床症状主体应为宠物，用户换粮动作主体应为用户。
5. 主体缺失或主体类型不匹配的事实不得进入状态融合。

中文省略主语可通过以下方式解析：

| 场景 | 允许的解析方式 |
|---|---|
| 上一轮询问“有没有呕吐”，用户回答“没有” | previous_question_target |
| 当前对话持续讨论当前宠物 | discourse_continuity |
| 用户明确说“它” | coreference |
| 多宠物且无法确定“它”是谁 | subject_ambiguous |

所有解析方式必须显式进入 metadata，不能静默默认。

### 6.3 TemporalObservation

时间表达至少应表达：

1. 原文；
2. 归一化值；
3. reference time；
4. 时区；
5. 精度，例如 day、approximate_duration；
6. 与事件的绑定关系，例如 started_at、duration、frequency、ended_at；
7. 置信度和状态。

示例语义：

```text
前天开始换新猫粮
```

应表达为换粮动作的 started_at 相对当前回合归一化到天。

```text
这两天大便有一点软
```

应表达为软便的 approximate duration，不得强行解释为精确 48 小时。

无法解析的时间表达必须输出 `temporal_unresolved` 或等价状态，不能静默丢弃。

### 6.4 MeasurementObservation

度量表达至少应表达：

1. 原文；
2. 数值；
3. 单位；
4. 精度；
5. 关联事件；
6. 归一化状态。

适用范围包括体重、食量、饮水量、排便频率、体温、药物剂量和间隔。无法归一化的口语化量词，例如“一小把”，应保留原文并输出 unresolved，不得猜数值。

## 7. Canonical Mapping 设计边界

Canonical Mapping 负责把用户表达映射到受控 canonical ID，例如：

```text
vomiting
retching
regurgitation
soft_stool
bloody_stool
diet_change
water_intake
```

它不负责判断医学风险，也不负责决定症状是否满足临床安全资产前提。

### 7.1 映射流程

目标流程如下：

```text
用户表达 / segment
→ 候选召回
→ canonical_type / domain / language / subject 约束过滤
→ 可选精排
→ 结构化 verifier
→ canonical mapping 契约
→ 质量门禁
→ 领域投影
```

Embedding 或其他召回方式只能产生候选，不能直接决定最终事实。最终结果必须区分：

```text
confirmed
ambiguous
not_found
type_mismatch
subject_mismatch
low_confidence
conflict
```

### 7.2 映射结果要求

每个 confirmed 或 ambiguous 结果必须保留：

1. surface form；
2. canonical_id；
3. canonical_type；
4. 候选列表；
5. 各阶段分数或置信度；
6. alternatives；
7. 原文证据；
8. 质量状态；
9. 词表和模型版本。

ambiguous 或 not_found 的表达不得写入固定问诊槽位或临床安全前提。它可以进入开放观察、澄清策略或人工审核队列，但必须显式标记状态。

## 8. 领域抽取投影

统一证据图不直接写入任何业务状态。它必须先经过领域投影。

### 8.1 问诊事实投影

问诊领域可消费：

1. 当前主诉和症状事实；
2. normal / denied / unknown 等断言语义；
3. 起病时间、持续时间和频率；
4. 当前食物、换粮和大便形态；
5. 精神、食欲、饮水等状态；
6. `answer_now` 等用户控制意图；
7. 主体可信的开放观察。

问诊投影输出必须保持现有问诊语义契约可兼容，不应要求状态层理解前置层内部实现。

### 8.2 临床安全事实投影

临床安全领域可消费：

1. 明确症状和暴露证据；
2. denied / normal / unknown 等证据状态；
3. 时间和主体约束；
4. confirmed canonical facts；
5. 证据充分性所需的摘要。

临床安全投影不得：

1. 把问诊槽位缺失直接解释为风险；
2. 把 open observation 直接映射为急诊动作；
3. 把 ambiguous canonical 当作满足 `required_context`；
4. 绕过既有临床安全召回和 OPA 裁决。

### 8.3 领域隔离原则

问诊投影和临床安全投影可以消费同一个证据图，但相互之间不得隐式复制结果。跨领域能力必须通过显式契约暴露；若对方能力尚未实现，应保留 TODO 空壳和显式 `not_implemented` 状态，不允许跨领域代为实现。

## 9. 跨 Agent 一致性与质量门禁

### 9.1 一致性状态

问诊领域与临床安全领域的输出不必完全相同，但对同一原文证据必须可解释。建议状态包括：

| 状态 | 含义 |
|---|---|
| consistent | 两个领域对同一证据的断言和主体一致 |
| domain_only_expected | 只有某一领域需要消费该证据，属于合理差异 |
| coverage_gap | 一边存在 evidence-backed facts，另一边为空或明显漏抽 |
| assertion_conflict | 一边为 present，另一边为 denied / normal |
| subject_conflict | 两边绑定主体不同 |
| temporal_conflict | 两边时间语义不同 |
| canonical_conflict | 同一表达映射到互斥核心概念 |
| unknown | 当前信息不足以判断 |

例如临床安全语义识别到换粮、软便、精神正常、食欲正常、饮水正常、呕吐 denied、血便 denied，而问诊语义为空，应标记为 coverage_gap，而不是把问诊空结果解释为用户未提供信息。

### 9.2 质量门禁分类

#### Contract Gate

同步校验：

1. schema；
2. 必填字段；
3. 枚举；
4. confidence 范围；
5. evidence span；
6. canonical_id 存在性；
7. subject 来源；
8. OPA 输入契约。

Contract Gate 失败应阻断该结果进入状态或策略。

#### Semantic Quality Gate

校验：

1. 原文非空多事实输入是否返回可疑空结果；
2. 重写是否引入新事实；
3. 否定、时间、程度是否丢失；
4. normal / denied / unknown 是否冲突；
5. canonical mapping 是否歧义；
6. 主体与事实类型是否匹配。

该类检查可由结构化模型、专用模型、Cross-Encoder 或 LLM verifier 支持，但具体实现是候选适配器，不属于本架构承诺。

#### Cross-Agent Consistency Gate

校验问诊投影与临床安全投影之间的覆盖缺口和冲突。该门禁只解释结构化结果，不扫描原始文本，不做医学判断。

#### Behavioral Gate

校验端到端行为：

1. `answer_now` 是否被尊重；
2. 已回答槽位是否重复追问；
3. 多轮补充后 unknown 是否下降；
4. denied 是否被误当 present；
5. 低风险输入是否产生 urgent / blocked；
6. required_context unknown 是否不合理阻塞普通问诊。

#### Offline Evaluation Gate

固化 A/B/C/D、负例和多轮样本，在提示词、模型、词表、embedding、OPA 输入或质量规则变更时执行回归。

### 9.3 Gate Result

每个门禁应输出：

```text
gate_id
status: passed / warning / failed / skipped / not_applicable / needs_review
severity: blocking / critical / major / minor / observability_only
reason_code
stage
evidence_refs
metadata
action
review_required
```

允许的 action 至少包括：

```text
pass
pass_with_metadata
retry_same_contract
require_clarification
route_to_review
fail_turn
```

失败不能被解释为“用户没有提供事实”。系统必须区分：

1. 用户未提供；
2. 模型漏抽；
3. schema 非法；
4. 主体未解析；
5. canonical 歧义；
6. 跨 Agent 冲突；
7. 质量门禁失败。

## 10. Fail Fast 与降级语义

### 10.1 显式失败状态

建议至少定义以下状态：

```text
turn_context_invalid
segmentation_failed
segmentation_suspicious_empty
segment_coverage_low
evidence_missing
rewrite_entailment_failed
coreference_unresolved
subject_missing
subject_ambiguous
subject_type_mismatch
assertion_missing
assertion_low_confidence
assertion_conflict
temporal_unresolved
temporal_precision_loss
measurement_unresolved
canonical_not_found
canonical_ambiguous
canonical_conflict
domain_projection_not_implemented
domain_projection_failed
cross_agent_coverage_gap
cross_agent_assertion_conflict
quality_gate_failed
```

这些状态必须进入响应 metadata、trace 或评估报告。

### 10.2 禁止回退

以下路径明确禁止：

1. 关键词、正则或短语匹配补造事实；
2. 宽松文本 JSON 检索；
3. 硬编码默认追问；
4. 本地医学规则状态机；
5. 静默降级为空结果；
6. 用 RAG 相似病例补造当前宠物事实；
7. 用放宽回答充分性策略掩盖漏抽；
8. 用无界 Agentic 自循环反复自我修复。

有限重试只允许在同一结构化契约内进行，并记录尝试次数、失败原因和最终状态。

## 11. Trace 与可观测性

### 11.1 Evidence Lineage

每个最终结构化事实应能追溯：

```text
turn
→ segment
→ entity / event mention
→ assertion
→ subject binding
→ temporal / measurement
→ canonical mapping
→ domain fact
→ state update / policy input
→ response
```

任何无法回指证据的候选事实不得进入状态或策略。

### 11.2 阶段 Trace

每个阶段至少记录：

1. stage 名称；
2. 输入摘要；
3. 输出摘要；
4. 状态和失败原因；
5. 模型或组件版本；
6. 提示词或词表版本；
7. 延迟；
8. token 或调用成本；
9. 重试次数；
10. gate 结果。

### 11.3 在线指标

第一阶段最小指标：

```text
segmentation_suspicious_empty_rate
segment_coverage_score
subject_missing_rate
subject_ambiguous_rate
assertion_conflict_rate
normal_as_denied_rate
denied_as_present_rate
canonical_not_found_rate
canonical_ambiguous_rate
consultation_fact_count
clinical_safety_projection_count
cross_agent_coverage_gap_rate
answer_now_recognition_rate
answer_now_respected_rate
answered_slot_repeat_rate
unknown_slot_reduction_rate
quality_gate_failed_rate
end_to_end_latency
```

临床安全回归指标：

```text
vague_triage_urgent_rate
low_risk_urgent_rate
denied_as_present_urgent_rate
required_context_unknown_rate
blocked_signal_rate
```

### 11.4 人工审核触发

以下样本应进入 review queue：

1. suspicious empty；
2. coverage gap；
3. assertion conflict；
4. subject ambiguous；
5. canonical ambiguous；
6. normal / denied 疑似混淆；
7. answer_now 未被尊重；
8. 重复追问；
9. 安全误报或用户纠正。

人工标注结果应反哺样本集、词表治理和候选组件评估。

## 12. 快速验证方案

### 12.1 验证目标

快速验证阶段只验证：

1. A/B/C/D 输入的 segment 覆盖；
2. 指代和省略补全不引入新事实；
3. normal / denied / unknown 分离；
4. subject 显式绑定；
5. `answer_now` 进入意图契约；
6. 问诊与临床安全基于统一证据视图；
7. 空结果和 coverage gap 可观察；
8. 多轮补充后 unknown 槽位下降；
9. 不产生 urgent / blocked 回退；
10. 延迟在预算内。

暂不要求：

1. 覆盖全部兽医领域；
2. 建成完整 canonical 词表；
3. 替换生产问诊抽取主路径；
4. 直接切换临床安全主路径；
5. 完成所有观测平台建设。

### 12.2 验证样本

#### A：原始低风险软便输入

包含：

```text
前天开始换新猫粮
大便软
无血便
精神正常
食欲正常
饮水正常
无呕吐
询问是否换回旧粮
```

#### B：规范化表达复测

医学内容与 A 相同，使用更规范表达，验证问题不依赖口语歧义。

#### C：answer-now 控制组

明确包含：

```text
请先根据现有信息给阶段建议，不要继续追问
```

#### D：第二轮逐项补充

第二轮覆盖第一轮追问槽位，包括精神、玩耍、呼唤反应、进食、饮水、呕吐、干呕、反流、流涎、舔唇、大便形态、频率和换粮时间。

#### E：负例组

覆盖：

1. 模糊分诊；
2. 无明确症状；
3. 低风险 normal / denied 场景；
4. 多宠物主体歧义；
5. 历史事实；
6. 假设表达。

### 12.3 Shadow Mode

新链路第一阶段必须以 shadow mode 运行：

```text
现有问诊抽取 / 临床安全抽取仍为生产主路径
新前置分析与领域投影并行执行
结果只进入 trace 和评估报告
不改变状态
不改变响应
```

Shadow Mode 退出条件：

1. A/B/C/D segment 覆盖达到验收标准；
2. suspicious empty 被稳定识别；
3. normal / denied / unknown 不混淆；
4. subject binding 稳定；
5. answer_now 识别稳定；
6. 无 urgent / blocked 安全回退；
7. 延迟和成本在预算内；
8. trace 完整；
9. 质量门禁可复现。

### 12.4 渐进消费顺序

1. **只观测**：新管道输出进入 metadata，不影响状态和响应。
2. **只消费控制意图**：优先让 `answer_now` 进入回答充分性策略。
3. **只消费问诊事实**：让 present / denied / normal 进入状态融合。
4. **再消费临床安全投影**：临床安全路径敏感，应最后切换。
5. **最后开启一致性阻断或 review**：两个领域稳定后再让冲突进入阻断策略。

## 13. 分阶段迁移方案

### 阶段 0：固化基线与测试样本

交付物：

1. A/B/C/D/E 样本集；
2. 当前空抽取、normal/denied 混淆、answer_now、多轮收敛和跨 Agent 差异的可重复测试；
3. shadow 报告格式；
4. 质量指标口径。

验收标准：

1. 当前问题可稳定复现；
2. 测试不依赖硬关键词断言实现；
3. 临床安全安全底线有负例覆盖。

### 阶段 1：定义稳定契约与 trace 骨架

交付物：

1. TurnContext、Segment、EvidenceSpan、SubjectBinding、AssertionObservation 等概念契约；
2. QualityGateResult 契约；
3. 阶段 trace 格式；
4. `not_implemented` 空壳适配器。

验收标准：

1. 阶段之间只通过显式契约通信；
2. 未实现能力显式暴露；
3. 不发生跨领域代实现。

### 阶段 2：最小前置分析 shadow

交付物：

1. 原文保留；
2. claim-level segmentation；
3. 指代与省略解析视图；
4. 信息源与话语角色标记；
5. suspicious empty 和 coverage gate。

验收标准：

1. A/B/C/D 输入非空多事实时不产生不可审计空 segment；
2. 每个结果可回指原文；
3. 不影响生产响应。

### 阶段 3：断言与主体契约 shadow

交付物：

1. AssertionObservation；
2. SubjectBinding；
3. normal / denied / unknown 分类结果；
4. 主体缺失、歧义和类型不匹配状态。

验收标准：

1. 精神、食欲、饮水 normal 不被表示成 denied；
2. 呕吐、血便 denied 不被表示成 present；
3. 未提及字段保持 unknown；
4. 多宠物歧义不默认当前宠物。

### 阶段 4：时间、度量与 canonical mapping shadow

交付物：

1. TemporalObservation；
2. MeasurementObservation；
3. CanonicalMappingResult；
4. confirmed / ambiguous / not_found 状态；
5. 词表版本和候选审计。

验收标准：

1. “前天”“这两天”“一天一次”等样本可解释；
2. 无法归一化时显式 unresolved；
3. ambiguous 不进入固定槽位；
4. 不使用最近邻结果直接写入状态。

### 阶段 5：领域投影 shadow

交付物：

1. 问诊事实投影；
2. 临床安全事实投影；
3. 领域投影契约；
4. 与既有问诊语义和临床安全语义输出的对比报告。

验收标准：

1. 投影输出符合各自领域现有稳定契约；
2. 不隐式复制跨领域字段；
3. A/B/C/D 的事实覆盖优于或可解释于当前基线。

### 阶段 6：跨 Agent 一致性门禁

交付物：

1. 一致性比较契约；
2. coverage gap 和冲突状态；
3. review queue 触发规则；
4. shadow 指标。

验收标准：

1. 当前“临床安全非空、问诊为空”的样本被显式标记；
2. 冲突不被静默丢弃；
3. 门禁不执行医学判断。

### 阶段 7：逐步消费新结果

交付物：

1. answer_now 消费路径；
2. 问诊状态消费 present / denied / normal 的路径；
3. 多轮 unknown 收敛测试；
4. 临床安全投影接入评估。

验收标准：

1. answer_now 在无 urgent / blocked 时得到尊重；
2. 已回答槽位不重复追问；
3. 第二轮补充后 unknown 下降；
4. followup_rounds 不是唯一收敛依据。

### 阶段 8：预发布观察与退出

交付物：

1. shadow 切换策略；
2. 回滚策略；
3. 在线指标看板；
4. 离线回归报告；
5. 人工 review 结果。

验收标准：

1. 空抽取率、重复追问率、answer_now 识别率、cross-agent gap 率和耗时达到观察阈值；
2. 无模糊分诊误升级；
3. 无 denied 变 present；
4. 无低风险 urgent / blocked 回退。

## 14. 候选实现与验证附录

以下均为候选适配器，不是稳定架构承诺。任一候选验证失败时，应替换适配器，而不是改变本文件的领域契约和边界。

| 能力 | 候选方向 | 验证重点 |
|---|---|---|
| 指代与省略消解 | Agentic structured rewrite | 不引入新事实、不丢否定、不改变时间、保留 evidence span |
| 断言消解 | 结构化模型输出或专用 assertion classifier | 中文口语 normal / denied / uncertain / hypothetical 分类 |
| 主体消歧 | Schema-Driven Subject Binding | 多宠物、用户行为、宠物行为和省略主语 |
| 时间与度量归一化 | Duckling、PyHeidelTime、SUTime 或等价组件 | 中文时间、频率、单位、precision 和 unresolved 状态 |
| 实体归一化 | 向量召回、Cross-Encoder 重排、结构化 verifier | confirmed / ambiguous / not_found 可区分，不使用最近邻直接写状态 |
| 领域抽取 | BAML、DSPy、强引导 few-shot、RAG-driven extraction | A/B/C/D 事实覆盖、证据锚定、失败显式化 |
| Trace 与评估 | Langfuse、Arize Phoenix、DeepEval 或等价工具 | trace 完整性、指标、离线回归和 review 闭环 |
| 框架内断言 | BAML assertions、DSPy LM Assertions | 仅作为阶段内契约校验，不替代统一质量门禁 |

引入任何候选组件前，应定义独立评估报告，至少记录：

1. 样本集；
2. 通过标准；
3. 中文表现；
4. 延迟与成本；
5. 失败状态；
6. 版本；
7. 是否允许进入 shadow；
8. 是否允许进入生产。

## 15. 有意 TODO

### 15.1 技术选型 TODO

1. 中文断言分类实现；
2. 中文时间与度量解析组件；
3. canonical mapping 的召回、精排和 verifier 组合；
4. BAML / DSPy 是否引入；
5. Langfuse / Phoenix / DeepEval 或等价观测评估平台；
6. Cross-Encoder 是否作为在线精排或仅离线评估。

### 15.2 词表与资产 TODO

1. canonical vocabulary 目录；
2. 中文别名和多语言 canonical 命名；
3. canonical_type 与 allowed_subject_type 约束；
4. 开放观察类别治理；
5. canonical 词表版本迁移和回滚。

### 15.3 质量门禁 TODO

1. suspicious empty gate；
2. subject binding gate；
3. assertion gate；
4. canonical mapping gate；
5. cross-agent consistency gate；
6. behavioral gate；
7. offline evaluation gate。

### 15.4 领域联动 TODO

1. 问诊状态消费 normal / denied / unknown；
2. `answer_now` 进入回答充分性策略；
3. 临床安全事实投影进入既有语义输入契约；
4. required_context unknown 与普通问诊回答准入联动；
5. 长期记忆候选抽取对齐统一证据图。

这些 TODO 未实现时，应保留空壳契约和显式 `not_implemented` 状态，不允许由前置分析层或另一个领域代为实现。

## 16. 明确不做事项

1. 不恢复关键词、正则或短语匹配作为语义抽取主路径。
2. 不在 Python 业务层实现医学规则状态机。
3. 不让 OPA 扫描原始用户文本。
4. 不把临床安全 `observed_features` 未经契约转换写入问诊状态。
5. 不把问诊事实直接映射为临床安全动作。
6. 不用 RAG 检索结果补造用户事实。
7. 不通过放宽回答充分性策略掩盖上游漏抽。
8. 不在语义失败时使用硬编码默认追问。
9. 不用宽松文本 JSON 解析兜底。
10. 不引入无界 Agentic 自循环。
11. 不把未验证组件写成生产必选依赖。
12. 不在本文档阶段承诺具体模型、阈值、提示词或实现库。

## 17. 验收基线

### 17.1 输入覆盖

A/B/C/D 输入包含多个显式事实时，新链路不得返回不可审计的空 segment 或空事实。若为空，必须输出 suspicious empty 或等价质量状态。

### 17.2 断言语义

最终结构化结果应达到：

```text
精神正常 → normal
食欲正常 → normal
饮水正常 → normal
没有呕吐 → denied
没有血便 → denied
未提及干呕 → unknown
```

该验收不要求实现层使用关键词规则，只要求最终契约行为满足语义。

### 17.3 主体绑定

所有进入状态或策略的事实必须有 subject。subject 必须来自可信 TurnContext 或显式解析链。多宠物歧义不得默认当前宠物。

### 17.4 answer-now

用户明确要求先回答且无 urgent / blocked 安全信号时：

1. `intent.answer_now=true`；
2. 最终状态为 completed；
3. 输出阶段性回答；
4. 不继续输出与已有信息冲突的追问。

### 17.5 多轮收敛

第二轮逐项回答后：

1. 新增事实进入问诊状态；
2. 已回答槽位不重复追问；
3. unknown 槽位数量下降；
4. `followup_rounds` 不是唯一收敛依据。

### 17.6 临床安全回归

A/B/C/D 均不得产生：

```text
urgent signal
blocked signal
```

同时不得出现：

1. 模糊分诊直接升级急诊；
2. denied 呕吐或血便被当作 present；
3. 低风险普通问诊进入大量急诊前提评估。

### 17.7 可观测性

每个验证请求至少可观察：

```text
segment_count
assertion_count
subject_binding_status
canonical_mapping_status
consultation_fact_count
clinical_safety_projection_count
cross_agent_consistency_status
quality_gate_status
latency
failure_reason
```

## 18. 风险与回滚

### 18.1 主要风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 前置层引入错误事实 | 污染问诊状态或临床安全裁决 | Contract Gate、证据锚定、shadow mode |
| Agentic 重写丢失否定或时间 | 安全回退或语义漂移 | rewrite entailment gate、原文保留 |
| 主体误绑定为当前宠物 | 多宠物事实污染 | SubjectBinding gate、多宠物样本 |
| canonical mapping 歧义 | 固定槽位误填充 | ambiguous / not_found 不进入槽位 |
| 链路延迟上升 | 用户体验下降 | shadow 指标、阶段预算、异步观测 |
| 工具碎片化 | 治理成本上升 | 内部 QualityGateContract 优先，平台作为适配器 |
| 跨领域职责漂移 | 架构退化为规则状态机 | TODO 空壳、显式 `not_implemented`、禁止跨领域实现 |

### 18.2 回滚策略

1. shadow 阶段不影响用户响应，可直接停止并行执行。
2. 渐进消费阶段按能力回滚，例如只回滚 answer_now 或问诊事实消费。
3. 临床安全投影最后切换，切换前保留既有生产输入。
4. 所有模型、词表、提示词和质量规则变更必须可版本化回滚。
5. 回滚不得把失败解释为用户未提供事实。

## 19. 迁移完成判定

当以下条件同时满足时，可认为本迁移完成：

1. 原始输入、segment、断言、主体、时间、canonical、领域事实和策略输入具备连续证据链。
2. normal / denied / unknown 不再混淆。
3. `answer_now` 被稳定识别并消费。
4. 多轮补充后问诊状态可收敛。
5. 问诊与临床安全对同一原文的覆盖差异可解释，coverage gap 和冲突可观察。
6. A/B/C/D/E 离线回归稳定。
7. 无临床安全误升级、denied 变 present、urgent / blocked 回退。
8. 在线指标和人工 review 闭环运行。
9. 候选组件均有独立验证结论，失败组件可替换。

## 20. 关联材料

1. [docs/tickets/ongoing/20260821-兽医 Agent 修复工单：问诊语义抽取空结果与过度追问.md](/home/vancer17/veterinary_agent/docs/tickets/ongoing/20260821-兽医 Agent 修复工单：问诊语义抽取空结果与过度追问.md)
2. [consultation-semantic-extraction-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-semantic-extraction-change-summary.md)
3. [consultation-state-answerability-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-state-answerability-change-summary.md)
4. [clinical-safety-open-issues-migration-plan.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-open-issues-migration-plan.md)
5. [clinical-safety-evidence-boundary-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-evidence-boundary-change-summary.md)
6. [clinical-safety-required-context-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-required-context-change-summary.md)
7. [clinical-safety-stage5-preprod-verification-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-stage5-preprod-verification-change-summary.md)
