<!--
=============================================================================
文件: docs/architecture/veterinary-core-infrastructure-architecture.md
作用: 定义 src/vet_agent/core 公共基础设施层的架构背景、模块边界、公共
      契约、执行协议、外部依赖适配、测试策略与实施顺序。
范围: 覆盖配置、错误、Strict JSON Gateway、selector、LiteLLM 模型网关、
      prompt registry、Grouped Review、Defect Cluster、Targeted Repair、
      ExecutionContext、审计、观测、授权、持久化、编排、就绪检查与测试。
说明: 本文是语义预处理各子层共享工程能力的实现权威，但不是语义真相源；
      不定义具体语义缺陷码含义、prompt 文案、patch 语义或临床判断。
维护: 当核心模块边界、模型调用协议、strict schema 协议、selector 协议、
      Review / Repair 执行协议、审计或 Fail Fast 底线调整时，必须先修订本文。
=============================================================================
-->

# 兽医智能体公共基础设施架构设计草案

> **文档状态**：架构草案基线
>
> **文档定位**：本文细化
> [veterinary-semantic-decomposition-charter.md](./veterinary-semantic-decomposition-charter.md)
> 与
> [veterinary-semantic-preprocessing-control-conventions.md](./veterinary-semantic-preprocessing-control-conventions.md)
> 下的共享工程实现边界。它描述 `src/vet_agent/core` 如何提供统一配置、模型
> 调用、schema 校验、selector、Review、Repair、审计和外部依赖适配能力。
>
> **权威关系**：本文不替代任何语义子层架构文档。意图层、anchor 层、Scope
> 层和 Contextual Query Proposition 层仍是各自 schema、缺陷码、prompt 语义
> 和 patch 语义的权威。若本文、语义子层文档或实现冲突，必须先显式修订其中
> 一方，不得在代码中形成第三种解释。

## 1. 核心结论

`src/vet_agent/core` 是所有语义预处理子层的共享工程基础设施层。它提供：

```text
统一配置管理
统一错误与失败码
Strict JSON Gateway
本地 selector 分配与 canonical 化
LiteLLM OpenAI-compatible 模型网关
版本化 prompt registry 与渲染
Grouped Review 执行框架
Review Delta 合并
Defect Cluster 聚合
有界 Targeted Repair 调度
ExecutionContext 与执行审计
脱敏日志、指标与追踪
OPA 授权端口
PostgreSQL 审计存储端口
Temporal / pipeline 编排端口
依赖就绪检查
测试替身与契约测试工具
```

核心控制原则：

```text
core 提供工程能力，不承载兽医语义判断；
core 提供执行协议，不定义语义真相；
core 是唯一模型调用入口；
core 是唯一 strict schema 入口；
selector 永远由系统分配；
Review / Repair 由 core 提供框架、由语义层提供语义配置；
所有失败显式分类，不得转空结果；
所有执行进入审计信封；
所有日志与观测数据默认脱敏。
```

最简洁的依赖方向是：

```text
semantic modules → core
```

禁止：

```text
core → semantic modules
core → intent / anchor / scope / proposition 业务实现
core → 临床安全或 Coverage 实现
core → Mem0 原始记忆读取
```

## 2. 架构范围

### 2.1 In Scope

本文覆盖：

```text
core.config
core.errors
core.schema
core.selectors
core.llm
core.prompts
core.review
core.repair
core.execution
core.observability
core.security
core.persistence
core.orchestration
core.readiness
core.testing
```

### 2.2 Out of Scope

本文不覆盖：

```text
Turn Intent 语义设计
semantic anchor 语义设计
joint context scope 语义设计
contextual query proposition 语义设计
具体 prompt 文案
具体缺陷码语义
具体 patch / join 语义
Coverage
RAG 查询工程
PDF 解析
知识命题治理
临床安全裁决
长期记忆治理
宠物档案写入
生产部署拓扑
```

特别是：

```text
core 不判断一个 proposition 是否忠实；
core 不判断一个 scope 是否医学综合越界；
core 不修复语义对象；
core 只执行语义层定义的模型调用、校验、调度和审计协议。
```

## 3. 架构背景与问题

语义预处理已经拆成多个子层：

