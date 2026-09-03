<!--
=============================================================================
文件: semantic-collaboration-dag-production-architecture.md
作用: 定义受限语义协作 DAG 在生产工程实现中的稳定架构、任务边界、
      契约状态机、审查与局部重写治理、上下文策略和领域投影边界。
范围: 适用于用户原始表达到可投影结构化语义 claim graph 之间的生产主路径，
      包括确定性初始 Root Plan、正交 SKILL、审查、局部修复、typed patch、
      artifact 版本、一致性门禁和问诊 / 临床安全 / 长期记忆投影契约。
说明: 本文是生产工程实现基线，固定 Temporal 作为 durable execution 边界，
      不包含实验计划，不展开软件包内部实现、提示词全文或测试替身实现。
维护: 当 SKILL 目录、Plan IR、TurnSnapshot、artifact 状态机、review / repair
      契约、上下文访问策略或领域投影边界调整时，必须同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG 生产架构基线

> **文档状态**：生产工程实现基线
>
> **当前边界修订**：M06 已收敛为 Turn Intent + 自然语言 Claim Proposition
> Inventory，详见
> [semantic-collaboration-dag-m06-production-boundary-revision.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-production-boundary-revision.md)。
>
> **适用范围**：输入前置语义协作、窄域语义生成、结构化审查、局部重写、
> typed patch、claim graph 组装、质量门禁与领域投影契约
>
> **不适用范围**：临床安全医学准入内容、临床安全 OPA 策略细节、问诊状态存储实现、
> RAG 知识证据、自然语言最终回复生成、长期记忆写入策略、前端展示编排

## 1. 背景与架构结论

输入前置预处理 V8～V14 的结论是：

1. GLiNER / span-first candidate 路线与当前 claim 划分、论元角色和话语功能任务错配，
   不能作为权威语义事实来源。
2. 单体 one-pass full schema 虽然比 support-first 路线有更好的 claim 覆盖潜力，
   但同时输出 intent、claim inventory、statement semantics、participant、temporal、
   measurement、relation 和 canonical descriptor 会导致字段交替缺失和冷执行漂移。
3. 继续主要依赖 prompt 微调、整体 retry 或后处理补丁，不能收敛当前稳定性问题。
4. 完整当前回合上下文对 shared scope、指代、否定和时间绑定仍然必要，不能把子任务
   降级为只看局部 phrase 的碎片输入。

因此生产架构采用：

```text
contract-first
+ deterministic orchestration
+ 受限语义协作
+ 正交窄域 SKILL
+ 独立 verifier
+ 正交 review
+ deterministic repair planning
+ 局部 typed patch
+ 显式 disagreement / failure state
```

核心判断：

> 把一个高负载语义任务拆成多个边界清晰、可验证、可并行、可局部修复的小任务；
> 每个任务可以看到足够的全局只读上下文，但只能写自己的局部权威字段。

## 2. 目标与非目标

### 2.1 目标

1. 将用户当前回合显式语义稳定转换为可审查的自包含自然语言 proposition，并在后续组装为可审计 claim graph。
2. 在 proposition 中保留 normal、denied、uncertain、corrected、未观察和用户归因等语义差异；结构化 enum 映射后置到领域投影。
3. 支持 shared scope、多事实陈述、多轮指代、用户纠正和控制意图。
4. 保留上下文版本，并将 evidence binding 后置为独立治理状态；生成 SKILL 不自证 evidence。
5. 通过正交 Review SKILL 发现 schema 无法判断的语义忠实性问题。
6. 通过局部 typed patch 修复可恢复错误，而不是整轮自由重写。
7. 让每个任务都有显式终态，失败不得表现为空事实。
8. 维护 artifact 版本、repair lineage 和下游 stale 关系。
9. 以独立领域投影 adapter 输出问诊、临床安全和长期记忆可消费契约。
10. 为生产观测、回归测试和排障提供稳定 metadata。

### 2.2 非目标

1. 不做医学诊断、治疗方案或临床风险判断。
2. 不产生 urgent / blocked 安全信号。
3. 不直接写问诊状态、宠物资料或长期记忆。
4. 不直接调用临床安全召回、`required_context` 或临床安全 OPA。
5. 不恢复硬关键词、正则或静态 seed 补抽路径。
6. 不使用宽松文本 JSON 检索或手工修复 JSON。
7. 不让 OPA 或 Python 扫描原始用户文本做医学判断。
8. 不把临床安全 `observed_features` 未经契约转换写入问诊状态。
9. 不通过放宽回答充分性策略掩盖上游语义失败。
10. 不引入无界审查、修复或 retry 循环。

## 3. 生产目标架构

