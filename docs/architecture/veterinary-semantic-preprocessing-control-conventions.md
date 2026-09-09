<!--
=============================================================================
文件: docs/architecture/veterinary-semantic-preprocessing-control-conventions.md
作用: 统一语义预处理各子层的 Review 分组、Delta 合并、缺陷簇、局部修复、
      join / renumber、selector namespace、来源元数据边界与单宠主体绑定
      控制策略。
范围: 覆盖 Turn Intent、Semantic Anchor、Joint Context Scope 与
      Contextual Query Proposition 各层共同遵守的控制协议。
说明: 本文只定义跨层控制约定，不定义各层具体语义对象、缺陷码 allowlist、
      JSON 契约或 prompt 文案。各子层文档仍是自身 schema 的权威。
维护: 当跨层 Review 分组、Delta 合并、缺陷簇、局部修复、join / renumber
      或主体绑定策略调整时，必须先修订本文，再同步子层文档。
=============================================================================
-->

# 兽医语义预处理跨层控制约定

> **文档状态**：架构草案基线
>
> **文档定位**：本文位于语义分解 charter 之下，作为意图层、anchor 层、
> Scope 层与 Contextual Query Proposition 层的共享控制约定。它不是新的
> 语义真相源，也不替代各子层架构文档。
> 其工程实现边界由
> [veterinary-core-infrastructure-architecture.md](./veterinary-core-infrastructure-architecture.md)
> 定义。

## 1. 核心结论

语义预处理各子层虽然语义对象不同，但必须共享同一套控制协议：

```text
Generator 输出薄 schema
→ 系统严格校验并分配本地 selector
→ Review 按缺陷组并行执行
→ 每组输出稀疏 Delta
→ 系统确定性合并 Delta
→ 按 target 聚合缺陷簇
→ Targeted Repair 只处理授权 target
→ 所有修复结果事务式 join 回原始快照
→ canonicalize / dedupe / renumber
→ 修复后重新 Review
→ 显式成功或失败
```

统一该协议的原因是：

```text
避免单体全维度 Review 导致注意力分散；
避免各子层分别演化出不同的修复语义；
避免缺陷码被误用为修复动作；
避免局部修复退化为全局重写；
避免 selector 被误认为长期身份；
避免来源治理元数据混入语义文本；
避免主体绑定在多个子层重复或互相矛盾。
```

## 2. Grouped Review 原则

当一个 Review 需要同时判断多个缺陷维度时，不得默认使用单体全维度调用。
系统应将相关缺陷维度分成有限组，每组由一个独立 Review LLM 处理。

推荐每组：

```text
2-4 个相关缺陷维度；
一个明确 allowlist；
一种相对集中的认知任务；
```

不建议：

```text
一次 Review 判断全部缺陷维度；
一个缺陷维度一个 LLM 调用；
不同 Review Group 输出彼此领域的缺陷码；
Review 输出 reason / confidence / corrected value。
```

### 2.1 Grouped Review 输出

每个 Review Group 仍输出稀疏数组：

```json
{
  "temporal_error": ["a2"],
  "quantity_or_unit_error": ["a2"]
}
```

系统校验：

```text
字段名属于该 Group 的 allowlist；
数组非空；
数组元素唯一；
target selector 存在；
target 类型符合字段要求；
不得输出其他 Group 的缺陷码；
不得输出未知字段；
不得输出空数组作为默认值。
```

跨 Group 输出未授权缺陷码时，进入：

```text
review_group_schema_invalid
```

不得由系统静默转移到其他 Group。

## 3. Review Delta Merge

各 Review Group 输出后，系统执行确定性合并：

```text
同 code 同 target 去重；
不同 code 同 target 保留为完整缺陷簇；
不同 target 分别聚合；
t0 / missing 类缺陷单独聚合；
每组输出保留执行审计。
```

多个缺陷码可以在同一 target 上同时成立。它们默认不是冲突，必须交给完整
缺陷簇处理。

若出现确定性逻辑冲突，例如同一控制协议字段同时被标记为 missing 与
unsupported，或 ambiguous 与 missing / unsupported 同时出现，应进入显式
Review 冲突状态，不得交给 Repair LLM 自行调和。

## 4. Defect Cluster 与局部修复

缺陷码是观察结果，不是修复动作命令。

系统必须按 target 聚合缺陷簇：

```text
一个 target = 一个 defect cluster
一个 defect cluster = 一次 cluster-level repair
```

不得按缺陷码分别执行：

```text
删除
追加
缩窄
扩展
替换
```

Repair prompt 中的缺陷解释只能描述现象，不得规定动作。例如可以写：

```text
高维语义关系丢失：
  当前输出可能丢失比较基线、时间基准、数量单位、事件顺序或相对对象。
```

不得写：

```text
高维语义关系丢失：补充比较关系
```

Targeted Repair 只接收：

```text
原始输入或授权上下文；
当前快照；
系统指定 target；
完整缺陷簇的固定中文短语；
人工维护的缺陷解释；
```

Repair 输出仍保持该子层 Generator 的薄 schema。系统决定 patch 语义、授权
范围、修复预算和最终状态。

## 5. 原始快照、事务式 Join 与重编号

同一轮多个修复任务必须基于同一份原始快照执行。修复任务不得消费彼此输出，
也不得在修复过程中即时重编号。

系统收集全部修复输出后：

```text
先统一验证；
任一输出无效则不提交 join；
全部有效后统一应用；
再 canonicalize / exact dedupe；
按确定性顺序重编号；
重新执行 grouped review。
```

本地 selector 例如：

```text
p* / r* / a* / s* / q*
```