```text
Turn Intent
Semantic Anchor
Joint Context Scope
Contextual Query Proposition
```

这些子层共享相同控制协议：

```text
Generator 输出薄 schema
→ 系统分配 selector
→ Grouped Review 输出稀疏 Delta
→ 系统合并 Delta
→ 按 target 聚合缺陷簇
→ Targeted Repair
→ 事务式 join / renumber
→ post review
→ 显式成功或失败
```

如果每个语义层自行实现工程能力，会出现：

```text
模型配置分散；
模型输出 JSON 校验行为不一致；
selector 解析和编号不一致；
Review Group allowlist 不一致；
缺陷簇聚合不一致；
修复预算和授权范围不一致；
审计字段缺失；
失败被伪装为空结果；
日志泄露敏感信息；
外部服务依赖方向混乱。
```

因此必须在实现语义层之前先固化公共基础设施。

## 4. 顶层设计原则

### 4.1 单一模型入口

所有模型调用必须通过：

```text
core.llm.LLMGateway
```

业务层不得：

```text
直接创建 OpenAI client；
直接读取模型 API key；
直接拼接 LiteLLM URL；
直接调用 LiteLLM Admin API；
直接调用模型厂商 SDK；
在业务层实现模型 retry；
在业务层记录 token usage。
```

### 4.2 Strict Schema 先于语义解释

所有模型输出必须先经过：

```text
strict JSON parse
→ strict schema validation
→ selector validation
→ semantic layer interpretation
```

不允许语义层先解释一个结构不合法的输出。

### 4.3 Selector 只由系统分配

模型不生成 selector。系统统一分配：

```text
c* / r* / t0
a* / p*
s* / q*
```

selector 是调用内定位符，不是长期身份，不写入档案，不作为权威事实 ID。

### 4.4 缺陷码不是修复动作

core 的 Review 框架只保证：

```text
allowlist 校验；
Delta 合并；
target 聚合；
缺陷短语透传。
```

缺陷码含义和修复动作边界由语义层文档定义。

### 4.5 传输重试与语义修复分离

```text
传输重试：
  由 core.llm 按配置处理。

语义修复：
  由语义层显式创建 Targeted Repair task。
```

不得因 schema invalid 自动全局重写，也不得因模型超时返回空数组。

### 4.6 Fail Fast

以下状态必须显式暴露：

```text
依赖不可用
模型调用失败
模型输出协议错误
schema invalid
selector invalid
review group invalid
repair invalid
repair budget exceeded
orchestration failure
persistence failure
```

禁止：

```text
失败转空数组
失败转 completed
失败后静默跳过步骤
单次请求内自动 fallback 旧链路
宽松修复 JSON
```

## 5. 推荐模块地图

初始实现可采用：

```text
src/vet_agent/core/
├── __init__.py
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── model_roles.py
│   └── runtime.py
├── errors/
│   ├── __init__.py
│   ├── base.py
│   ├── codes.py
│   └── exceptions.py
├── schema/
│   ├── __init__.py
│   ├── base.py
│   ├── json_gateway.py
│   ├── validation.py
│   └── canonical.py
├── selectors/
│   ├── __init__.py
│   ├── allocator.py
│   ├── namespace.py
│   └── validator.py
├── llm/
│   ├── __init__.py
│   ├── client.py
│   ├── contracts.py
│   ├── errors.py
│   ├── retry.py
│   └── usage.py
├── prompts/
│   ├── __init__.py
│   ├── registry.py
│   ├── renderer.py
│   ├── loader.py
│   └── versions.py
├── review/
│   ├── __init__.py
│   ├── group_runner.py
│   ├── delta_merger.py
│   └── cluster_aggregator.py
├── repair/
│   ├── __init__.py
│   ├── scheduler.py
│   └── contracts.py
├── execution/
│   ├── __init__.py
│   ├── context.py
│   ├── ids.py
│   ├── audit.py
│   └── pipeline.py
├── observability/
│   ├── __init__.py
│   ├── logging.py
│   ├── metrics.py
│   └── tracing.py
├── security/
│   ├── __init__.py
│   ├── authorization.py
│   └── opa_client.py
├── persistence/
│   ├── __init__.py
│   ├── ports.py
│   └── db.py
├── orchestration/
│   ├── __init__.py
│   ├── contracts.py
│   └── temporal.py
├── readiness/
│   ├── __init__.py
│   └── probes.py
└── testing/
    ├── __init__.py
    ├── fake_llm.py
    ├── fixtures.py
    └── assertions.py
```