```text
用户当前回合
→ TurnSnapshot Assembly
→ Deterministic Root Plan Compiler
→ Root Plan IR
→ Plan Validator
→ Temporal SemanticDAGWorkflow
   ├─ Turn Intent Generator
   └─ Claim Proposition Inventory Generator
→ Deterministic Verifier
→ Coverage Review / Faithfulness Review
→ Review Verifier
→ Deterministic Repair / Clarification Router
   ├─ 局部 Repair SKILL
   │    → Patch Verifier
   │    → Patch Applier
   └─ Clarification Gap Artifact
→ Artifact Store / Version
→ Claim Graph Assembly
→ Graph Consistency Gate
→ Domain Projection Adapter
```

架构要求：

1. 生成、审查、修复均是受限 SKILL 任务，不是自由 Agent 对话。
2. Plan IR 只能选择已注册 SKILL 和已声明依赖。
3. 全局上下文对任务只读，输出权限由 SKILL 契约限制。
4. Review 只诊断，不直接修改 artifact。
5. Repair 只输出 typed patch proposal。
6. patch 由 deterministic applier 校验并应用。
7. claim graph 在局部结果通过验证后组装。
8. 下游领域只能通过 adapter 消费 verified graph。
9. 任务队列、worker 租约、基础设施重试、超时与中断恢复由 Temporal 负责。
10. PostgreSQL 只保存语义终态投影，不保存 ready / running / attempt 调度状态。

## 4. 核心不变量

### 4.1 全局可读、局部可写

每个任务可以使用当前回合全文和有界历史上下文进行语义消歧，但只能输出自己
`owns` 声明的字段。

禁止：

```text
看到全局输入后新增其他领域的 claim
看到全局输入后输出 canonical_id
看到全局输入后输出临床风险
看到全局输入后直接改写其他任务的 artifact
```

### 4.2 一个字段只有一个权威来源

字段所有权由 SkillCatalog 全局校验。禁止两个 SKILL 同时拥有同一权威字段的
最终写入权。

### 4.3 Review 不改结果

Review SKILL 只能输出：

```text
固定布尔检查矩阵
有界的 repair hint
```

不得直接输出 corrected artifact，也不得绕过 Repair SKILL 和 Patch Applier。

### 4.4 Repair 只做局部 patch

Repair SKILL 只能针对注册过的 failure code 和白名单 patch path 输出
`RepairPatchProposal`。

禁止：

```text
自由重写完整 schema
修复 forbidden field
补造无证据事实
修复一次失败后继续递归修复
```

### 4.5 失败必须有终态

任何任务不能以悬空状态结束。特别是：

```text
模型漏抽 ≠ 用户未提供
审查失败 ≠ 原任务通过
修复耗尽 ≠ 修复成功
候选不足或指代不明 ≠ 可自动确认事实
上下文不足 ≠ unknown 事实
```

### 4.6 领域隔离

语义协作 DAG 不因下游需要而提前实现其他领域职责。若投影 adapter 尚未实现，
应保留显式 TODO 空壳并抛出：

```text
projection_adapter_not_implemented
```

不得在 preprocessing 中替问诊、临床安全或长期记忆实现业务逻辑。

### 4.7 Durable execution 边界

Temporal 是执行基础设施权威：

```text
activity 队列
worker 恢复
基础设施 retry
语义 retryable failure 的下一次 attempt
workflow / activity timeout
执行 event history
```

自有代码只保留：

```text
Plan IR 依赖推进
任务业务终态
语义 retryable failure code
artifact lineage
repair budget
claim graph 准入
```

禁止：

```text
数据库任务队列
数据库 worker 租约
数据库 attempt 调度状态
自研 worker 抢占与恢复协议
```

## 5. TurnSnapshot 与受限全局视图

### 5.1 契约

TurnSnapshot 是不可变上下文对象，至少包含：

```text
turn_id
turn_index
original_user_text
last_assistant_questions
verified_prior_fact_summary
trusted_pet_context
context_digest
snapshot_version
```

要求：

1. `original_user_text` 必须保留原文，不得被摘要替代。
2. 历史上下文必须有界，只允许进入上一轮追问和已验证事实摘要。
3. 宠物画像只作为可信上下文，不得被模型输出覆盖。
4. `context_digest` 必须贯穿 generator、reviewer 和 repairer。
5. Snapshot 一旦创建不得修改。

### 5.2 上下文访问矩阵

| 任务 | 当前回合全文 | 有界历史 | 宠物画像 | 其他任务输出 | 下游领域状态 |
|---|---:|---:|---:|---:|---:|
| 生成 SKILL | 必需 | 按需 | 按需 | 禁止 | 禁止 |
| 局部 Review SKILL | 必需 | 按需 | 按需 | 只看被审查输出 | 禁止 |
| 局部 Repair SKILL | 必需 | 按需 | 按需 | 只看失败输出与 hint | 禁止 |
| Graph Consistency Review | 必需 | 按需 | 按需 | 只看已验证摘要 | 禁止 |

禁止进入 TurnSnapshot：

```text
问诊状态
临床安全召回结果
required_context 评估
临床安全 OPA 输入或输出
长期记忆
未验证同伴任务输出
```

上下文预算不足时必须输出：

```text
context_budget_exceeded
```

不得静默截断当前回合原文。

