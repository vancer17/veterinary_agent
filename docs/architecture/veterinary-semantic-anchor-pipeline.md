<!--
=============================================================================
文件: docs/architecture/veterinary-semantic-anchor-pipeline.md
作用: 定义兽医问诊当前回合患者语义锚点生成、审查、局部修复、patch 应用
      与失败治理的数据管道。
范围: 覆盖 Semantic Anchor Generator、Semantic Anchor Review、固定缺陷
      Delta、Targeted Anchor Repair、系统局部更新、修复后重审、
      TurnSemanticAnchorEnvelope、执行审计与 LangGraph 使用边界。
说明: 本文不覆盖意图识别、Coverage、RAG、知识命题治理、临床安全裁决、
      长期记忆或宠物档案更新。
维护: 当 anchor 契约、Review Delta、缺陷码、局部修复策略、patch 语义、
      失败状态或 Fail Fast 底线调整时，必须先修订本文，再同步实现。
=============================================================================
-->

# 兽医语义锚点生成与局部修复架构草案

> **文档状态**：架构草案基线
>
> **文档定位**：本文细化
> [veterinary-semantic-decomposition-charter.md](./veterinary-semantic-decomposition-charter.md)
> 下的当前回合患者语义预处理边界。它与
> [veterinary-turn-intent-architecture.md](./veterinary-turn-intent-architecture.md)
> 平行：意图层处理用户请求与会话控制协议，本文处理用户陈述的患者事实语义锚点。
> 它同时遵守
> [veterinary-semantic-preprocessing-control-conventions.md](./veterinary-semantic-preprocessing-control-conventions.md)
> 中的跨层控制约定。
>
> **权威关系**：本文服从语义分解 charter。若本文、charter 或实现出现冲突，
> 必须先显式修订其中一方，不得在代码中形成第三种解释。

## 1. 核心结论

当前回合患者语义预处理采用：

```text
开放自然语言 semantic anchors
+ 人工定义原子性与语义边界
+ Generator 动态生成
+ Review 按缺陷组使用固定缺陷维度
+ Targeted Repair 局部修复
+ 系统确定性应用 patch
+ 修复后重新审查
+ 显式失败治理
```

核心控制原则：

```text
Generator 生成候选；
Review 按缺陷组并行发现缺陷；
系统合并各组 Delta；
Repair 只重写指定目标；
系统应用 patch、分配状态并裁决；
失败显式暴露。
```

本文不采用：

```text
全局医学事实维度枚举
固定症状槽位
精确 span 抽取
每条 anchor 携带 source_ref
模型输出 reason / confidence
Review 直接输出 corrected value
失败后默认全局重写
失败转空结果
```

## 2. 架构范围

### 2.1 In Scope

本文覆盖：

```text
Semantic Context Projector
Semantic Anchor Generator
Strict Schema Validation
确定性清理与本地 a* selector 分配
Semantic Anchor Review
分组 Semantic Anchor Review
分组固定缺陷 Delta Schema
缺陷码到固定中文短语的映射
Targeted Anchor Repair
Repair Output Validation
Deterministic Local Patch
修复后重审
TurnSemanticAnchorEnvelope
执行审计边界
LangGraph 使用边界
失败状态与防漂移原则
```

### 2.2 Out of Scope

本文不覆盖：

```text
意图识别
请求内容重写
控制协议识别
患者事实最终权威提交
Coverage
RAG
知识命题治理
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
semantic anchor 是当前回合候选患者事实语义，不是权威宠物档案事实。
```

后续患者事实治理层可以消费本文输出的 `TurnSemanticAnchorEnvelope`，但不得
因此绕过版本化、冲突保留或领域投影。

## 3. 背景问题

### 3.1 语义维度枚举的失败

如果按医学事实维度建立任务，系统会逐渐演化出：

```text
呕吐语义层
食欲语义层
体重语义层
时间语义层
量化比较语义层
基线比较语义层
因果归因语义层
```

这与固定槽位系统本质相同，只是换了一层名称。医疗文本是开放集合，不可能
预先穷举所有事实类型和高维语义模式。

例如：

```text
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便。
```

其中包含：

```text
历史测量值
当前测量值
平时基线
当前状态
相对比较
事件顺序
可能的用户归因
```

这些关系不应拆散为全局维度任务，而应保留在自包含自然语言 anchor 中。

### 3.2 精确 span 抽取的失败

历史实验表明，未训练 span 模型在兽医口语文本中的信噪比极低。确定性代码
又无法可靠处理语义切分问题。

因此本文不要求模型输出：

```text
source_span
source_ref
quote
start / end offset
token selector
```