实现初期允许合并子模块，但依赖边界必须保持不变。

## 6. `core.config`

### 6.1 职责

统一提供：

```text
运行环境配置
LiteLLM 连接配置
模型角色映射
超时与重试配置
并发与预算配置
观测配置
授权配置
持久化配置
Temporal 配置
依赖启用配置
```

业务模块不得直接读取 `os.environ`。

### 6.2 环境变量命名

正式契约只使用环境变量名，不写入真实地址或密钥：

```text
VET_AGENT_ENV
VET_AGENT_LOG_LEVEL

VET_AGENT_LITELLM_BASE_URL
VET_AGENT_LITELLM_API_KEY
VET_AGENT_LLM_TIMEOUT_SECONDS
VET_AGENT_LLM_MAX_TRANSPORT_RETRIES
VET_AGENT_LLM_MAX_CONCURRENCY

VET_AGENT_OPA_ENABLED
VET_AGENT_OPA_BASE_URL

VET_AGENT_PERSISTENCE_ENABLED
VET_AGENT_DATABASE_DSN

VET_AGENT_TEMPORAL_ENABLED
VET_AGENT_TEMPORAL_HOST
VET_AGENT_TEMPORAL_NAMESPACE
VET_AGENT_TEMPORAL_TASK_QUEUE

VET_AGENT_AUDIT_MODE
VET_AGENT_TRACE_ENABLED
```

开发环境中的具体地址、账号和密钥只能来自部署环境或本地 env 文件；
`tmp/dev-external-services/README.md` 是临时联调参考，不是正式架构契约。

### 6.3 Settings 原则

```text
启动时加载一次；
immutable / frozen；
缺失 required 配置显式失败；
secret 只保存在进程内；
普通日志不得输出 Settings 全量对象；
业务层只消费 typed settings 或 role config。
```

### 6.4 模型角色映射

模型角色是稳定契约，模型名是可替换配置。

初始角色至少包括：

```text
intent_control_generator
intent_control_review
request_completeness_review
request_fidelity_review
request_context_reference_review
intent_control_repair
request_items_repair

anchor_generator
anchor_completeness_review
anchor_support_boundary_review
anchor_state_certainty_review
anchor_measurement_relation_review
anchor_subject_form_atomicity_review
anchor_repair

scope_planner
scope_context_support_review
scope_boundary_review
scope_temporal_redundancy_review
scope_repair

contextual_proposition_generator
contextual_proposition_support_review
contextual_proposition_form_source_review
contextual_proposition_state_certainty_review
contextual_proposition_measurement_relation_review
contextual_proposition_boundary_review
contextual_proposition_repair
```

开发环境可将全部角色映射到同一文本模型；生产环境可按角色拆分模型。

## 7. `core.errors`

### 7.1 基础异常

建议定义：

```text
CoreError
ConfigurationError
DependencyUnavailableError
DependencyTimeoutError
AuthenticationError
AuthorizationDeniedError
RateLimitError

LLMTransportError
LLMProtocolError
LLMBudgetExceededError

SchemaValidationError
StrictJSONValidationError
SelectorValidationError

ReviewGroupSchemaError
RepairSchemaError
RepairBudgetExceededError

PipelineExecutionError
PersistenceError
OrchestrationError
```

### 7.2 错误信封

所有 core 异常携带：

```text
error_code
message
retryable
context
cause
```

要求：

```text
message 不包含密钥；
context 不包含 API key、Authorization header 或 DSN；
cause 可保留底层异常但不直接透出给用户；
retryable 仅描述传输层可重试性，不表示语义可修复。
```

### 7.3 错误映射

core 提供异常到顶层状态的通用映射；语义层再映射为各自 envelope 状态。

例如：

```text
LLMTransportError
→ dependency_unavailable / llm_transport_failed

StrictJSONValidationError
→ llm_protocol_invalid / schema_invalid

ReviewGroupSchemaError
→ review_group_schema_invalid

RepairBudgetExceededError
→ repair_budget_exceeded
```

