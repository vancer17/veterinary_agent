<!--
=============================================================================
文件: docs/architecture/consultation-dynamic-evidence-architecture.md
作用: 定义 M13 问诊投影之后的动态证据问诊领域目标架构。
范围: 覆盖可信宠物事实输入、Evidence Knowledge Retrieval 消费边界、
      自包含知识命题抽取、来源支持审查、重复关系审查、确定性归一、
      患者事实与知识命题覆盖判断、动态回答充分性策略和旧槽位迁移边界。
说明: 本文是问诊领域层目标架构基线，不是已实现状态总结；旧问诊核心槽位
      链路仍是当前生产实现，切换必须等待 M15 显式配置与回滚边界。
维护: 当 M13 消费契约、可信宠物事实契约、KnowledgeRetrievalPort 消费契约、
      知识命题输出、重复判定 JSON、覆盖状态机、动态问诊策略或生产接入
      前提调整时，必须同步更新本文，不得在代码中形成第二权威解释。
=============================================================================
-->

# 问诊动态证据架构基线

> **文档状态**：目标架构基线，尚未实施
>
> **上游权威**：
> [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
> 定义 M01～M13 语义协作 DAG 边界；本文只定义 M13 之后的问诊领域内部架构。
>
> **RAG 数据治理权威**：
> [veterinary-rag-knowledge-graph-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/veterinary-rag-knowledge-graph-architecture.md)
> 定义 PDF 来源、section / chunk 索引、scope cluster、embedding、索引版本和
> 检索服务；本文只定义问诊动态证据域如何消费 `KnowledgeRetrievalPort`。
>
> **实施清单**：
> [consultation-dynamic-evidence-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-implementation-plan.md)
> 定义组件顺序、依赖、验收与实施状态；本文不承载工程进度。
>
> **当前生产状态**：既有 `ConsultationSemanticExtractorAgent` +
> `ConsultationStateService` 核心槽位链路仍在生产主路径中；本文描述的是后续
> 重构目标。未完成本文前置依赖前，不得通过 M15 自动切换。

## 1. 文档定位

本文定义问诊领域从：

```text
固定核心槽位状态机
```

重构为：

```text
自然语言患者事实
+ 可信宠物事实
+ 架构驱动 RAG 知识命题
+ 显式覆盖关系
+ 动态回答充分性策略
```

的目标架构。

旧链路的问题是：

```text
用户输入
→ ConsultationSemanticExtractorAgent
→ SemanticExtractionResult
→ ConsultationStateService.update()
→ fixed slots / missing_slots
→ 追问或回答
```

目标链路是：

```text
M13 ConsultationProjection
→ 当前回合已审查患者 proposition

TrustedPetFactBundle
→ 已确认宠物背景事实

KnowledgeRetrievalPort
→ candidate scopes + scoped chunks

Knowledge Evidence Proposition Extraction
→ 自包含知识观察命题

Support Review
→ 验证 PDF 来源支持

Duplicate Review
→ candidate vs canonical
+ pairwise batch internal

Deterministic Canonicalization
→ canonical knowledge propositions

Coverage Review
   ├─ Coverage State Review
   │  → knowledge proposition → coverage state
   └─ Coverage Source Binding Review
      → coverage state → supporting claim / fact selectors

Deterministic Coverage Relation Merge
→ merged coverage relations

Dynamic Consultation Policy
→ 阶段性回答或继续追问
```

本文不改变 M01～M13 的既有权威关系；它只消费 M13 输出。

### 1.1 当前资产状态

| 能力 | 当前状态 | 本文边界 |
|---|---|---|
| M13 ConsultationProjection | 已实现 | 上游输入契约，不因问诊重构修改 |
| 旧核心槽位问诊链路 | 当前生产实现 | 仅作为兼容与回滚路径 |
| TrustedPetFactBundle | 目标契约 | 待独立实现或保留 TODO 空壳 |
| ConsultationSemanticProjectionConsumer | 目标契约 | 待实现，禁止硬转换旧契约 |
| Evidence Knowledge Retrieval Port | 目标契约 | 依赖 RAG 数据治理域；本文只定义消费边界 |
| Evidence Proposition Extraction | 目标契约 | 待实现，输出固定薄 JSON |
| Support Review | 目标契约 | 待实现，unsupported 必须 blocked |
| Duplicate Review | 目标契约 | 待实现，输出 keyed boolean |
| Deterministic Canonicalization | 目标契约 | 待实现，不由 LLM 归一 |
| Coverage State Review | 目标契约 | 待实现，输出 keyed enum |
| Coverage Source Binding Review | 目标契约 | 待实现，输出 keyed selector array |
| Dynamic Consultation Policy | 目标契约 | 待实现，旧 OPA 策略需显式演进 |
| M15 生产接入 | 未实现 | 必须等待本文前置依赖闭合 |

## 2. 目标与非目标

### 2.1 目标

1. 让问诊证据需求由知识上下文动态派生，而不是依赖固定全局槽位。
2. 保留 `normal / denied / uncertain / unobserved / corrected` 等自然语言语义差异。
3. 让 RAG 命题可追溯到 PDF 文档、章节、页面和 chunk。
4. 让模型只输出薄结构候选，不承担来源自证、身份绑定或最终归一。
5. 用显式支持审查与重复审查隔离概率输出误差。
6. 将患者当前事实、可信宠物事实和知识侧命题分源治理。
7. 让回答充分性策略消费覆盖状态，而不是复述文本或解析用户原文。
8. 保留旧槽位作为兼容视图，逐步退出主路径。

### 2.2 非目标

本文不做：

```text
医学诊断
治疗方案生成
临床安全 urgent / blocked 裁决
临床安全 evaluator / required_context / OPA 调用
长期记忆写入
Mem0 原始候选消费
固定症状词表或关键词规则
人工维护全量病症证据索引
第二语义协作权威事实源
单次请求内回退旧问诊抽取器
```

## 3. 核心概念

| 概念 | 来源 | 语义 | 权威关系 |
|---|---|---|---|
| Patient Proposition | M13 ConsultationProjection | 当前回合用户明确表达且已审查的自包含事实 | M11 artifact 是权威，M13 是领域投影 |
| TrustedPetFact | 宠物档案、已确认临床记录、已治理长期事实 | 已确认宠物背景事实 | 原始领域记录是权威，bundle 是读取投影 |
| Knowledge Scope | PDF 标题、章节、页码和派生聚类 | 候选知识上下文 | PDF 结构是输入，scope cluster 是派生索引 |
| Knowledge Evidence Proposition | PDF chunk + LLM 抽取 + 审查 | 知识侧观察命题 | PDF chunk 是权威，canonical proposition 是可重建投影 |
| Coverage Relation | 知识命题 × 患者事实 / 可信宠物事实 | 当前证据是否覆盖知识命题 | 当前回合派生结果，可缓存但不是权威事实源 |
| Clarification Gap | M13 | 当前回合语义绑定缺口 | 不自动等于追问指令 |

## 4. 总体管道

```text
当前用户输入
→ M02 TurnSnapshot
→ M06 生成
→ M08 审查
→ M09 / M10 修复
→ M11 Artifact Store
→ M12 Claim Graph
→ M13 ConsultationProjection

TrustedPetFactReader
→ TrustedPetFactBundle

ConsultationSemanticProjectionConsumer
→ ConsultationEvidenceContext

KnowledgeRetrievalPort
→ candidate scopes + scoped chunks

Per Scope / Per Chunk Evidence Proposition Extraction
→ candidate evidence propositions

Deterministic Source Binding
→ candidate / source / scope identity

Support Review
→ supported / unsupported

├─ Candidate vs Canonical Duplicate Review
└─ Pairwise Batch Internal Duplicate Review

Deterministic Canonicalization
→ canonical knowledge propositions

Coverage Review
   ├─ Coverage State Review
   │  → knowledge proposition → coverage state
   └─ Coverage Source Binding Review
      → coverage state → supporting claim / fact selectors

Deterministic Coverage Relation Merge
→ canonical coverage relations

Dynamic Consultation Policy
→ answer / ask / reject
```

## 5. M13 患者事实输入边界

问诊领域唯一可消费的当前回合患者语义事实来源是：

```text
M13 ConsultationProjection
```

可用字段：

```text
Turn Intent:
  answer_now
  wants_triage
  correction
  clarification_request
  question_present
  fact_statement_present

Patient fact:
  claim_ref
  claim_digest
  claim_index
  proposition
  review_status
  gap_refs

Clarification gap:
  gap_id
  claim_ref
  ambiguous_dimension
  required_binding_type
  status
```

问诊领域不得读取：

```text
M11 内部 payload
M08 review matrix
M09 repair plan
M10 patch set
M12 内部结构
M06 proposal
原始用户文本
scheduler projection
```

禁止在 `VetOrchestrator` 或 M15 中做：

```text
ConsultationProjection
→ SemanticExtractionResult
```

这类硬转换会重新引入槽位猜测、`normal / denied` 混淆和 gap 强制追问。

## 6. 可信宠物事实输入

### 6.1 引入原则

已确认宠物事实应参与动态问诊，但不得并入 M13：

```text
M13 = 当前回合患者事实
TrustedPetFactBundle = 独立来源的已确认背景事实
```

二者在 `ConsultationEvidenceContext` 中显式分源组合，不共享身份。

### 6.2 TrustedPetFactBundle

目标契约：

```text
TrustedPetFactBundle
├─ pet_id
├─ bundle_id
├─ bundle_digest
├─ generated_at
├─ source_snapshot_version
└─ facts
```

每条事实：

```text
fact_id
fact_text
fact_kind
status
source_type
source_id
confirmed_at
valid_from
valid_to
fact_digest
```

`fact_text` 也必须是自包含自然语言 proposition，例如：

```text
宠物是猫
宠物年龄约十二岁
宠物患有慢性肾病
宠物无已知药物过敏史
```

### 6.3 允许来源

只允许：

```text
服务端宠物档案
已确认临床记录
经独立确认策略治理的长期事实
有来源、版本和时间窗口的可信事实
```

禁止：

```text
Mem0 原始召回片段
未确认 LLM 记忆抽取候选
无来源对话摘要
跨宠物相似文本召回
被用户纠正但未完成治理的旧事实
```

M13 长期记忆 TODO Adapter 不因本文启用；问诊层只消费已确认事实，不写长期事实。

### 6.4 时间与冲突原则

```text
历史体重不能覆盖当前体重
去年呕吐不能覆盖今天呕吐
无已知药物过敏不等于绝无过敏
当前用户明确纠正优先于旧档案
但问诊层不得静默改写宠物档案
```

冲突应进入显式状态：

```text
trusted_fact_conflict
temporal_scope_mismatch
correction_requires_profile_update
```

后续档案更新由对应领域处理，问询层只保留当前回合冲突证据。

## 7. ConsultationEvidenceContext

`ConsultationSemanticProjectionConsumer` 负责构造：

```text
ConsultationEvidenceContext
```

输入：

```text
ConsultationProjection
TrustedPetFactBundle
```

输出保留两个来源集合：

```text
current_patient_propositions
trusted_pet_facts
turn_intent
clarification_gaps
source identities
```

要求：

```text
不合并 claim_ref 与 fact_id
不改写 proposition
不把可信事实伪装成当前用户事实
不解析原始用户文本
不映射旧 ConsultationFactKey
```

当前实现状态是 TODO 边界。若依赖未就绪，应显式失败：

```text
consultation_projection_consumer_not_implemented
trusted_pet_fact_reader_not_implemented
```

## 8. Evidence Knowledge Retrieval 依赖边界

PDF 来源治理、结构解析、section tree、chunking、embedding、scope cluster、
索引发布与重建均属于 RAG 数据治理域，由以下文档定义：

```text
veterinary-rag-knowledge-graph-architecture.md
```

本文只定义问诊动态证据域如何消费：

```text
KnowledgeRetrievalPort
```

问诊层不得访问：

```text
RAG 数据库表
向量库内部集合
embedding model 内部对象
PDF parser
scope clustering 内部状态
index admin API
```

### 8.1 检索请求构造

问诊域基于 `ConsultationEvidenceContext` 构造检索请求，不把原始用户文本
交给 RAG 服务。

请求包含：

```text
request_id
query_propositions
trusted_fact_propositions 可选
top_scopes
top_chunks_per_scope
allowed_document_policy
retrieval_policy_version
```

其中：

```text
query_propositions 来自 M13 patient propositions
trusted_fact_propositions 来自 TrustedPetFactBundle
```

Query planner 不得：

```text
输出诊断结论
把历史事实扩大为当前症状
把知识 scope 当作疾病判断
读取原始用户文本
```

### 8.2 检索结果消费契约

`KnowledgeRetrievalPort` 返回：

```text
retrieval_id
index_release_id
index_version
terminal_state
failure_code
scopes
chunks
retrieval_metadata
```

问诊层消费每个 scope 的：

```text
scope_cluster_id
scope_text
scope_version
scope_cluster_digest
membership_strength
related_section_ids
```

消费每个 chunk 的：

```text
chunk_id
chunk_text
chunk_digest
document_id
document_version
section_id
section_path
page_start
page_end
parse_quality
scope_cluster_id
```

这些字段用于：

```text
scope / chunk 独立调用
support review
来源追溯
缓存 key
审计
```

字段的生产与维护细节由 RAG 数据治理域负责。

### 8.3 Scope-first 消费策略

运行时检索顺序：

```text
ConsultationEvidenceContext
→ candidate scope retrieval
→ chunk retrieval within each scope
```

例如当前事实：

```text
猫今天呕吐两次
猫精神状态正常
猫没有腹泻
```

可信事实：

```text
宠物是猫
宠物年龄约十二岁
宠物患有慢性肾病
```

可检索：

```text
scope_vomiting
scope_acute_vomiting
scope_feline_chronic_kidney_disease
scope_gastroenteritis
```

禁止一次抽取调用混合多个病症 scope 的 chunks。

`scope_confidence` / `membership_strength` 只能影响：

```text
候选排序
是否补充召回
是否复核 scope
调用优先级
```

不得用于：

```text
判定医学事实
判定 proposition 等价
产生 urgent / blocked
替代临床安全裁决
```

### 8.4 RAG 失败与无命中

问诊侧稳定失败状态：

```text
rag_service_unavailable
rag_retrieval_failed
knowledge_source_unavailable
no_authoritative_match
scope_metadata_missing
source_chunk_missing
```

RAG 内部失败由 `KnowledgeRetrievalPort` 映射为以上消费域稳定失败码；
问诊层不解释 RAG 内部 parser、embedding、index 或 clustering 错误细节。

不得回退到代码内固定症状槽位，除非该通用目录本身也是：

```text
显式配置
有版本
有来源
可审计
```

## 9. 知识证据命题抽取

### 9.1 调用粒度

每次 LLM 抽取调用只处理：

```text
一个 candidate scope
+ 一个 PDF chunk
```

或在严格预算下处理：

```text
同一 scope 下的连续小窗口
```

不得混合多个病症方向。

### 9.2 轻量 TAG 输入

输入使用 TAG，不使用深嵌套 JSON：

```text
<task>
从资料片段中总结当前候选病情方向相关的观察证据命题。
每个命题必须是自包含的自然语言陈述句。
只输出资料片段明确支持的观察命题。
不要诊断，不要治疗建议，不要安全动作，不要生成追问。
</task>

<rules>
主语只能来自当前资料 scope。
不确定物种时使用“患病动物”。
不要使用“它”“该症状”“此情况”。
保留时间、数量、程度、否定方向。
一个命题只表达一个观察。
资料片段没有支持的观察不要输出。
</rules>

<scope>
候选病情方向：犬猫呕吐相关鉴别观察
</scope>

<source>
<s0>
PDF chunk 原文……
</s0>
</source>
```

### 9.3 固定输出 JSON

模型输出必须是：

```json
{
  "evidence_propositions": [
    "患病动物呕吐物中带有血丝",
    "患病动物持续拒食超过一天"
  ]
}
```

契约：

```text
evidence_propositions: array[string]
默认最大 8 条
每条 1～120 字符
单行自然语言陈述句
extra field forbidden
```

命题必须：

```text
自包含
有明确主语
有观察对象和状态
保留时间 / 数量 / 程度 / 否定方向
一个命题只表达一个观察
```

禁止：

```text
孤立维度名词
反问句或问句
诊断
治疗建议
安全动作
追问建议
dimension_kind
candidate_condition_ref
source_chunk_refs
reason
confidence
corrected proposition
```

空输出合法：

```json
{
  "evidence_propositions": []
}
```

若 source chunk 明显包含可观察证据但输出为空，可进入：

```text
suspicious_empty_extraction
```

## 10. 确定性来源绑定

模型输出后，系统立即附加：

```text
candidate_id
candidate_text
source_chunk_id
section_id
scope_cluster_id
document_id
document_version
page_start / page_end
model metadata
review_state
```

绑定原则：

```text
source_chunk_refs 不由模型输出
scope / condition 身份不由模型输出
所有来源身份由调用上下文和确定性代码绑定
```

## 11. Support Review

### 11.1 输入

```text
candidate evidence propositions
source chunk
candidate scope
```

不输入：

```text
canonical propositions
用户事实
其他 source chunk
问诊策略
```

### 11.2 固定输出 JSON

```json
{
  "supported_by_candidate": {
    "e0": true,
    "e1": false
  }
}
```

语义：

```text
true  = 当前 source chunk 支持该 candidate proposition
false = 当前 source chunk 不支持该 candidate proposition
```

Verifier 必须：

```text
output key set == candidate selector set
value 严格为 boolean
extra / missing key 均 blocked
禁止 reason / confidence / corrected value / source refs
```

Unsupported candidate 不得进入 canonical active set。

## 12. 重复关系审查

重复关系分为两类，二者可在同一冻结候选集上并发执行：

```text
Candidate vs Canonical Duplicate Review
Pairwise Batch Internal Duplicate Review
```

### 12.1 Candidate vs Canonical

输入：

```text
canonical knowledge propositions
candidate propositions
```

固定输出：

```json
{
  "duplicate_by_candidate": {
    "e0": true,
    "e1": false,
    "e2": false
  }
}
```

语义：

```text
true  = candidate 与某个 canonical proposition 等价
false = candidate 与给定 canonical 集合不等价
```

不输出：

```text
重复目标 canonical id
pair
reason
confidence
dimension text
```

`true` 只表示真值条件等价。以下必须 `false`：

```text
同一主题但方向相反
同一主题但粒度不同
时间范围不同
数量阈值不同
程度不同
broader / narrower
不确定等价
```

示例：

```text
患病动物呕吐物中带有血丝
患病动物呕吐物中出现血丝
→ true
```

```text
患病动物出现呕吐
患病动物没有呕吐
→ false
```

```text
患病动物拒食
患病动物持续拒食超过一天
→ false
```

### 12.2 Pairwise Batch Internal

不应要求模型判断：

```text
每个 candidate 是否与排列在它之前的 candidate 重复
```

模型不擅长隐式维护输入顺序和前序集合。系统必须显式生成 pair。

进入 pairwise review 前，系统必须先执行确定性 exact dedupe，并限制本批
candidate 数量。若超过策略上限，应按 scope / source window 拆分或进入
显式人工系统性审查；不得截断 pair 列表，也不得只比较相邻 candidate。

候选：

```text
e0 患病动物呕吐发作次数增加
e1 患病动物呕吐频率增加
e2 患病动物呕吐物中带有血丝
e3 患病动物持续拒食超过一天
```

系统生成：

```text
p0 = e0 vs e1
p1 = e0 vs e2
p2 = e0 vs e3
p3 = e1 vs e2
p4 = e1 vs e3
p5 = e2 vs e3
```

固定输出：

```json
{
  "equivalent_by_pair": {
    "p0": true,
    "p1": false,
    "p2": false,
    "p3": false,
    "p4": false,
    "p5": false
  }
}
```

Pair 输入使用显式 TAG：

```text
<pair selector="p0">
<a selector="e0">患病动物呕吐发作次数增加</a>
<b selector="e1">患病动物呕吐频率增加</b>
</pair>
```

禁止输出：

```text
e* / d* 作为 key
merge target
pair text
reason
confidence
```

Verifier 必须：

```text
pair key set == 系统生成 pair selector set
value 严格为 boolean
全部 pair 显式处理
```

Pairwise 调用预算必须显式配置。例如 candidate 数量为 `N` 时，pair 数量为：

```text
N × (N - 1) / 2
```

超出预算不是 silently drop 的理由，而是：

```text
split_batch
candidate_count_exceeded
human_system_review
```

三者必须显式选择其一。

### 12.3 为什么禁止布尔数组

禁止：

```json
{
  "duplicate_flags": [true, false, false]
}
```

原因：

```text
模型需隐式维护输入顺序
错位难以检测
长度碰巧一致时可能 silent misalignment
```

也禁止：

```json
{
  "same_pairs": [["e0", "d0"]]
}
```

原因：

```text
当前不需要目标映射
要求模型复述两个 selector 并组织嵌套 pair
```

## 13. 确定性 Canonicalization

### 13.1 输入

```text
supported candidates
candidate vs canonical duplicate result
pairwise internal duplicate result
```

### 13.2 流程

```text
1. deterministic exact text dedupe
2. 根据 true pair 构建 connected components
3. 校验 pair 传递一致性
4. 每个 component 选择确定性 representative
5. component 内 candidate vs canonical 结论必须一致
6. 生成 canonical proposition id / digest
7. 绑定 source / scope / document edges
8. 保留 raw candidate 审计记录
```

Representative 选择应使用：

```text
稳定输入顺序中的最早 candidate
```

不得由 LLM 选择 canonical 文本。

### 13.3 一致性失败

示例：

```text
e0 ↔ e1 = true
e1 ↔ e2 = true
e0 ↔ e2 = false
```

应输出：

```text
duplicate_relation_disagreement
```

不得多数投票、静默重试或自动择一。

### 13.4 跨批处理

多批并发抽取时，各批可先执行批内审查；保留候选进入最终 cross-batch
pairwise review，再进入全局 canonical set。

禁止多个并发批直接写入同一 canonical set。

## 14. Coverage Review

### 14.1 输入

```text
canonical knowledge propositions
current patient propositions
trusted pet facts
clarification gaps
```

不输入：

```text
PDF chunk
原始用户文本
旧核心槽位
临床安全裁决结果
```

### 14.2 覆盖状态

目标状态：

```text
covered_present
covered_absent
covered_normal
covered_abnormal
partially_covered
uncertain
unknown
unobserved
not_applicable
scope_mismatch
temporal_scope_mismatch
conflict
```

必须保留：

```text
reported normal ≠ denied
denied ≠ unknown
unobserved ≠ absolute denial
partially covered ≠ fully covered
历史事实 ≠ 当前症状
无已知否定 ≠ 绝对不存在
```

### 14.3 两阶段薄输出原则

Coverage 不使用单个嵌套对象同时输出：

```text
knowledge proposition
→ state
→ source_refs
```

该形态要求模型同时完成：

```text
1. 覆盖状态判断
2. 支持来源选择
```

并迫使 `unknown` 也输出空数组，增加 JSON 长度、嵌套深度和 selector 漂移风险。

目标契约拆为两次顺序调用：

```text
Coverage State Review
→ k* → coverage state

Coverage Source Binding Review
→ k* → supporting c* / p* selectors
```

二者不能并发执行。来源绑定依赖状态审查结果，系统必须先获得 coverage state，
再筛出需要来源支持的 target knowledge propositions。

### 14.4 Coverage State Review

输入使用轻量 TAG：

```text
<task>
判断每个 knowledge proposition 被当前患者事实或可信宠物事实覆盖的状态。
输出 key 只能使用 knowledge selector。
不需要输出来源，不需要解释。
</task>

<rules>
covered_present 表示当前证据明确支持该情况存在。
covered_absent 表示当前证据明确否认该情况。
covered_normal 表示当前证据说明对应状态正常。
unknown 表示没有相关信息。
unobserved 表示没有形成可判定观察结果。
时间、数量、程度不足时使用 partially_covered。
用户表达不确定时使用 uncertain。
物种、对象或场景不适用时使用 not_applicable 或 scope_mismatch。
历史事实不能覆盖当前症状。
</rules>

<knowledge_propositions>
k0 患病动物呕吐物中带有血丝
k1 患病动物持续拒食超过一天
k2 患病动物年龄超过十岁
k3 患病动物精神状态明显下降
k4 患病动物当前体重明显下降
</knowledge_propositions>

<current_patient_facts>
c0 猫今天呕吐两次
c1 猫呕吐物中没有血丝
c2 猫精神状态正常
c3 猫从昨天开始不吃东西
</current_patient_facts>

<trusted_pet_facts>
p0 宠物年龄约十二岁
p1 宠物去年体重五公斤
</trusted_pet_facts>

<clarification_gaps>
g0 c3 拒食持续时长仍不明确
</clarification_gaps>
```

固定输出：

```json
{
  "coverage_state_by_knowledge": {
    "k0": "covered_absent",
    "k1": "partially_covered",
    "k2": "covered_present",
    "k3": "covered_normal",
    "k4": "temporal_scope_mismatch"
  }
}
```

Verifier 必须：

```text
输出 key set == 输入 knowledge selector set
state 来自白名单
value 是字符串枚举，不得是非枚举值
禁止 reason / confidence / corrected proposition
禁止 source refs
禁止 extra field
```

### 14.5 Coverage Source Binding Review

状态审查完成后，系统根据 state 筛出需要来源支持的 targets，再发起第二次
调用。该阶段不重新判断 coverage state。

该来源绑定不能像 PDF source chunk 一样完全由调用上下文确定性附加；判断哪条
当前患者事实或可信宠物事实支持某个 coverage state，本身仍是语义关系。因此
它必须作为独立受控审查执行，并由系统校验 selector 闭合与来源白名单。

若状态审查后 target set 为空，例如全部 knowledge propositions 均为
`unknown`，系统可以跳过 Source Binding 调用，但仍必须记录空 target set 的
确定性 digest，供 coverage relation 信封追溯。

输入示例：

```text
<task>
为每个 target knowledge proposition 选择支持其覆盖状态的患者事实或可信宠物事实。
输出 key 只能使用 target knowledge selector。
source selector 只能使用 c* 或 p*。
不需要输出状态，不需要解释。
</task>

<targets>
k0 covered_absent 患病动物呕吐物中带有血丝
k1 partially_covered 患病动物持续拒食超过一天
k2 covered_present 患病动物年龄超过十岁
k3 covered_normal 患病动物精神状态明显下降
k4 temporal_scope_mismatch 患病动物当前体重明显下降
</targets>

<sources>
c1 猫呕吐物中没有血丝
c2 猫精神状态正常
c3 猫从昨天开始不吃东西
p0 宠物年龄约十二岁
p1 宠物去年体重五公斤
</sources>
```

固定输出：

```json
{
  "source_refs_by_knowledge": {
    "k0": ["c1"],
    "k1": ["c3"],
    "k2": ["p0"],
    "k3": ["c2"],
    "k4": ["p1"]
  }
}
```

`source_refs` 只能使用输入中的：

```text
c*  当前患者 proposition selector
p*  trusted pet fact selector
```

Verifier 必须：

```text
输出 key set == 系统筛选的 target knowledge selector set
source_refs 必须来自输入 sources
source selector 不得重复
禁止 k* / e* / d* 作为 source selector
禁止 reason / confidence / corrected proposition
禁止重新输出 coverage state
禁止 extra field
```

### 14.6 状态与来源需求

默认规则：

| Coverage state | Source Binding 要求 |
|---|---|
| `covered_present` | 必须至少绑定 1 个来源 |
| `covered_absent` | 必须至少绑定 1 个来源 |
| `covered_normal` | 必须至少绑定 1 个来源 |
| `covered_abnormal` | 必须至少绑定 1 个来源 |
| `partially_covered` | 必须至少绑定 1 个来源 |
| `uncertain` | 必须至少绑定表达不确定的来源 |
| `conflict` | 必须至少绑定 2 个冲突来源 |
| `not_applicable` | 必须至少绑定适用范围判断来源 |
| `scope_mismatch` | 必须至少绑定范围不匹配来源 |
| `temporal_scope_mismatch` | 必须至少绑定时间不匹配来源 |
| `unknown` | 不进入 Source Binding Review |
| `unobserved` | 当前契约下不进入 Source Binding Review |

实施前需冻结 `unobserved` 的精确语义：

```text
若表示“证据集中缺少观察结果”，则不携带来源；
若表示“用户明确说明未观察”，应增加 reported_unobserved 状态，
或映射为 uncertain 并允许绑定来源。
```

在该契约冻结前，不得静默把用户明确表达的“未观察”当作绝对否定。

### 14.7 确定性合并

系统在两次审查通过后合并内部结果：

```text
k0:
  state = covered_absent
  source_refs = [c1]

k1:
  state = partially_covered
  source_refs = [c3]

k2:
  state = covered_present
  source_refs = [p0]
```

系统信封附加：

```text
coverage_relation_id
knowledge_set_digest
patient_fact_set_digest
trusted_fact_bundle_digest
coverage_state_review_digest
source_binding_review_digest
coverage_contract_version
model snapshot
```

下游动态策略只消费系统合并后的 coverage relation，不直接消费两次模型调用的
原始 JSON。

## 15. 动态问诊策略

### 15.1 输入

```text
candidate knowledge scopes
canonical knowledge propositions
merged coverage relations
Turn Intent
clarification gaps
followup limits
previous consultation state
```

策略层不读取自然语言原文，不解析 proposition 文本。

### 15.2 决策

输出：

```text
answer
ask
reject
```

优先级考虑：

```text
公共关键证据
鉴别性证据
信息增益
用户回答负担
追问上限
answer_now
gap 对回答充分性的实际影响
临床安全链路的独立结论
```

不得机械选择缺失证据最少的候选病症。

### 15.3 answer_now 与 gap

`answer_now=true` 允许输出阶段性回答，但不得抹除不确定性：

```text
基于当前信息
仍有不确定处
建议观察哪些信号
何时需要线下就医
```

Clarification gap 不直接等于追问：

```text
gap
→ 候选缺失信息
→ 回答充分性策略
→ 是否追问
```

## 16. 追问生成

知识抽取层不生成问句。

策略决定 `ask` 后，回复生成层才将缺失知识命题转换为用户可理解的问题。

例如：

```text
知识命题：
患病动物呕吐物中带有血丝
```

可生成：

```text
它呕吐物里有没有血丝或咖啡色物质？
```

该层只负责语言表达，不决定是否追问，不判断医学风险。

## 17. 临床安全边界

动态问诊层可以产生：

```text
需要补充观察的知识命题
```

不得产生：

```text
urgent signal
blocked signal
急诊结论
诊断结论
治疗方案
临床动作
```

临床安全仍由既有链路独立完成：

```text
clinical safety semantic extraction
→ clinical safety evaluator
→ required_context
→ clinical safety OPA
```

PDF 中的急症内容只能形成观察命题，例如：

```text
患病动物出现呼吸困难
患病动物出现抽搐
患病动物误食有毒物质
```

不得由问询层自行裁决急诊。

## 18. 旧核心槽位迁移

### 18.1 当前状态

旧 13 个 `ConsultationFactKey` 槽位仍是生产实现的一部分：

```text
species
life_stage_or_age
weight
onset
mental_status
appetite
vomiting
stool
breathing
pain_or_mobility
behavior_context
current_food
symptom_detail
```

目标架构中，它们不再作为问诊语义主表达。

### 18.2 阶段一：兼容层

旧槽位仅用于：

```text
历史状态兼容
旧 prompt 兼容
展示摘要
部署级回滚
```

新策略逐步改为消费 coverage graph。

### 18.3 阶段二：派生视图

只有明确等价时才从 canonical proposition coverage 派生旧槽位，例如：

```text
精神状态 → mental_status
```

不得强行合并：

```text
进食量变化
饮水量变化
→ appetite
```

除非问诊领域显式定义该粒度归并契约。

### 18.4 阶段三：退出主路径

最终：

```text
state.slots 不再是问诊策略权威
```

主输入变为：

```text
patient propositions
trusted pet facts
knowledge propositions
merged coverage relations
```

## 19. 数据权威与持久化

| 数据 | 权威 / 来源 | 派生关系 |
|---|---|---|
| 当前患者事实 | M11 SemanticTurnArtifact | M12 / M13 是确定性投影 |
| PDF 知识原文 | PDF source chunk | section / chunk index 是结构化索引 |
| scope cluster | section / chunk / embedding 派生 | 可重建缓存 |
| canonical knowledge proposition | PDF chunk + 审查链路 | 可重建派生索引 |
| trusted pet fact | 宠物档案或已确认记录 | TrustedPetFactBundle 是读取投影 |
| coverage relation | 当前回合派生 | 可缓存，不是权威事实源 |

未来数据库实现必须满足：

```text
SQLAlchemy 建模
字段级 description
仓储 Protocol 显式继承
业务层不得直接操作表模型
```

## 20. 缓存

可缓存：

```text
scope retrieval result
per chunk evidence propositions
support review result
duplicate review result
canonical proposition set
coverage state review result
source binding review result
coverage relation
```

缓存 key 至少包含：

```text
chunk_digest
scope_digest
prompt_hash
skill version
model snapshot
review contract version
canonical set digest
```

缓存是性能优化，不是权威事实源。

## 21. 最小观测字段

M14 完整可观测性暂后，但实现仍必须保留最小身份字段：

```text
turn_snapshot_digest
graph_digest
projection_id
pet_fact_bundle_digest
document_id
section_id
chunk_id
scope_cluster_id
candidate_proposition_id
canonical_proposition_digest
coverage_state_review_digest
source_binding_review_digest
coverage_relation_digest
review_contract_version
model snapshot
terminal_state
failure_code
```

不得为补偿 M14 后置而添加散乱日志或悬空指标配置。

## 22. 失败状态

目标失败码至少包括：

```text
consultation_projection_invalid
consultation_projection_consumer_not_implemented
trusted_pet_fact_reader_not_implemented
trusted_pet_fact_source_unavailable
trusted_pet_fact_conflict
rag_retrieval_failed
knowledge_source_unavailable
no_authoritative_match
pdf_parse_quality_degraded
scope_metadata_missing
source_chunk_missing
evidence_proposition_schema_invalid
suspicious_empty_extraction
evidence_proposition_unsupported
support_review_schema_invalid
duplicate_review_schema_invalid
duplicate_relation_disagreement
candidate_count_exceeded
canonicalization_failed
coverage_state_review_schema_invalid
coverage_source_binding_schema_invalid
coverage_source_binding_failed
coverage_conflict
dynamic_policy_failed
followup_generation_failed
```

所有失败显式暴露，禁止转为：

```text
facts=[]
空槽位
空知识命题
旧抽取器自动 fallback
```

## 23. M15 接入边界

M15 不得实现问诊领域逻辑，只调用稳定端口：

```text
SemanticConsultationPreprocessingService
→ ConsultationProjection
→ Consultation dynamic evidence pipeline
```

开启 primary 前必须确认：

```text
ConsultationSemanticProjectionConsumer 就绪
TrustedPetFactReader 就绪
KnowledgeRetrievalPort 与 RAG active index release 就绪
Evidence Proposition Extraction / Review 契约就绪
Coverage State Review 契约就绪
Coverage Source Binding Review 契约就绪
动态问诊策略就绪
失败映射与回滚配置就绪
```

旧问诊链路只允许：

```text
部署级显式回滚
配置级显式关闭
```

禁止单次请求失败后自动回退。

## 24. 防退化约束

后续实现不得引入：

```text
ConsultationProjection → SemanticExtractionResult 硬转换
固定症状槽位回归主路径
关键词 / 正则症状抽取
embedding 阈值直接判定医学事实
embedding 阈值直接判定命题等价
模型输出 reason / confidence / corrected value
模型输出 source_chunk_refs
模型输出 condition id
多病症 chunk 混合调用
人工维护全量病症证据索引
RAG no-hit 后回退硬编码槽位
M15 直接读取 M11 / M12 内部结构
VetOrchestrator 硬转换 M13 projection
Mem0 原始候选进入可信事实
gap 直接变成强制追问
Coverage 单次输出嵌套 state / source 对象
问诊层产生 urgent / blocked
问诊层调用临床安全 evaluator / OPA / required_context
问诊层写长期记忆
失败转空 facts
单次请求自动回退旧链路
```

## 25. 实施清单

组件顺序、依赖、交付物、PR 切分、TODO / blocked 状态和实施验收由以下文档
单独维护：

```text
consultation-dynamic-evidence-implementation-plan.md
```

本文不维护工程进度，不重复维护 PR 列表。

## 26. 验收测试

至少覆盖：

```text
M13 输出未读取原始用户文本
问诊层未读取 M11 / M12 内部结构
TrustedPetFact 与 current claim 身份未混合
Mem0 原始候选未进入
检索返回 chunk 均可追溯 document / section / page
scope 内 chunk 未跨病症混合
抽取输出为自包含陈述句
抽取输出不含问句 / 诊断 / 治疗 / 安全动作
模型未输出 source_chunk_refs
support review key closure 有效
unsupported proposition blocked
candidate vs canonical 输出 keyed boolean
pairwise review 输出 keyed boolean
pairwise 传递一致性冲突 blocked
重复候选未进入 active canonical set
normal / denied / unknown / unobserved 覆盖状态分离
coverage state 输出 keyed enum
source binding 输出 keyed selector array
source binding target set 由 state 结果确定性筛选
state review 与 source binding 顺序执行
unknown / unobserved 未进入 source binding
source refs 不包含不存在 selector
嵌套 state / source 单对象输出被拒绝
历史事实未覆盖当前症状
gap 未直接转为 followup_required
answer_now 未抹除不确定性
问诊层未调用临床安全 evaluator / OPA
问诊层未写长期记忆
失败未转空 facts
旧抽取器未在 primary 失败路径自动调用
```

## 27. 文档同步触发

以下变化必须同步更新本文：

```text
M13 ConsultationProjection 契约调整
TrustedPetFactBundle 契约调整
KnowledgeRetrievalPort 消费契约调整
知识命题抽取输出契约调整
Support Review 输出契约调整
重复判定 JSON 调整
Coverage 状态机或两阶段输出契约调整
动态问诊策略调整
旧槽位迁移策略调整
M15 接入前提调整
```

## 28. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m13-domain-projection-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m13-domain-projection-change-summary.md)
4. [consultation-semantic-extraction-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-semantic-extraction-change-summary.md)
5. [consultation-state-answerability-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-state-answerability-change-summary.md)
6. [clinical-safety-semantic-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-semantic-change-summary.md)
7. [veterinary-rag-knowledge-graph-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/veterinary-rag-knowledge-graph-architecture.md)
8. [consultation-dynamic-evidence-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-implementation-plan.md)
