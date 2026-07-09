# 兽医 Agent 组件清单

## 1. 文档目的

本文用于指导兽医 Agent 后续设计、开发、排期与职责拆分。

当前组件划分采用三层结构：

```text
L0 通用基础组件
L1 AI 通用运行组件
L2 兽医业务组件
```

划分原则：

- 通用组件负责“系统如何运行”：请求接入、编排、模型调用、工具调用、存储、配置、日志、Trace、流式发布等。
- 业务组件负责“兽医 Agent 应该如何判断和回答”：宠物绑定、问诊、急症、用药边界、化验单、知识库使用、安全红线、业务留痕等。

> 当前阶段 Agent 服务仅在局域网内访问，暂不引入 JWT、OAuth 等正式鉴权机制。`user_id`、`pet_id`、`session_id` 默认由上游客户端或 BFF 可信传入。

## 2. 总体组件视图

```text
客户端 / BFF
  |
  | 可信传入 user_id / pet_id / session_id
  v
L0 通用基础组件
  - API 接入
  - Session / Message Store
  - Checkpoint
  - Config
  - Observability
  |
  v
L1 AI 通用运行组件
  - Graph Runtime
  - Agent Runner
  - LLM Gateway
  - Tool Registry
  - RAG Platform
  - Guardrail Framework
  - Logic Trace Store
  |
  v
L2 兽医业务组件
  - Pet Session Policy
  - Vet Task Decomposer
  - Input Safety Assessor
  - Vet Context Builder
  - Standard Consultation Agent
  - Education Agent
  - Safety Trigger Agent
  - Non-medical Agent
  - Output Safety Reviewer
  - Deterministic Fallback Gate
  - Lab OCR / Reference Range
  - Veterinary Memory
  - Response Composer
  - Evaluation Suites
```

## 4. L0 通用基础组件

### 4.1 API 接入组件

组件名：`ApiIngress`

职责：

- 接收客户端 / BFF 请求。
- 校验基础请求格式。
- 转发到 Agent 编排服务。
- 支持同步或流式响应。
- 处理上传附件元信息。
- 注入 request_id / trace_id。

### 4.2 Session / Message Store

组件名：`ConversationStore`

职责：

- 创建和读取 session。
- 存储用户消息与助手消息。
- 支持按 `session_id` 查询消息。
- 支持消息 metadata。
- 支持消息与 `pet_id` 绑定。

### 4.3 Checkpoint 组件

组件名：`CheckpointStore`

职责：

- 持久化图编排状态。
- 保存节点执行结果。
- 支持失败恢复。
- 保存 session 业务状态。

### 4.4 配置与参数组件

组件名：`RuntimeConfig`

职责：

- 加载运行参数。
- 管理参数版本。
- 支持环境覆盖。
- 在逻辑链中记录 `params_version`。

### 4.5 Observability 组件

组件名：`Observability`

职责：

- 记录请求耗时。
- 记录节点耗时。
- 记录模型调用耗时与 token。
- 记录工具调用耗时。
- 记录错误、超时、fallback。
- 支持运行指标看板。

## 5. L1 AI 通用运行组件

### 5.1 图编排运行时

组件名：`GraphRuntime`

职责：

- 定义节点与边。
- 支持条件分支。
- 支持并行子任务。
- 支持超时、重试、失败恢复。
- 支持与 checkpoint 集成。
- 支持分段流式发布。

### 5.2 Agent Runner

组件名：`AgentRunner`

职责：

- 调用指定模型。
- 注入 prompt。
- 绑定工具权限。
- 解析结构化输出。
- 做 schema 校验。
- 处理模型超时与重试。

### 5.3 LLM Gateway

组件名：`LlmGateway`

职责：

- 统一接入模型供应商。
- 管理模型配置。
- 管理调用超时。
- 统计 token。
- 支持模型降级。
- 支持不同 Agent 使用不同模型。

### 5.4 Tool Registry

组件名：`ToolRegistry`

职责：

- 注册工具。
- 管理工具 schema。
- 管理工具权限。
- 记录工具调用。
- 管理工具超时。

### 5.5 RAG 平台

组件名：`RagPlatform`

职责：

- 文档导入。
- 文档切片。
- embedding。
- 检索。
- rerank。
- 返回引用元数据。
- 管理来源版权标记。

### 5.6 Guardrail Framework

组件名：`GuardrailFramework`

职责：

- 提供 pre-gen hook。
- 提供 post-gen review hook。
- 提供 deterministic gate hook。
- 统一记录 guard action。
- 支持 allow / rewrite / block / fallback。

### 5.7 逻辑链留痕组件

组件名：`LogicTraceStore`

职责：

- 保存每轮关键决策链。
- 保存节点输入输出摘要或哈希。
- 保存模型调用摘要。
- 保存工具调用摘要。
- 保存最终响应。
- 保存 fallback 与 guard actions。

## 6. L2 兽医业务组件

### 6.1 宠物会话策略组件

组件名：`PetSessionPolicy`

职责：

- 校验请求必须携带 `pet_id`。
- 新 session 绑定 `pet_id`。
- 老 session 校验 `pet_id` 一致。
- 不一致时返回错误，提示客户端新开 session。
- 禁止 Agent 自行定宠。
- 禁止 session 内切宠。
- 不解析用户文本中的宠物指代、宠物名错别字或近似称呼。
- 文本归一化 / 纠错候选不得作用于结构化 `pet_id`，也不得改变 session 绑定关系。