也不在每条 anchor 上写入：

```text
source_ref = current_turn
```

原因是当前回合内所有 anchor 都来自同一输入，该字段对每条 anchor 都没有
区分能力，且容易制造伪来源可信感。

来源与执行审计由系统外层保存；语义支持由 Review LLM 对照当前整段输入判断。

### 3.3 单体 one-pass 的失败

让一个模型调用同时完成：

```text
anchor 生成
语义审查
自我修复
最终接受
状态判断
```

会导致职责混杂和自证。因此本文将生成、审查和修复拆成不同模型角色，并由
系统控制状态迁移。

### 3.4 复杂 JSON 的失败

多轮实验表明，长 JSON、深嵌套结构和逐字回写会显著增加模型幻觉风险。

因此所有模型输出都保持薄结构：

```text
Generator：anchors 字符串数组
Review：缺陷码 -> target selector 数组
Repair：anchors 字符串数组
```

禁止输出：

```text
reason
confidence
corrected value
source span
权威状态
```

### 3.5 全局重写的失败

如果某条 anchor 出现缺陷就重新生成全部 anchors，原本正确的 anchor 可能
发生回归，尤其是时间、数量、否定和高维关系语义。

因此默认采用：

```text
局部修复
局部 patch
未受影响 anchor 原样保留
```

## 4. 顶层管道

```text
TurnSnapshot
→ Semantic Context Projector
→ Semantic Anchor Generator
→ Strict Schema Validation
→ Deterministic Cleanup / Local Selector Assignment
→ Parallel Grouped Semantic Anchor Review
   ├─ Anchor Completeness Review
   ├─ Anchor Support / Boundary Review
   ├─ Anchor State / Certainty Review
   ├─ Anchor Measurement / Relation Review
   └─ Anchor Subject / Form / Atomicity Review
→ Review Delta Merge
→ Repair Router

   ├─ 无缺陷
   │   → Commit
   │
   ├─ 可局部修复缺陷
   │   → Targeted Anchor Repair
   │   → Repair Output Validation
   │   → Deterministic Local Patch
   │   → Post-repair Semantic Review
   │   → Commit / Fail
   │
   └─ 不可修复或超预算
       → Explicit Failure

→ TurnSemanticAnchorEnvelope
```

示意图：

```text
              ┌────────────────────┐
              │ TurnSnapshot       │
              └─────────┬──────────┘
                        │
              ┌─────────▼────────────┐
              │ Semantic Context     │
              │ Projector           │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Anchor Generator     │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Schema Validation    │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Assign a0/a1/a2...   │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Grouped Reviews      │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Delta Merge          │
              └─────────┬────────────┘
                        │
              ┌─────────▼────────────┐
              │ Repair Router        │
              └─────────┬────────────┘
                        │
         ┌──────────────┼──────────────┐
         │              │              │
    no defect     local repair    explicit fail
         │              │              │
         │       patch + re-review    │
         │              │              │
         └──────────────┴──────────────┘
                        │
              ┌─────────▼────────────┐
              │ Anchor Envelope      │
              └──────────────────────┘
```

## 5. Semantic Context Projector

语义锚点层不直接读取完整会话历史。系统必须先构造最小授权上下文。

### 5.1 输入

```text
current_turn_text
subject_binding_context
optional_subject_projection
language
必要的当前回合上下文
```

其中：

```text
subject_binding_context 是系统级身份绑定，不直接进入模型 prompt；
optional_subject_projection 是可选的模型可见主体投影。
```

模型可以看到：

```text
当前用户输入
可用的最小主体总结上下文
可选的显式主体投影
语言信息
```

模型不可以看到：

```text
完整会话历史
未治理长期记忆
宠物档案原始记录
RAG chunk
意图层生成结果作为事实依据
临床安全结论
```

### 5.2 主体边界

当前业务主路径是单宠会话：BFF 向后端传递当前会话绑定的宠物身份，系统负责
最终身份归属。该绑定不要求一定以显式主体投影形式暴露给模型。

如果模型可见上下文存在显式主体投影，Generator 必须使用该主体。如果没有
显式主体投影，Generator 应从当前输入或必要的当前回合上下文中总结主体。

例如：

```text
它今天吐了两次
```

在单宠物上下文中可重写为：

```text
狗今天呕吐两次
```

如果上下文不足以总结具体主体，可使用：

```text
当前宠物今天呕吐两次
```

若实现配置不允许使用“当前宠物”作为自包含主体，则进入：

```text
subject_reference_context_insufficient
```