## 6. 确定性 Root Plan 与 Plan IR

### 6.1 初始规划职责

当前生产不存在任务规划 LLM，也不存在 `PlanSelection`。初始 Turn Plan 由
Deterministic Root Plan Compiler 根据 PlanPolicy 直接生成：

```text
turn_root envelope
turn_intent task
claim_inventory task
```

两个根任务互不依赖，可由 Temporal 并行调度。claim 数量、claim envelope 与
claim binding 均不得在 Claim Inventory 前预估或预分配。

以下结构由 Deterministic Plan Compiler 根据 `PlanPolicy` 生成：

1. turn root envelope 与稳定标识。
2. 必选并行根任务。
3. task_id 与 canonical dependency edge。
4. exact skill version 与 expected output schema reference。
5. turn / snapshot / catalog / policy digest 绑定。
6. canonical plan_id。

claim envelope 分配属于 Claim Inventory、M07 结构验证与 Coverage /
Faithfulness Review 之后的确定性后置阶段。初始 Root Plan 中预分配 claim envelope
会被 Plan Validator 阻断。

禁止：

```text
调用规划 LLM
预估或输出 claim 数量
预分配 claim envelope
发明新 skill / task_type / dependency
输出自由自然语言命令
访问或调用下游领域
```

### 6.2 Plan IR

Plan IR 至少包含：

```text
plan_id
skill_catalog_digest
plan_policy_digest
turn_id
snapshot_digest
plan_version
tasks
dependencies
expected_outputs
```

每个 task 至少包含：

```text
task_id
skill_id
skill_version
target_envelope
depends_on
expected_output_schema
selection_source
```

### 6.3 Plan 校验

Plan Validator 必须校验：

```text
skill 已注册
skill version 已注册
task_id 唯一
依赖存在
依赖无环
envelope schema 合法
context policy 合法
expected output 与 SkillSpec 一致
未选择禁止 lane
PlanPolicy digest 合法
catalog digest 合法
plan_id 与 canonical 内容一致
```

失败状态：

```text
plan_schema_invalid
unknown_skill_selected
skill_version_invalid
dependency_cycle_detected
context_policy_violation
plan_budget_exceeded
mandatory_task_missing
forbidden_skill_selected
output_schema_mismatch
plan_id_invalid
```

规划失败不得触发硬编码默认任务。

## 7. SKILL 契约与正交性

### 7.1 权威契约

权威契约由机器可读的 SkillSpec、strict JSON Schema、context policy、failure policy 和
verifier binding 承担。`SKILL.md` 只能作为提示词与审计投影，不是运行时权威来源。

M06 当前生产生成面收窄为：

```text
turn_intent
claim_proposition_inventory
```

以下 lane 为 deferred，不进入当前生产 PlanPolicy，也不得为了“看起来完整”而提前注册为
可选生成任务：

```text
statement_semantics
participant_phrase
temporal_phrase
measurement_phrase
canonical_descriptor
```

deferred 原则：

1. 必须先有明确下游消费者。
2. 必须先有 candidate-only resolver、deterministic parser 或领域投影契约。
3. 必须同步交付 verifier 和负例测试。
4. 不得把自然语言 proposition 反向拆成无人消费的结构化字段。

### 7.2 正交粒度

正交粒度是语义权威域，不是医学槽位或症状词。

当前正确划分：

```text
turn intent
claim proposition inventory
claim coverage review
claim faithfulness review
repair
patch apply
```

错误划分：

```text
呕吐抽取 skill
软便抽取 skill
食欲抽取 skill
换粮抽取 skill
```

后者会退化成模型版关键词状态机。

### 7.3 所有权矩阵

| SKILL | 权威输出 | 明确不拥有 |
|---|---|---|
| Turn Intent | fixed-field boolean intent signals | evidence、claim 事实、医学语义 |
| Claim Proposition Inventory | `claims[]` 自然语言 proposition | claim_id、evidence、assertion enum、canonical |
| Coverage Review | 覆盖问题布尔矩阵和有界 missing hint | 直接追加或修改 claim |
| Faithfulness Review | 语义漂移布尔矩阵 | corrected proposition、evidence、verdict |
| Repair SKILL | 受限 proposition patch proposal | artifact 直接应用权 |
| Patch Applier | artifact 新版本 | 语义猜测 |

## 8. 生成任务契约

每个生成 SKILL 必须同时交付标准化 `SKILL.md`、版本化 prompt renderer 和对应
verifier。renderer 根据静态校验后的 SKILL 文档、SkillSpec 与受限 TurnSnapshot
投影生成不可变 `SkillPromptProjection`。

M05 只消费、校验、序列化和哈希该投影，不得生成 SKILL 语义提示词、解析 Markdown、
按症状词扩展上下文或读取未授权资源。

标准化 `SKILL.md` 的结构为：

```text
文件头部静态元数据区
角色定位与目标
工作流 / 业务规则
输出约束与回复规范
异常处理与边界规则
记忆与上下文规则
Prompt Context Template
安全与领域隔离
```