## 8. `core.schema`

### 8.1 StrictModel

所有模型输出 schema 继承统一 Base Schema：

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
    )
```

统一行为：

```text
额外字段拒绝；
严格类型；
不隐式 coercion；
不可变；
未知字段不得自动丢弃。
```

### 8.2 Strict JSON Gateway

`StrictJSONGateway` 负责将模型 content 转成 typed schema 对象。

必须拒绝：

```text
空 content
非 JSON 文本
Markdown code block
stringified JSON
多个 JSON 对象
注释式 JSON
未知字段
类型不匹配
空对象伪装成功
```

不得：

```text
用另一个 LLM 修复 JSON；
自动补字段；
忽略未知字段；
截断输出后继续；
把异常转为 None 或空数组。
```

是否进行结构性有界重试必须由配置显式启用，并进入审计。

### 8.3 公共校验器

至少提供：

```text
non_empty_string
single_line_string
bounded_string
unique_array
bounded_array
allowed_keys
selector_array
namespace_array
canonical_scope_string
```

### 8.4 稀疏 Delta 规则

Review 输出采用：

```text
字段出现 = 存在缺陷
数组元素 = 受影响 target
字段省略 = 无缺陷
```

禁止输出空数组作为默认值；无缺陷应输出空对象或省略字段，具体由该层
schema 明确规定。

## 9. `core.selectors`

### 9.1 Namespace

当前固定 namespace：

```text
t0：当前 turn 输入
c*：Turn Intent 控制协议字段
r*：request items

a*：当前回合 semantic anchors
p*：TrustedPetFactProjection

s*：系统物化 joint context scope
q*：contextual query propositions
```

### 9.2 职责

```text
分配 selector；
解析 selector；
校验 namespace；
校验数字范围；
比较顺序；
去重；
canonical 化 scope 字符串；
拒绝非法组合。
```

### 9.3 Scope canonical 规则

对于包含 `a*` 与 `p*` 的 scope：

```text
selector 去重；
a* 排在 p* 前；
a* 数字递增；
p* 数字递增；
至少两个 selector；
至少一个 a*；
不得输出 pure p* scope。
```

示例：

```text
p1+a3+a1 → a1+a3+p1
```

### 9.4 边界

core 不判断 scope 语义是否成立，只校验格式、namespace 和 canonical 顺序。

## 10. `core.llm`

### 10.1 定位

`LLMGateway` 是唯一模型调用入口，使用 LiteLLM 的 OpenAI-compatible chat
completions API。

不使用：

```text
厂商 SDK 直连；
LiteLLM key management API 作为运行时依赖；
自由 tool calling；
模型自主 function calling；
multi-agent 自由讨论。
```

### 10.2 核心契约

概念接口：

```python
class LLMGateway(Protocol):
    async def complete_json(
        self,
        *,
        role: ModelRole,
        prompt: RenderedPrompt,
        output_schema: type[T],
        context: ExecutionContext,
    ) -> LLMResponse[T]:
        ...