Generator 与 Repair 均不得猜测物种、名字或品种。当前单宠主路径不使用
多宠物主体选择状态；`subject_ambiguous` 只能作为未来多宠扩展预留。

## 6. Semantic Anchor Generator

### 6.1 职责

Generator 只做一件事：

```text
根据当前用户输入生成动态自然语言 semantic anchors。
```

anchor 是：

```text
当前回合候选患者事实语义
```

不是：

```text
权威档案事实
医学诊断
知识命题
用户意图
临床安全信号
```

### 6.2 输出契约

Generator 输出必须是：

```json
{
  "anchors": [
    "狗历史体重为30千克",
    "狗当前体重为15千克",
    "狗当前进食量低于平时基线",
    "狗在换粮后开始出现软便",
    "狗当前精神状态正常",
    "主人未观察到狗呕吐"
  ]
}
```

约束：

```text
顶层唯一字段是 anchors
anchors 是字符串数组
每个元素是非空单行自然语言
锚点应自包含
锚点应尽量表达一个独立观察、状态或事件
```

### 6.3 禁止输出

Generator 不输出：

```text
id
text 包装对象
source_ref
source_span
quote
reason
confidence
dimension
fact_type
task_type
medical_category
status
ambiguous
```

原因：

```text
模型生成 ID 容易跳号、重复或伪造；
source_ref=current_turn 无区分能力；
span 抽取信噪比低；
reason / confidence 无显式消费者并增加幻觉；
dimension / fact_type 会退化为固定槽位；
ambiguous 是无人消费的自由文本死字段。
```

### 6.4 原子性边界

人工定义原子性，LLM 根据输入动态执行。

正确示例：

```text
狗今天呕吐两次
主人未观察到狗呕吐
主人不知道狗当前是否腹泻
狗当前进食量低于平时基线
狗在换粮后开始出现软便
```

错误示例：

```text
呕吐
没有呕吐
时间不明
体重下降
换粮导致狗软便
```

原子性要求：

```text
狗今天呕吐两次
```

不应拆成：

```text
狗发生呕吐
呕吐发生在今天
呕吐次数为两次
```

而：

```text
狗进食正常且饮水减少
```

应拆成：

```text
狗当前进食正常
狗当前饮水减少
```

### 6.5 语义保留要求

anchor 必须保留用户明确表达的：

```text
主体
时间范围
否定
不确定性
未知
未观察
数量
单位
频率
程度
比较基线
事件顺序
观察者归因
```

示例：

```text
没看到它吐
```

应输出：

```text
主人未观察到狗呕吐
```

不得输出：

```text
狗没有呕吐
```

```text
主人感觉它瘦了
```

应输出：

```text
主人感觉狗体重下降
```

不得输出：

```text
狗体重下降
```

### 6.6 量化边界

如果用户只说：

```text
狗去年30千克，现在15千克
```

Generator 应保留：

```text
狗历史体重为30千克
狗当前体重为15千克
```

不得主动输出：

```text
狗体重明显下降
```

除非用户明确说：

```text
瘦了
体重下降
降到
```

后续量化比较必须由下游在存在明确知识需求时，通过通用、有界、可审计的方式
按需派生，不得在语义锚点层穷举生成所有趋势。

## 7. 系统确定性处理与 selector 分配

Generator 输出后，系统执行：

```text
严格 JSON 解析
schema 校验
空白归一
单行校验
长度限制
数量限制
精确重复去除
按顺序分配本地 selector
```

例如模型输出：

```json
{
  "anchors": [
    "狗历史体重为30千克",
    "狗当前体重为15千克",
    "狗当前进食量低于平时基线"
  ]
}
```

系统物化为：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
a2 狗当前进食量低于平时基线
```

规则：

```text
a* 由系统分配；
模型不生成 ID；
a* 只在当前调用或当前修复流程内有效；
a* 不写入宠物档案或长期记忆；
a* 不是长期权威身份。
```

精确重复可以由系统去除。语义等价与重复关系不由确定性代码判断，保留给
Review 或后续患者事实治理层。

## 8. Semantic Anchor Review

Semantic Anchor Review 不使用单体全维度调用。系统将相关缺陷维度分成五个
固定 Group，并行调用 Review LLM，再确定性合并 Delta。

### 8.1 职责

各 Group 共同判断当前 anchor 集相对于当前整段输入的语义质量：

```text
是否遗漏显式语义锚点
是否添加原文未表达语义
是否主体错误或仍保留未消解主体
是否极性、时间、数量、单位、不确定性或关系语义错误
是否因果过度推断
是否原子性不足
是否误把非患者事实当作事实
```

Review 不做：

```text
输出 corrected value
输出 reason
输出 confidence
输出 source span
直接决定权威接受
直接修改 anchor
执行临床判断
```

### 8.2 输入形态

每个 Review Group 都看到：

```text
t0 当前原始输入
可用主体总结上下文或可选主体投影
a0/a1/a2... 系统 selector 化后的 anchors
```

示例：

```text
<t0>
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行，没看到吐。
</t0>