只是当前快照中的定位符，不是长期身份。系统不维护旧 selector 到新 selector
的权威映射。执行审计保存原始快照、Review Delta、缺陷簇、修复输出和最终
快照即可。

## 6. Selector namespace 与来源元数据

语义预处理各子层可使用本地 selector namespace：

```text
a*：当前回合 semantic anchors
p*：只读可信宠物事实投影
s*：系统物化后的 joint context scope
q*：contextual query proposition
```

其中：

```text
a* / p* 的 namespace 足以让系统确定性推导来源分区；
p* 不是 source_ref；
p* 不是长期身份；
s* / q* 也不是长期身份。
```

Scope 层输出统一为：

```json
{
  "joint_context_scopes": [
    "a0+a1",
    "a1+p1"
  ]
}
```

不得拆成：

```text
turn_joint_context_scopes
cross_source_scopes
```

也不得让模型输出：

```text
scope_id
current_anchor_refs
trusted_fact_refs
来源分区
关系类型
```

系统可从 selector namespace 推导来源分区，并绑定到执行审计或外层
metadata。

### 6.1 proposition 文本不携带来源元数据

Contextual query proposition 的自然语言文本只表达患者语义。

应保留：

```text
历史
当前
近期
平时基线
用药前
用药后
事件顺序
比较方向
医生认为
主人感觉
```

不应写入：

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

“历史”是时间语义；
“可信历史记录”是来源治理元数据。

来源、权威性和派生关系由系统外层绑定：

```text
origin_scope_refs
current_anchor_refs
trusted_fact_refs
derivation_kind
```

这些字段不由模型输出，也不进入 RAG 查询语义文本。

## 7. 单宠绑定与 unresolved reference

当前业务前提是：

```text
一次会话只绑定一个宠物；
BFF 向后端传递该会话的宠物绑定参数；
后端系统负责把结果绑定到该 pet identity；
```

这表示当前主路径不存在“在多只宠物之间选择一只”的语义消解问题。

但必须区分三层概念：

```text
1. 系统级 pet identity 绑定
2. 模型可见 subject projection
3. 输出短句中的自包含主体表达
```

单宠绑定只解决第一层，不自动保证后两层。

### 7.1 模型可见主体投影

模型可能看到显式主体投影：

```text
当前会话唯一讨论主体为猫。
```

也可能只看到当前输入和最小授权上下文，需要自行总结主体。

系统级 pet identity、内部 ID、档案主键和工程 metadata 不进入模型 prompt。
模型只可见安全的最小主体投影或授权上下文。

如果存在显式主体投影，Generator / Repair 必须使用它。如果不存在，模型只能
从授权上下文总结，不得猜测物种、名字或品种。

当上下文不足以总结具体主体时，可以使用：

```text
当前宠物
```

作为自包含主体表达。若实现配置不允许泛化主体，则进入：

```text
subject_reference_context_insufficient
```

### 7.2 unresolved_reference 仍必须保留

`unresolved_reference` 不因单宠绑定而移除。

在当前单宠主路径中，它的含义是：

```text
输出项仍包含“它 / 这个 / 那个”等未消解指代；
或未根据可用上下文形成自包含主体表达。
```

它不再表示：

```text
多宠物语境下不知道选择哪只宠物。
```

因此：

```text
subject_error：
  输出主体与可用主体投影或授权上下文不一致。

unresolved_reference：
  输出仍保留未消解指代，或缺少自包含主体。

subject_binding_missing：
  系统输入契约缺少会话宠物绑定，应在进入 LLM 前确定性失败。

subject_reference_context_insufficient：
  系统绑定存在，但模型可见上下文不足且不允许使用“当前宠物”。
```

`subject_ambiguous` 只能作为未来多宠物扩展状态，不得出现在当前单宠主路径。

### 7.3 提及其他宠物

单宠绑定不禁止用户在文本中提及其他动物，但其他动物不能取代当前绑定主体。

若其他动物只是背景，例如：

```text
它和另一只猫分开吃之后软便好了一点。
```

当前患者主体仍是绑定宠物，可输出：

```text
当前宠物与另一只猫分开进食后软便有所好转。
```

若用户请求针对另一只宠物，应进入：

```text
out_of_scope_subject
subject_context_mismatch
```

或由外层路由新开会话，不得由 LLM 在当前会话中重新绑定主体。

## 8. 子层职责边界

各子层对主体的职责是：

```text
Turn Intent / Semantic Anchor：
  生成自包含主体表达；
  Review 保留 unresolved_reference；
  Repair 只做局部主体改写。

Joint Context Scope：
  消费已完成主体表达的 a* 与只读可信事实 p*；
  不重新消解主体；
  不输出主体歧义状态。

Contextual Query Proposition：
  继承 anchors / scope 的主体；
  不重新总结主体；
  subject_error 仅表示与上游主体不一致。
  proposition 文本不得携带来源治理元数据。
```

## 9. 防偏移清单

实现与 code review 时必须检查：

```text
是否使用单体全维度 Review；
Review Group 是否输出跨组缺陷码；
Delta 是否确定性合并；
是否按 target 聚合缺陷簇；
缺陷码解释是否包含修复动作；
Repair 是否修改未授权 target；
修复是否基于同一原始快照；
是否在全部修复输出前局部提交；
是否维护 selector 长期身份；
是否把 p* 当作 source_ref；
是否拆分 turn scope 与 cross-source scope 输出；
是否让模型输出来源分区对象；
是否在 proposition 文本中写入“可信 / 档案 / 已确认记忆”；
是否因单宠绑定移除 unresolved_reference；
是否让模型猜测物种、名字或多宠物主体；
是否把系统 pet identity 暴露为模型输入；
是否把提及其他宠物误解为切换当前患者主体。
```

任一项不满足，应阻断实现合并。
