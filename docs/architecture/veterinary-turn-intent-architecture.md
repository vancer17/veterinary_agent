<!--
=============================================================================
文件: docs/architecture/veterinary-turn-intent-architecture.md
作用: 定义兽医问诊意图识别层的控制协议、请求内容重写、审查、修复、
      合并与失败治理架构。
范围: 覆盖 Turn Intent / Request Items 数据管道、稀疏 Delta Schema、
      省略语义推断、上下文继承、审查契约、target 级缺陷簇聚合、
      局部修复、确定性 patch、修复后重审、状态边界和实施防漂移原则。
说明: 本文不覆盖患者事实抽取、semantic anchors、atomic propositions、
      Coverage、知识命题、临床安全裁决、长期记忆或宠物档案更新。
维护: 当控制协议字段、request_items 契约、省略推断边界、Review Delta、
      修复策略或 Fail Fast 底线调整时，必须先修订本文，再同步实现。
=============================================================================
-->

# 兽医问诊意图识别层架构草案

> **文档状态**：架构草案基线
>
> **文档定位**：本文细化
> [veterinary-semantic-decomposition-charter.md](./veterinary-semantic-decomposition-charter.md)
> 下的当前回合意图预处理边界。它只定义意图识别层的架构契约，不定义患者
> 事实抽取层。
> 它同时遵守
> [veterinary-semantic-preprocessing-control-conventions.md](./veterinary-semantic-preprocessing-control-conventions.md)
> 中的跨层控制约定。
>
> **权威关系**：意图识别层输出 `TurnIntentEnvelope`。患者事实层未来成熟后，
> 应在外层 `TurnSemanticEnvelope` 中与 `TurnIntentEnvelope` 合并，不得让
> 患者事实层重新解释用户意图，也不得让意图层抽取患者事实。

## 1. 核心结论

用户意图分为两类语义：

```text
1. 会话控制协议
2. 用户请求内容
```

二者必须由不同模型调用处理：

```text
ControlProtocolGenerator
→ 只识别极少数确定性控制协议

RequestItemsGenerator
→ 将用户请求内容重写为自包含自然语言短句
```

核心原则：

```text
控制协议可枚举，但只枚举直接改变会话控制流的协议。
用户请求内容不可枚举，只能重写为开放自然语言 request_items。
```

禁止把用户意图拆成：

```text
wants_triage
wants_diagnosis
wants_treatment
wants_diet_advice
wants_medication_advice
wants_report_explanation
```

这些是请求内容，不是控制协议。

意图层修复采用以下控制协议：

```text
Generator 生成候选；
Review 按缺陷组并行发现缺陷；
系统合并各组 Delta；
系统按 target 聚合缺陷簇；
Repair 只重写授权 target；
系统应用 patch、重编号并重新审查；
失败显式暴露。
```

## 2. 架构范围

### 2.1 In Scope

本文覆盖：

```text
TurnIntentContext 投影
ControlProtocolGenerator
RequestItemsGenerator
ControlProtocolReview
Grouped RequestItemsReview
Review Delta Merge
Defect Cluster Aggregation
ControlProtocolTargetedRepair
RequestItemsTargetedRepair
Repair Output Validation
确定性结构校验
本地 selector 分配
确定性局部 patch 与 request items 重编号
修复后重审
省略语义推断
上下文请求目标继承
target 级有界局部修复
意图结果合并
TurnIntentEnvelope
失败状态与审计边界
```

### 2.2 Out of Scope

本文不覆盖：

```text
患者事实抽取
semantic anchors
atomic propositions
患者事实治理
Coverage
知识命题
RAG
临床安全裁决
急诊或 urgent signal 判断
诊断结论
治疗建议
长期记忆写入
宠物档案更新
```

特别是：

```text
correction=true 不表示本层已经修正患者事实。
```

它只说明当前回合存在修正控制协议。事实修正、冲突保留和修正目标绑定由
未来患者事实治理层处理。

## 3. 背景问题

### 3.1 意图路径枚举的失败

如果为用户请求建立全局意图枚举，系统会逐渐演化出：

```text
wants_triage
wants_diagnosis
wants_treatment
wants_diet_advice
wants_medication_advice
wants_behavior_advice
wants_report_explanation
```

这会重新形成意图槽位系统，并导致：

```text
枚举外请求丢失
多意图被压扁
请求内容与控制行为混淆
下游策略依赖不稳定分类字段
```

### 3.2 单体意图调用的失败

让一个模型调用同时完成：

```text
控制协议识别
用户请求重写
省略语义推断
上下文继承
请求内容分类
患者事实判断
```

会造成注意力分散，并容易输出深层结构或自证字段。

### 3.3 空结果语义的失败

在兽医问诊场景中，用户陈述：

```text
今天狗吐了两次。
```

虽然没有显式问句，但通常蕴含：

```text
希望系统评估狗当前情况
```

因此成功完成的 request lane 不应输出：

```json
{
  "request_items": []
}
```

空数组不能表示“用户没有请求”，更不能表示模型失败。

### 3.4 source / span 自证的失败

意图层不要求模型输出：

```text
source_ref
source_span
quote
reason
confidence
```

用户请求是否被当前输入和授权上下文支持，由独立 Review 判断。执行来源和
审计身份由系统绑定。

### 3.5 与历史 M06 设计的关系

本文复用历史 M06 生产边界修订中已经验证的方向：

```text
Turn Intent 使用薄契约
strict JSON Gateway
生成器不输出 evidence / reason / confidence
Review 按缺陷组使用固定缺陷维度
失败显式暴露
```

本文不复用历史实现中的：

```text
wants_triage
question_present
fact_statement_present
report_context_present
one-pass Claim Proposition Inventory
结构化 phrase lane
```

原因不是这些字段从未工作，而是它们会把开放请求内容重新压入意图枚举，或
与患者事实层形成双真相源。

### 3.6 全局重写的失败

如果任一 Review 缺陷都触发整条 lane 重新生成，原本正确的输出可能发生
回归。例如：