<anchors>
a0 狗历史体重为30千克
a1 狗当前体重为15千克
a2 狗进食减少
a3 换粮导致狗软便
a4 它没有呕吐
</anchors>
```

### 8.3 Group A：Anchor Completeness Review

只判断：

```text
missing_explicit_anchor
```

Delta Schema：

```json
{
  "missing_explicit_anchor": ["t0"]
}
```

该字段只允许 `t0`。

### 8.4 Group B：Anchor Support / Boundary Review

只判断：

```text
unsupported_addition
causal_overreach
not_patient_assertion
```

Delta Schema：

```json
{
  "unsupported_addition": ["a3"],
  "causal_overreach": ["a3"],
  "not_patient_assertion": ["a4"]
}
```

该 Group 只允许 `a*`。

### 8.5 Group C：Anchor State / Certainty Review

只判断：

```text
polarity_error
temporal_error
uncertainty_error
```

Delta Schema：

```json
{
  "polarity_error": ["a4"],
  "temporal_error": [],
  "uncertainty_error": []
}
```

实际输出省略空数组。该 Group 只允许 `a*`。

### 8.6 Group D：Anchor Measurement / Relation Review

只判断：

```text
quantity_or_unit_error
relation_loss
```

Delta Schema：

```json
{
  "quantity_or_unit_error": [],
  "relation_loss": ["a2"]
}
```

该 Group 只允许 `a*`。

### 8.7 Group E：Anchor Subject / Form / Atomicity Review

只判断：

```text
subject_error
unresolved_reference
not_self_contained
non_atomic
```

Delta Schema：

```json
{
  "subject_error": [],
  "unresolved_reference": ["a4"],
  "not_self_contained": ["a4"],
  "non_atomic": ["a5"]
}
```

该 Group 只允许 `a*`。

在单宠绑定场景下，`unresolved_reference` 仍必须保留。它不再表示多宠物
主体选择歧义，而表示：

```text
anchor 仍包含“它 / 这个 / 那个”等未消解指代；
或未根据可用上下文形成自包含主体表达。
```

`subject_error` 表示输出主体与可用主体投影或授权上下文不一致。

### 8.8 合并后的完整 Delta 语义视图

系统将五个 Group 的输出确定性合并后，得到完整语义视图：

```json
{
  "missing_explicit_anchor": ["t0"],
  "unsupported_addition": ["a3"],
  "subject_error": [],
  "unresolved_reference": ["a4"],
  "not_self_contained": ["a4"],
  "polarity_error": ["a4"],
  "temporal_error": [],
  "quantity_or_unit_error": [],
  "uncertainty_error": [],
  "relation_loss": ["a2"],
  "causal_overreach": ["a3"],
  "non_atomic": [],
  "not_patient_assertion": []
}
```

实际输出采用稀疏 Delta，省略空数组：

```json
{
  "missing_explicit_anchor": ["t0"],
  "unsupported_addition": ["a3"],
  "unresolved_reference": ["a4"],
  "not_self_contained": ["a4"],
  "polarity_error": ["a4"],
  "relation_loss": ["a2"],
  "causal_overreach": ["a3"]
}
```

含义：

```text
字段出现 = 存在缺陷
数组元素 = 受影响 target
字段省略 = 无缺陷
```

### 8.9 缺陷维度定义

| 缺陷码 | 中文短语 | 含义 | 允许 target |
|---|---|---|---|
| `missing_explicit_anchor` | 显式语义锚点缺失 | 当前输入存在显式患者语义但 anchors 未覆盖 | `t0` |
| `unsupported_addition` | 添加原文未表达语义 | anchor 包含输入未表达的结论、事件、程度或归因 | `a*` |
| `subject_error` | 主体语义错误 | 输出主体与可用主体投影或授权上下文不一致 | `a*` |
| `unresolved_reference` | 指代未消解 | anchor 仍保留代词或缺少自包含主体 | `a*` |
| `not_self_contained` | 锚点未自包含 | anchor 需要外部上下文才能理解 | `a*` |
| `polarity_error` | 极性语义错误 | 肯定、否定、未知、未观察或不确定混淆 | `a*` |
| `temporal_error` | 时间语义错误 | 历史、当前、今天、近期、长期等时间语义错误 | `a*` |
| `quantity_or_unit_error` | 数量单位语义错误 | 数量、单位、次数或程度丢失、改错 | `a*` |
| `uncertainty_error` | 不确定性语义丢失 | 可能、感觉、不确定等语义被绝对化 | `a*` |
| `relation_loss` | 高维语义关系丢失 | 比较基线、时间基准、事件顺序、相对对象等关系丢失 | `a*` |
| `causal_overreach` | 因果过度推断 | 时间、相关或主人感受被升格为因果结论 | `a*` |
| `non_atomic` | 原子性不足 | 复合事实未拆分，或事件属性被过度拆散 | `a*` |
| `not_patient_assertion` | 非患者事实语义 | 用户请求、意图、控制协议或元对话被误当作事实 | `a*` |

这些是有限治理缺陷维度，不是医学事实维度。禁止加入：

```text
weight_error
vomiting_error
appetite_error
diarrhea_error
blood_pressure_error
```

如需新增缺陷码，必须先修订本文，并同步 Review prompt、Repair prompt、
schema 校验和测试。

### 8.10 Group 与 Delta 校验规则

每个 Group 必须独立校验：

```text
字段名属于该 Group allowlist
数组元素唯一
数组元素存在
不得输出其他 Group 的缺陷码
不得输出未知字段
不得输出空数组作为默认值
不得输出嵌套 defects 对象
不得输出 supported / rejected
不得输出 reason / confidence / corrected value
```

全局 target 规则：

```text
missing_explicit_anchor 只允许 t0
其他字段只允许 a*
```

系统合并时：

```text
同 code 同 target 去重；
不同 code 同 target 聚合为同一缺陷簇；
不同 target 分别聚合；
t0 缺陷单独作为 turn-level defect。
```

`rejected` 不是模型输出。系统根据缺陷 Delta 和修复结果推导执行状态。

## 9. 缺陷码映射与 prompt 解释边界

系统只做稳定缺陷码到固定中文短语的映射：

```text
missing_explicit_anchor      → 显式语义锚点缺失
unsupported_addition         → 添加原文未表达语义
subject_error                → 主体语义错误
unresolved_reference         → 指代未消解
not_self_contained           → 锚点未自包含
polarity_error               → 极性语义错误
temporal_error               → 时间语义错误
quantity_or_unit_error       → 数量单位语义错误
uncertainty_error            → 不确定性语义丢失
relation_loss                → 高维语义关系丢失
causal_overreach             → 因果过度推断
non_atomic                   → 原子性不足
not_patient_assertion        → 非患者事实语义
```

系统不根据当前输入动态生成完整语义解释。

缺陷的完整解释由人工维护在 Repair prompt 中，例如：

```text
高维语义关系丢失：
  当前 anchor 可能丢失比较基线、时间基准、数量单位、事件顺序或相对对象。
  修复时必须回到原始输入，恢复用户明确表达的关系，不得添加医学推断。

