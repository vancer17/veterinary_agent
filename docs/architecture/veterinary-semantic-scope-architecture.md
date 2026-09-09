<!--
=============================================================================
文件: docs/architecture/veterinary-semantic-scope-architecture.md
作用: 定义兽医问诊语义预处理中 Scope 层的数据管道、稀疏 Delta 契约、
      缺陷簇聚合、局部修复、事务式 join、重编号与失败治理。
范围: 覆盖 Joint Context Scope Planner、Scope Schema Validation、并行
      Scope Review、固定缺陷维度、target 级缺陷簇、cluster-level repair、
      transactional scope join、canonicalize / dedupe / renumber、
      post-review、TurnSemanticScopeEnvelope、审计与 LangGraph 边界。
说明: 本文消费已受治理的自包含自然语言 semantic anchors 与只读可信宠物
      事实投影，不重新拆分 anchor，不生成 proposition，不做 Coverage、
      诊断或临床安全裁决。
维护: 当 scope 语义边界、planner/review/repair 契约、缺陷码、缺陷簇、
      join 与重编号策略或 Fail Fast 底线调整时，必须先修订本文。
=============================================================================
-->

# 兽医语义 Scope 层架构设计草案

> **文档状态**：架构草案基线
>
> **文档定位**：本文细化
> [veterinary-semantic-decomposition-charter.md](./veterinary-semantic-decomposition-charter.md)
> 下的语义预处理 Scope 层。它位于
> [veterinary-semantic-anchor-pipeline.md](./veterinary-semantic-anchor-pipeline.md)
> 之后，消费该层输出的自包含 semantic anchors，并消费
> TrustedPetFactReader 投影出的只读可信宠物事实。
> 它同时遵守
> [veterinary-semantic-preprocessing-control-conventions.md](./veterinary-semantic-preprocessing-control-conventions.md)
> 中的跨层控制约定。
>
> **权威关系**：本文服从语义分解 charter。若本文、anchor pipeline、charter
> 或实现发生冲突，必须先显式修订其中一方，不得在代码或 prompt 中形成第三种
> 解释。

## 1. 核心结论

Scope 层采用：

```text
自包含 semantic anchors
+ 只读可信宠物事实投影
+ 显式共同解释语境识别
+ 可重叠 joint context scopes
+ 分组固定缺陷维度 Review
+ target 级缺陷簇聚合
+ cluster-level 局部修复
+ 所有修复结果事务式 join 回原始快照
+ canonicalize / dedupe / renumber
+ 修复后完整重审
+ Fail Fast
```

核心边界：

```text
当前回合 anchor 是自包含原子事实候选；
可信宠物事实是独立来源的只读事实投影；
scope 是 a* / p* 语义单元之间的显式共同解释语境 overlay；
scope 不拆分 anchor；
scope 不生成 proposition；
scope 不输出因果关系；
scope 不做医学综合、诊断、严重程度或急症判断。
```

最简洁的判断标准是：

```text
Scope 是解释语境，不是临床判断单元。
```

## 2. 架构范围

### 2.1 In Scope

本文覆盖：

```text
Scope 层输入边界
TrustedPetFactProjection p*
Joint Context Scope Planner
Scope Planner 输出契约
确定性 schema 校验与 scope 物化
Context Support Review
Scope Boundary Review
分组固定缺陷 Delta Schema
Review Delta Merge
缺陷码到固定中文短语的映射
target 级 defect cluster 聚合
cluster-level Targeted Scope Repair
repair output validation
transactional scope join
canonicalize / deduplicate / deterministic renumber
post-repair Scope Review
TurnSemanticScopeEnvelope
执行审计边界
LangGraph 使用边界
失败状态与防漂移原则
```

### 2.2 Out of Scope

本文不覆盖：

```text
semantic anchor 生成
anchor 忠实性审查与修复
患者事实最终权威提交
领域投影
Coverage
知识命题治理
RAG
量化比较派生
临床安全裁决
急诊或 urgent signal 判断
诊断结论
治疗建议
长期记忆写入
宠物档案更新
旧槽位迁移主路径
```

特别是：

```text
joint context scope 不是权威事实；
scope 不改变 anchor 的真实性；
scope 不生成新事实。
```

下游可以在有限通用治理算子中消费 scope，但不得把 scope 解释为诊断边、
因果边或临床综合征分组。

## 3. 背景问题

### 3.1 anchor 层已经完成自包含事实表达

Anchor pipeline 的输出是自包含自然语言事实短句，例如：

```text
狗历史体重为30千克
狗当前体重为15千克
狗当前进食量低于平时基线
狗近期换粮
狗在换粮后开始出现软便
狗当前精神状态正常
```

这些 anchors 已保留：

```text
主体
时间范围
否定
未知 / 未观察
数量与单位
比较基线
事件顺序
观察者归因
```

因此 Scope 层不得再把 anchor 拆成：

```text
平时
少吃
去年
30千克
换粮后
软便
```

如果允许这种二次拆分，会重新引入语义分解粒度定义问题，并让 Scope 层承担
Anchor 层职责。

### 3.2 部分 anchors 存在显式共同解释语境

某些 anchors 虽然各自自包含，但分开处理会丢失它们之间的明确语境。