文件头部元数据区包含：

```text
skill_id / skill_version / prompt_version
task_kind / execution_family
verifier id / version
output schema id / version
context resources
prompt variables
model-visible sections
```

元数据区仅由确定性 loader、SkillCatalog、PlanValidator 和 PromptRenderer 消费，
永远不进入模型消息。启动期必须校验：

```text
SKILL.md skill 身份与 SkillSpec 一致
task kind / execution family 与 SkillSpec 一致
output schema id / version 与 SkillSpec 一致
verifier id / version 与 SkillSpec 一致
context resources 与 SkillSpec context contract 一致
prompt variables 在全局白名单内
model-visible sections 是标准章节子集
文档 SHA-256 与注册投影一致
```

### 8.1 极薄 JSON 信封

生产传输仍使用 strict JSON Schema，但 JSON 只是传输信封；语义载荷是自然语言。

禁止在模型输出中构造深层语义 schema、自证字段或无消费者字段：

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

工程身份由系统根据 PlanTask、attempt、TurnSnapshot digest 和 schema contract 附加。

### 8.2 Prompt 输入形态

Prompt user message 使用结构化 tag 或极浅文本，不使用深层 JSON：

```text
<current_turn>
...
</current_turn>

<trusted_pet_context>
species: cat
description: 英短
</trusted_pet_context>
```

要求：

1. 不向模型展示 task_id、run_id、snapshot digest、skill version 或完整 JSON schema。
2. 不提示任何 claim 数量，避免模型为凑数量合并或拆错 claim。
3. 当前回合原文必须完整保留，不得用摘要替代。
4. renderer 必须处理 tag delimiter collision；无法安全渲染时 Fail Fast。
5. 上下文只能来自 TurnSnapshotProjector 授权资源。

### 8.3 受限模板规则

Prompt Context Template 使用受限 Jinja 子集，只允许顶层白名单字符串变量：

```jinja
{{ current_turn }}
{{ last_assistant_questions }}
{{ verified_prior_facts }}
{{ trusted_pet_context }}
```

模板必须在启动期通过 AST 白名单校验，只允许：

```text
Template
Output
TemplateData
Name
```

禁止：

```text
{% if %} / {% for %}
过滤器
属性访问
方法调用
宏、import、include
任意表达式
动态模板源
```

动态值只能来自 TurnSnapshotProjector 授权并预格式化后的字符串。渲染变量集合
必须与 SKILL 文档声明完全一致，使用 StrictUndefined；输出中不得残留 Jinja 标记。
用户原文只作为数据值传入模板，不得作为模板源被执行。

### 8.4 Turn Intent

输出 fixed-field boolean：

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

要求：

1. 每个信号在当前回合最多出现一次。
2. 不输出 evidence、reason 或 confidence。
3. `answer_now` 是控制意图，不是医学事实。
4. `question_present` 与 claim proposition 分离。

### 8.5 Claim Proposition Inventory

Claim Inventory 输出完整、自包含的自然语言 proposition，而不是主题词。
proposition 的主语义必须是当前宠物、宠物状态、宠物行为或宠物相关事件；
不得把 `用户报告`、`用户认为`、`用户询问` 作为 claim 主语义。
来源、说话人与观察方式由系统 metadata 和后续审查状态承载。

输出形式：

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

禁止输出：

```text
呕吐
血便
精神状态
食欲
饮水
```

主题词会丢失主体、否定、时间、程度和断言方向，导致下游重新猜语义或 RAG 查询过宽。

### 8.6 受限语义重写

Claim Proposition Inventory 允许做受限语义重写，以便把口语、省略和 shared scope 整理
成自包含 proposition。

允许：

```text
根据可信上下文补全当前讨论宠物主体
拆分 shared scope
保留否定、否定范围和纠正语义
保留不确定、未观察和可能因果
保留时间、频率、数量、程度和比较基线
```

示例：

```text
饭和水都正常
```

应拆为：

```text
英短进食正常
英短饮水正常
```

禁止：

```text
把“精神正常”写成“否认精神异常”
把“没看到吐过”写成“绝对没有呕吐”
把“可能有关”写成“确定因果关系”
把宠物状态写成“用户报告……”元命题
输出诊断、疾病名、风险、就医建议或治疗建议
在指代不明时猜测对象
```

如果上下文不足以消解指代，应保留保守表达并交给 Faithfulness Review 标记歧义。

### 8.7 Claim 与 Plan envelope 的关系

初始 Root Plan 不包含 claim envelope，也不包含 claim 数量。Claim proposition
数组顺序是后续确定性分配 envelope 的唯一顺序来源，claim count 与 claim id 均由
系统附加。

claim envelope 分配必须发生在 Claim Inventory 通过结构验证和语义审查之后：

```text
claims.length → claim_count
claims[0] → claim_env_0000
claims[1] → claim_env_0001
```

如果 claims 数量超过 SkillCatalog schema 上限：