因果过度推断：
  原文仅表达时间先后、相关、同时出现或主人感受时，不得改写为因果结论。

极性语义错误：
  必须区分肯定、否定、未知、未观察和不确定。
```

这些解释属于版本化 prompt 资产，不属于确定性代码的动态渲染结果。

## 10. Repair Router

Review 输出缺陷后，系统先路由，而不是立即重写。

### 10.1 无缺陷

```text
Review Delta 为空
→ Commit
```

如果 anchors 为空且 Review 确认无显式患者事实：

```text
→ completed_no_patient_anchors
```

### 10.2 可局部修复缺陷

以下缺陷默认进入 Targeted Anchor Repair：

```text
unsupported_addition
unresolved_reference
not_self_contained
polarity_error
temporal_error
quantity_or_unit_error
uncertainty_error
relation_loss
causal_overreach
non_atomic
missing_explicit_anchor
```

### 10.3 不可猜测修复

以下情况不得由 Repair LLM 猜测：

```text
subject_error 且可用上下文不足以确认正确主体
模型可见上下文不足以总结主体且实现不允许使用“当前宠物”
主体授权不足
```

应进入：

```text
subject_reference_context_insufficient
```

或对应显式失败状态。

### 10.4 系统性失败

以下情况不使用默认局部修复：

```text
缺陷比例极高
missing_explicit_anchor 范围过大
anchor 数量超限
Generator schema 失败
输入过长
```

初始实现应显式失败。若未来引入显式 full regenerate，必须作为系统配置的
受控修复策略进入审计，不得成为单次请求内的静默 fallback。

## 11. Targeted Anchor Repair

### 11.1 职责

Targeted Anchor Repair 只处理系统指定的一个 target 或一组 target：

```text
基于原始输入和当前 anchor 集；
根据固定中文缺陷短语；
重写受影响 anchor。
```

Repair 不负责：

```text
全局重写
决定最终接受
输出 reason / confidence
输出 source span
创建任务类型
决定临床含义
```

### 11.2 修复输入形态

示例：

```text
<original_input>
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行，没看到吐。
</original_input>

