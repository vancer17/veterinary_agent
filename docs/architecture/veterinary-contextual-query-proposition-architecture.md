<!--
=============================================================================
文件: docs/architecture/veterinary-contextual-query-proposition-architecture.md
作用: 定义兽医问诊语义预处理中 Contextual Query Proposition 层的检索用
      语境命题生成、审查、缺陷簇修复、事务式 join 与失败治理架构。
范围: 覆盖 Contextual Query Proposition Scheduler、按 scope fan-out 的
      Generator、严格 schema 校验、本地 q* selector、origin scope 绑定、
      并行 Contextual Proposition Review、固定缺陷 Delta、target 级缺陷簇、
      cluster-level repair、transactional join、canonicalize / dedupe /
      renumber、post-review、Retrieval Query Bundle、RAG 消费边界、
      执行审计与 LangGraph 边界。
说明: 本文消费已受治理的自包含 semantic anchors、只读可信宠物事实投影与
      joint context scopes，只把 scope 内明确存在的组合语义显式化为检索用
      命题；不生成权威患者事实，不做 Coverage、知识命题治理、诊断、因果
      推断或临床安全裁决。
维护: 当 contextual proposition 的权威地位、输入输出契约、Review Delta、
      缺陷簇、修复或 join 策略、RAG 消费边界或 Fail Fast 底线调整时，
      必须先修订本文，再同步实现。
=============================================================================
-->

# 兽医 Contextual Query Proposition 层架构设计草案

> **文档状态**：架构草案基线
>
> **文档定位**：本文细化
> [veterinary-semantic-decomposition-charter.md](./veterinary-semantic-decomposition-charter.md)
> 下的语义预处理检索视图边界。它位于
> [veterinary-semantic-anchor-pipeline.md](./veterinary-semantic-anchor-pipeline.md)
> 和
> [veterinary-semantic-scope-architecture.md](./veterinary-semantic-scope-architecture.md)
> 之后，消费已受治理的自包含 anchors、可信事实投影与显式 joint context
> scopes。
> 它同时遵守
> [veterinary-semantic-preprocessing-control-conventions.md](./veterinary-semantic-preprocessing-control-conventions.md)
> 中的跨层控制约定。
>
> **权威关系**：本文服从语义分解 charter。若本文、anchor pipeline、Scope
> 层架构、charter 或实现发生冲突，必须先显式修订其中一方，不得在代码或
> prompt 中形成第三种解释。

## 1. 核心结论

Contextual Query Proposition 层采用：

```text
已受治理的自包含 semantic anchors
+ 只读可信宠物事实投影
+ 已受治理的统一 joint context scopes
+ 按 scope fan-out 的显式化 Generator
+ 自包含自然语言 contextual query propositions
+ 分组固定缺陷维度 Review
+ target 级缺陷簇聚合
+ cluster-level 局部修复
+ 所有修复结果事务式 join 回原始快照
+ canonicalize / dedupe / renumber
+ 修复后完整重审
+ Retrieval Query Bundle
+ Fail Fast
```

核心边界：

```text
patient fact anchor 是患者事实候选；
joint context scope 是 a* / p* 语义单元之间的显式共同解释语境 overlay；
contextual query proposition 是检索用派生语义视图；
contextual query proposition 不是权威患者事实；
contextual query proposition 不是知识命题；
contextual query proposition 不是 evidence；
contextual query proposition 不是 RAG query rewrite。
proposition 文本不携带来源、权威性或治理元数据。
```

最简洁地说：

```text
显式化 scope 中已经存在的组合语义；
不推断新医学语义。
```

## 2. 架构范围

### 2.1 In Scope

本文覆盖：

```text
Contextual Query Proposition Scheduler
Contextual Query Proposition Generator
Generator 输出契约
严格 schema 校验
q* 本地 selector 分配
origin_scope_refs 系统绑定
current_anchor_refs / trusted_fact_refs 系统推导
精确重复清理
Completeness / Support Review
Form / Subject Review
State / Certainty Review
Measurement / Relation Review
Boundary Review
分组固定缺陷 Delta Schema
缺陷码到固定中文短语的映射
target 级 defect cluster 聚合
cluster-level Targeted Proposition Repair
repair output validation
transactional proposition join
canonicalize / deduplicate / deterministic renumber
post-repair Contextual Proposition Review
TurnSemanticRetrievalQueryBundle
RAG 查询消费边界
执行审计边界
LangGraph 使用边界
失败状态与防漂移原则
```

### 2.2 Out of Scope

本文不覆盖：

```text
semantic anchor 生成与修复
joint context scope 生成与修复
患者事实最终权威提交
患者事实版本化与冲突治理
领域投影
Coverage
覆盖来源绑定
PDF 解析与 scope-first RAG
知识命题抽取与来源支持审查
医学诊断
治疗方案
严重程度判断
急诊或 urgent signal 判断
临床安全裁决
长期记忆写入
宠物档案更新
全局同义词扩展
HyDE
多语言检索改写
旧槽位迁移主路径
```

特别是：

```text
contextual query proposition 默认不得进入 Coverage；
不得写入宠物档案；
不得作为知识命题 evidence；
不得替代用户陈述事实。
```

## 3. 背景问题

### 3.1 自包含 anchors 对治理正确，但对检索可能不足

Anchor 层输出自包含自然语言事实短句，例如：

```text
狗历史体重为30千克
狗当前体重为15千克
狗当前进食量低于平时基线
狗近期换粮
狗在换粮后开始出现软便
狗当前精神状态正常
```

这些 anchors 对患者事实治理是正确粒度。它们已经保留：

```text
主体
时间范围
否定与未知
数量与单位
比较基线
事件顺序
观察者归因
```

但以下两个 anchors 分开进入 RAG 时：

```text
狗历史体重为30千克
狗当前体重为15千克
```

检索系统可能无法直接得到组合语义：

```text
当前体重低于历史体重
体重从历史测量值变为当前测量值
历史 / 当前体重需要共同解释
```

如果兽医教学材料按章节粒度组织，例如：

```text
体重下降
消瘦
营养状态评估
慢性疾病表现
饮食变化相关消化问题
```