```text
r0 狗是否患有胰腺炎        # 过度推断
r1 狗是否需要送医          # 正确
```

为修复 `r0` 而重写整个 request lane，可能导致 `r1` 丢失、改变或产生新的
unsupported request。控制协议 lane 也一样：为修复一个误判的
`correction` 而重写全部协议，可能使原本正确的 `answer_now` 发生回归。

因此默认修复策略必须是 target 级局部修复，而不是整 lane 全局重写。

## 4. 顶层管道

```text
TurnSnapshot
→ TurnIntentContextProjector
→ Parallel Intent Lanes

Lane A:
ControlProtocolGenerator
→ Control Protocol Schema Validation
→ Local c* Selector Assignment
→ ControlProtocolReview
→ control_protocol delta
→ ControlProtocol Defect Cluster Aggregation
→ ControlProtocolTargetedRepair
→ Repair Output Validation
→ Deterministic Control Protocol Patch
→ Post ControlProtocolReview

Lane B:
RequestItemsGenerator
→ Request Items Schema Validation
→ Local r* Selector Assignment
→ Parallel Grouped RequestItemsReview
   ├─ Request Completeness / Ellipsis Review
   ├─ Request Fidelity / Boundary Review
   └─ Request Context / Reference / Self-containment Review
→ Review Delta Merge
→ request_items delta
→ Request Items Defect Cluster Aggregation
→ RequestItemsTargetedRepair
→ Repair Output Validation
→ Deterministic Request Items Patch
→ Request Items Join / Renumber
→ Post RequestItemsReview

→ Deterministic Intent Merge
→ TurnIntentEnvelope
```

示意图：

```text
              ┌────────────────────┐
              │ TurnSnapshot       │
              └─────────┬──────────┘
                        │
            ┌───────────┴───────────┐
            │                       │
┌───────────▼───────────┐ ┌─────────▼─────────────┐
│ ControlProtocol       │ │ RequestItems          │
│ Generator             │ │ Generator             │
└───────────┬───────────┘ └─────────┬─────────────┘
            │                       │
┌───────────▼───────────┐ ┌─────────▼─────────────┐
│ Protocol Schema       │ │ Items Schema          │
│ Validation            │ │ Validation            │
└───────────┬───────────┘ └─────────┬─────────────┘
            │                       │
┌───────────▼───────────┐ ┌─────────▼─────────────┐
│ ControlProtocol       │ │ Local r* Selector     │
│ Review                │ │ Assignment            │
└───────────┬───────────┘ └─────────┬─────────────┘
            │                       │
            │                ┌──────▼──────────────┐
            │                │ Grouped Reviews     │
            │                └──────┬──────────────┘
            │                       │
            │                ┌──────▼──────────────┐
            │                │ Delta Merge         │
            │                └──────┬──────────────┘
            │                       │
┌───────────▼───────────┐ ┌─────────▼─────────────┐
│ Protocol Defect       │ │ Items Defect Cluster │
│ Cluster + Repair      │ │ + Repair             │
└───────────┬───────────┘ └─────────┬─────────────┘
            │                       │
┌───────────▼───────────┐ ┌─────────▼─────────────┐
│ Protocol Patch        │ │ Items Patch/Renumber │
│ + Recheck             │ │ + Recheck            │
└───────────┬───────────┘ └─────────┬─────────────┘
                        │
            ┌───────────▼───────────┐
            │ Deterministic Merge  │
            └───────────┬───────────┘
                        │
            ┌───────────▼───────────┐
            │ TurnIntentEnvelope   │
            └───────────────────────┘
```

两条生成 lane 必须并行或逻辑等价地独立执行：

```text
RequestItemsGenerator 不读取 ControlProtocolGenerator 结果。
ControlProtocolGenerator 不读取 request_items。
ControlProtocolTargetedRepair 不读取 request_items。
RequestItemsTargetedRepair 不读取 control_protocol。
```

这样避免一个 lane 的输出污染另一个 lane 的语义判断。

## 5. TurnIntentContext

意图层不直接读取完整会话历史。`TurnIntentContextProjector` 必须构造最小
授权上下文。

### 5.1 输入

```text
current_turn_text
active_request_context
pending_system_question
subject_binding_context
optional_subject_projection
language
```

其中：

```text
subject_binding_context 是系统级身份绑定，不直接进入模型 prompt；
optional_subject_projection 是可选的模型可见主体投影。
```

### 5.2 输出边界

模型可以看到：

```text
当前用户回合
当前活跃咨询目标
待澄清问题
可用的最小主体总结上下文
可选的显式主体投影
```

模型不得看到：

```text
task_id
run_id
attempt_number
snapshot_digest
skill_version
完整 JSON schema
工程 metadata
任意历史对话
未授权宠物信息
患者事实抽取结果
```

### 5.3 主体上下文

当前业务主路径是单宠会话：BFF 向后端传递当前会话绑定的宠物身份，系统
负责最终身份归属。该绑定不要求一定以显式主体投影形式暴露给模型。

RequestItemsGenerator 需要消解：

```text
它
这只猫
小的那只
```

如果模型可见上下文存在显式主体投影，应直接使用该主体：

```text
狗是否患有感冒
```

如果没有显式主体投影，模型应从当前输入、活跃咨询目标或待澄清问题等授权
上下文中总结主体。上下文不足以总结具体主体时，应使用：

```text
当前宠物是否患有感冒
```

若实现配置不允许使用“当前宠物”作为自包含主体，则进入：

```text
subject_reference_context_insufficient
```

模型不得猜测物种、名字或品种。当前单宠主路径不使用多宠物主体选择状态；
`subject_ambiguous` 只能作为未来多宠扩展预留。

## 6. Lane A：ControlProtocolGenerator

### 6.1 职责

ControlProtocolGenerator 只识别确定性极强、直接改变会话控制流的协议。

当前阶段只允许：

```text
answer_now
correction
clarification_request
```

### 6.2 Delta Schema

模型输出稀疏 boolean delta：