```text
blocked
不得截断
不得合并
不得为凑数修改 proposition
```

## 9. Deterministic Verifier

每个生成任务输出必须先经过 deterministic verifier。

当前 M07 只验证结构与身份，不做医学语义判断：

```text
strict schema check
boolean / string 类型校验
required field 校验
extra field 拒绝
字段所有权校验
字符串非空、长度与换行约束
claims 数量上限
claims 重复检测
claim 数量由 claims.length 确定性派生
target envelope 存在性
context digest 校验
task / skill / schema 身份一致性
```

当前不验证：

```text
evidence phrase
assertion enum
participant resolver
temporal parser
measurement parser
canonical selector
```

典型失败：

```text
schema_invalid
forbidden_field_present
field_ownership_violation
claim_count_exceeds_schema_limit
duplicate_claim
empty_claim_proposition
context_digest_mismatch
semantic_conflict
```

禁止：

```text
静默删除 forbidden field 后继续
宽松解析非法 JSON
把 proposal 标记为 verified
用关键词规则补造语义
截断或合并 claim 以匹配 Plan envelope
```

## 10. 正交 Review SKILL

### 10.1 拆分原则

Review 必须拆成 coverage 与 faithfulness 两个语义权威域：

```text
claim_coverage_review
claim_faithfulness_review
```

Coverage Review 是 turn 级任务，用于发现漏抽、合并、多抽和非自包含 proposition。
Faithfulness Review 是 claim 级任务，一次只审查一个 proposition。

### 10.2 输入

Coverage Review 输入：

```text
current_turn
generated_claims
必要的有界上下文
```

Faithfulness Review 输入：

```text
current_turn
单条 claim proposition
必要的有界上下文
```

Review 与 generator 使用同一 `context_digest`。Reviewer 不读取生成器 prompt、reason、
confidence、evidence 或下游领域状态，避免被生成器自证锚定。

### 10.3 Coverage Review 输出

Coverage Review 使用固定布尔矩阵，不输出自由 verdict：

```json
{
  "存在漏抽显式事实": false,
  "存在多事实合并": false,
  "存在重复claim": false,
  "存在原文不支持的claim": false,
  "存在非自包含proposition": false,
  "存在shared scope拆分错误": false,
  "未分类覆盖问题": false
}
```

可附带不超过上限的 `missing_claim_candidates` 作为 repair hint；该数组不是权威 artifact，
不能直接追加到原结果。

### 10.4 Faithfulness Review 输出

Faithfulness Review 使用固定中文布尔矩阵，不输出 `verdict`、`reason`、`confidence`
或 corrected proposition：

```json
{
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
```

所有字段必须为 boolean、required 且 `additionalProperties=false`。

### 10.5 确定性结果派生

Review LLM 不输出业务 verdict。业务结果由 deterministic rules 从布尔矩阵派生。

维度先按信息来源分类：

```text
模型漂移类：信息在 current_turn / 授权上下文中存在，但 proposition 表达错误
模型越权类：模型添加了推断、风险、建议或用户未表达的结论
来源绑定缺失类：current_turn / 授权上下文本身无法确定对象、时间基准、否定范围或比较基线
未分类类：无法稳定归入上述类型
```

派生规则：

```text
全部 false
→ review_supported

仅来源绑定缺失字段 true
→ clarification_required

仅模型漂移 / 模型越权字段 true，且数量不超过上限
→ repair_required

模型漂移 / 越权字段与来源绑定缺失字段同时 true
→ repair_then_clarification_required：
  先删除或还原模型引入的漂移，再保留保守 proposition 与 clarification gap

未分类字段 true
→ human_review_required

可修复 true 维度数量超过上限
→ human_review_required，禁止自动全局重写
```