```

内部流程：

```text
1. 根据 role 读取模型配置
2. 检查并发与预算
3. 构造 OpenAI-compatible messages
4. 调用 LiteLLM
5. 分类传输 / 协议 / 授权错误
6. Strict JSON Gateway 解析
7. strict schema 校验
8. 返回 typed output 和执行 metadata
```

### 10.3 请求参数

每次调用显式携带：

```text
model
messages
temperature
max_tokens
timeout
request_id
model_role
prompt_id
prompt_version
response_format 配置
```

初始建议：

```text
低温度或 temperature=0
禁止 tools
禁止 tool_choice
max_tokens 由模型角色配置决定
```

### 10.4 重试策略

仅以下情况可传输层重试：

```text
网络瞬时错误
408
429
5xx
```

不自动重试：

```text
schema invalid
prompt invalid
模型协议错误
授权失败
输出超限导致的结构失败
```

### 10.5 并发与预算

core.llm 应支持：

```text
全局并发上限
role 级并发上限
timeout
请求次数预算
token 预算
cancellation
```

超限进入：

```text
llm_budget_exceeded
```

不得通过降低 schema 约束继续执行。

### 10.6 返回 metadata

返回并进入审计：

```text
model
model_role
prompt_id
prompt_version
prompt_sha256
response_id
latency_ms
prompt_tokens
completion_tokens
total_tokens
finish_reason
attempt_count
```

默认不进入普通日志：

```text
API key
Authorization header
完整 prompt
完整模型输出
```

完整输入输出是否写入专用审计存储，由 `VET_AGENT_AUDIT_MODE` 显式控制。

## 11. `core.prompts`

### 11.1 职责

core 提供：

```text
prompt registry
prompt loader
prompt renderer
prompt digest
prompt version 解析
prompt 与 schema / model role 绑定
```

### 11.2 Prompt 内容归属

core 不拥有语义 prompt 文案。

推荐资产位置：

```text
semantic modules / prompts
```

例如：

```text
semantic_decomposition/prompts/
turn_intent/
anchors/
scopes/
contextual_propositions/
```

core 只负责按 `prompt_id + prompt_version` 加载和渲染。

### 11.3 渲染边界

渲染器必须：

```text
显式插值；
包裹安全 tag；
避免把用户输入拼接为系统指令；
不暴露工程 metadata；
不暴露 API key / run_id / pet_id；
计算 prompt digest。
```

### 11.4 PromptRecord

```text
prompt_id
prompt_version
model_role
output_schema
template
sha256
```

所有模型调用必须绑定 PromptRecord，否则配置失败。

## 12. `core.review`

### 12.1 ReviewGroupSpec

语义层提供：

```text
group_id
prompt_id
prompt_version
allowed_defect_codes
allowed_targets
target_kind
output_schema
```

core 执行：

```text
加载 prompt；
调用 LLMGateway；
strict schema 校验；
allowlist 校验；
target selector 校验；
返回 group delta 和执行 metadata。
```

### 12.2 Group 校验

必须校验：

```text
字段名属于该 group allowlist；
数组非空；
数组元素唯一；
target 存在；
target namespace 正确；
不得输出其他 group 缺陷码；
不得输出 reason；
不得输出 confidence；
不得输出 corrected value；
不得输出 supported / rejected。
```

违反进入：

```text
review_group_schema_invalid
```

### 12.3 Delta Merger

core 确定性合并：

```text
同 code 同 target 去重；
不同 code 同 target 聚合；
不同 target 分离；
t0 / missing 类 target 单独聚合。
```

### 12.4 Defect Cluster Aggregator

输入：

```text
merged delta
defect code 到中文短语映射
target kind 规则
```

输出：

```text
target
target_kind
defect_codes
defect_phrases
```

core 不理解缺陷含义，不根据缺陷码推导修复动作。

## 13. `core.repair`

### 13.1 RepairTask

```text
repair_task_id
target
target_kind
defect_phrases
authorized_selectors
prompt_id
prompt_version
output_schema
attempt
```

### 13.2 调度职责

core 提供：

```text
修复任务排队；
串行 / 并行调度；
attempt 预算；
target 授权范围；
输出统一收集；
输出统一 schema 校验；
全部成功后才交给语义层 patch。
```

### 13.3 事务式边界

任一修复输出无效时：

```text
不提交任何 patch；
不部分应用成功结果；
进入 repair_schema_invalid 或 repair_failed。
```

### 13.4 Patch 归属

core 不定义：

```text
anchor replace / split / merge
request items join
scope join / renumber
proposition join / renumber
```

这些由语义层实现。

## 14. `core.execution`

### 14.1 ExecutionContext

每次请求或 workflow execution 包含：

```text
environment
route
conversation_id
turn_id
run_id
attempt_id
worker_id
started_at
trace_id
```

这些字段全部由系统生成，不由模型生成。

### 14.2 AuditEvent

基础事件类型：

```text
execution_started
dependency_checked
prompt_rendered
llm_called
llm_failed
schema_validated
schema_invalid
review_group_completed
delta_merged
defect_cluster_created
repair_scheduled
repair_completed
repair_failed
patch_applied
pipeline_completed
pipeline_failed
```

事件字段：

```text
event_id
event_type
timestamp
execution_context
step
status
input_digest
output_digest
model_metadata
prompt_metadata
error_code
duration_ms
```

### 14.3 审计原则

```text
append-only；
不可用普通日志替代执行审计；
模型原始输出按显式配置保存；
selector 不作为长期身份；
审计保存原始快照、Delta、缺陷簇、修复输出和最终快照所需的摘要。
```

## 15. `core.observability`

### 15.1 日志

统一提供结构化、脱敏 logger。

默认删除或拒绝：

```text
api_key
authorization
cookie
token
password
dsn
secret
```

普通日志默认不输出：

```text
完整用户输入
完整 prompt
完整模型输出
pet_id
内部数据库主键
```

### 15.2 指标

推荐指标：

```text
llm_requests_total
llm_request_duration_seconds
llm_tokens_total
llm_schema_invalid_total
review_group_requests_total
review_defects_total
repair_attempts_total
repair_failed_total
pipeline_duration_seconds
pipeline_status_total
dependency_ready_status
```

标签可使用：

```text
environment
pipeline
step
model_role
prompt_version
status
```

不得使用用户文本或密钥作为 label。

### 15.3 Tracing

trace 贯穿：

```text
入口
core pipeline
LLM 调用
review group
repair
Temporal workflow / activity
审计写入
```

trace id 与 ExecutionContext 关联。

## 16. `core.security`

### 16.1 AuthorizationClient

提供协议：

```python
class AuthorizationClient(Protocol):
    async def authorize(
        self,
        *,
        decision_path: str,
        input: Mapping[str, Any],
        context: ExecutionContext,
    ) -> AuthorizationDecision:
        ...