```json
{
  "answer_now": true,
  "correction": true
}
```

未出现的字段默认为 false。

完整语义字段：

```text
answer_now
correction
clarification_request
```

最终 envelope 中由系统归一化为完整 boolean：

```json
{
  "answer_now": true,
  "correction": false,
  "clarification_request": false
}
```

### 6.3 字段边界

#### `answer_now`

表示：

```text
用户要求现在回答，而不是继续追问。
```

它不是强制回答指令。下游仍需结合：

```text
临床安全状态
覆盖关系
追问轮数
回答充分性
```

决定最终行为。

#### `correction`

表示：

```text
用户正在纠正此前信息。
```

本层不得：

```text
覆盖历史事实
修改宠物档案
删除旧 proposition
绑定修正目标
```

未来患者事实层应基于该信号执行：

```text
correction target binding
conflict preservation
clarification gap
```

#### `clarification_request`

表示：

```text
用户请求系统澄清此前问题或表述。
```

它不是系统需要继续追问用户。

### 6.4 禁止输出

```text
wants_triage
wants_diagnosis
wants_treatment
request_present
question_present
fact_statement_present
report_context_present
reason
confidence
source_ref
source_span
```

## 7. Lane B：RequestItemsGenerator

### 7.1 职责

RequestItemsGenerator 将当前回合的用户请求内容重写为：

```text
自包含自然语言短句
```

它负责：

```text
显式请求识别
多意图请求拆分
省略请求推断
上下文请求目标继承
指代消解
省略消解
陈述语境下的保守默认请求生成
```

它不负责：

```text
患者事实抽取
医学结论生成
诊断
治疗建议
安全判断
回答用户问题
```

### 7.2 Delta Schema

输出：

```json
{
  "request_items": [
    "狗是否患有感冒",
    "狗是否需要送医"
  ]
}
```

单意图：

```json
{
  "request_items": [
    "狗是否患有感冒"
  ]
}
```

Schema 要求：

```text
request_items: array[string]
minItems: 1
maxItems: 3
每项非空
每项长度受限
不得输出未知字段
```

成功结果不允许空数组。

### 7.3 不使用“用户询问”前缀

内部 request item 不应写成：

```text
用户询问狗是否患有感冒
```

应写成：

```text
狗是否患有感冒
```

原因：

```text
request_items 字段名已经表达请求语用。
“用户询问”是冗余元信息。
它会增加模型注意力负担。
它容易污染下游医疗语义对象。
最终回答层可以再自然化为人称表达。
```

### 7.4 省略推断优先级

#### 1. 显式请求优先

用户输入：

```text
狗会不会是感冒？
```

输出：

```json
{
  "request_items": [
    "狗是否患有感冒"
  ]
}
```

#### 2. 有上下文目标时继承目标

当前活跃咨询目标：

```text
处理狗软便问题
```

用户输入：

```text
两天了
```

输出：

```json
{
  "request_items": [
    "狗软便情况应如何处理"
  ]
}
```

患者事实层未来另行输出：

```text
狗软便持续两天
```

#### 3. 无显式请求且无上下文目标时使用保守默认请求

用户输入：

```text
今天狗吐了两次。
```

输出：

```json
{
  "request_items": [
    "狗当前情况如何"
  ]
}
```

保守默认请求应避免：

```text
狗是否患有胰腺炎
狗是否需要急诊
狗是否需要用药
```

#### 4. 修正语境继承或使用默认请求

用户输入：

```text
狗不是呕吐，是干呕。
```

若当前存在咨询目标，则继承该目标；若没有，则输出：

```json
{
  "request_items": [
    "狗当前情况如何"
  ]
}
```

同时 ControlProtocolGenerator 输出：

```json
{
  "correction": true
}
```

患者事实层未来应处理为：

```text
狗存在干呕
```

并记录其修正此前“呕吐”描述的关系。该处理不属于本文范围。

### 7.5 省略推断边界

允许推断：

```text
用户希望继续评估宠物当前情况
用户希望解释某个现象
用户希望处理当前咨询目标
```

禁止推断：

```text
具体疾病
急诊必要性
用药需求
治疗方案
临床安全结论
```

### 7.6 非问诊回合

如果输入明显不属于兽医问诊范围，应在上下文投影或路由层显式标记：

```text
non_consultation_turn
```

不得为了满足 `minItems=1` 而编造医疗请求，也不得输出空数组伪装成功。

## 8. 确定性校验

### 8.1 原则

结构问题必须由系统处理，不交给 LLM Review。

### 8.2 ControlProtocol 校验

系统校验：

```text
字段是否属于 allowlist
值是否为 boolean
是否输出未知字段
字段数量是否超限
```

非法输出进入：

```text
control_protocol_schema_invalid
```

### 8.3 RequestItems 校验

在进入 LLM Review 前，系统校验：

```text
JSON schema 是否合法
request_items 是否存在
request_items 是否为空
数量是否超限
字符串是否为空
长度是否超限
是否存在精确重复
是否输出未知字段
```

如果输出：

```json
{
  "request_items": []
}
```

系统直接进入：

```text
request_items_empty
```

不得交给 LLM 判断“是否错误输出空数组”。

## 9. 本地 Selector

系统在 Review 前分配本地 selector，不由模型生成。

当前回合：

```text
t0
```

请求项：

```text
r0
r1
r2
```

控制协议字段可使用：

```text
c0 answer_now
c1 correction
c2 clarification_request
```

这些 selector 只在当前调用内有效，不是长期身份，也不得写入档案或长期记忆。

## 10. ControlProtocolReview

ControlProtocolReview 只有一个缺陷组，allowlist 固定为上述三个协议缺陷码。

### 10.1 职责

ControlProtocolReview 只判断：

```text
控制协议是否漏判
控制协议是否不被当前输入支持
控制协议是否歧义
```

### 10.2 Delta Schema

使用同构稀疏数组：

```json
{
  "missing_control_protocol": ["c0"],
  "unsupported_control_protocol": ["c1"],
  "ambiguous_control_protocol": ["c2"]
}
```