仅使用两个独立测量事实作为查询，可能导致相关章节无法命中。

### 3.2 Scope 只标记语境，不显式化检索语义

Scope 层可能输出：

```text
s0 = a0+a1
```

其中：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
```

这表示两个 anchors 存在历史 / 当前体重的显式共同语境。但 scope 本身只有
成员关系，不携带关系类型，也不生成自然语言查询语义。

因此需要一层受治理的显式化：

```text
s0 = a0+a1
→ 狗体重由历史30千克变为当前15千克
```

该输出用于 RAG 检索，而不是作为新的用户陈述事实。

### 3.3 直接 query rewrite 的失败

如果让 RAG 层自由改写用户输入，容易产生：

```text
同义词扩展
医学术语升格
疾病联想
因果推断
整体状态综合
混合长查询
```

例如：

```text
狗去年30千克，现在15千克
```

可能被改写成：

```text
狗明显消瘦
狗患有慢性消耗性疾病
狗体重下降提示严重疾病
```

这些不是检索语义显式化，而是医学推断。

因此本文将检索语义显式化从 RAG 查询工程中拆出，并施加与 anchor / Scope
层相同的固定缺陷治理。

## 4. 核心语义对象

### 4.1 Patient Fact Anchor

Patient fact anchor 是：

```text
自包含自然语言原子事实候选
```

例如：

```text
狗当前体重为15千克
主人未观察到狗呕吐
狗在换粮后开始出现软便
```

它由 anchor pipeline 生成并审查，是患者事实治理的输入。

### 4.2 Joint Context Scope

Joint context scope 是：

```text
多个 anchor 之间显式共同解释语境的 overlay
```

例如：

```text
s0 = a0+a1
```

Scope 不改变 anchor 语义，不生成事实，不输出关系类型，不做医学综合。

### 4.3 Contextual Query Proposition

Contextual query proposition 是：

```text
基于一个 joint context scope，将其中明确存在的组合语义显式化而成的
自包含自然语言命题，用于 RAG 查询。
```

示例：

```text
anchor:
狗历史体重为30千克
狗当前体重为15千克

scope:
s0 = a0+a1

contextual query proposition:
狗体重由历史30千克变为当前15千克
```

它的权威地位是：

```text
检索用派生语义视图
```

不是：

```text
用户原话
权威患者事实
知识命题
evidence
医学结论
```

## 5. 输入边界与启动条件

Contextual Query Proposition 层的输入由系统从以下结果构造：

```text
当前原始用户输入 t0
最终 TurnSemanticAnchorEnvelope
最终 TrustedPetFactProjection
最终 TurnSemanticScopeEnvelope
必要的主体上下文
语言策略
```

示例：

```text
<t0>
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行。
</t0>

<subject_context>
当前讨论主体为狗。
</subject_context>

<anchors>
a0 狗当前体重为15千克
a1 狗当前进食量低于平时基线
a2 狗近期换粮
a3 狗在换粮后开始出现软便
a4 狗当前精神状态正常
</anchors>

<trusted_pet_facts>
p0 狗历史体重为30千克
p1 狗平时每日进食量约为200克
</trusted_pet_facts>

<joint_context_scopes>
a0+p0
a2+a3
</joint_context_scopes>
```

主体边界：

```text
本层不重新消解“它 / 这个 / 那个”；
本层继承 anchors 与 scopes 中已经完成的主体表达；
subject_error 仅表示 contextual proposition 与上游主体不一致；
本层不得猜测物种、名字或多宠物主体。
```

启动条件：

```text
anchor envelope 状态为成功；
TrustedPetFactProjection 状态为成功或合法为空；
scope envelope 状态为成功；
anchors、trusted facts 和 scopes 数量在限制内；
当前 turn 输入仍可提供给 Generator 与 Review；
本层配置和 prompt 版本就绪。
```

若 `joint_context_scopes` 为空，本层可以合法输出：

```text
contextual_query_propositions = []
```

但该状态必须由系统显式标记，不得把模型失败伪装为空结果。

## 6. 顶层管道

```text
TurnSemanticAnchorEnvelope
→ a*

TrustedPetFactProjection
→ p*

a* + p*
→ TurnSemanticScopeEnvelope
→ Contextual Query Proposition Scheduler

→ fan-out one scope per Generator call
→ Contextual Query Proposition Generator
→ Strict Generator Schema Validation
→ Deterministic Concatenation
→ Exact Duplicate Cleanup
→ Local q* Selector Assignment
→ origin_scope_refs Binding

→ Parallel Contextual Proposition Review
   ├─ Completeness / Support Review
   ├─ Form / Subject Review
   ├─ State / Certainty Review
   ├─ Measurement / Relation Review
   └─ Boundary Review

→ Review Delta Merge
→ Defect Cluster Aggregation
→ Contextual Proposition Repair Scheduler

   ├─ no defect → Transactional Proposition Join
   ├─ existing q* cluster → Targeted Proposition Repair
   └─ missing s* cluster → Missing Context Proposition Repair

→ Collect All Repair Outputs
→ Repair Output Validation
→ Transactional Proposition Join
→ Canonicalize / Deduplicate / Deterministic Renumber
→ Post Contextual Proposition Review
→ TurnSemanticRetrievalQueryBundle
```

示意图：

```text
┌────────────────────────────┐
│ Anchor Envelope            │
└─────────────┬──────────────┘
              │
┌─────────────▼──────────────┐
│ Final Joint Context Scopes │
└─────────────┬──────────────┘
              │
┌─────────────▼──────────────┐
│ Query Proposition          │
│ Scheduler                  │
└─────────────┬──────────────┘
              │
     fan-out per scope
              │
┌─────────────▼──────────────┐
│ Contextual Proposition     │
│ Generator                  │
└─────────────┬──────────────┘
              │
┌─────────────▼──────────────┐
│ Validation + q* + origins  │
└─────────────┬──────────────┘
              │
┌─────────────▼──────────────┐
│ Parallel Review            │
├─ Completeness / Support    │
├─ Form / Subject            │
├─ State / Certainty         │
├─ Measurement / Relation    │
└─ Boundary                  │
└─────────────┬──────────────┘
              │