例如：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
```

存在历史 / 当前体重测量语境。

再如：

```text
a3 狗近期换粮
a4 狗在换粮后开始出现软便
```

存在明确事件顺序语境。

Scope 层的目标是标记这些需要共同处理的显式语境。

### 3.3 仅看当前回合会丢失跨时间高维语义

当前回合 anchor 可能只表达：

```text
a1 狗当前体重为15千克
```

而已确认可信事实中可能存在：

```text
p0 狗历史体重为30千克
```

如果 Scope Planner 只读取当前回合 anchors，就无法识别：

```text
历史体重 + 当前体重
```

这一跨时间测量语境，后续 RAG 也难以命中体重变化、体重下降或消瘦相关
知识 scope。

因此 Scope 层输入必须包含：

```text
当前回合 anchors a*
只读可信宠物事实投影 p*
```

但当前事实与可信事实仍必须分源治理，不得把可信历史事实改写成本轮用户
陈述，也不得用当前表述静默覆盖历史事实。

### 3.4 Scope 容易退化成医学主题聚类

如果只因为多个 anchors 都属于当前状态、饮食、消化或潜在疾病线索，就把它们
放在同一 scope，系统会逐渐形成：

```text
整体状态 scope
消化系统 scope
急症风险 scope
某疾病相关表现 scope
```

这会将语义预处理层推向诊断推理或临床综合判断。

例如不应因以下 anchors 都是当前状态而形成 scope：

```text
狗当前体重为15千克
狗当前进食量低于平时基线
狗当前精神状态正常
```

也不应因以下 anchors 都与饮食或消化相关而形成 scope：

```text
狗当前进食量低于平时基线
狗在换粮后开始出现软便
```

除非原文明确表达：

```text
换粮后进食减少
```

或存在其他显式共同语境。

### 3.5 复杂 scope JSON 的失败

多轮实验表明，长 JSON、深嵌套结构和逐字回写会显著增加模型幻觉风险。

因此本文采用薄 selector 表达：

```json
{
  "joint_context_scopes": [
    "a0+a1",
    "a3+a4"
  ]
}
```

而不是让模型输出：

```text
scope_id
anchor 文本
relation type
reason
confidence
corrected scopes
嵌套 scope 对象
```

### 3.6 稳定 scope ID 的失败

`s*` 只是当前调用内的本地 selector，不是长期身份。若维护修复前后的稳定 ID，
会引入不必要的状态：

```text
old s2 → deleted
old s2 → new s2
old s2 → new s2 + new s3
```

这会让下游误以为 scope 是权威对象。

因此所有局部修复完成后，系统统一 join 回原始快照，再对最终 scope 集合
重新编号。

## 4. 核心概念

### 4.1 当前回合 anchor / 可信事实 p*

在本文中，当前回合 anchor 指：

```text
自包含自然语言原子事实候选 / proposition candidate
```

Scope 层假设 anchors 已经完成：

```text
生成
忠实性审查
局部修复
修复后重审
本地 a* selector 分配
```

TrustedPetFactReader 独立输出：

```text
只读可信宠物事实投影
本地 p* selector
```

示例：

```text
p0 狗历史体重为30千克
p1 狗平时每日进食量约为200克
p2 狗近期开始使用某药物
```

`p*` 不是长期身份，也不是 source_ref。它只是当前调用内定位可信事实投影
数组位置的 selector。来源、快照版本和授权范围由系统执行信封保存。

### 4.2 joint context scope

joint context scope 指：

```text
多个 a* / p* 语义单元之间显式共同解释语境的 overlay
```

scope 表示：

```text
这些语义单元需要共同解释或共同处理
```

scope 不表示：

```text
这些 anchors 属于同一疾病
这些 anchors 组成综合征
这些 anchors 有因果关系
这些 anchors 表示整体病情
```

### 4.3 合法语境

Scope 只能覆盖以下直观、显式的语境：

```text
明确时间基准
明确事件顺序
明确比较基线
同一事件或同一观察片段
用户或医生明确转述的归因
```

示例：

```text
历史体重 + 当前体重
当前体重 + 可信历史体重
换粮事件 + 换粮后软便
平时进食量 + 当前进食量
当前进食量 + 可信平时进食基线
同一次呕吐事件 + 呕吐物性状
医生认为换粮导致软便 + 相应事件事实
```

这些语境不需要医学推理即可从原文看出。

### 4.4 非法语境

以下不得作为 scope 成立理由：

```text
仅共享当前状态
仅共享医学主题
可能相关
潜在疾病关联
临床表现组合
整体状态判断
严重程度判断
急症判断
系统推断因果
诊断推理
治疗建议
```

判断标准：

> 如果一个非医学读者可以直接从原始输入中看出这些 anchors 需要共同解释，
> scope 才可能成立。
> 如果需要兽医知识、疾病联想、严重程度判断或因果推断才能说明为什么
> grouped，scope 不成立。

### 4.5 因果边界

原文：

```text
换粮后开始软便
```

只支持：

```text
换粮事件与软便出现顺序存在显式语境
```

不支持：

```text
换粮导致软便
狗对粮食不适应
狗患肠胃疾病
```

如果用户明确转述：

```text
医生说软便是换粮导致的
```

anchor 层可以保留：

```text
医生认为换粮导致狗软便
```

这属于明确转述归因。Scope 层仍不得将其升格为系统因果结论。

## 5. 输入契约

Scope Planner 的输入由系统构造：

```text
t0 当前原始用户输入
a0/a1/a2... 系统 selector 化后的 anchors
p0/p1/p2... TrustedPetFactReader 投影出的可信事实
```

示例：

```text
<t0>
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行。
</t0>