实际输出省略空数组：

```json
{
  "missing_control_protocol": ["c0"]
}
```

维度定义：

| 字段 | 含义 |
|---|---|
| `missing_control_protocol` | 存在控制协议但 Generator 未识别 |
| `unsupported_control_protocol` | Generator 输出的协议不被当前输入支持 |
| `ambiguous_control_protocol` | 协议语义不明确，不能确认 |

Review 不输出：

```text
reason
confidence
corrected value
原始引用
```

若同一 `c*` 同时出现：

```text
missing_control_protocol + unsupported_control_protocol
ambiguous_control_protocol + missing/unsupported_control_protocol
```

系统应进入：

```text
control_protocol_review_conflict
```

不得交给 Repair LLM 自行调和。

## 11. RequestItemsReview

RequestItemsReview 不使用单体全维度调用。系统将相关缺陷维度分成三个固定
Group，并行调用 Review LLM，再确定性合并 Delta。

### 11.1 职责

RequestItemsReview 只处理语义和语用问题：

```text
是否漏掉省略请求
是否过度推断具体医学问题
是否将请求改写成医学结论
是否正确继承上下文目标
是否完成指代消解
是否完成省略消解
request item 是否自包含
request item 是否被当前输入或上下文支持
```

它不做逐字审查，不输出 quote 或 source span。

### 11.2 输入形态

每个 Review Group 都看到：

```text
t0 当前用户输入
授权上下文目标
可用主体总结上下文或可选主体投影
r0/r1/... request items
```

示例：

```text
<t0>今天狗吐了两次。</t0>

<request_items>
r0 狗是否患有胰腺炎
</request_items>
```

### 11.3 Group A：Request Completeness / Ellipsis Review

只判断：

```text
missing_ellipsis_semantics
unresolved_ellipsis
```

Delta Schema：

```json
{
  "missing_ellipsis_semantics": ["t0"],
  "unresolved_ellipsis": ["r1"]
}
```

`missing_ellipsis_semantics` 只允许 `t0`；`unresolved_ellipsis` 只允许
`r*`。

### 11.4 Group B：Request Fidelity / Boundary Review

只判断：

```text
over_specific_medical_question
medical_conclusion_added
unsupported_request
```

Delta Schema：

```json
{
  "over_specific_medical_question": ["r0"],
  "medical_conclusion_added": ["r1"],
  "unsupported_request": ["r2"]
}
```

该 Group 只允许 `r*`。

### 11.5 Group C：Context / Reference / Self-containment Review

只判断：

```text
incorrect_context_goal_inheritance
unresolved_reference
not_self_contained
```

Delta Schema：

```json
{
  "incorrect_context_goal_inheritance": ["r0"],
  "unresolved_reference": ["r1"],
  "not_self_contained": ["r1"]
}
```

该 Group 只允许 `r*`。

在单宠绑定场景下，`unresolved_reference` 仍必须保留。它不再表示“多宠物
语境下不知道选择哪只宠物”，而表示：

```text
request item 仍包含“它 / 这个 / 那个”等未消解指代；
或未根据可用上下文形成自包含主体表达。
```

### 11.6 合并后的完整 Delta 语义视图

系统将三个 Group 的输出确定性合并后，得到完整语义视图：

```json
{
  "missing_ellipsis_semantics": ["t0"],
  "unresolved_ellipsis": ["r1"],
  "over_specific_medical_question": ["r0"],
  "medical_conclusion_added": [],
  "unsupported_request": [],
  "incorrect_context_goal_inheritance": [],
  "unresolved_reference": [],
  "not_self_contained": []
}
```

实际稀疏输出省略空数组：

```json
{
  "missing_ellipsis_semantics": ["t0"],
  "over_specific_medical_question": ["r0"]
}
```

含义：

```text
字段出现 = 存在缺陷
数组元素 = 受影响 target
字段省略 = 无缺陷
```

### 11.7 维度定义

| 字段 | 含义 | 允许 target |
|---|---|---|
| `missing_ellipsis_semantics` | 当前 turn 存在省略请求但 request_items 未表达 | `t0` |
| `unresolved_ellipsis` | request item 的省略语义未完成消解 | `r*` |
| `over_specific_medical_question` | request item 过度推断具体医学问题 | `r*` |
| `medical_conclusion_added` | 请求被改写成医学结论 | `r*` |
| `unsupported_request` | request item 无当前输入或上下文支持 | `r*` |
| `incorrect_context_goal_inheritance` | 未正确继承上下文咨询目标 | `r*` |
| `unresolved_reference` | 输出仍包含未消解指代，或缺少自包含主体 | `r*` |
| `not_self_contained` | request item 不自包含 | `r*` |

### 11.8 Group 校验规则

每个 Group 必须独立校验：

```text
字段名属于该 Group allowlist
数组非空
数组元素唯一
target selector 存在
missing_ellipsis_semantics 只允许 t0
其他字段只允许 r*
不得输出其他 Group 的缺陷码
不得输出未知字段
不得输出 reason / confidence / corrected value
```

系统合并时：

```text
同 code 同 target 去重；
不同 code 同 target 聚合为同一缺陷簇；
不同 target 分别聚合；
t0 缺陷单独作为 turn-level defect。
```

### 11.9 示例

#### 陈述句过度推断

输入：

```text
今天狗吐了两次。
```

Generator 输出：

```text
r0 狗是否患有胰腺炎
```

Group A 输出：

```json
{
  "missing_ellipsis_semantics": ["t0"]
}
```

Group B 输出：

```json
{
  "over_specific_medical_question": ["r0"]
}
```

合并后：

```json
{
  "missing_ellipsis_semantics": ["t0"],
  "over_specific_medical_question": ["r0"]
}
```

修复后应得到：

```json
{
  "request_items": [
    "狗当前情况如何"
  ]
}
```

#### 指代未消解

输入：

```text
它会不会是感冒？
```

Generator 输出：

```text
r0 它是否患有感冒
```

Group C 输出：