<current_anchors>
a0 狗历史体重为30千克
a1 狗当前体重为15千克
a2 狗进食减少
a3 换粮导致狗软便
a4 狗没有呕吐
</current_anchors>

<repair_target>
a3
</repair_target>

<semantic_defects>
因果过度推断
</semantic_defects>
```

同一个 target 存在多个缺陷时：

```text
<semantic_defects>
因果过度推断
添加原文未表达语义
</semantic_defects>
```

### 11.3 修复输出契约

Repair 输出必须是：

```json
{
  "anchors": [
    "狗在换粮后开始出现软便"
  ]
}
```

不输出：

```json
{
  "target": "a3",
  "operation": "replace",
  "reason": "...",
  "confidence": 0.9
}
```

原因：

```text
target 由系统指定；
operation 由系统决定；
reason 无消费者；
confidence 不是权威。
```

### 11.4 缺失锚点修复

当 Review 输出：

```json
{
  "missing_explicit_anchor": ["t0"]
}
```

系统使用 add missing 模式：

```text
<repair_mode>
add_missing
</repair_mode>

<semantic_defects>
显式语义锚点缺失
</semantic_defects>
```

Repair 只输出新增项：

```json
{
  "anchors": [
    "主人未观察到狗呕吐"
  ]
}
```

系统追加新增项，不重写既有 anchors。

## 12. Repair Output Validation

Repair 输出必须通过与 Generator 相同的薄 schema 校验：

```text
顶层唯一字段 anchors
anchors 是字符串数组
字符串非空、单行
长度和数量受配置限制
不得输出未知字段
```

另有模式相关约束：

```text
add_missing 模式必须输出非空数组
replace / split / merge 模式默认必须输出非空数组
delete-compatible 模式可允许空数组
```

是否启用 delete-compatible 模式必须显式配置。默认不支持通过空数组静默删除
anchor。

## 13. Deterministic Local Patch

系统根据修复模式和 target 应用 patch。模型不指定 operation。

### 13.1 replace

修复前：

```text
a3 换粮导致狗软便
```

修复输出：

```json
{
  "anchors": [
    "狗在换粮后开始出现软便"
  ]
}
```

系统替换 `a3`。

### 13.2 split

修复前：

```text
a5 狗进食正常且饮水减少
```

修复输出：

```json
{
  "anchors": [
    "狗当前进食正常",
    "狗当前饮水减少"
  ]
}
```

系统将一个 target 替换为两个 anchor。

### 13.3 merge

如果关系语义被拆散，系统可将一组 target 作为修复对象。

修复前：

```text
a3 狗发生换粮事件
a4 狗出现软便
```

修复输出：

```json
{
  "anchors": [
    "狗在换粮后开始出现软便"
  ]
}
```

系统将整组替换为一个 anchor。

### 13.4 append missing

Review 标记 `missing_explicit_anchor` 后，系统追加修复输出，不修改既有
anchor。

### 13.5 局部保留原则

Patch 应用时必须满足：

```text
未受影响 anchor 原样保留
修复历史进入执行审计
旧 target 与新 anchor 的映射可追溯
patch 后 anchor 集重新编号或保持稳定编号由实现配置决定
```

但最终输出不携带内部 selector。

## 14. 修复预算与修复后重审

生产配置应固定：

```text
初始实现整条 anchor lane 最多执行一轮 repair
```

一轮 repair 中可以对多个 target 并行或顺序执行局部修复，但不得对同一目标
循环修复。

Patch 应用后必须重新执行完整 Semantic Anchor Review，至少确认：

```text
原缺陷是否消除
是否引入 unsupported_addition
是否丢失时间、数量、单位、否定或不确定性
是否破坏原子性
是否产生新的关系丢失或因果过度推断
整体显式事实是否完整
```

如果重审后仍存在缺陷：

```text
不再继续修复；
进入 repair_failed 或 repair_budget_exceeded。
```

禁止：

```text
多个模型自由讨论
无限递归修复
每次失败全局重写
失败后静默丢弃
失败转空 anchors
单次请求内回退旧抽取器
宽松修复模型 JSON
```

## 15. TurnSemanticAnchorEnvelope

核心输出保持薄契约：

```json
{
  "status": "completed",
  "anchors": [
    "狗历史体重为30千克",
    "狗当前体重为15千克",
    "狗当前进食量低于平时基线",
    "狗在换粮后开始出现软便",
    "狗当前精神状态正常",
    "主人未观察到狗呕吐"
  ]
}
```

核心契约不携带：

```text
每条 anchor 的 id
source_ref
source_span
fact_type
confidence
review_reason
repair_reason
```

如果下游调用需要引用某条 anchor，由 prompt renderer 在调用内临时生成：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
```