┌─────────────▼──────────────┐
│ Defect Cluster Repair      │
└─────────────┬──────────────┘
              │
┌─────────────▼──────────────┐
│ Transactional Join         │
├─ canonicalize              │
├─ deduplicate               │
├─ renumber                  │
└─ post-review               │
└─────────────┬──────────────┘
              │
┌─────────────▼──────────────┐
│ Retrieval Query Bundle     │
└────────────────────────────┘
```

## 7. Contextual Query Proposition Scheduler

Scheduler 是系统节点，不调用 LLM。

职责：

```text
检查输入 envelope 状态；
读取最终 anchors、trusted facts 与 scopes；
检查数量与长度限制；
为每个 scope 构造一个 Generator 调用；
控制并发与预算；
绑定执行审计身份。
```

调度规则：

```text
一个 joint context scope 一次 Generator 调用；
不把所有 scopes 混入一次调用；
不把无 scope 的 turn 送入 Generator；
不并行处理同一 scope 的多个候选版本。
```

按 scope fan-out 的原因：

```text
模型注意力集中在单一语境；
输出更短；
便于局部修复；
便于系统绑定 origin_scope_refs；
便于系统从 a* / p* 推导 current_anchor_refs 与 trusted_fact_refs；
避免一个 scope 的错误污染其他 scope。
```

## 8. Contextual Query Proposition Generator

### 8.1 职责

Generator 只处理一个 scope，将该 scope 中已经明确存在的组合语义显式化为
自包含自然语言命题。

它回答：

```text
这个 scope 中的 a* / p* 语义单元组合起来，明确表达了什么可直接检索的
语境语义？
这个 scope 中的 a* / p* 语义单元组合起来，明确表达了什么可直接检索的
语境语义？
```

它不回答：

```text
这些事实可能意味着什么疾病？
这些事实是否严重？
这些事实是否相关或相似？
应如何治疗？
```

### 8.2 输入契约

每个 Generator 调用输入：

```text
<original_input>当前原始用户输入</original_input>

<subject_context>主体上下文</subject_context>

<scope>a*+p*</scope>

<anchors>
a0 ...
a1 ...
</anchors>

<trusted_pet_facts>
p0 ...
p1 ...
</trusted_pet_facts>

<trusted_pet_facts>
p0 ...
p1 ...
</trusted_pet_facts>
```

示例：

```text
<original_input>
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行。
</original_input>

<subject_context>
当前讨论主体为狗。
</subject_context>

<scope>
a0+p0
</scope>

<anchors>
a0 狗当前体重为15千克
a1 狗当前进食量低于平时基线
</anchors>

<trusted_pet_facts>
p0 狗历史体重为30千克
</trusted_pet_facts>
```

Generator 可以看到当前原始输入和该 scope 的 a* / p* 语义单元，但不得读取：

```text
完整会话历史
未治理记忆
宠物档案原始记录
RAG chunk
知识命题
其他 scope 的 Generator 输出
```

### 8.3 输出契约

输出必须是薄结构：

```json
{
  "propositions": [
    "狗体重由历史30千克变为当前15千克"
  ]
}
```

无新增可显式化语义时可以输出：

```json
{
  "propositions": []
}
```

禁止输出：

```text
id
q*
reason
confidence
relation
source_ref
source_span
quote
medical_dimension
fact_type
task_type
corrected propositions
```

模型不分配 selector。系统在收集和去重后统一分配 `q*`。

### 8.4 输出限制

生产配置应显式定义：

```text
单个 scope 最多输出 proposition 数
单条 proposition 最大长度
proposition 必须单行
proposition 必须非空
禁止深层 JSON
禁止额外字段
```

初始建议：

```text
每个 scope 最多 1-3 条 contextual propositions
```

## 9. 允许的显式化语义

本层只允许显式化 scope 与原始输入中已经明确支持的关系语义。

### 9.1 时间基准或测量变化语境

输入语义单元：

```text
a1 狗当前体重为15千克
p0 狗历史体重为30千克
```

可输出：

```text
狗体重由历史30千克变为当前15千克
狗当前体重低于历史体重
```

不得输出：

```text
狗明显消瘦
狗患有慢性消耗性疾病
狗体重下降提示严重疾病
```

本层默认不计算：

```text
下降15千克
下降50%
```

百分比、绝对变化量或医学阈值判断应由后续通用、有界、可审计的量化比较层
处理，不得伪装成本层命题。

### 9.2 明确事件顺序语境

输入 anchors：

```text
a3 狗近期换粮
a4 狗在换粮后开始出现软便
```

可输出：

```text
狗在换粮后开始出现软便
```

不得输出：

```text
换粮导致狗软便
狗对新粮食过敏
狗患有肠胃疾病
```

### 9.3 明确基线比较语境

输入语义单元：

```text
a6 狗今天进食量约为50克
p0 狗平时每日进食量约为200克
```

如果原始输入明确提供二者作为比较语境，可输出：

```text
狗今天进食量低于平时进食基线
```

不得输出：

```text
狗严重食欲下降
狗出现厌食症
狗需要强制喂食
```

### 9.4 同一事件或观察片段语境

输入 anchors：

```text
a8 狗今天呕吐两次
a9 狗今天呕吐物中含有血丝
```

若 scope 来自同一事件描述，可输出：

```text
狗今天呕吐两次，呕吐物中含有血丝
```

不得输出：

```text
狗消化道出血
狗病情危重
狗需要立即输血
```

### 9.5 明确转述归因语境

若原始输入为：

```text
医生说软便是换粮导致的
```

可输出：

```text
医生认为换粮导致狗软便
```

不得输出：

```text
换粮导致狗软便
```

区别是：

```text
reported attribution 可以保留；
system inferred causality 不得生成。
```

## 10. 禁止的显式化语义

Generator 与 Repair 一律禁止：

```text
推断因果关系
推断诊断
推断疾病机制
推断预后
判断严重程度
判断急诊或 urgent signal
生成治疗建议
生成护理动作
把口语表达升格为医学结论
把相近观察改写成更具体疾病名
把未观察改成绝对不存在
把不确定改成确定
在 proposition 文本中写入来源治理元数据
```

来源治理元数据包括：

```text
可信历史记录
可信档案
档案记录
已确认记忆
系统确认
当前回合 anchor
trusted fact
scope
来源
权威记录
```

示例：

```text
正确：狗体重由历史30千克变为当前15千克
错误：狗体重由可信历史记录的30千克变为当前15千克
```

“历史”和“当前”是时间语义，必须保留；
“可信历史记录”是来源治理元数据，不得进入 proposition 文本。

示例：

```text
精神还行
```

不应被改写为：

```text
狗精神状态正常
狗无神经系统异常
```

更保守输出应为：

```text
主人报告狗精神还可以
```

```text
软便
```

不应被改写为：

```text
腹泻
```

除非用户明确使用“腹泻”且语义等价性经过审查确认。

## 11. Generator 输出校验与 q* 分配

系统对每个 Generator 输出执行严格校验：

```text
唯一字段 propositions
propositions 是字符串数组
字符串非空、单行
长度不超限
数量不超限
不输出对象元素
不输出未知字段
不输出本地或全局 ID
```

校验通过后，系统执行：

```text
1. 按 scope 顺序收集 propositions
2. 去除完全重复文本
3. 对跨 scope 重复项合并 origin_scope_refs
4. 按确定性顺序分配 q0/q1/q2...
5. 由系统绑定每个 proposition 的 origin_scope_refs
```

示例：

```text
q0 狗体重由历史30千克变为当前15千克
  origin_scope_refs = [s0]