```json
{
  "unresolved_reference": ["r0"],
  "not_self_contained": ["r0"]
}
```

如果上下文能总结“它”是狗，则修复为：

```text
狗是否患有感冒
```

如果没有显式主体投影且上下文不足以总结具体主体，则修复为：

```text
当前宠物是否患有感冒
```

若实现配置不允许泛化主体，则进入：

```text
subject_reference_context_insufficient
```

不得猜测物种、名字或品种。

#### 请求被结论化

输入：

```text
狗会不会是感冒？
```

Generator 输出：

```text
r0 狗患有感冒
```

Group B 输出：

```json
{
  "medical_conclusion_added": ["r0"]
}
```

## 12. Target 级有界局部修复

### 12.1 原则

```text
Review 只输出缺陷；
系统按 target 聚合缺陷簇；
Repair 只重写授权 target；
系统应用 patch 并重编号；
修复后必须重新审查。
```

禁止：

```text
Review 直接输出 corrected value
Repair 修改未授权 target
多个模型自由讨论
无限递归修复
任一缺陷默认重写整条 lane
失败后静默降级
```

`c*` 与 `r*` 只是在当前调用快照内定位缺陷的本地 selector，不是长期身份。
修复 join 后由系统重新编号，不维护旧 selector 到新 selector 的权威映射。

### 12.2 Defect Cluster Aggregation

缺陷码是观察结果，不是修复动作命令。单个 target 可能同时命中多个缺陷，
因此系统必须先按 target 聚合缺陷簇，再交给 Repair LLM 处理完整缺陷上下文。

控制协议示例：

```json
{
  "missing_control_protocol": ["c0"],
  "unsupported_control_protocol": ["c1"]
}
```

系统内部聚合为：

```json
{
  "repair_clusters": [
    {
      "target": "c0",
      "field": "answer_now",
      "defect_codes": ["missing_control_protocol"],
      "defect_phrases": ["控制协议缺失"]
    },
    {
      "target": "c1",
      "field": "correction",
      "defect_codes": ["unsupported_control_protocol"],
      "defect_phrases": ["控制协议不受支持"]
    }
  ]
}
```

该对象是系统内部执行状态，不是模型输出契约。

请求项示例：

```json
{
  "missing_ellipsis_semantics": ["t0"],
  "over_specific_medical_question": ["r0"],
  "medical_conclusion_added": ["r0"]
}
```

系统聚合为：

```text
turn-level defect:
  t0 省略请求语义缺失

item-level defect cluster:
  r0:
    医学问题过度具体
    添加医学结论
```

不得按缺陷码分别执行“删除、替换、追加”等动作。完整缺陷簇必须交给同一个
RequestItemsTargetedRepair 调用处理。

### 12.3 缺陷码到中文短语映射

系统只做稳定缺陷码到固定中文短语的映射：

```text
missing_control_protocol              控制协议缺失
unsupported_control_protocol          控制协议不受支持
ambiguous_control_protocol            控制协议语义不明

missing_ellipsis_semantics            省略请求语义缺失
over_specific_medical_question        医学问题过度具体
medical_conclusion_added              添加医学结论
incorrect_context_goal_inheritance    上下文请求目标继承错误
unresolved_reference                  指代未消解
unresolved_ellipsis                   省略未消解
not_self_contained                    请求项不自包含
unsupported_request                   请求不受支持
```

缺陷解释由人工维护在 Repair prompt 中，并且只能描述现象，不得规定修复动作。

允许：

```text
医学问题过度具体：
  当前输入或上下文仅支持较保守的请求，
  不足以支持具体疾病、治疗或急诊判断请求。
```

禁止：

```text
医学问题过度具体：改成“狗当前情况如何”
unsupported_request：删除该请求
missing_control_protocol：设置为 true
```

修复动作由系统 patch 语义决定，不由缺陷码解释决定。

### 12.4 Repair Router

#### 无缺陷

```text
Review Delta 为空
→ lane result ready
```

#### 可局部修复

控制协议：

```text
missing_control_protocol
unsupported_control_protocol
```

请求项：

```text
over_specific_medical_question
medical_conclusion_added
incorrect_context_goal_inheritance
unresolved_reference
unresolved_ellipsis
not_self_contained
unsupported_request
missing_ellipsis_semantics
```

#### 不可猜测修复

以下情况不得交给 Repair LLM 猜测：

```text
ambiguous_control_protocol
模型可见上下文不足以总结主体且实现不允许使用“当前宠物”
授权上下文不足以继承请求目标
clarification_request 无可解释对象
correction 无可修正上下文对象
answer_now 无活跃咨询目标或请求内容
```

应进入：

```text
control_protocol_ambiguous
control_protocol_context_invalid
subject_reference_context_insufficient
request_context_insufficient
intent_merge_conflict
```

#### 系统性失败

以下情况不使用默认局部修复：

```text
Generator schema invalid
Review schema invalid
缺陷比例极高
request_items 数量超限
输入过长
```

初始实现应显式失败。若未来引入整 lane regenerate，必须作为部署或配置级
受控修复策略进入审计，不得成为单次请求内的静默 fallback。

### 12.5 ControlProtocolTargetedRepair

#### 职责

ControlProtocolTargetedRepair 只处理系统授权的控制协议字段：

```text
c0 answer_now
c1 correction
c2 clarification_request
```

它根据当前输入、授权意图上下文、当前控制协议结果和完整缺陷簇重新判断
目标字段值。

它不处理：

```text
request_items
患者事实
临床安全
跨 lane merge conflict
```

#### 输入形态

示例：

```text
<original_input>
先直接告诉我严重吗，不要再问了。
</original_input>

<current_control_protocol>
c0 answer_now=false
c1 correction=false
c2 clarification_request=false
</current_control_protocol>

<repair_target>
c0 answer_now
</repair_target>

<semantic_defects>
控制协议缺失
</semantic_defects>
```

#### 输出契约

输出是与 Generator 同构的稀疏 boolean delta：

```json
{
  "answer_now": true
}
```