### 6.2 兽医多任务拆解组件

组件名：`VetTaskDecomposer`

职责：

- 将单轮输入拆成 1-N 个同宠子任务。
- 标注任务类型。
- 标注任务优先级。
- 判断附件是否作为医疗依据，还是拆独立 OCR 段。

### 6.3 输入安全与剖面判决组件

组件名：`VetInputSafetyAssessor`

职责：

- 检出 SAF-01 毒物信号。
- 检出 SAF-03 急症信号。
- 标注非医疗跨域 L1 / L2 / L3。
- 判断 intent。
- 判断 `generation_profile`。
- 判断 route。
- 输出 `compression_strategy`。
- 记录 `disambiguation_method`。

### 6.4 领域上下文适配组件

组件名：`VetContextBuilder`

职责：

- 按当前 `pet_id` 装配上下文。
- 读取 `CoreFactSnapshot`。
- 回源 API 校验和补全。
- 丢弃非当前 `pet_id` 数据。
- 读取 session checkpoint。
- 生成 `slot_coverage`。
- 生成 `prompt_blocks`。
- 执行上下文压缩策略。
- trim 后强制注入 P0 字段。
- 记录 `compression_audit`。

### 6.5 兽医记忆组件

组件名：`VetMemoryService`

职责：

- 管理宠物级记忆。
- 管理主人级偏好。
- 管理 `CoreFactSnapshot`。
- 管理 session `rolling_summary`。
- 支持查看、纠正、删除。
- 支持异步 Memory Writer。

### 6.6 标准问诊 Agent

组件名：`StandardConsultationAgent`

适用剖面：`standard`

职责：

- 症状咨询。
- 分诊与紧急度判断。
- 方向提示。
- 鉴别方向。
- 处置和非处方级建议。
- 主动追问。
- 每轮最多 1-3 个问题。
- 四层跨轮递进。
- 使用 RAG 接地。
- 输出依据。

### 6.7 科普 Agent

组件名：`EducationAgent`

适用剖面：`education`

职责：

- 回答科普、假设、通识问题。
- 使用 RAG 接地。
- 以当前问题为叙事主轴。
- 可自然接续近期话题。
- 不进入问诊追问链。
- 不输出四层诊断结构。

### 6.8 急症 Agent

组件名：`SafetyTriggerAgent`

适用剖面：`safety_trigger`

职责：

- 输出急症简版。
- 明确就医导向。
- 输出 A 级护理要点。
- 最多 0-1 个关键确认。
- 禁止 RAG。
- 禁止完整鉴别诊断长文。
- 原则上不输出个案用药建议。

### 6.9 非医疗养宠 Agent

组件名：`NonMedicalPetCareAgent`

职责：

- 饲养建议。
- 行为建议。
- 日常护理建议。
- 基于物种、年龄、体重、生活方式个性化。
- 对 L1 / L2 跨域信号嵌入轻量观察或就医提示。

### 6.10 用药策略组件

组件名：`MedicationPolicy`

职责：

- 定义 T0-T4 用药表述阶梯。
- 允许 T2 药名。
- 允许 T3 使用建议。
- 禁止 T4 精确计量。
- 为生成 Agent 提供边界说明。
- 为安全审查与兜底门提供检测规则。

### 6.11 输出安全审查 Agent

组件名：`VetOutputSafetyReviewer`

职责：

- 独立审查 `draft_response`。
- 删除或改写 T4。
- 检查毒物建议。
- 检查急症是否缺就医导向。
- 检查涉医输出是否缺免责。
- 检查是否伪造检查值。
- 检查是否把 OCR / 病历里的剂量转成新的用药建议。
- 检查剖面边界。

### 6.12 化验 OCR 业务组件

组件名：`LabOcrService`

职责：

- 接收 OCR 原始文本或表格。
- 识别检验项。
- 抽取数值。
- 抽取单位。
- 抽取报告印刷参考区间。
- 生成用户确认文本。
- 用户确认后写入结构化结果。

### 6.13 参考区间策略组件

组件名：`ReferenceRangePolicy`

职责：

- 执行 P1 / P2 / P4 参考区间策略。
- P1：报告印刷参考区间优先。
- P2：内置表匹配物种、生命阶段、单位后使用。
- P4：无法匹配时不标异常。
- 禁止 LLM 猜参考区间。

### 6.14 回复合成与分段发布组件

组件名：`VetResponseComposer`

职责：

- 合成多任务输出。
- 生成 `segments[]`。
- 保证急症段优先。
- 保证医疗段优先于非医疗段。
- 保证独立 OCR 段位于医疗段之后、非医疗段之前。
- 避免急症就医导向被稀释。
- 为流式发布提供段顺序。

### 6.15 兽医逻辑链 schema 组件

组件名：`VetTraceSchema`

职责：

- 定义 A/B/C 三级业务留痕字段。
- 定义每轮逻辑链结构。
- 定义子任务和 segment 留痕。
- 定义 guard actions 留痕。
- 定义 RAG、OCR、slot、stop_reason 留痕。

### 6.16 业务验收集组件

组件名：`VetEvaluationSuites`

职责：

- 管理红队 P0 用例。
- 管理 SAF-01 用例。
- 管理急症用例。
- 管理 T4 用药用例。
- 管理跨域非医疗用例。
- 管理路由回归用例。
- 管理 OCR / 参考区间用例。
- 输出可回归测试报告。