q1 狗在换粮后开始出现软便
  origin_scope_refs = [s1]
```

`q*` 是当前调用内的本地 selector，不是长期身份，不写入档案，不进入最终
核心契约。

## 12. origin_scope_refs 边界

最终 contextual proposition 的执行视图可由系统绑定：

```text
origin_scope_refs
current_anchor_refs
trusted_fact_refs
derivation_kind
```

例如：

```json
{
  "proposition": "狗体重由历史30千克变为当前15千克",
  "origin_scope_refs": ["s0"],
  "current_anchor_refs": ["a1"],
  "trusted_fact_refs": ["p0"],
  "derivation_kind": "contextual_query"
}
```

这些字段均由系统绑定，不由模型输出，也不进入 proposition 自然语言文本。

它与此前被移除的 `source_ref=current_turn` 不同：

```text
source_ref=current_turn：
  对每条事实无区分度；
  无明确消费者；
  容易形成伪来源自证。

origin_scope_refs：
  表示命题由哪个 scope 派生；
  由系统绑定；
  用于检索分组、审计和解释；
  不是原始用户文本来源自证。
```

`origin_scope_refs` 不得解释为：

```text
用户说了这句话
权威事实来源
RAG evidence
知识命题来源
```

## 13. Contextual Proposition Review

Review 独立于 Generator，并按缺陷组拆分为五个并行 LLM 调用，避免单一调用
同时判断支持性、语义保留和架构边界。

Review 输入由系统构造：

```text
<t0>当前原始输入</t0>

<anchors>
a0 ...
a1 ...
</anchors>

<trusted_pet_facts>
p0 ...
p1 ...
</trusted_pet_facts>

<joint_context_scopes>
s0 = ...
s1 = ...
</joint_context_scopes>