多个授权 target 可在同一输出中出现：

```json
{
  "answer_now": true,
  "correction": false
}
```

不输出：

```json
{
  "target": "c0",
  "operation": "set_true",
  "reason": "...",
  "confidence": 0.9
}
```

#### 输出校验

```text
字段属于 answer_now / correction / clarification_request allowlist
字段属于授权修复 targets
值必须是 boolean
输出不得为空
不得输出未知字段
不得输出 reason / confidence / target / operation
```

若输出与当前值完全相同：

```text
→ control_protocol_repair_no_change
```

若输出未授权字段：

```text
→ control_protocol_repair_unauthorized_target
```

#### Patch 语义

系统保留未授权字段，仅用授权字段输出覆盖原值。模型不指定 operation。

### 12.6 RequestItemsTargetedRepair

#### 职责

RequestItemsTargetedRepair 只处理受影响 request items，不重写完整
request lane。

输入包含：

```text
当前用户输入
授权上下文目标
待澄清问题
可用主体总结上下文或可选主体投影
当前 request_items with r*
repair_targets
item-level defect clusters
turn-level missing defect
```

未受影响的 request item 必须原样保留。

#### 修复目标集合

系统将所有出现在 item-level Review 缺陷中的 `r*` 作为修复目标集合。例如：

```json
{
  "over_specific_medical_question": ["r0"],
  "unsupported_request": ["r2"]
}
```

修复目标为：

```text
r0, r2
```

初始实现建议在一次 RequestItemsTargetedRepair 调用中处理全部受影响
targets，避免多个 patch 互相冲突。

#### 输入形态

```text
<original_input>
今天狗吐了两次。
</original_input>

<intent_context>
无活跃咨询目标
</intent_context>

<current_request_items>
r0 狗是否患有胰腺炎
</current_request_items>

<repair_targets>
r0
</repair_targets>

<item_defects>
r0：医学问题过度具体
</item_defects>

<turn_level_defects>
省略请求语义缺失
</turn_level_defects>
```

#### 输出契约

输出为薄 schema：

```json
{
  "request_items": [
    "狗当前情况如何"
  ]
}
```

该输出表示：

```text
受影响 request items 的替换候选集合
```

不是完整 `request_items`。

不输出：

```json
{
  "target": "r0",
  "operation": "replace",
  "reason": "...",
  "confidence": 0.9
}
```

初始实现规定：

```text
request_items 必须非空
不支持通过空数组删除请求项
```

如果某条请求不受支持，应替换为被当前输入或授权上下文支持的请求，而不是
删除后让最终结果为空。

#### 输出校验

```text
顶层唯一字段 request_items
request_items 是字符串数组且非空
每项非空、单行、长度受限
数量不超过剩余可用位置
不得输出未知字段
不得输出 reason / confidence / target / operation
```

若修复输出与受影响目标完全相同：

```text
→ request_items_repair_no_change
```

若输出数量会导致 join 后超过 `maxItems=3`：

```text
→ request_items_join_count_exceeded
```

### 12.7 Deterministic Patch / Join / Renumber

#### 控制协议 patch

```text
未授权字段保持不变；
授权字段由 repair 输出覆盖；
系统最终归一化为完整 boolean。
```

#### 请求项 replace affected

如果存在受影响 `r*`：

```text
1. 保留未受影响 request items
2. 删除受影响 r*
3. 将 repair 输出插入原受影响项的第一个位置
4. 去除精确重复
5. 重新编号 r0/r1/r2
6. 校验最终数量仍为 1..3
```

示例：

```text
修复前：
r0 狗是否患有胰腺炎
r1 狗是否需要送医

受影响：
r0

修复输出：
["狗当前情况如何"]

修复后：
r0 狗当前情况如何
r1 狗是否需要送医
```

`r1` 必须原样保留。

#### missing-only

如果只有：

```json
{
  "missing_ellipsis_semantics": ["t0"]
}
```

且没有 item-level defect，修复输出作为追加项：

```text
修复前：
r0 狗是否需要送医

修复输出：
["狗当前情况如何"]

修复后：
r0 狗是否需要送医
r1 狗当前情况如何
```

追加后超过三条时不得截断，应进入：

```text
request_items_join_count_exceeded
```

#### missing 与 item defect 同时存在

当 `missing_ellipsis_semantics` 与 item-level defect 同时出现时，turn 级
缺陷附加到受影响 `r*` 的同一个修复任务中：

```text
修复输出替换受影响 request items，
并应同时补足省略请求语义。
```

不应先替换受影响项，再单独追加一条默认请求，避免产生重复请求。

#### 事务式应用

一轮 repair 中收集全部授权输出并先完整校验。任一输出无效时不提交部分
patch，而进入对应显式失败状态。

### 12.8 修复预算与修复后重审

生产配置应固定：

```text
每条 lane 最多修复 1 次
```

一轮内可以处理多个 target，但不得对同一 target 循环修复。

Patch 后必须重新执行对应 lane 的 Review：

```text
ControlProtocolTargetedRepair 后执行 ControlProtocolReview
RequestItemsTargetedRepair 后执行全部 grouped RequestItemsReview
```

重审至少确认：

```text
原缺陷是否消除
是否引入新的 unsupported 结果
是否仍不self-contained
是否仍存在未消解指代或省略
是否仍添加医学结论
是否仍过度推断具体医学问题
是否正确继承上下文目标
```

如果重审后仍有缺陷：

```text
不再继续修复；
进入 repair_failed 或 repair_budget_exceeded。
```

## 13. Deterministic Intent Merge

系统只在两条 lane 均完成 Generator、Review、局部 Repair 和修复后重审后，
才合并结果并执行一致性检查。

### 13.1 合并规则

```text
control_protocol 由系统补全为完整 boolean。
request_items 保持自然语言数组。
review 结果转为执行状态。
修复历史进入执行审计。
```

### 13.2 一致性检查

至少检查：