来源绑定缺失字段包括：

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
```

`命题不自包含` 必须条件路由：

```text
授权上下文足以补全主体 / target → repair
授权上下文不足以补全 → clarification_required
```

`医学推断或建议添加` 属于模型越权类，不是来源绑定缺失类。它应进入局部修复，
删除或还原模型添加的医学推断、风险判断或建议；Repair 不得评估该医学内容是否正确。

`clarification_required` 不是 verified，也不是系统失败。它表示当前 proposition 存在
显式语义 gap，需要下游问诊策略决定是否追问、是否带 gap 阶段性回答，或在下一回合
TurnSnapshot 中消解。

### 10.6 审查边界

Review 可以判断：

```text
claim 是否覆盖当前回合显式事实
proposition 是否改变主体、否定、时间、频率、数量、程度、确定性或因果
是否添加医学推断、风险或建议
proposition 是否自包含
shared scope 是否漏拆
```

Review 禁止：

```text
直接修改 artifact
输出 corrected proposition
输出诊断或临床风险
读取下游领域状态
把审查失败当作原任务通过
把未分类问题自动归入某个已知维度
把来源绑定缺失当作可修复漂移
```

## 11. 局部重写与 typed patch

### 11.1 Repair Planner

Repair Planner 是 deterministic 组件，根据 Faithfulness / Coverage 布尔矩阵中为 true 的
具体维度创建受限修复或 clarification gap 任务。禁止由 Review LLM 直接决定自由修复。

映射示例：

| review dimension | 路由 | 允许的 Repair / Gap SKILL |
|---|---|---|
| `否定方向改变` | repair | `repair.claim.assertion_direction` |
| `否定范围改变` | repair | `repair.claim.assertion_scope` |
| `正常状态误写为否认` | repair | `repair.claim.normal_statement` |
| `时间范围改变` | repair | `repair.claim.temporal_wording` |
| `频率或数量改变` | repair | `repair.claim.frequency_or_quantity` |
| `程度或强度改变` | repair | `repair.claim.degree_wording` |
| `确定性改变` | repair | `repair.claim.certainty_wording` |
| `因果关系改变` | repair | `repair.claim.causality_wording` |
| `医学推断或建议添加` | repair | `repair.claim.remove_external_medical_inference` |
| `命题不自包含` 且上下文可补全 | repair | `repair.claim.self_containment` |
| `命题不自包含` 且上下文不可补全 | clarification | `clarification.claim.binding` |
| `指代对象不明` | clarification | `clarification.claim.reference_target` |
| `时间基准不明` | clarification / gap | `clarification.claim.temporal_anchor` |
| `否定范围不明` | clarification | `clarification.claim.negation_scope` |
| `比较基线不明` | clarification / gap | `clarification.claim.comparison_baseline` |
| `存在多事实合并` / `存在shared scope拆分错误` | repair | `repair.claim_inventory.scope_split` |
| `存在漏抽显式事实` | repair | `repair.claim_inventory.missing_claim` |
| `未分类语义改变` | human review | 无自动修复 |

`医学推断或建议添加` 的修复只能是受限删除 / 还原：

```text
删除模型添加的诊断、医学解释、风险判断、就医建议或治疗建议
恢复用户明确表达的事实、猜测、未观察或请求语义
不得判断医学结论是否正确
不得生成新的医学建议
不得补造用户未提供的事实
```

因此该修复不是医学判断，而是移除模型越权生成内容。

以下问题不得自动修复：

```text
forbidden field 出现
schema 根本非法
原文无证据且无法还原为 supported proposition
来源绑定缺失：指代对象、时间基准、否定范围或比较基线不明
review 维度未分类
结构 / ID 越权
下游 adapter 未实现
```

来源绑定缺失输出 `clarification_required`，不得由 Repair 猜测对象、时间基准、否定
范围或比较基线。

### 11.2 RepairPatchProposal

Repair SKILL 只能输出白名单 typed patch proposal：

```json
{
  "patch_id": "string",
  "repair_skill_id": "string",
  "target_task_id": "string",
  "target_artifact_id": "string",
  "base_version": "number",
  "review_dimension": "string",
  "operations": "array",
  "reason_code": "string"
}
```

禁止自由 JSON Patch、整轮重写或修复未申报维度。

### 11.3 Patch 应用校验

Patch Applier 必须校验：

```text
target artifact 存在
base_version 一致
repair skill 已注册
review dimension 匹配
patch path 在白名单
patch value 符合 proposition schema
未修改 forbidden path
无并行 patch 冲突
repair budget 未超限
```

Evidence binding 不作为 Patch Applier 的语义判断条件；patch 后仍处于
`evidence_binding_pending`，除非独立证据门禁已完成。

### 11.4 修复与澄清边界

核心原则：

```text
Repair may remove or restore model-introduced drift,
but must not create information absent from the TurnSnapshot.
Clarification is required when the source itself lacks the binding.
```

即：

```text
信息存在但模型表达错 → repair
信息不存在但下游需要 → clarification
模型越权添加内容 → repair by removal / restoration
无法归类 → human review
结构非法 → blocked
```

可修复：

```text
否定方向或范围漂移
normal / denied 表达漂移
时间、频率、数量、程度或确定性措辞漂移
因果措辞漂移
医学推断、风险判断或建议等模型越权内容
proposition 不自包含且授权上下文可补全
shared scope 漏拆或合并
漏抽显式 claim，且 coverage hint 可用
```

应进入 clarification：

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
proposition 不自包含且授权上下文无法补全
```

不得自动修复：

```text
forbidden field 出现
schema 根本非法
原文无证据且无 supported proposition 可还原
模型发明 entity_id / canonical_id
review 维度未分类
下游 adapter 未实现
```

### 11.5 修复预算

生产默认约束：

```text
repair_depth = 1
不允许 repair of repair
同一 proposition 最多一次修复
单次 patch 只能修复声明的有限维度
每轮全局 repair budget 固定
必须有全局 deadline
```

超过预算输出 `repair_exhausted`。这是合法终态，不是可静默吞掉的失败。

## 12. Artifact 状态机与 DAG 闭环

### 12.1 任务与证据门禁状态

每个任务必须有显式终态或门禁状态：