<anchors>
a0 狗历史体重为30千克
a1 狗当前体重为15千克
a2 狗当前进食量低于平时基线
a3 狗近期换粮
a4 狗在换粮后开始出现软便
a5 狗当前精神状态正常
</anchors>

<trusted_pet_facts>
p0 狗历史体重为30千克
p1 狗平时每日进食量约为200克
p2 狗近期开始使用某药物
</trusted_pet_facts>
```

输入要求：

```text
anchors 必须来自已完成的 TurnSemanticAnchorEnvelope；
anchors 必须是自包含自然语言事实短句；
a* 由系统按数组顺序分配；
trusted facts 必须来自只读 TrustedPetFactReader；
trusted facts 必须是已确认自包含自然语言短句；
p* 由系统按投影顺序分配；
Scope 层不重新消解主体；
Scope 层不得创建、删除或改写 anchors；
Scope 层不得创建、删除或改写 trusted facts；
Scope 层不得读取原始宠物档案或未治理记忆。
```

若 anchor envelope 未完成，或 anchors 为空但状态不是成功无事实，Scope 层
不得启动。

若配置要求可信事实参与 Scope 层，但 TrustedPetFactReader 投影失败，不得
静默视为没有历史事实，应进入显式 partial / failed 状态。

## 6. 顶层管道

```text
TurnSemanticAnchorEnvelope
→ 当前回合 anchors a*

TrustedPetFactReader
→ TrustedPetFactProjection p*

a* + p*
→ Scope Planner
→ Scope Schema Validation
→ Scope Materialization
→ Parallel Scope Review
   ├─ Context Support Review
   └─ Scope Boundary Review
→ Scope Review Delta Merge
→ Defect Cluster Aggregation
→ Scope Repair Scheduler

   ├─ 无缺陷
   │   → Transactional Scope Join
   │
   ├─ existing scope defect cluster
   │   → Cluster-level Targeted Scope Repair
   │
   └─ missing scope defect cluster
       → Missing Scope Repair

→ Collect All Repair Outputs
→ Repair Output Validation
→ Transactional Scope Join
→ Canonicalize / Deduplicate / Sort
→ Deterministic Renumber
→ Post Scope Review
→ Commit / Explicit Failure

→ TurnSemanticScopeEnvelope
```

示意图：

```text
              ┌────────────────────┐
              │ Anchor Envelope    │
              └─────────┬──────────┘
                        │
              ┌─────────▼────────────┐
              │ Trusted Facts p*     │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Scope Planner        │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Schema Validation    │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Scope Materializer   │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Parallel Review      │
              ├─ Context Support     │
              └─ Boundary Review     │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Delta Merge          │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Defect Clusters      │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Cluster Repairs      │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Transactional Join   │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Renumber + Re-review │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Scope Envelope       │
              └──────────────────────┘
```

## 7. Joint Context Scope Planner

### 7.1 职责

Scope Planner 只判断：

```text
当前回合 anchors 与可信事实投影中哪些成员组合需要共同解释或共同处理。
```

它不负责：

```text
生成 anchors
拆分 anchors
改写 anchors
生成 propositions
输出关系类型
判断因果关系
判断疾病关联
判断整体状态
判断严重程度
执行临床安全判断
```

### 7.2 输出契约

Scope Planner 输出必须是：

```json
{
  "joint_context_scopes": [
    "a0+a1",
    "a1+p0",
    "a3+a4"
  ]
}
```

约束：

```text
顶层唯一字段是 joint_context_scopes
数组元素是受限格式字符串
格式为 a* / p* 用 + 连接
每个 scope 至少两个 selector
每个 scope 至少一个 a*
不得输出 pure p* scope
selector 必须存在
selector 不得重复
a* 必须按数字顺序递增
p* 必须按数字顺序递增
a* 必须排在 p* 前
不同 scopes 允许重叠
不得输出单个 selector
不得输出语义单元文本
不得输出关系名
不得输出来源分区
不得输出 scope_id
```

合法输出：

```text
a0+a1
a1+a2+a5
a1+p0
a3+a4
```

非法输出：

```text
a1
a1+a0
p1
p0+p1
a1+a1
a3+a2
p1+a1
a0+体重事实
cause:a3+a4
```

空数组是合法状态，表示当前回合 anchors 与可信事实之间没有需要共同处理的
显式语境：

```json
{
  "joint_context_scopes": []
}
```

### 7.3 Planner 示例

输入：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
a2 狗当前进食量低于平时基线
a3 狗近期换粮
a4 狗在换粮后开始出现软便
a5 狗当前精神状态正常

p0 狗历史体重为30千克
p1 狗平时每日进食量约为200克
```

合理输出：

```json
{
  "joint_context_scopes": [
    "a0+a1",
    "a2+p1",
    "a3+a4"
  ]
}
```

不合理输出：

```json
{
  "joint_context_scopes": [
    "a0+a1",
    "a1+a2+a5",
    "a3+a4"
  ]
}
```

原因是：