```text
clarification_request=true 但 request_items 无可解释对象
answer_now=true 但当前无活跃咨询目标或请求内容
correction=true 但上下文不存在可修正对象
request_items 与控制协议重复表达同一协议行为
request item 使用未授权主体
request item 主体不明
```

这些不一致不能由模型自行调和，应进入：

```text
可确定性映射回单条 lane 已审查缺陷的 target repair
intent_merge_conflict
explicit partial / failed
```

跨 lane 修复边界：

```text
不允许一个合并修复模型同时读取并改写 control_protocol 与 request_items。
```

如果系统能把 merge 冲突确定性映射为某条 lane 的既有缺陷，可以回到该 lane
的 Targeted Repair；否则必须显式进入 `intent_merge_conflict`、partial 或
failed，不得让模型自由调和。

## 14. TurnIntentEnvelope

最终输出：

```json
{
  "status": "completed",
  "control_protocol": {
    "answer_now": true,
    "correction": false,
    "clarification_request": false
  },
  "request_items": [
    "狗是否患有感冒",
    "狗是否需要送医"
  ]
}
```

修正语境：

```json
{
  "status": "completed",
  "control_protocol": {
    "answer_now": false,
    "correction": true,
    "clarification_request": false
  },
  "request_items": [
    "狗当前情况如何"
  ]
}
```

核心契约不包含：

```text
request_present
question_present
wants_triage
fact_statement_present
report_context_present
intent_type
reason
confidence
source_ref
source_span
patient_facts
```

下游只消费：

```text
status
control_protocol
request_items
```

## 15. 状态与失败治理

### 15.1 顶层状态

```text
completed
completed_with_control_ambiguity
partial
failed
```

### 15.2 控制协议状态

```text
control_protocol_generation_failed
control_protocol_review_failed
control_protocol_schema_invalid
control_protocol_review_conflict
control_protocol_ambiguous
control_protocol_repair_schema_invalid
control_protocol_repair_no_change
control_protocol_repair_unauthorized_target
control_protocol_repair_failed
control_protocol_context_invalid
```

### 15.3 Request 状态

```text
request_items_generation_failed
request_items_review_failed
request_review_group_schema_invalid
request_items_schema_invalid
request_items_empty
request_items_count_exceeded
request_resolution_failed
request_context_insufficient
subject_reference_context_insufficient
request_review_failed
request_items_repair_schema_invalid
request_items_repair_no_change
request_items_repair_unauthorized_target
request_items_join_count_exceeded
request_items_repair_failed
```

### 15.4 合并状态

```text
intent_merge_conflict
repair_budget_exceeded
intent_repair_budget_exceeded
```

### 15.5 底线

局部修复状态含义：

```text
repair_schema_invalid：
  Repair 输出未通过 strict schema 校验。

repair_no_change：
  Repair 输出与原受影响 target 完全相同，未产生有效变化。

repair_unauthorized_target：
  Repair 输出试图修改未授权字段或请求项。

join_count_exceeded：
  patch 后 request_items 数量超过 maxItems。

repair_failed：
  局部 patch 后重审仍存在缺陷。
```

```text
request_items=[] 不是成功状态。
失败时 request_items 应缺失或为 null，而不是空数组。
partial 必须显式标明哪条 lane 失败。
不得静默丢弃控制协议或请求内容。
不得失败后自动 fallback 旧意图抽取器。
```

## 16. 执行审计

虽然核心语义契约不携带工程身份，系统必须在外层记录：

```text
turn_id
input_digest
pipeline_version
prompt_version
model_version
run_id
review_status
repair_count
failure_reason
base control_protocol
base request_items
Review Delta
defect clusters
repair target
repair input digest
repair output
patch / join 后结果
post-review Delta
```

示例：

```json
{
  "execution_audit": {
    "turn_id": "turn_123",
    "input_digest": "sha256:...",
    "pipeline_version": "turn-intent-v1",
    "prompt_versions": {
      "control_protocol_generator": "...",
      "request_items_generator": "...",
      "control_protocol_review": "...",
      "request_completeness_review": "...",
      "request_fidelity_review": "...",
      "request_context_reference_review": "...",
      "control_protocol_targeted_repair": "...",
      "request_items_targeted_repair": "..."
    },
    "repair_count": 1,
    "repair_targets": {
      "control_protocol": ["c0"],
      "request_items": ["r0"]
    },
    "review_status": "passed_after_repair"
  }
}
```

审计 metadata 不进入模型 prompt，也不得伪装成语义结果。

## 17. 实施注意项

### 17.1 Prompt 独立版本化

至少独立维护：

```text
control_protocol_generator prompt version
request_items_generator prompt version
control_protocol_review prompt version
request_completeness_review prompt version
request_fidelity_review prompt version
request_context_reference_review prompt version
control_protocol_targeted_repair prompt version
request_items_targeted_repair prompt version
```

Prompt 语义变化必须触发契约测试。

### 17.2 Strict JSON Schema

所有模型输出必须 strict schema 校验。

禁止：

```text
宽松 JSON 修复
自动补字段
忽略未知字段
失败转空结果
```

### 17.3 数量与长度限制

必须固定：

```text
request_items minItems=1
request_items maxItems=3
单条 request item 最大长度
Review target 数量上限
repair target 数量上限
修复次数上限
```

超限显式进入：

```text
request_items_count_exceeded
repair_budget_exceeded
```

### 17.4 上下文最小化

不得为了省略推断而向模型暴露完整历史。只暴露当前活跃请求目标和最小主体
上下文。

### 17.5 不做医学判断

意图层只表达用户请求内容，不生成：

```text
诊断
风险结论
急诊结论
治疗建议
用药建议
```

临床安全链路独立。

### 17.6 缺陷解释不得包含修复动作

Repair prompt 中的缺陷码解释只能描述现象。不得写成：

```text
控制协议缺失：设置为 true
unsupported_request：删除该请求
over_specific_medical_question：改成当前情况如何
```

具体修复结果由 Repair LLM 根据原始输入、授权上下文和完整缺陷簇生成；
patch 语义由系统决定。

### 17.7 局部保留原则