<contextual_query_propositions>
q0 ... [s0]
q1 ... [s1]
</contextual_query_propositions>
```

### 13.1 Proposition Completeness / Support Review

只判断：

```text
scope 中是否存在未显式化的明确语境；
proposition 是否被原始输入、scope 与可信事实支持。
```

Delta Schema：

```json
{
  "missing_explicit_context_proposition": ["s0"],
  "unsupported_addition": ["q0"]
}
```

`missing_explicit_context_proposition` 只允许 `s*`；`unsupported_addition`
只允许 `q*`。

### 13.2 Proposition Form / Subject / Source Metadata Review

只判断：

```text
主体是否与上游 anchors / scope 一致；
proposition 是否自包含；
proposition 文本是否混入来源治理元数据。
```

Delta Schema：

```json
{
  "subject_error": ["q0"],
  "not_self_contained": ["q1"],
  "provenance_metadata_added": ["q2"]
}
```

该 Group 只允许 `q*`。

`provenance_metadata_added` 用于捕获：

```text
可信历史记录
可信档案
已确认记忆
系统确认
当前回合 anchor
trusted fact
scope
来源
权威记录
```

等来源治理元数据。

本层不重新消解“它 / 这个 / 那个”。如果上游输入仍保留未消解主体，说明
anchor / intent 层未完成，不应进入本层。

### 13.3 Proposition State / Certainty Review

只判断：

```text
时间
极性
不确定性
观察者归因
```

Delta Schema：

```json
{
  "polarity_error": ["q0"],
  "temporal_error": ["q1"],
  "uncertainty_error": ["q3"]
}
```

该 Group 只允许 `q*`。

### 13.4 Proposition Measurement / Relation Review

只判断：

```text
数量
单位
关系语义
```

Delta Schema：

```json
{
  "quantity_or_unit_error": ["q2"],
  "relation_loss": ["q4"]
}
```

该 Group 只允许 `q*`。

### 13.5 Proposition Boundary Review

只判断：

```text
proposition 是否越界成因果推断；
是否添加医学结论、诊断、严重程度或临床动作。
```

Delta Schema：

```json
{
  "causal_overreach": ["q0"],
  "medical_conclusion_added": ["q1"]
}
```

该 Group 只允许 `q*`。

### 13.6 合并后的完整 Delta 语义视图

系统将五个 Group 的输出确定性合并后，得到完整语义视图：

```json
{
  "missing_explicit_context_proposition": ["s0"],
  "unsupported_addition": ["q0"],
  "subject_error": ["q1"],
  "not_self_contained": ["q2"],
  "provenance_metadata_added": ["q3"],
  "polarity_error": ["q4"],
  "temporal_error": ["q5"],
  "quantity_or_unit_error": ["q6"],
  "uncertainty_error": ["q7"],
  "relation_loss": ["q8"],
  "causal_overreach": ["q9"],
  "medical_conclusion_added": ["q10"]
}
```

实际输出省略空数组。

### 13.7 字段定义

| 字段 | 含义 | 允许 target |
|---|---|---|
| `missing_explicit_context_proposition` | scope 中存在可显式化的明确语境，但当前 propositions 未表达 | `s*` |
| `unsupported_addition` | proposition 添加了原始输入、scope 或可信事实不支持的内容 | `q*` |
| `subject_error` | proposition 主体与上游 anchors / scope 主体不一致 | `q*` |
| `not_self_contained` | proposition 依赖上下文才能理解 | `q*` |
| `provenance_metadata_added` | proposition 文本混入来源、权威性或治理元数据 | `q*` |
| `polarity_error` | 肯定、否定、未知或未观察混淆 | `q*` |
| `temporal_error` | 时间范围或时间基准错误 | `q*` |
| `quantity_or_unit_error` | 数量、单位、次数或程度丢失 / 错写 | `q*` |
| `uncertainty_error` | 确定性或观察者归因丢失 | `q*` |
| `relation_loss` | 时间、基线、事件顺序或比较关系丢失 | `q*` |
| `causal_overreach` | proposition 需要系统推断因果才能成立，或把时间顺序升格为因果 | `q*` |
| `medical_conclusion_added` | proposition 添加诊断、疾病、严重程度、急诊或治疗结论 | `q*` |

### 13.8 Review 输出校验

每个 Group 必须独立校验：

```text
字段名属于该 Group allowlist
数组非空
数组元素唯一
target selector 存在
missing_explicit_context_proposition 只允许 s*
其他字段只允许 q*
不得输出其他 Group 的缺陷码
不输出未知字段
不输出 reason
不输出 confidence
不输出 corrected proposition
不输出 source span
不输出 supported / rejected
```

系统合并时：

```text
同 code 同 target 去重；
不同 code 同 target 聚合为同一缺陷簇；
不同 target 分别聚合；
s* 缺陷单独作为 missing scope defect。
```

Review 不直接修改 proposition，也不输出 corrected value。

## 14. 缺陷码映射与 prompt 边界

### 14.1 固定中文短语

系统只做稳定缺陷码到固定中文短语的映射：

```text
missing_explicit_context_proposition → 显式语境命题缺失
unsupported_addition                → 添加原文未表达语义
subject_error                       → 主体语义错误
not_self_contained                  → 命题未自包含
provenance_metadata_added           → 来源元数据混入命题
polarity_error                      → 极性语义错误
temporal_error                      → 时间语义错误
quantity_or_unit_error              → 数量单位语义错误
uncertainty_error                   → 不确定性语义丢失
relation_loss                       → 高维语义关系丢失
causal_overreach                    → 因果过度推断
medical_conclusion_added            → 添加医学结论
```

这些是有限治理缺陷维度，不是医学事实维度。禁止加入：

```text
weight_query_error
diarrhea_query_error
emergency_query_error
diagnosis_query_error
```

### 14.2 缺陷码解释只描述现象

Repair prompt 中的缺陷码解释必须只描述现象，不得断言修复动作。

正确示例：

```text
添加原文未表达语义：
  当前 proposition 包含原始输入或 scope 不支持的内容。

添加医学结论：
  当前 proposition 添加了诊断、疾病、严重程度、急诊或治疗结论。

来源元数据混入命题：
  当前 proposition 文本包含可信、档案、已确认记忆、系统确认、anchor、
  scope 或 trusted fact 等来源治理元数据。

因果过度推断：
  当前 proposition 需要依赖因果关系才能成立，但原始输入没有明确表达
  因果、事件顺序或转述归因。

高维语义关系丢失：
  当前 proposition 丢失了 scope 中明确存在的时间基准、比较基线、
  数量单位或事件顺序关系。
```

禁止写：

```text
添加原文未表达语义：删除该 proposition
因果过度推断：改成事件顺序
高维语义关系丢失：补充比较关系
```

原因是：

```text
同一个 proposition 可能命中多个缺陷码；
缺陷码是观察结果，不是修复命令；
修复必须针对完整缺陷簇；
按缺陷码断言动作会误导 Repair 模型。
```

## 15. Defect Cluster 聚合

系统必须将 Review delta 按 target 聚合，而不是按缺陷码分别修复。

示例 Review delta：

```json
{
  "missing_explicit_context_proposition": ["s0"],
  "unsupported_addition": ["q0"],
  "medical_conclusion_added": ["q0"]
}
```

系统聚合为：

```json
{
  "repair_clusters": [
    {
      "target": "q0",
      "target_kind": "existing_proposition",
      "origin_scope_refs": ["s0"],
      "defect_codes": [
        "unsupported_addition",
        "medical_conclusion_added"
      ],
      "defect_phrases": [
        "添加原文未表达语义",
        "添加医学结论"
      ]
    },
    {
      "target": "s0",
      "target_kind": "missing_scope",
      "defect_codes": [
        "missing_explicit_context_proposition"
      ],
      "defect_phrases": [
        "显式语境命题缺失"
      ]
    }
  ]
}
```

规则：

```text
一个 target proposition = 一个 defect cluster
一个 defect cluster = 一次 cluster-level repair
不按缺陷码分别修复
```

同一 proposition 同时出现多个缺陷码不视为冲突，必须交给完整缺陷簇处理。

## 16. Targeted Proposition Repair

### 16.1 修复模式

只保留两种 target 级模式：

```text
existing proposition q* → replace_target_proposition
missing scope s*        → append_missing_contextual_propositions
```

对已有 `q*`：

```text
模型输出表示该目标 proposition 的替换候选集合。
```

对 `missing_explicit_context_proposition` 的 `s*`：

```text
模型输出表示需要为该 scope 追加的 contextual propositions。
```

系统不按缺陷码映射修复动作。

### 16.2 修复输入契约

Cluster repair 输入由系统构造：

```text
<original_input>
当前原始用户输入
</original_input>

<subject_context>
当前讨论主体为狗
</subject_context>

<anchors>
a0 ...
a1 ...
</anchors>

<joint_context_scopes>
s0 = a0+a1
</joint_context_scopes>

<current_contextual_query_propositions>
q0 ... [s0]
q1 ... [s1]
</current_contextual_query_propositions>