这些 selector 不进入长期状态。

## 16. 执行审计边界

虽然核心语义契约不携带 source metadata，系统仍必须保存执行审计：

```text
turn_id
conversation_id
input_digest
pipeline_version
generator_model
generator_prompt_version
review_model
grouped_review_prompt_versions
repair_model
repair_prompt_version
model_run_ids
各 Group Review delta
合并后 review delta
repair_history
patch_history
final_status
```

审计可以记录：

```text
初始 anchors
系统 selector 视图
缺陷 Delta
修复输入
修复输出
patch 前后映射
最终 anchors
```

原则：

```text
核心契约薄；
执行审计厚。
```

审计信息不作为每条 anchor 的语义负载，也不得让模型自证来源。

## 17. 高维语义与防枚举原则

高维语义不通过维度 worker 或全局枚举处理，而通过开放自然语言 anchor 与
通用缺陷维度处理。

应保留为自包含 anchor 的例子：

```text
狗当前进食量低于平时基线
狗在换粮后开始出现软便
狗右前肢疼痛程度高于左前肢
狗用药后呕吐次数少于用药前
主人感觉狗体重下降
```

不应拆散为：

```text
时间维度
数量维度
比较维度
基线维度
因果维度
部位维度
```

禁止建立：

```text
体重趋势语义层
血压趋势语义层
食欲比较语义层
症状频率语义层
因果归因语义层
```

后续如果知识命题明确需要比较关系，应由下游通用量化比较层按需派生，并显式
记录派生输入与结果。

## 18. LangGraph 使用边界

LangGraph 可以作为执行编排层，但必须是固定拓扑状态图，不是自由 Agent 网络。

推荐节点：

```text
context_projector
anchor_generator
anchor_schema_validator
selector_assigner
parallel grouped semantic review
  ├─ anchor_completeness_review
  ├─ anchor_support_boundary_review
  ├─ anchor_state_certainty_review
  ├─ anchor_measurement_relation_review
  └─ anchor_subject_form_atomicity_review
review_delta_merge
repair_router
targeted_repair
repair_output_validator
patch_applier
post_repair_review
commit_or_fail
```

允许动态变化的只有：

```text
修复 target 数量
并行 Review 或 Repair 调用数量
```

禁止：

```text
模型创建节点
模型创建任务类型
模型自由递归分解
模型直接修改图状态
模型自行 fallback
模型决定临床动作
```

LangGraph 不是语义架构的必要条件。先用普通服务 pipeline 实现时，本文契约
同样适用。

## 19. 状态与失败治理

至少区分：

```text
completed
completed_no_patient_anchors
subject_binding_missing
subject_reference_context_insufficient
input_invalid
input_too_large
anchor_count_exceeded
generator_schema_invalid
review_schema_invalid
review_group_schema_invalid
repair_schema_invalid
repair_failed
repair_budget_exceeded
semantic_review_failed
pipeline_failed
```

空数组语义：

```text
anchors: []
不能直接等价于成功无事实。
```

处理规则：

```text
Review 确认当前输入没有患者事实
→ completed_no_patient_anchors

Review 发现遗漏
→ missing_explicit_anchor
→ repair 或显式失败
```

所有失败必须显式暴露。禁止：

```text
失败转空 anchors
失败转 completed
partial 结果伪装为 completed
单次请求内静默回退旧链路
```

## 20. 与意图识别层的边界

本文复用意图识别层的控制协议：

```text
薄 schema
本地 selector
固定缺陷维度
稀疏 Delta
有界修复
修复后重审
Fail Fast
```

但不复用其语义职责。

归意图层：

```text
用户想问什么
是否要求立即回答
是否要求纠正
是否请求建议
```

归语义锚点层：

```text
用户陈述了哪些患者事实
事实中保留了哪些时间、数量、否定、不确定性和关系语义
```

因此：

```text
帮我看看要不要去医院
```

不应成为患者事实 anchor。

```text
狗今天呕吐两次
```

才是患者事实 anchor。

## 21. 实施注意项

### 21.1 Strict JSON Gateway