Patch 应用时必须满足：

```text
未受影响控制协议字段保持不变
未受影响 request_items 原样保留
不得静默截断 request_items
不得通过空数组删除请求项
不得维护 c*/r* 长期身份
```

### 17.8 LangGraph 固定拓扑

如使用 LangGraph，只能映射为固定节点：

```text
turn_intent_context_projector

control_protocol_generator
→ control_protocol_schema_validator
→ control_protocol_review
→ control_protocol_defect_cluster_aggregator
→ control_protocol_repair_router
   ├─ no defect → control_protocol_ready
   ├─ repairable → control_protocol_targeted_repair
   │              → control_protocol_repair_validation
   │              → control_protocol_patch
   │              → control_protocol_post_review
   └─ unrepairable → explicit fail

request_items_generator
→ request_items_schema_validator
→ parallel grouped request review
   ├─ request_completeness_review
   ├─ request_fidelity_review
   └─ request_context_reference_review
→ request_review_delta_merge
→ request_items_defect_cluster_aggregator
→ request_items_repair_router
   ├─ no defect → request_items_ready
   ├─ repairable → request_items_targeted_repair
   │              → request_items_repair_validation
   │              → request_items_join_and_renumber
   │              → request_items_post_review
   └─ unrepairable → explicit fail

→ deterministic_intent_merge
→ turn_intent_envelope
```

必须保持：

```text
固定图拓扑
模型不创建节点
模型不创建任务类型
模型不直接修改状态
系统应用 patch
修复次数有界
Fail Fast
```

## 18. 最小验收样本

至少覆盖：

```text
显式单意图：
  狗会不会是感冒？

显式多意图：
  狗会不会是感冒？要不要送医院？

陈述句省略请求：
  今天狗吐了两次。

上下文省略继承：
  当前目标为狗软便处理，用户输入“两天了”。

事实修正：
  狗不是呕吐，是干呕。

answer_now：
  先直接告诉我严重吗。

clarification_request：
  你刚才说的软便是什么意思？

指代可消解：
  它会不会是感冒？

无显式主体投影且上下文不足以总结主体：
  使用“当前宠物”或进入 subject_reference_context_insufficient。

过度推断：
  呕吐输入被改写为“是否患有胰腺炎”。

医学结论化：
  “是否感冒”被改写为“患有感冒”。

request_items 空数组。

request_items 数量超限。

JSON schema 非法。

Review 输出非法 target。

Review Group 输出其他 Group 缺陷码。

多个 Review Group 对同一 request item 输出缺陷但未聚合为同一缺陷簇。

局部修复：r0 过度推断但 r1 正确，修复后 r1 必须保持不变。

局部修复：missing + over-specific，一次替换 r0 并补足省略请求。

局部修复：missing-only，输出作为追加项且不超出 maxItems。

局部修复：上下文目标继承错误，根据 active_request_context 修复。

局部修复：可消解指代，根据最小主体上下文修复。

局部修复：不可消解指代，不调用 Repair 猜测。

局部修复：control protocol missing / unsupported 后仅授权字段变化。

修复输出未知字段。

修复输出未授权 target。

修复输出与原目标相同。

patch 后 request_items 超过三条。

修复后 Review 仍存在缺陷。

一条 lane 成功、另一条 lane 失败的 partial 状态。
```

## 19. 防偏移清单

实现和 code review 时必须检查：

```text
是否只保留 answer_now / correction / clarification_request 控制协议
是否新增 wants_triage / wants_diagnosis 等意图枚举
是否恢复 request_present / question_present
是否建立独立 UserRequestIntent 真相源
是否让 request_items 为空数组表示成功
是否在 request item 前加“用户询问”
是否将 request item 写成医学结论
是否将控制协议识别与请求内容重写混入同一模型调用
是否让 RequestItemsGenerator 读取 ControlProtocolGenerator 结果
是否让 ControlProtocolGenerator 读取 request_items
是否让 Review 输出 corrected value / reason / confidence
是否使用单体全维度 RequestItemsReview
是否让 Review Group 输出其他 Group 缺陷码
是否用 LLM 判断空数组或 schema 非法
是否让 Review 输出 target 数组以外的复杂结构
是否仍默认任一缺陷触发整 lane 全局重写
是否按 target 聚合缺陷簇
是否让缺陷码直接映射修复动作
是否让 Repair 输出 reason / confidence / target / operation
是否让 Repair 修复未授权 target
是否让 ControlProtocolTargetedRepair 读取 request_items
是否让 RequestItemsTargetedRepair 读取 control_protocol
是否让 merge 阶段模型自由调和跨 lane 冲突
是否维护 c*/r* 长期身份
是否在 patch 时静默删除或截断 request_items
是否因单宠绑定移除 unresolved_reference
是否让模型猜测物种、名字或品种
是否把 subject_ambiguous 用于当前单宠主路径
是否修复后未重新 Review
是否允许无限递归修复
是否将 correction 直接应用于患者事实
是否在本层做诊断、安全裁决或治疗建议
是否读取未授权历史上下文
是否失败后自动 fallback 旧意图抽取器
是否把工程 metadata 暴露给模型
```

任一项不满足，应阻断实现合并。

## 20. 与患者事实层的未来合并点

患者事实层不在本文范围内。未来设计应通过外层合并：

```text
TurnIntentEnvelope
PatientSemanticEnvelope
→ TurnSemanticEnvelope
```

合并时必须保持：

```text
intent 层不抽取患者事实
事实层不重新解释用户意图
correction 只作为事实治理输入信号
request_items 不进入 Coverage 患者事实集合
临床安全链路独立消费授权输入
```

## 21. 变更原则

本文只应因以下原因修订：

```text
控制协议字段调整
request_items 契约调整
省略推断或上下文继承边界调整
Review Delta 调整
修复策略调整
失败状态或 Fail Fast 底线调整
与患者事实层合并边界调整
```

不得把临时 prompt 问题、单个模型缺陷、局部工程实现细节或无人消费字段
直接加入本文。