```text
schema_valid
semantic_review_pending
semantic_review_supported
clarification_required
repair_then_clarification_required
evidence_binding_pending
human_review_required
verified
repair_verified
not_applicable
blocked
disagreement
repair_exhausted
repair_failed
dependency_failed
review_failed
context_budget_exceeded
timeout
```

`semantic_review_supported` 不等于 `verified`。在生产证据门禁完成前，artifact 必须保持
`evidence_binding_pending`、`clarification_required`、`repair_then_clarification_required`
或 `human_review_required`。

### 12.2 空结果语义

空 claim 集合必须区分：

```text
no_explicit_fact
suspicious_empty
model_returned_empty
schema_invalid
extraction_failed
dependency_failed
review_failed
```

原文包含多个显式事实而输出空集合时，Coverage Review 必须标记 `suspicious_empty`。

### 12.3 disagreement

生成、Coverage Review、Faithfulness Review 或人工审查结果不一致时，不得默认任一方
正确。必须保留：

```text
claim proposition
review boolean matrix
candidate repair hint
人工审查状态
artifact base version
```

终态为 `disagreement`。除非存在显式 adjudicator 契约，否则不得自动裁决。

### 12.4 Clarification gap artifact

`clarification_required` 必须产生显式 gap artifact 或等价结构化状态，至少保留：

```text
claim proposition
ambiguous dimension
required binding type
turn snapshot digest
artifact base version
是否已经过模型越权修复
```

Clarification gap 语义：

```text
不是 verified
不是 failure
不是 unknown fact
不是自动追问指令
```

语义协作 DAG 不直接生成最终用户追问文案。是否追问由问诊领域结合以下信息决定：

```text
answer_now
安全状态
回答充分性策略
已有事实
追问轮数
医学必要缺口
```

上一轮 clarification gap 在未消解前不得进入 `verified_prior_fact_summary`。下一回合
TurnSnapshot 可通过 `last_assistant_questions` 支持用户短答消解。

### 12.5 Evidence binding 后置

Evidence binding 是独立后续任务，不由生成器或 Faithfulness Review 自证。

当前过渡阶段允许人工审查承担证据判断，但必须显式记录：

```text
review_mode=human
proposal id / claim id
supported / rejected / ambiguous
reviewer role
review time
decision digest
```

禁止：

```text
把人工通过伪装成自动 verified
无 evidence binding 时静默标记 verified
人工审查直接改写 artifact
clarification gap 被当成已验证事实
```

### 12.6 下游 stale

上游 claim proposition 修复导致拆分、合并或删除时，必须标记相关下游结果 stale：

```text
claim inventory 修复
→ claim 集合变化
→ 相关 review / repair / graph / domain projection 结果 stale
→ 下游任务重新执行
```

禁止把旧 claim 的审查或投影结果直接迁移到新 claim。

## 13. Claim Graph 与一致性门禁

### 13.1 组装

当前 claim graph 的最小节点是：

```text
turn intent
claim proposition
coverage review outcome
faithfulness review outcome
repair outcome
clarification gap status
evidence binding status
artifact version / lineage
```

LLM 不直接输出最终完整图。后续领域如需要 assertion enum、participant、temporal、
measurement 或 canonical 绑定，必须通过独立领域投影或专门 resolver 立项，不得反向要求
M06 输出这些字段。

### 13.2 Graph Consistency Gate

优先 deterministic 检查：

```text
ID 引用存在
依赖完整
claim proposition 无重复
intent 与 claim 集合身份一致
review / repair / clarification / evidence 状态完整
artifact version 与 lineage 一致
field ownership 不冲突
```

需要语义判断的冲突可交给受限 graph consistency reviewer，例如：

```text
intent 与 claim proposition 冲突
多个 proposition 表达同一事实但语义相反
repair 后 proposition 与 coverage 结果不一致
```

图级终态：

```text
graph_verified
graph_partial_with_gaps
graph_disagreement
graph_blocked
```

## 14. 领域投影

### 14.1 投影原则

语义协作 DAG 只输出 verified graph 和显式 gap / disagreement。

下游消费必须通过 adapter：

```text
ConsultationProjectionAdapter
ClinicalSafetyProjectionAdapter
LongTermMemoryProjectionAdapter
```

每个 adapter 必须声明：

```text
接受的 claim type
忽略的 claim type
normal / denied / uncertain 映射
冲突处理策略
必需字段
禁止字段
失败状态
```

### 14.2 问诊投影

可消费：

```text
控制意图
起病时间
当前食物 / 换粮
大便形态
精神状态
食欲状态
饮水状态
呕吐否认
血便否认
用户纠正
clarification_required gap
```

问诊投影必须把 clarification gap 交给问诊回答充分性 / followup 策略。语义 DAG 不得
把 gap 直接转换为强制追问；当 `answer_now=true` 且无安全阻断时，问诊策略可以带 gap
输出阶段性回答。

禁止：

```text
把 reported_normal 映射为 denied
把 unknown 映射为追问已完成
在 adapter 内做医学风险判断
```