```text
a1+a2+a5
```

只是当前状态聚类，容易演变成整体病情判断。

### 7.4 关系类型禁止

Scope Planner 不输出：

```json
{
  "relation": "temporal_change"
}
```

也不得使用：

```text
cause:a3+a4
compare:a0+a1
diagnosis:a1+a2+a4
```

scope 只表达成员组合，不表达关系类型。具体治理含义由下游有限通用算子按需
解释。

## 8. 系统校验与 scope 物化

### 8.1 Planner 输出校验

系统必须校验：

```text
唯一字段 joint_context_scopes
数组元素满足 a*/p* selector 组合格式
selector 存在
selector 不重复
每个 scope 至少一个 a*
不得出现 pure p* scope
a* 与 p* 分别按数字顺序递增
a* 排在 p* 前
每个 scope 至少两个成员
单个 scope 成员数不超限
scope 总数不超限
单个语义单元参与的 scope 数不超限
不得输出未知字段
不得输出空数组以外的空值
```

如违反，进入：

```text
scope_plan_schema_invalid
```

不得宽松修复 JSON。

### 8.2 Scope 物化

模型输出通过校验后，系统构造内部对象：

```json
{
  "joint_context_scopes": [
    {
      "scope_id": "s0",
      "refs": ["a0", "a1"]
    },
    {
      "scope_id": "s1",
      "refs": ["a1", "p0"]
    }
  ]
}
```

系统可从 selector namespace 确定性推导：

```text
current_anchor_refs = [a1]
trusted_fact_refs = [p0]
```

这些来源分区是系统内部执行状态和审计信息，不是模型输出契约。

规则：

```text
s* 由系统分配；
模型不生成 s*；
s* 是当前调用内本地 selector；
s* 不是长期身份；
s* 不写入档案或长期记忆；
s* 不作为权威事实 ID。
```

## 9. Scope Review

Scope Review 拆成三个并行 LLM 调用，避免单一模型同时判断支持性、时间语境
和架构边界。每个调用遵守跨层 Grouped Review 约定：只输出自身 Group
allowlist 内的缺陷码，系统再确定性合并 Delta。

Review 输入由系统构造：

```text
<t0>当前原始用户输入</t0>

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

<joint_context_scopes>
s0 = a0+a1
s1 = a1+p0
</joint_context_scopes>
```

### 9.1 Context Support Review

#### 职责

Context Support Review 判断：

```text
当前输入与可信事实中是否存在明确共同语境但 scope 未表达；
scope 是否被当前输入、anchors 与 trusted facts 支持；
scope 是否包含不必要成员；
scope 是否缺少必要成员。
```

#### Delta Schema

```json
{
  "missing_joint_context_scope": ["t0"],
  "unsupported_joint_context_scope": ["s2"],
  "over_broad_joint_context_scope": ["s3"],
  "incomplete_joint_context_scope": ["s4"]
}
```

实际输出省略空数组。

字段定义：

| 字段 | 含义 | 允许 target |
|---|---|---|
| `missing_joint_context_scope` | 输入与可信事实中存在明确共同语境，但当前 scope 集合未表达 | `t0` |
| `unsupported_joint_context_scope` | scope 组合不被输入、anchors 或 trusted facts 支持 | `s*` |
| `over_broad_joint_context_scope` | scope 有合理核心，但包含不必要语义单元 | `s*` |
| `incomplete_joint_context_scope` | scope 缺少必要成员 | `s*` |

### 9.2 Scope Boundary Review

#### 职责

Boundary Review 判断 scope 是否越过语义预处理边界。

#### Delta Schema

```json
{
  "topic_only_scope": ["s2"],
  "medical_synthesis_scope": ["s3"],
  "causal_overreach_scope": ["s4"]
}
```

实际输出省略空数组。

字段定义：

| 字段 | 含义 | 允许 target |
|---|---|---|
| `topic_only_scope` | 语义单元只是主题相似，不构成显式共同语境 | `s*` |
| `medical_synthesis_scope` | scope 用于整体状态、病情、诊断或严重程度综合 | `s*` |
| `causal_overreach_scope` | scope 需要系统推断因果关系才能成立 | `s*` |

### 9.3 Temporal / Redundancy Review

#### 职责

Temporal / Redundancy Review 判断：

```text
历史、当前、长期、基线等时间语境是否错配；
当前语义是否被误写成历史；
历史语义是否被误写成当前；
a* 与 p* 是否表达同一语义并形成冗余 scope。
```

#### Delta Schema

```json
{
  "temporal_scope_mismatch": ["s0"],
  "redundant_joint_context_scope": ["s1"]
}
```

实际输出省略空数组。

字段定义：

| 字段 | 含义 | 允许 target |
|---|---|---|
| `temporal_scope_mismatch` | scope 中历史 / 当前 / 基线等时间语境错配 | `s*` |
| `redundant_joint_context_scope` | 当前回合 a* 已表达同一语义时，又引入等价 p* 形成重复 scope | `s*` |

冗余优先级：

```text
同一语义已由当前回合 a* 表达时，优先使用 a*；
只有当前回合缺失该语义时，才引入 p*。
```