<repair_target>
q0
</repair_target>

<semantic_defects>
添加原文未表达语义
添加医学结论
</semantic_defects>
```

对于 missing scope：

```text
<repair_target>
s0
</repair_target>

<semantic_defects>
显式语境命题缺失
</semantic_defects>
```

Repair LLM 可以看到完整缺陷簇，但不接收按缺陷码编写的动作命令。

### 16.3 修复输出契约

修复输出必须是：

```json
{
  "propositions": [
    "狗体重由历史30千克变为当前15千克"
  ]
}
```

不得输出：

```text
target
operation
reason
confidence
corrected full propositions
origin_scope_refs
current_anchor_refs
trusted_fact_refs
relation
source_ref
```

`origin_scope_refs`、`current_anchor_refs` 与 `trusted_fact_refs` 均由系统
根据 target 和 selector namespace 继承或绑定，不由模型输出。

### 16.4 Patch 语义

对已有 `q*`：

```text
[]     表示删除目标 proposition
[...]  表示用一个或多个 propositions 替换目标
```

例如：

```json
{
  "propositions": []
}
```

表示删除目标。

```json
{
  "propositions": [
    "狗体重由历史30千克变为当前15千克"
  ]
}
```

表示替换目标。

对 missing scope `s*`：

```text
输出必须非空；
输出为追加到该 scope 的 contextual propositions；
不得替换已有 propositions。
```

### 16.5 修复授权范围

为保持局部性，系统必须限制修复可用输入：

```text
existing q* repair 默认只能使用该 q* 的 origin scopes 内 a* / p*；
missing s* repair 只能使用该 s* 内 a* / p*；
不得使用其他 scope 语义单元；
不得创建新 anchor 或 trusted fact；
不得影响非 target propositions。
```

该授权由系统执行，不在 prompt 中写成缺陷码动作。

### 16.6 修复输出校验

系统必须校验：

```text
唯一字段 propositions
propositions 是字符串数组
字符串非空、单行、长度合法
输出数量不超限
不输出未知字段
不输出对象元素
不输出 ID / reason / confidence
只使用授权上下文
```

已有 target 修复输出如果与原 proposition 完全相同：

```text
→ contextual_proposition_repair_no_change
```

missing target 修复输出如果为空：

```text
→ contextual_proposition_repair_empty_for_missing
```

## 17. 所有修复基于同一原始快照

多个 cluster repair 必须基于同一份 base propositions 快照执行。

示例：

```text
base propositions:
q0 ... [s0]
q1 ... [s1]
q2 ... [s2]

repair(q0) = [...]
repair(q2) = [...]
repair(s3) = [...]
```

禁止：

```text
修复 q0 后立即重编号；
再用新编号修复 q2；
让修复任务读取其他修复输出；
让修复任务之间互相污染。
```

修复任务可以串行或并行执行，但都必须以 base snapshot 为准，最终统一 join。

## 18. Transactional Proposition Join

### 18.1 Join 规则

所有修复输出返回并通过校验后，系统统一执行：

```text
1. 保留未修复的 base propositions
2. 用已有 q* target 的修复输出替换该 target
3. 追加 missing s* target 的修复输出
4. 精确重复文本去重
5. 跨 target 重复项合并 origin_scope_refs
6. 确定性排序
7. 重新分配 q0/q1/q2...
8. 执行完整 post-review
```

Join 必须是事务式的：

```text
任一修复输出无效，则不提交 join；
不得部分应用成功结果；
不得把失败静默转为空 propositions；
不得宽松修复 JSON。
```

### 18.2 示例

Base propositions：

```text
q0 狗体重明显下降并可能患有慢性疾病 [s0]
q1 狗在换粮后开始出现软便 [s1]
```

修复结果：

```text
repair(q0) = ["狗体重由历史30千克变为当前15千克"]
```

Join 后：

```text
狗体重由历史30千克变为当前15千克 [s0]
狗在换粮后开始出现软便 [s1]
```

重编号为内部 selector：

```text
q0 狗体重由历史30千克变为当前15千克
q1 狗在换粮后开始出现软便
```

最终输出不携带长期 `q*` 身份。

### 18.3 不维护旧 ID 映射

不保存：

```text
old q0 → deleted
old q0 → new q0
old q0 → new q0 + new q1
```

原因是：

```text
q* 不是长期身份；
q* 只是当前审查快照中的数组下标；
真正有意义的是 proposition 文本与 origin_scope_refs。
```

审计层可以从：

```text
base propositions + repair outputs + join 规则
```

确定性推导最终结果。

## 19. Post Contextual Proposition Review

Join、去重和重编号后，必须使用最终 proposition 集合重新执行完整 Review：

```text
Completeness / Support Review
Form / Subject Review
State / Certainty Review
Measurement / Relation Review
Boundary Review
```

Post-review 输入使用重新编号后的 `q*`，不比较旧编号，不读取修复历史。

检查：

```text
原缺陷是否消除；
是否引入 unsupported addition；
是否添加医学结论；
是否因果过度推断；
是否混入来源治理元数据；
是否丢失时间、数量、单位、否定、不确定或关系语义；
是否仍有 scope 缺失显式语境命题；
是否出现不可接受的重复。
```

如果 post-review 仍输出任何缺陷：

```text
不再继续修复；
进入 contextual_proposition_post_review_failed。
```

初始实现建议：

```text
每个 defect cluster 最多修复 1 次；
整个 turn 的 contextual proposition repair 总预算固定。
```

## 20. TurnSemanticRetrievalQueryBundle

成功输出：

```json
{
  "status": "completed",
  "patient_fact_anchors": [
    "狗当前体重为15千克",
    "狗当前进食量低于平时基线",
    "狗近期换粮",
    "狗在换粮后开始出现软便",
    "狗当前精神状态正常"
  ],
  "trusted_pet_facts": [
    "狗历史体重为30千克"
  ],
  "joint_context_scopes": [
    "a0+p0",
    "a2+a3"
  ],
  "contextual_query_propositions": [
    {
      "proposition": "狗体重由历史30千克变为当前15千克",
      "origin_scope_refs": ["s0"],
      "current_anchor_refs": ["a0"],
      "trusted_fact_refs": ["p0"],
      "derivation_kind": "contextual_query"
    },
    {
      "proposition": "狗在换粮后开始出现软便",
      "origin_scope_refs": ["s1"],
      "current_anchor_refs": ["a2", "a3"],
      "trusted_fact_refs": [],
      "derivation_kind": "contextual_query"
    }
  ]
}
```

若无 scopes 或 Review 确认无新增可显式化语境：

```json
{
  "status": "completed_no_contextual_query_propositions",
  "patient_fact_anchors": [
    "狗今天呕吐两次"
  ],
  "trusted_pet_facts": [],
  "joint_context_scopes": [],
  "contextual_query_propositions": []
}
```

契约边界：

```text
patient_fact_anchors 是事实候选；
trusted_pet_facts 是只读可信事实投影；
joint_context_scopes 是解释语境 overlay；
contextual_query_propositions 是检索用派生视图；
来源与派生关系由系统绑定，不在 proposition 文本中表达；
四者不得混成单一权威集合。
```

## 21. RAG 消费边界

RAG 查询层可以消费：

```text
patient_fact_anchors
contextual_query_propositions
origin_scope_refs
```

RAG 实际用于 embedding / keyword query 的文本只来自：

```text
patient_fact_anchors
contextual query proposition 的 proposition 字段
```

`origin_scope_refs`、`current_anchor_refs`、`trusted_fact_refs` 与
`derivation_kind` 只用于分组、审计和解释，不进入查询文本。

推荐查询方式：

```text
1. 使用 anchors 查询基础观察语义
2. 使用 contextual propositions 查询高维组合语义
3. 按 proposition 或 scope 小范围分组检索
4. 不把所有 propositions 混成一个巨大 query
5. 命中结果进入既有 PDF scope-first RAG 流程
6. 命中 chunk 再进入知识命题治理与来源支持审查
```

例如：

```text
基础查询：
狗历史体重为30千克
狗当前体重为15千克
狗当前进食量低于平时基线
狗在换粮后开始出现软便