```

### 16.2 OPA adapter

OPA adapter 调用 Data API。

边界：

```text
OPA 只做授权；
不做语义审查；
不做模型输出治理；
不做临床安全判断。
```

授权失败进入：

```text
authorization_denied
```

OPA 不可达不得默认放行。

## 17. `core.persistence`

### 17.1 初始定位

初期只提供端口和可选 PostgreSQL 实现：

```text
ExecutionAuditRepository
PipelineRunRepository
ModelCallRepository
```

### 17.2 边界

不得把以下对象直接作为长期权威事实写入：

```text
semantic anchors
joint context scopes
contextual query propositions
request items
intent control protocol
```

这些是当前回合候选、overlay 或派生视图。患者事实治理、档案写入和长期
记忆治理必须由后续独立架构定义。

### 17.3 事务要求

审计写入失败是否阻断 pipeline 由配置明确：

```text
required audit：失败则 pipeline failed
optional audit：记录 persistence degraded，但不得伪装审计成功
```

## 18. `core.orchestration`

### 18.1 抽象

core 提供：

```text
PipelineDefinition
PipelineStep
StepResult
WorkflowContext
ActivityContext
```

语义 pipeline 可先用普通 async pipeline 实现，再映射到 Temporal。

### 18.2 Temporal 边界

Temporal 是可选 durable execution 适配，不是语义架构必要条件。

必须保持：

```text
固定拓扑；
模型不创建 workflow 节点；
模型不创建 activity；
模型不决定递归深度；
repair attempt 有界；
失败显式进入 workflow 状态。
```

Temporal 依赖应作为 optional extra，不迫使所有测试和环境启动 worker。

## 19. `core.readiness`

### 19.1 ReadinessResult

```text
dependency_name
required
status
error_code
latency_ms
```

### 19.2 初始 required

```text
配置合法
Prompt registry 可加载
LiteLLM 可用
```

### 19.3 按配置 required

```text
PostgreSQL audit
OPA
Temporal worker
```

### 19.4 不应默认 required

```text
Mem0
RAG
PDF parser
```

这些服务只有在对应功能启用时才参与 readiness。

## 20. `core.testing`

### 20.1 FakeLLM

必须能模拟：

```text
正常 JSON
schema invalid
空 content
非 JSON
Markdown JSON
stringified JSON
未知字段
网络错误
timeout
429
5xx
usage metadata
```

### 20.2 契约断言

提供统一断言：

```text
无额外字段；
无 reason / confidence；
selector 闭合；
scope canonical；
Delta 合并正确；
缺陷簇按 target 聚合；
修复不越权；
日志无密钥；
prompt version 进入审计；
失败状态显式。
```

### 20.3 测试替身

```text
FakeLLMGateway
FakePromptRegistry
FakeAuthorizationClient
InMemoryAuditRepository
FakeClock
FixedIdGenerator
```

## 21. 实施顺序

### Phase 1：最小执行底座

实现：

```text
core.config
core.errors
core.schema
core.selectors
core.prompts
core.llm
core.execution
core.observability
core.testing.fake_llm
```

验收：

```text
能通过 LiteLLM 发起 strict JSON 调用；
能加载版本化 prompt；
能校验薄 schema；
能分配 selector；
能生成审计事件；
FakeLLM 可覆盖主要失败。
```

### Phase 2：Review / Repair 框架

实现：

```text
core.review.group_runner
core.review.delta_merger
core.review.cluster_aggregator
core.repair.scheduler
```

验收：

```text
Group allowlist 生效；
Delta 确定性合并；
缺陷簇按 target 聚合；
repair attempt 有界；
任一修复输出无效时不提交 patch。
```

### Phase 3：外部治理与 durable execution

实现：

```text
core.security.opa_client
core.persistence.db
core.orchestration.temporal
core.readiness
```

验收：

```text
授权不默认放行；
审计写入显式成功或失败；
Temporal 可选启用；
依赖 readiness 分级；
核心契约不依赖具体外部服务实现。
```

## 22. 最小验收清单

正式实现合并前应满足：

```text
业务语义层不直接读取环境变量；
业务语义层不直接创建模型 client；
所有模型输出经过 Strict JSON Gateway；
所有 prompt 有 id / version / sha256；
所有 selector 由系统分配；
所有 Review Group 有 allowlist；
所有 Delta 由系统确定性合并；
所有 repair 有预算和授权范围；
所有执行有 ExecutionContext；
所有失败显式分类；
所有日志经过脱敏；
core 不 import 语义业务模块；
core 不包含兽医语义判断；
core 不包含临床安全判断；
OPA / PostgreSQL / Temporal 均为可替换端口；
FakeLLM 可覆盖关键失败场景。
```

## 23. 防漂移清单

实现和 code review 时必须检查：

```text
是否在语义层直接调用 OpenAI SDK；
是否在语义层直接读取 os.environ；
是否绕过 Strict JSON Gateway；
是否用 LLM 宽松修复 JSON；
是否让模型生成 selector；
是否把 p* 当作 source_ref；
是否把 selector 写入长期档案；
是否让 Review Group 输出其他 Group 缺陷码；
是否让 Review 输出 corrected value；
是否按缺陷码分别修复同一 target；
是否在全部修复输出前部分提交；
是否维护旧 selector 到新 selector 的权威映射；
是否把传输错误转成空数组；
是否把 schema invalid 转成 completed；
是否在日志输出密钥或完整 prompt；
是否让 core 理解医学缺陷含义；
是否让 core 执行临床判断；
是否让 OPA 做语义审查；
是否把 PostgreSQL 当作语义真相源；
是否让 Temporal 动态创建任务类型。
```

任一项不满足，应阻断实现合并。

## 24. 与其他文档的关系

```text
veterinary-semantic-decomposition-charter.md
  定义最高层语义治理主旨。

veterinary-semantic-preprocessing-control-conventions.md
  定义语义子层共享的 Review / Repair / selector / renumber 协议。

本文
  定义上述协议在 src/vet_agent/core 中的工程实现边界。

veterinary-turn-intent-architecture.md
veterinary-semantic-anchor-pipeline.md
veterinary-semantic-scope-architecture.md
veterinary-contextual-query-proposition-architecture.md
  定义各语义子层自身 schema、缺陷码、prompt 语义和 patch 语义。
```

## 25. 变更原则

本文只应因以下原因修订：

```text
core 模块边界调整；
模型调用协议调整；
strict schema 协议调整；
selector namespace 或 canonical 规则调整；
Review / Repair 执行协议调整；
审计或观测边界调整；
授权、持久化、编排或 readiness 边界调整；
Fail Fast 底线调整。
```

不得把具体 prompt 全文、临时环境地址、开发密钥、单个模型缺陷或局部工程
问题直接加入本文。