例如当前已有：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
p0 狗历史体重为30千克
```

应形成：

```text
a0+a1
```

不应同时形成：

```text
a1+p0
```

### 9.4 Review 输出校验

每个 Group 必须独立校验：

```text
字段名属于该 Group allowlist
数组非空
数组元素唯一
target selector 存在
missing_joint_context_scope 只允许 t0
其他字段只允许 s*
Context Support Review 不得输出其他 Group 缺陷码
Boundary Review 不得输出其他 Group 缺陷码
Temporal / Redundancy Review 不得输出其他 Group 缺陷码
不输出未知字段
不输出 reason
不输出 confidence
不输出 corrected scopes
不输出 supported / rejected
```

系统合并时：

```text
同 code 同 target 去重；
不同 code 同 target 聚合为同一缺陷簇；
不同 target 分别聚合；
t0 缺陷单独作为 turn-level defect。
```

Review 不直接修改 scope，也不输出 corrected value。

## 10. 缺陷码映射与 prompt 边界

### 10.1 固定中文短语

系统只做稳定缺陷码到固定中文短语的映射：

```text
missing_joint_context_scope        → 显式共同语境缺失
unsupported_joint_context_scope    → 共同语境不受支持
over_broad_joint_context_scope     → 共同语境范围过宽
incomplete_joint_context_scope     → 共同语境成员缺失
topic_only_scope                   → 仅主题相关
medical_synthesis_scope            → 医学综合越界
causal_overreach_scope             → 因果推断越界
temporal_scope_mismatch            → 时间语境错配
redundant_joint_context_scope      → 共同语境冗余
```

这些是有限治理缺陷维度，不是医学事实维度。禁止加入：

```text
weight_scope_error
digestive_scope_error
emergency_scope_error
diagnosis_scope_error
```

### 10.2 缺陷码解释只描述现象

Repair prompt 中的缺陷码解释必须只描述现象，不得断言修复动作。

正确示例：

```text
共同语境不受支持：
  当前 scope 的语义单元组合没有被当前输入或可信事实中的明确共同语境
  直接支持。

共同语境范围过宽：
  当前 scope 中存在与该共同语境无关的成员。

共同语境成员缺失：
  当前 scope 不足以表达原始输入中的明确共同语境。

仅主题相关：
  当前语义单元只是共享主题或医学领域，不构成明确时间、事件、基线或
  归因语境。

时间语境错配：
  当前 scope 中历史、当前、长期或基线等时间语义互相冲突。

共同语境冗余：
  当前回合 a* 已表达同一语义时，scope 又引入等价 p* 形成重复语境。

医学综合越界：
  当前 scope 的组合意图接近整体状态、病情严重程度、诊断推理或临床表现
  综合，而不是直接共同解释语境。

因果推断越界：
  当前 scope 需要依赖因果关系才能成立，但原始输入没有明确表达因果、
  事件顺序或转述归因。
```

禁止写：

```text
共同语境不受支持：删除对应 scope
共同语境范围过宽：移除多余成员
共同语境成员缺失：加入缺失成员
```

原因是：

```text
同一个 scope 可能命中多个缺陷码；
缺陷码是观察结果，不是修复命令；
修复动作不能由单个缺陷码推导；
必须针对完整缺陷簇处理。
```

## 11. Defect Cluster 聚合

系统必须将 Review delta 按 target 聚合，而不是按缺陷码分别修复。

示例 Review delta：

```json
{
  "unsupported_joint_context_scope": ["s2"],
  "over_broad_joint_context_scope": ["s2"],
  "incomplete_joint_context_scope": ["s2"],
  "medical_synthesis_scope": ["s2"],
  "missing_joint_context_scope": ["t0"]
}
```

系统聚合为：

```json
{
  "repair_clusters": [
    {
      "target": "s2",
      "target_kind": "existing_scope",
      "defect_codes": [
        "unsupported_joint_context_scope",
        "over_broad_joint_context_scope",
        "incomplete_joint_context_scope",
        "medical_synthesis_scope"
      ],
      "defect_phrases": [
        "共同语境不受支持",
        "共同语境范围过宽",
        "共同语境成员缺失",
        "医学综合越界"
      ]
    },
    {
      "target": "t0",
      "target_kind": "missing_scope",
      "defect_codes": [
        "missing_joint_context_scope"
      ],
      "defect_phrases": [
        "显式共同语境缺失"
      ]
    }
  ]
}
```

规则：

```text
一个 target scope = 一个 defect cluster
一个 defect cluster = 一次 cluster-level repair
不按缺陷码分别修复
```

同一 scope 同时出现：

```text
共同语境范围过宽
共同语境成员缺失
```

不自动视为冲突。它们可以同时成立：

```text
scope 中有不必要成员；
同时也缺少必要成员。
```

正确处理方式是针对整个缺陷簇生成候选替换集合。

## 12. Targeted Scope Repair

### 12.1 修复模式

只保留两种 target 级模式：

```text
existing_scope → replace_target_scope
missing_scope  → append_missing_scope
```

对于已有 `s*`：

```text
模型输出表示该目标 scope 的替换候选集合。
```

对于 `t0` 缺失 scope：

```text
模型输出表示需要追加的新 scopes。
```

系统不按缺陷码映射修复动作。

### 12.2 输入契约

Cluster repair 输入由系统构造：

```text
<original_input>
当前原始用户输入
</original_input>

<anchors>
a0 ...
a1 ...
</anchors>