高维查询：
狗体重由历史30千克变为当前15千克
```

RAG 层不得：

```text
把 contextual proposition 当作 evidence；
把 contextual proposition 当作知识命题；
根据命中或未命中直接生成诊断；
根据查询改写添加医学结论；
在单次请求中静默回退为只查 anchors；
绕过知识命题来源支持审查。
```

本层也不负责：

```text
同义词扩展
中英文扩展
HyDE
关键词改写
检索式拼接
```

这些属于后续 RAG 查询工程层，且不得反向修改本文输出契约。

## 22. 与 Coverage 和量化派生的关系

默认情况下：

```text
contextual_query_propositions 不进入 Coverage
```

Coverage 输入仍是：

```text
患者事实
可信宠物事实
知识命题
覆盖关系
```

如果未来某条 contextual proposition 需要参与 Coverage，例如：

```text
狗体重由历史30千克变为当前15千克
```

必须显式扩展 Coverage 契约，并携带：

```text
derived proposition
origin anchors
origin scope
derivation kind
```

不得与用户陈述事实混用。

本层默认不生成：

```text
绝对变化量
百分比变化
医学阈值判断
明显下降
严重程度
```

这些属于后续通用、有界、可审计的量化比较派生层。

## 23. 失败状态

至少定义：

```text
contextual_proposition_input_invalid
contextual_proposition_scope_count_exceeded
contextual_proposition_generation_failed
contextual_proposition_schema_invalid
contextual_proposition_count_exceeded
contextual_proposition_review_schema_invalid
contextual_proposition_review_group_schema_invalid
contextual_proposition_review_failed
contextual_proposition_repair_schema_invalid
contextual_proposition_repair_no_change
contextual_proposition_repair_empty_for_missing
contextual_proposition_repair_unauthorized
contextual_proposition_repair_failed
contextual_proposition_repair_budget_exceeded
contextual_proposition_join_failed
contextual_proposition_post_review_failed
```

失败治理原则：

```text
失败不得伪装为空 propositions；
失败不得静默跳过 Review；
失败不得在单次请求内回退旧链路；
失败不得触发默认全局重写；
失败必须显式进入 envelope 状态。
```

若系统采用 explicit partial 策略，也必须显式标记：

```text
partial
失败 scope
失败 proposition
可用 anchors
可用 trusted facts
```

不得把 partial 伪装为 completed。

## 24. 执行审计

审计层保存：

```text
输入 anchor envelope
输入 trusted fact projection
输入 scope envelope
每个 scope 的 Generator 原始输出
Generator schema 校验结果
base propositions
origin_scope_refs 绑定
current_anchor_refs / trusted_fact_refs 系统推导
Completeness / Support Review delta
Form / Subject / Source Metadata Review delta
State / Certainty Review delta
Measurement / Relation Review delta
Boundary Review delta
defect clusters
每个 target 的 Repair 输入
每个 target 的 Repair 输出
join 前中间集合
最终 propositions
post-review delta
最终状态
prompt / model / schema 版本
并发与预算执行信息
```

不需要保存：

```text
old q* → new q* 映射
```

核心原则：

```text
核心检索契约薄；
执行审计厚。
```

## 25. LangGraph 使用边界

LangGraph 可以作为执行编排层，但必须是固定拓扑状态图。

推荐节点：

```text
anchor_trusted_scope_envelope
contextual_query_scheduler
contextual_proposition_generator
generator_output_validation
proposition_concatenation
exact_duplicate_cleanup
q_selector_assignment
parallel_contextual_review
contextual_review_delta_merge
defect_cluster_aggregator
contextual_repair_scheduler
targeted_proposition_repair
missing_context_proposition_repair
repair_output_validation
transactional_proposition_join
canonicalize_and_dedupe
deterministic_renumber
post_contextual_review
retrieval_query_commit_or_fail
```

允许动态变化的只有：

```text
scope 数量
repair cluster 数量
并行模型调用数量
```

禁止：

```text
模型创建节点
模型创建任务类型
模型自由递归修复
模型直接修改状态
模型决定最终接受
模型执行 RAG 检索
模型执行临床动作
```

LangGraph 是可选实现形态。本文契约不得绑定特定图执行框架。

## 26. 实施注意项

### 26.1 执行顺序

本层只能在以下条件满足后启动：

```text
anchor envelope 成功；
scope envelope 成功；
scope 数量在限制内；
当前原始输入可提供给 Generator 与 Review；
模型、prompt、schema 与预算配置就绪。
```

### 26.2 严格 schema

所有模型输出必须 strict schema validation：

```text
Contextual Proposition Generator
Completeness / Support Review
Form / Subject Review
State / Certainty Review
Measurement / Relation Review
Boundary Review
Targeted Proposition Repair
Missing Context Proposition Repair
```

不得宽松修复 JSON。结构失败进入显式状态或有界重试。

### 26.3 上限配置

生产配置应显式定义：

```text
最大 scope 数
单个 scope 最大 proposition 数
整体最大 proposition 数
单条 proposition 最大长度
最大修复 cluster 数
每次修复最大输出 proposition 数
修复总预算
最大并发 Generator / Repair 调用数
```

### 26.4 空输出必须审查

某个 Generator 输出：

```json
{
  "propositions": []
}
```

可能是合法状态：

```text
scope 的语境已由某个 anchor 完整表达；
没有新增可显式化检索语义。
```

也可能是遗漏。必须由 Review 判断是否：

```text
missing_explicit_context_proposition
```

不得直接把空输出视为成功或失败。

### 26.5 不做全局 query rewrite

本层不得输出：

```text
同义词列表
关键词列表
多语言翻译
HyDE 假设文档
检索式组合
全局扩展查询
```

这些属于 RAG 查询工程层。

### 26.6 局部性

默认只修复 defect cluster target：

```text
不得顺手修改其他 propositions；
不得默认全局重写；
未修复 propositions 在 join 时原样保留。
```

全局重生成只能作为未来显式配置策略，不得成为单次请求内静默 fallback。

### 26.7 Prompt 防漂移

Prompt 必须明确：

```text
只显式化 scope 中明确存在的组合语义；
不得添加医学推断；
不得推断因果；
不得判断严重程度或急症；
不得生成治疗建议；
不得输出 relation / reason / confidence / source；
不得创建新 anchor。
```

Prompt 示例只能说明边界，不得演化为医学事实类型枚举。

## 27. 验收边界

最小验收用例应覆盖：

```text
历史体重 + 当前体重 → 生成体重变化语境查询
历史体重 + 当前体重 → 不得生成消瘦或慢性疾病结论
换粮事件 + 换粮后软便 → 保留事件顺序
换粮事件 + 换粮后软便 → 不得生成因果结论
平时进食基线 + 当前进食量 → 生成低于基线查询
平时进食基线 + 当前进食量 → 不得生成厌食症或严重食欲下降
同一呕吐事件 → 可合并事件语境
同一呕吐事件 → 不得推断消化道出血或危重
医生明确归因 → 保留医生认为
医生明确归因 → 不得升格为系统因果
scope 语境已由 anchor 表达 → 空 propositions 可通过 Review
无 scopes → 合法输出空 contextual propositions
proposition 未自包含 → Review 标记 not_self_contained
Completeness Review 输出 Boundary 缺陷码 → review_group_schema_invalid
Form / Subject Review 输出 State / Certainty 缺陷码 → review_group_schema_invalid
多个 Group 对同一 proposition 输出缺陷 → 聚合为一个 repair cluster
proposition 主体与上游 anchor 不一致 → Review 标记 subject_error
本层输入仍含未消解“它” → 上游未完成，本层不得启动
同一 proposition 多缺陷码 → 聚合为一个 repair cluster
已有 q* 修复输出为空 → 表示删除
missing s* 修复输出为空 → fail
多个修复输出 → 统一 join 后重编号
post review 仍失败 → fail
```

示例输入：

```text
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行。
```

期望：

```text
s0 = 历史体重 + 当前体重
→ 可生成“狗体重由历史30千克变为当前15千克”
→ 不得生成“狗明显消瘦”或“狗可能患有慢性疾病”

