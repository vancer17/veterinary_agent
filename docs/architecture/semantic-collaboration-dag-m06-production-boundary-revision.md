<!--
=============================================================================
文件: semantic-collaboration-dag-m06-production-boundary-revision.md
作用: 记录受限语义协作 DAG M06 生产边界从多 lane 结构化 schema 收敛为
      Turn Intent + 自然语言 Claim Proposition Inventory 的设计决策。
范围: 覆盖 M06 输出契约、prompt 形态、evidence 自证移除、Review 布尔矩阵、
      deferred lane、人工审查过渡和实现防漂移原则。
说明: 本文是生产实现前的边界修订记录，不是新的架构实验计划，也不改变
      Temporal-first durable execution 与领域隔离边界。
维护: 当 M06 输出契约、Review 矩阵、evidence 门禁或 deferred lane 启用条件调整时，
      必须同步更新本文和生产架构 / 实施计划。
=============================================================================
-->

# 受限语义协作 DAG M06 生产边界修订记录

> **文档状态**：生产边界修订已由 M06 实现落地；实现状态见
> [semantic-collaboration-dag-m06-generation-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-generation-skill-change-summary.md)
>
> **权威关系**：本文细化
> [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
> 与
> [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
> 中的 M06 / M08 生产契约。若后续实现与本文冲突，应先修订架构基线，不得在代码中保留双权威解释。

## 1. 修订结论

早期 M06 设计要求多个生成 SKILL 分别输出：

```text
intent
claim inventory
statement semantics
participant phrase
temporal phrase
measurement phrase
canonical descriptor
```

并同时携带：

```text
claim binding
evidence phrase
结构化 enum
权威 phrase / descriptor
```

V8～V14 的现象说明，高负载 schema、模型自证和字段对齐会带来：

```text
字段交替缺失
field false alignment
inventory / claim 对应关系漂移
模型为满足 schema 编造可兼容字段
```

因此 M06 生产边界收敛为：

```text
Turn Intent Generator
Claim Proposition Inventory Generator
```

核心形态：

```text
输入：结构化 tag / 极浅文本
传输：strict JSON Schema
语义：自包含自然语言 proposition
审查：Coverage Review + Faithfulness Review
证据：后置绑定，不由生成器自证
```

这不是回到宽松文本解析，也不是新的架构实验；M05 strict JSON Gateway 仍是唯一结构化
模型调用边界。

## 2. 稳定输出契约

### 2.1 Turn Intent

Turn Intent 输出 fixed-field boolean：

```json
{
  "answer_now": true,
  "wants_triage": false,
  "correction": false,
  "clarification_request": false,
  "fact_statement_present": true,
  "question_present": true,
  "report_context_present": false
}
```

禁止输出：

```text
evidence
reason
confidence
claim
医学事实
```

### 2.2 Claim Proposition Inventory

Claim Inventory 输出自包含自然语言 proposition：

```json
{
  "claims": [
    "英短前天开始更换新猫粮",
    "英短这两天大便偏软",
    "英短精神状态良好",
    "英短进食正常",
    "英短饮水正常",
    "英短没有呕吐",
    "英短大便没有血"
  ]
}
```

不得输出主题词：

```text
呕吐
血便
精神状态
食欲
饮水
```

不得输出无人消费字段：

```text
claim_id
ordinal
target
unit_type
shared_parent
evidence_phrase
assertion_state
certainty
scope
entity_id
canonical_id
reason
confidence
```

工程身份由系统从 PlanTask、attempt、TurnSnapshot digest 和 SkillCatalog schema 附加。

## 3. 自然语言 proposition 原则

每条 claim 必须满足：

```text
自包含
一个 proposition
有主体和断言
主语义是当前宠物、宠物状态、宠物行为或宠物相关事件
保留否定、否定范围和纠正语义
保留不确定、未观察和可能因果
保留时间、频率、数量、程度和比较基线
不包含诊断、风险、就医建议或治疗建议
```

Claim proposition 是对象层语义，不是“用户报告行为”的元命题。禁止把
`用户报告`、`用户认为`、`用户询问` 作为 claim 主语义。来源、说话人和观察方式
由系统 metadata、审查状态与后续投影承载，不进入 RAG 消费的主 proposition。

示例：

```text
饭和水都正常
```

必须拆为：

```text
英短进食正常
英短饮水正常
```

以下表达必须区分：

```text
英短没有呕吐
未观察到英短呕吐
更换猫粮可能与英短软便有关
英短当前没有呕吐（纠正此前信息）
```

不得改写为：

```text
用户报告英短精神异常
我家英短绝对没有呕吐
换粮导致软便
```

指代不明时保留保守表达，交给 Faithfulness Review 标记 `指代对象不明`。

## 4. Prompt 边界

Prompt user message 使用结构化 tag 或极浅文本：

```text
<current_turn>
...
</current_turn>

<trusted_pet_context>
species: cat
description: 英短
</trusted_pet_context>
```

禁止向模型展示：

```text
task_id
run_id
attempt_number
snapshot_digest
skill_id / skill_version
完整 JSON schema
owned_fields / forbidden_fields
任何 claim 数量或 envelope 数量
```

要求：

1. `prompt_version` 独立版本化。
2. 原文必须完整保留，不得摘要或截断。
3. renderer 必须处理 tag delimiter collision。
4. 上下文只来自 TurnSnapshotProjector 授权资源。
5. prompt 语义变化必须更新 renderer version 和契约测试。

## 5. Evidence 自证移除

生成器和 Faithfulness Review 均不输出：

```text
evidence phrase
quote
offset
reason
confidence
self-justification
```

原因：

1. 字面 quote 不能证明语义正确。
2. 中文省略、指代和 shared scope 使 quote-only 证据不可靠。
3. 复杂自证 schema 会分散模型注意力并加剧幻觉。
4. 语义忠实性应由独立 Review 判断。

Evidence binding 后置为独立状态：

```text
clarification_required
repair_then_clarification_required
evidence_binding_pending
human_review_required
verified
```

当前允许人工审查作为过渡，但必须记录：

```text
review_mode=human
proposal id / claim id
supported / rejected / ambiguous
reviewer role
review time
decision digest
```

人工通过不得伪装成自动 verified。

## 6. Review 契约

### 6.1 Coverage Review

Coverage Review 是 turn 级任务，用于发现：

```text
漏抽显式事实
多事实合并
重复 claim
原文不支持的 claim
非自包含 proposition
shared scope 拆分错误
```

输出固定布尔矩阵；`missing_claim_candidates` 只能作为 bounded repair hint。
当前生产 envelope 为 `coverage_matrix` 与 `missing_claim_candidates`，详见
[semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)。

### 6.2 Faithfulness Review

Faithfulness Review 是 claim 级任务，一次只审查一个 proposition。

它不输出 `verdict`、`reason`、`confidence` 或 corrected proposition，而输出固定中文
布尔矩阵：

```json
{
  "faithfulness_matrix": {
    "主体或指代范围改变": false,
    "否定方向改变": false,
    "否定范围改变": false,
    "正常状态误写为否认": false,
    "事实类型改变": false,
    "时间范围改变": false,
    "频率或数量改变": false,
    "程度或强度改变": false,
    "确定性改变": false,
    "因果关系改变": false,
    "医学推断或建议添加": false,
    "命题不自包含": false,
    "指代对象不明": false,
    "时间基准不明": false,
    "否定范围不明": false,
    "比较基线不明": false,
    "未分类语义改变": false
  }
}
```

业务结果由 deterministic rules 派生：

```text
全部 false → semantic_review_supported
来源绑定缺失 true → clarification_required
模型漂移 / 模型越权 true → repair_required
模型漂移与来源绑定缺失同时 true → repair_then_clarification_required
未分类 true → human_review_required
可修复 true 过多 → human_review_required
Coverage 与 Faithfulness 冲突 → disagreement
Review schema / 身份失败 → review_failed
```

来源绑定缺失维度包括：

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
```

这些维度不允许由 Repair 猜测补全，应生成显式 clarification gap 并交给问诊策略决定
是否追问或带 gap 阶段性回答。

`医学推断或建议添加` 属于模型越权，不属于来源绑定缺失。它应进入删除式局部修复：
只移除模型添加的诊断、风险、就医或治疗建议，并恢复用户明确表达；Repair 不判断医学
结论是否正确，也不生成新的医学建议。

## 7. Repair 与 Clarification 边界

核心原则：

```text
信息存在但模型表达错 → repair
信息不存在但下游需要 → clarification
模型越权添加内容 → repair by removal / restoration
无法归类 → human review
结构非法 → blocked
```

可修复问题包括：

```text
否定方向或范围漂移
normal / denied 表达漂移
时间、频率、数量、程度或确定性措辞漂移
因果措辞漂移
医学推断、风险判断或建议添加
proposition 不自包含且授权上下文可补全
shared scope 漏拆或合并
漏抽显式 claim
```

`医学推断或建议添加` 的允许修复方式仅限：

```text
删除模型添加的医学解释、诊断、风险、就医建议或治疗建议
恢复用户明确报告、猜测、未观察或请求语义
```

禁止：

```text
判断医学结论是否正确
生成新的诊断、风险或建议
补造用户未提供的事实
```

应进入 clarification 的问题包括：

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
命题不自包含且授权上下文无法补全
```

`clarification_required` 不是 verified，也不是强制追问指令。语义协作 DAG 输出显式
gap；是否追问由问诊领域结合 `answer_now`、安全状态、回答充分性、已有事实和追问轮数
决定。上一轮未消解 clarification gap 不得进入 verified prior fact summary。

## 8. Deferred lane

以下 lane 不进入当前生产实现：

```text
ClaimStatementSemanticsGenerator
ParticipantPhraseGenerator
TemporalPhraseGenerator
MeasurementPhraseGenerator
CanonicalDescriptorGenerator
```

启用前必须同时具备：

```text
明确下游消费者
领域投影或 candidate-only resolver / deterministic parser 契约
strict schema
verifier
负例测试
成本与延迟预算
```

不得为了“看起来完整”而把自然语言 proposition 反向拆成无人消费的结构化字段。

## 9. 实现防漂移检查清单

实现和 code review 时必须检查：

```text
是否只有 Turn Intent + Claim Proposition Inventory 进入生产 PlanPolicy
是否输出深层结构化语义 schema
是否输出 evidence / reason / confidence 自证字段
是否输出主题词而非自包含 proposition
是否把 normal 写成 denied
是否把未观察写成绝对否定
是否把可能因果写成确定因果
是否把宠物状态写成用户报告行为
是否提示 estimated claim count
是否暴露工程元数据给模型
是否使用标准化 SKILL.md 作为版本化 prompt 来源
SKILL.md front matter 是否仅确定性代码可见
受限模板是否只允许顶层白名单字符串变量
模板是否包含条件、循环、过滤器或属性访问
是否处理 tag delimiter collision
claim 数量超过 schema 上限是否 blocked
空集合是否交给 Coverage Review 区分 no_explicit_fact / suspicious_empty
Faithfulness Review 是否输出固定布尔矩阵
review 是否输出 corrected value
repair 是否只针对具体 true 维度
来源绑定缺失是否输出 clarification_required 而不是自动修复
医学推断 / 建议添加是否只做删除或还原式修复
修复后医学推断是否被重新审查
semantic_review_supported 是否被误写成 verified
人工审查是否显式记录
生产模块是否 import 实验 runner
默认测试是否读取实验 held-out
```

任一项不满足，应阻断实现合并。

## 10. 最小验收样本

至少覆盖：

```text
原始低风险换粮软便输入
多事实输入
shared scope：饭和水都正常
精神正常
没有呕吐
没有血便
未观察到呕吐
好像没有呕吐
可能是换粮导致
上一轮短答：正常
指代不明：它有点软，进入 clarification gap
医学推断添加，进入删除式修复
用户纠正
answer_now
纯问题且无事实
claim 数量超过 schema 上限
原文事实丰富但 claims=[]
```

这些样本是工程质量门禁，不是重新开启模型质量实验或 held-out 优化。

## 11. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m05-structured-llm-gateway-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m05-structured-llm-gateway-change-summary.md)
4. [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)
5. [input-preprocessing-v14-onepass-governance-convergence-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-change-summary.md)
6. [semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)