<current_joint_context_scopes>
s0 = a0+a1
s1 = a3+a4
s2 = a1+a2+a5
</current_joint_context_scopes>

<repair_target>
s2 = a1+a2+a5
</repair_target>

<semantic_defects>
共同语境不受支持
共同语境范围过宽
共同语境成员缺失
医学综合越界
</semantic_defects>
```

对于 `t0`：

```text
<repair_target>
t0
</repair_target>
```

Repair LLM 可以看到完整缺陷簇，但不接收按缺陷码编写的动作命令。

### 12.3 输出契约

修复输出必须是：

```json
{
  "joint_context_scopes": [
    "a0+a1"
  ]
}
```

不得输出：

```text
target
operation
reason
confidence
corrected full scopes
关系类型
语义单元文本
来源分区
```

### 12.4 Patch 语义

对已有 `s*`：

```text
[] 表示删除目标 scope
一个 scope 表示替换目标 scope
多个 scopes 表示将目标 scope 替换为多个 scopes
```

例如：

```json
{
  "joint_context_scopes": []
}
```

表示删除目标。

```json
{
  "joint_context_scopes": [
    "a0+a1"
  ]
}
```

表示用 `a0+a1` 替换目标。

```json
{
  "joint_context_scopes": [
    "a1+a4",
    "a2+a5"
  ]
}
```

表示将目标拆分为两个 scopes。

对 `t0` missing scope：

```text
输出必须非空；
输出为追加的新 scopes；
不得替换已有 scopes。
```

### 12.5 修复授权范围

为保持局部性，系统可以按 defect cluster 授权可用语义单元：

```text
默认情况下，已有 target 的替换候选只能使用该 target 内已有 selectors；
若缺陷簇包含 incomplete_joint_context_scope，可授权使用其他既有 selectors；
missing scope repair 可使用全部既有 a* 与 p*；
所有输出仍必须满足“至少一个 a*”规则。
```

该授权由系统执行，不在 prompt 中写成缺陷码动作。

### 12.6 修复输出校验

系统必须校验：

```text
唯一字段 joint_context_scopes
scope 字符串格式合法
selector 存在
selector 不重复
a* 与 p* 分别按数字顺序递增
a* 排在 p* 前
每个 scope 至少两个 selector
每个 scope 至少一个 a*
不得输出 pure p* scope
scope 数量和大小不超限
只使用授权 selectors
不得影响非 target scopes
```

已有 target 修复输出如果与原 target 完全相同：

```text
→ scope_repair_no_change
```

missing target 修复输出如果为空：

```text
→ scope_repair_empty_for_missing
```

## 13. 所有修复基于同一原始快照

多个 cluster repair 必须基于同一份 base scopes 快照执行。

示例：

```text
base scopes:
s0 = a0+a1
s1 = a1+p0
s2 = a1+a2+a5
s3 = a2+a4