s1 = 换粮事件 + 换粮后软便
→ 可生成“狗在换粮后开始出现软便”
→ 不得生成“换粮导致狗软便”
```

## 28. 防漂移清单

后续设计、实现或测试不得引入：

```text
把 contextual proposition 当作权威患者事实
把 contextual proposition 当作知识命题
把 contextual proposition 当作 evidence
把 contextual proposition 直接写入 Coverage
把 contextual proposition 写入宠物档案或长期记忆
让本层重新抽取原始输入事实
让本层拆分 anchor
让本层生成新的 anchor
让本层修改 anchors 或 scopes
让本层推断因果
让本层推断诊断或疾病机制
让本层判断严重程度或急症
让本层生成治疗建议
让本层做全局同义词或 HyDE 扩展
让模型输出 q* / origin_scope_refs / relation / reason / confidence
让 Review 输出 corrected proposition
让 Review Group 输出其他 Group 缺陷码
按缺陷码分别修复同一 target
在 prompt 中把缺陷码映射成修复动作
修复过程中维护稳定 q* 身份
在本层重新消解主体
部分修复失败后静默提交
失败转空 propositions
单次请求自动回退旧链路
```

## 29. 文档层级与变更原则

本文位于：

```text
veterinary-semantic-decomposition-charter.md
→ veterinary-semantic-preprocessing-control-conventions.md
→ veterinary-semantic-anchor-pipeline.md
→ veterinary-semantic-scope-architecture.md
→ veterinary-contextual-query-proposition-architecture.md
→ 后续 RAG 查询工程 / PDF scope-first RAG / 知识命题治理
→ 后续 Coverage / 动态证据架构
```

本文只应因以下原因修订：

```text
contextual proposition 权威地位调整
输入或输出契约调整
Generator / Review / Repair Delta 调整
缺陷码或缺陷簇策略调整
join / dedupe / renumber 策略调整
RAG 消费边界调整
Coverage 或量化派生边界调整
失败状态或 Fail Fast 底线调整
LangGraph 或执行边界调整
```

不得把具体 prompt 全文、临时模型表现、局部工程问题或单个实现细节直接加入
本文。