所有模型输出必须经过严格 schema 校验。禁止：

```text
宽松 JSON 修复
忽略未知字段
接受字符串化 JSON
接受 Markdown 代码块中的非契约内容
用另一个模型宽松修复 JSON
```

初始实现建议 schema 失败直接进入对应失败状态；如需重试，必须是显式配置的
有界结构重试，并进入审计。

### 21.2 字段必须有消费者

每个输出字段必须有显式下游消费者。禁止加入：

```text
ambiguous
reason
confidence
source_ref
fact_type
dimension
```

除非先修订架构并说明消费者、状态迁移和失败治理。

### 21.3 Prompt 版本化

至少版本化：

```text
Generator prompt
Anchor completeness review prompt
Anchor support / boundary review prompt
Anchor state / certainty review prompt
Anchor measurement / relation review prompt
Anchor subject / form / atomicity review prompt
Repair prompt
缺陷码定义
原子性标准
高维语义保留标准
```

模型执行结果必须绑定 prompt 版本进入审计。

### 21.4 数量与长度限制

系统必须显式配置：

```text
最大输入长度
最大 anchor 数量
最大 anchor 字符长度
最大修复 target 数量
最大修复轮数
```

超限不得截断后伪装成功，应进入对应失败状态。

### 21.5 Review 与 Generator 分离

即使底层使用同一模型服务，也必须使用不同角色、不同 prompt 和不同调用上下文。

禁止：

```text
Generator 自己审查自己
Generator 自行修复后直接提交
Review 输出 corrected value
```

### 21.6 Grouped Review 与主体验收

最小验收用例必须覆盖：

```text
Completeness Review 输出其他 Group 缺陷码 → review_group_schema_invalid
Support / Boundary Review 输出 non_atomic → review_group_schema_invalid
多个 Group 对同一 target 输出缺陷 → 合并为一个 defect cluster
无显式主体投影但上下文可总结主体 → 输出具体主体
无显式主体投影且上下文不足 → 输出“当前宠物”或显式失败
输出保留“它” → unresolved_reference + not_self_contained
输出主体与可用主体投影冲突 → subject_error
单宠绑定不能作为猜测物种、名字或品种的理由
```

## 22. 示例闭环

原始输入：

```text
狗去年30千克，现在15千克，比平时少吃，换粮后开始软便，精神还行，没看到吐。
```

初始 anchors：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
a2 狗进食减少
a3 换粮导致狗软便
a4 狗没有呕吐
a5 狗当前精神状态正常
```

Review Delta：

```json
{
  "relation_loss": ["a2"],
  "causal_overreach": ["a3"],
  "polarity_error": ["a4"]
}
```

局部修复结果：

```text
a2 狗当前进食量低于平时基线
a3 狗在换粮后开始出现软便
a4 主人未观察到狗呕吐
a5 狗当前精神状态正常
```

系统更新后：

```text
a0 狗历史体重为30千克
a1 狗当前体重为15千克
a2 狗当前进食量低于平时基线
a3 狗在换粮后开始出现软便
a4 主人未观察到狗呕吐
```

重审 Delta：

```json
{}
```

最终输出：

```json
{
  "status": "completed",
  "anchors": [
    "狗历史体重为30千克",
    "狗当前体重为15千克",
    "狗当前进食量低于平时基线",
    "狗在换粮后开始出现软便",
    "主人未观察到狗呕吐",
    "狗当前精神状态正常"
  ]
}
```

## 23. 防偏移清单

后续设计与实现不得引入：

```text
全局医学事实维度枚举
固定症状槽位
领域专用语义管道
精确 span 抽取
每条 anchor 携带 source_ref / source_span
模型自证来源
模型输出权威置信度
Generator 输出 ambiguous / reason / confidence
Review 输出 corrected value
使用单体全维度 Semantic Anchor Review
让 Review Group 输出其他 Group 缺陷码
因单宠绑定移除 unresolved_reference
让模型猜测物种、名字或品种
Repair 全局重写未受影响 anchors
无限递归修复
失败转空结果
失败后单次请求静默回退
把意图请求当作患者事实
把 semantic anchor 写入权威档案
在语义锚点层做诊断、治疗或临床安全裁决
```

## 24. 变更原则

本文只应因以下原因修订：

```text
anchor 核心契约调整
Review Delta 或缺陷码调整
缺陷中文短语映射调整
局部修复策略或 patch 语义调整
修复预算或失败状态调整
执行审计边界调整
LangGraph 拓扑边界调整
与 charter 或意图层权威边界调整
```

不得把单个模型缺陷、临时 prompt 问题或局部工程实现细节直接加入本文。