repair(s2) = [...]
repair(s3) = [...]
repair(t0) = [...]
```

禁止：

```text
修复 s2 后立即重编号；
再用新编号修复 s3；
让修复任务之间互相污染。
```

这样修复任务可以串行或并行执行，最终统一 join 回 base scopes。

## 14. Transactional Scope Join

### 14.1 Join 规则

所有修复输出返回并通过校验后，系统统一执行：

```text
1. 保留未修复的 base scopes
2. 用已有 target 的修复输出替换该 target
3. 追加 missing target 的修复输出
4. 对所有 scope 做 canonical 化
5. 完全重复 scope 去重
6. 保留合法重叠
7. 确定性排序
8. 统一重新编号
```

Join 必须是事务式的：

```text
任一修复输出无效，则不提交 join；
不得部分应用成功结果；
不得将失败静默转为空 scopes。
```

### 14.2 示例

Base scopes：

```text
s0 = a0+a1
s1 = a3+a4
s2 = a1+a2+a5
s3 = a2+a4
```

修复结果：

```text
repair(s2) = []
repair(s3) = []
repair(t0) = ["a6+a7"]
```

Join 后中间集合：

```text
a0+a1
a1+p0
a6+a7
```

最终重新编号：

```text
s0 = a0+a1
s1 = a1+p0
s2 = a6+a7
```

### 14.3 Canonical 化

每个 scope 是无序 selector 成员集合，但 canonical 输出必须使用固定的
namespace 顺序：

```text
p1+a3+a1+a2 → a1+a2+a3+p1
```

规则：

```text
selector 去重
a* 排在 p* 前
a* 按数字顺序排序
p* 按数字顺序排序
```

### 14.4 去重与重叠

允许重叠：

```text
a0+a1
a0+a2
a1+p0
```

可以同时存在。

完全重复：

```text
a0+a1
a0+a1
```

只保留一个。

若 missing repair 输出在 join 后没有引入新的 distinct scope，应进入：

```text
scope_repair_no_effect
```

### 14.5 排序与重编号

系统对去重后的 scope 使用确定性排序，例如：

```text
先按最小 selector 编号排序；
再按 selector 序列排序。
```

然后统一编号：

```text
s0 = ...
s1 = ...
s2 = ...
```

不维护：

```text
old s* → new s*
```

原因：

```text
s* 是本地 selector；
scope 没有稳定身份；
真正有意义的是语义单元成员组合。
```

## 15. Post Scope Review

Join 和重编号后，必须使用最终 scope 集合重新执行完整 Scope Review。

输入：

```text
t0
anchors
trusted facts
重新编号后的 joint_context_scopes
```

要求：

```text
Review 不看旧编号；
不比较新旧 scope ID；
只审查最终语义集合。
```

重新 Review 检查：

```text
原缺陷是否消除；
是否引入 unsupported scope；
是否仍过宽或缺成员；
是否变成 topic-only；
是否医学综合越界；
是否因果推断越界；
是否时间语境错配；
是否产生 a* / p* 冗余 scope；
是否造成新的 missing joint context。
```

如果第二次 Review 仍有缺陷：

```text
不再继续修复；
进入 scope_repair_failed 或 repair_budget_exceeded。
```

初始预算：

```text
每个 defect cluster 最多修复 1 次；
整个 turn 的 scope repair 总预算固定。
```

## 16. 输出契约

Scope 层成功输出：

```json
{
  "status": "completed",
  "anchors": [
    "狗当前体重为15千克",
    "狗当前进食量低于平时基线",
    "狗近期换粮",
    "狗在换粮后开始出现软便",
    "狗当前精神状态正常"
  ],
  "trusted_pet_facts": [
    "狗历史体重为30千克",
    "狗平时每日进食量约为200克"
  ],
  "joint_context_scopes": [
    "a0+p0",
    "a2+a3"
  ]
}
```

selector 渲染规则：

```text
a0 → anchors[0]
p0 → trusted_pet_facts[0]
```

无共同语境时：

```json
{
  "status": "completed",
  "anchors": [
    "狗今天呕吐两次"
  ],
  "trusted_pet_facts": [],
  "joint_context_scopes": []
}
```

这是合法完成状态。

输出边界：

```text
anchors 是核心事实候选；
trusted_pet_facts 是只读可信事实投影；
joint_context_scopes 是处理 overlay；
scope 不改变 anchors 或 trusted facts 语义；
scope 不生成新事实；
来源分区由 a* / p* namespace 与系统推导；
s* 只作为内部 Review selector，不写入档案或长期记忆。
```

## 17. 失败状态

至少定义：

```text
scope_input_invalid
trusted_fact_projection_invalid
trusted_fact_count_exceeded
scope_plan_schema_invalid
scope_count_exceeded
scope_size_exceeded
scope_overlap_too_dense
scope_review_schema_invalid
scope_review_group_schema_invalid
scope_review_failed
scope_repair_schema_invalid
scope_repair_no_change
scope_repair_no_effect
scope_repair_unauthorized_selector
scope_repair_empty_for_missing
scope_repair_failed
scope_repair_budget_exceeded
scope_join_failed
scope_post_review_failed
pipeline_failed
```

含义示例：

| 状态 | 含义 |
|---|---|
| `scope_input_invalid` | 上游 anchor envelope 未成功或不可消费 |
| `scope_plan_schema_invalid` | Planner 输出格式非法 |
| `scope_count_exceeded` | scope 总数超限 |
| `scope_size_exceeded` | 单个 scope 成员数超限 |
| `scope_overlap_too_dense` | 重叠密度超限 |
| `scope_repair_no_change` | 已有 target 修复输出与原 scope 完全相同 |
| `scope_repair_no_effect` | 修复在 transactional join 后没有产生有效变化 |
| `trusted_fact_projection_invalid` | TrustedPetFactReader 投影失败或输出不可消费 |
| `trusted_fact_count_exceeded` | 可信事实投影数量超限 |
| `scope_repair_unauthorized_selector` | 修复输出使用了未授权 selector |
| `scope_repair_empty_for_missing` | missing scope 修复输出为空 |
| `scope_join_failed` | 统一 join 后无法通过确定性校验 |
| `scope_post_review_failed` | 修复并重编号后 Review 仍有缺陷 |

所有失败必须显式暴露。禁止：

```text
失败转空 scopes
失败后静默降级
宽松修复 JSON
单次请求内自动回退旧实现
```

## 18. 执行审计

审计层保存：

```text
base anchors
base trusted facts
base scopes
Scope Planner 原始输出
Context Support Review delta
Boundary Review delta
Temporal / Redundancy Review delta
系统推导的 current_anchor_refs / trusted_fact_refs
defect clusters
每个 target 的修复输入摘要
每个 target 的修复输出
join 前中间集合
最终 canonical scopes
post-review delta
最终状态
prompt / model / schema 版本
```

不需要保存：

```text
old scope ID → new scope ID 映射
```

若调试需要，可从：

```text
base scopes + repair outputs + join 规则
```

确定性推导最终结果。

核心原则：

```text
核心语义契约薄；
执行审计厚。
```

## 19. LangGraph 使用边界

LangGraph 可以作为执行编排层，但必须是固定拓扑状态图。

推荐节点：

```text
anchor_envelope
trusted_pet_fact_reader
trusted_pet_fact_projection
scope_planner
scope_schema_validator
scope_materializer
parallel_scope_review
scope_review_delta_merge
defect_cluster_aggregator
scope_repair_scheduler
cluster_scope_repair
missing_scope_repair
repair_output_validation
transactional_scope_join
canonicalize_and_dedupe
deterministic_renumber
post_scope_review
scope_commit_or_fail
```

允许动态变化的只有：

```text
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
模型执行临床动作
```

LangGraph 是可选实现形态。Scope 层契约不得绑定特定图执行框架。

## 20. 实施注意项

### 20.1 执行顺序

Scope 层只能在以下条件满足后启动：

```text
anchor envelope 状态为成功；
anchors 已完成局部 selector 分配；
anchor 数量在限制内；
TrustedPetFactReader 投影状态为成功或合法为空；
trusted facts 已完成本地 p* selector 分配；
trusted facts 数量在限制内；
当前 turn 输入仍可提供给 Review；
Scope 层配置和 prompt 版本就绪。
```

### 20.2 严格 schema

所有模型输出必须 strict schema validation：

```text
Scope Planner
Context Support Review
Boundary Review
Temporal / Redundancy Review
Targeted Scope Repair
```

不得宽松修复 JSON。结构失败进入显式状态或有界重试。

### 20.3 上限配置

生产配置应显式定义：

```text
最大 anchor 数
最大 trusted fact 数
最大 scope 数
单个 scope 最大成员数
单个语义单元最大参与 scope 数
最大修复 cluster 数
每次修复最大输出 scope 数
修复总预算
```

### 20.4 局部性

默认只修复 defect cluster target：

```text
不得顺手修改其他 scopes；
不得默认全局重写；
未修复 scopes 在 join 时原样保留。
```

全局重生成只能作为未来显式配置策略，不得成为单次请求内静默 fallback。

### 20.5 Prompt 防漂移

Prompt 中必须明确：

```text
只允许显式时间、事件、基线、同一观察或明确转述归因语境；
不得按医学主题聚类；
不得做整体状态判断；
不得推断因果；
不得输出关系类型；
不得输出 reason / confidence。
```

Prompt 示例只能说明边界，不得演化为全局事实类型枚举。

## 21. 验收边界

最小验收用例应覆盖：

```text
历史体重 + 当前体重 → 可形成 scope
当前体重 + 可信历史体重 → 可形成 scope
换粮事件 + 换粮后软便 → 可形成 scope，但不得因果化
当前体重 + 进食 + 精神 → 不得形成整体状态 scope
仅饮食主题相关 anchors → 不得形成 scope
当前回合已有等价历史体重 anchor 时，不得再输出 a1+p0 冗余 scope
无共同语境 → 合法输出空 scopes
单个 selector → schema invalid
pure p* scope → schema invalid
重复 selector → schema invalid
非顺序 selector → schema invalid
Context Support Review 输出 Boundary 缺陷码 → review_group_schema_invalid
Boundary Review 输出 Context Support 缺陷码 → review_group_schema_invalid
Temporal / Redundancy Review 输出其他 Group 缺陷码 → review_group_schema_invalid
两个 Group 对同一 scope 输出缺陷 → 合并为一个 repair cluster
输入 anchor 保留“它” → anchor envelope 未完成，本层不得启动
同一 scope 多缺陷码 → 聚合为一个 repair cluster
已有 target 修复输出为空 → 表示删除
missing target 修复输出为空 → fail
多个修复输出 → 统一 join 后重编号
post review 仍失败 → fail
```

示例输入：

```text
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行。
```

期望：

```text
a0+a1 可形成历史 / 当前体重语境；
a3+a4 可形成换粮后事件顺序语境；
a1+p0 可在当前回合缺失历史体重时形成跨来源体重语境；
a1+a2+a5 不得形成整体状态 scope；
a2+a4 在无明确关联时不得形成饮食主题 scope。
```

## 22. 防漂移清单

后续设计、实现或测试不得引入：

```text
把 scope 作为医学主题分组
把 scope 作为诊断推理单元
把 scope 作为综合征分组
把 scope 作为因果边
把 scope 作为急症或严重程度信号
把 scope 作为权威事实
把 scope 作为长期档案对象
让 Scope Planner 拆分 anchor
让 Scope Planner 生成 proposition
让模型输出 relation type
让模型输出 reason / confidence
拆分 turn scope 与 cross-source scope 输出结构
让模型输出 current_anchor_refs / trusted_fact_refs
输出 pure p* scope
让历史事实覆盖当前事实
让当前事实覆盖可信历史事实
让 Review 输出 corrected scopes
让 Review Group 输出其他 Group 缺陷码
按缺陷码分别修复同一 target
在 prompt 中把缺陷码映射成修复动作
修复过程中维护稳定 scope ID
在本层重新消解主体
部分修复失败后静默提交
失败转空 scopes
单次请求自动回退旧链路
```

## 23. 文档层级与变更原则

本文位于：

```text
veterinary-semantic-decomposition-charter.md
→ veterinary-semantic-preprocessing-control-conventions.md
→ veterinary-semantic-anchor-pipeline.md
→ veterinary-semantic-scope-architecture.md
→ veterinary-contextual-query-proposition-architecture.md
→ 后续 Coverage / 动态证据架构
```

本文只应因以下原因修订：

```text
scope 语义边界调整
输入或输出契约调整
planner / review / repair Delta 调整
缺陷码或缺陷簇策略调整
join / renumber 策略调整
失败状态或 Fail Fast 底线调整
LangGraph 或执行边界调整
```

不得把具体 prompt 全文、临时模型表现、局部工程问题或单个实现细节直接加入
本文。