### 14.3 临床安全投影

临床安全投影只提供其声明允许的结构化事实，不产生：

```text
urgent signal
blocked signal
临床动作
诊断
```

临床安全仍由既有链路独立完成：

```text
临床安全语义
候选召回
required_context 评估
临床安全 OPA
```

### 14.4 长期记忆投影

长期记忆投影只输出候选，不直接写入长期事实。写入仍由独立候选抽取和策略裁决负责。

## 15. 可观测性

每次任务至少记录：

```text
turn_id
snapshot_digest
plan_id
task_id
skill_id
skill_version
prompt_hash
model snapshot
input envelope digest
artifact id / version
review outcome
review matrix digest
evidence binding status
failure code
repair lineage
latency
token usage
terminal state
```

关键指标：

```text
schema invalid rate
forbidden field blocked rate
claim count mismatch rate
review disagreement rate
coverage missing rate
faithfulness drift dimension distribution
clarification required rate
repair then clarification rate
repair required rate
repair by medical-inference removal rate
repair success rate
repair regression rate
repair exhausted rate
context budget exceeded rate
suspicious empty rate
graph partial rate
cross-agent inconsistency rate
terminal state distribution
```

## 16. 生产工程交付顺序

以下顺序是工程依赖顺序，不是实验计划。

### 阶段 A：契约与目录

交付 `SkillSpec`、`SkillCatalog`、所有权校验、context policy、failure code 目录和
repair mapping 目录。

验收：重复字段所有权、forbidden 与 owns 冲突、缺失 verifier 均在启动时失败。

### 阶段 B：TurnSnapshot 与 Plan IR

交付 TurnSnapshot、context digest、Plan IR 和 Plan Validator。

验收：未知 skill、非法依赖和上下文越权全部 blocked。

### 阶段 C：生成 SKILL 与 verifier

交付 Turn Intent、Claim Proposition Inventory、版本化 prompt renderer 和 strict
deterministic verifier。

验收：所有输出 strict schema；forbidden field 不被清洗后放行；claim 为自包含自然语言
proposition；claim 数量由 `claims.length` 派生，超过 schema 上限显式 blocked。

### 阶段 D：Review 与 Repair

交付 Coverage Review、Faithfulness Review、review verifier、deterministic outcome
derivation、repair / clarification router、typed patch、patch verifier 和 applier。

验收：review 不直接修改 artifact；review 不输出 corrected value；repair 只能修改白名单
path；修复预算和终态有效。

### 阶段 E：Artifact 与 Claim Graph

交付 artifact store、版本管理、repair lineage、stale 标记、graph assembly 和
consistency gate。

验收：每个任务有终态；上游变化触发下游 stale；graph 只消费 verified artifact。

### 阶段 F：领域投影与生产接入

交付问诊投影 adapter、临床安全投影 adapter、长期记忆候选投影 adapter 和
orchestrator 接入边界。

验收：preprocessing 不直接写领域状态；adapter 未实现时显式失败；安全裁决仍由
既有领域链路负责。

## 17. 防退化测试要求

生产测试至少覆盖：

```text
SkillCatalog 所有权冲突
Plan IR 非法 skill / 依赖环 / 上下文越权
strict schema extra field 拒绝
forbidden field blocked
normal / denied / uncertain / unobserved / corrected 自然语言语义回归
shared scope 拆分
多轮指代与 answer_now
claim 数量超过 schema 上限 blocked
主题词 claim 拒绝
review 布尔矩阵 extra field 拒绝
医学推断添加可被删除式局部修复
来源绑定缺失进入 clarification_required
review 输出越权拒绝
repair patch 越权拒绝
base version 冲突拒绝
repair budget 耗尽终态
上游修复后的下游 stale
graph consistency 冲突
领域 adapter 隔离
失败不得转换为空 facts
```

禁止使用：

```text
按医学症状词组织的正向测试全集
以关键词命中作为验收标准
以 retry 后结果冒充单次稳定结果
```

## 18. 文档与实现同步

以下内容变化时必须同步更新本文：

```text
Skill 目录
字段所有权
Plan IR schema
TurnSnapshot 契约
artifact 状态机
review 状态机
repair patch 契约
context policy
领域投影边界
durable execution 边界
生产验收口径
```

代码、manifest、测试或 `SKILL.md` 与本文冲突时，必须通过显式架构变更同步，
不得留下双权威解释。

## 19. 关联材料

1. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
2. [semantic-collaboration-dag-m06-production-boundary-revision.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-production-boundary-revision.md)
3. [agent-input-preprocessing-domain-extraction-migration-plan.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-domain-extraction-migration-plan.md)
4. [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)
5. [input-preprocessing-v14-onepass-governance-convergence-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-change-summary.md)
6. [consultation-semantic-extraction-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-semantic-extraction-change-summary.md)
7. [consultation-state-answerability-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-state-answerability-change-summary.md)
8. [clinical-safety-semantic-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-semantic-change-summary.md)
9. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)
