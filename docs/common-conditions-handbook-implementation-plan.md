<!--
文件：docs/common-conditions-handbook-implementation-plan.md
作用：沉淀 common_conditions_handbook.md 功能工单的完整实现方案，说明结构化临床知识资产如何入库、审核、发布并被 Agent RAG 链路消费。
说明：本文档面向 Agent 开发、后端/BFF、后台运维和临床审核同事；仅描述方案与接口边界，不包含具体代码实现。
-->

# 常见病症临床手册结构化知识资产实现方案

## 1. 背景

当前项目已经具备兽医 Agent 的核心运行链路：

```text
用户请求
  -> 安全分诊
  -> 宠物上下文
  -> 记忆读取
  -> 任务拆分
  -> 语义抽取
  -> 多轮问诊状态
  -> RAG 检索
  -> RAG 动态追问
  -> 最终回答生成
  -> 输出安全复审
  -> 记忆和 trace 持久化
```

`tmp/feature/docs/common_conditions_handbook.md` 提供了常见犬猫病症卡，包括典型表现、鉴别方向、病症特异追问、分诊建议、红旗升级、用药方向和居家护理原则。该文档的价值不在于替代兽医诊断，而在于为 Agent 提供更可靠的问诊与回答依据，避免系统退化为固定槽位追问或关键词决策树。

本方案建议将该手册转化为可治理、可审核、可发布、可回滚的结构化临床知识资产，并通过 PostgreSQL + pgvector 的 RAG 链路供 Agent 使用。

## 2. 总体结论

应将 `common_conditions_handbook.md` 对应的结构化数据作为稳定的后台运维能力实现，核心形态是：

```text
结构化病症卡 JSON
  -> Admin API 创建导入批次
  -> schema 与临床安全校验
  -> 字段级 chunk 拆分
  -> embedding 向量化
  -> pending_review
  -> 兽医/管理员审核
  -> approved
  -> 发布快照
  -> Agent 运行时只检索已发布知识
```

不建议长期依赖手工脚本直接写库。脚本可以保留为 bootstrap、灾备和离线修复工具，但生产知识变更应通过稳定 Admin API 完成。

不建议以语义匹配或字符串匹配实现病症决策树。正确方式是：关键词和向量检索只负责召回候选知识，最终追问与回答由 LLM 在知识证据、问诊状态、安全规则约束下动态生成。

## 3. 目标

本工单最终应达成以下目标：

| 目标 | 说明 |
| --- | --- |
| 知识资产化 | 将常见病症手册从静态文档变成可版本化、可审核、可发布的结构化资产。 |
| 动态追问 | 使用病症卡的 `followupQuestions` 反推高价值追问，避免固定模板式追问。 |
| 证据展示 | 将用户回答、宠物资料和知识卡证据整合到 `reasoning_display`。 |
| 安全分诊补强 | 将病症卡中的 `redFlagsEscalate` 作为安全层与回答层的重要依据。 |
| 运维友好 | 后台可以导入、预览、审核、发布、禁用、回滚知识。 |
| 可审计 | 所有导入、审核、发布、禁用、回滚动作均写入审计日志。 |
| 可回归测试 | 建立病例回归集，验证追问质量、分诊安全和非决策树行为。 |

## 4. 非目标

本方案不追求：

- 不将 Agent 改造成确诊系统。
- 不让病症卡直接决定最终诊断。
- 不把每个病症写成硬编码 if-else 分支。
- 不让用户上传的知识未经审核直接进入线上召回。
- 不在用户侧暴露完整隐藏推理链。
- 不提供处方药具体剂量或替代线下兽医诊疗。

## 5. 当前代码承接点

当前代码已有以下可复用模块：

| 模块 | 当前职责 | 本工单中的作用 |
| --- | --- | --- |
| `KnowledgeService` | RAG 检索 facade | 继续作为 Agent 运行时知识检索入口。 |
| `PostgresKnowledgeRepository` | PostgreSQL/pgvector 检索 | 支持从已发布知识 chunk 中召回候选病症卡。 |
| `KnowledgeChunkModel` | RAG chunk 表 | 可短期复用为病症字段级 chunk 存储。 |
| `RagQuestionPlannerAgent` | 基于 RAG 证据生成追问 | 使用病症卡 `followupQuestions` 生成病症特异追问。 |
| `ConsultationStateAgent` | 多轮问诊状态收敛 | 保存用户下一轮回答，避免一次性武断回答。 |
| `ResponseComposer` | 最终回答生成 | 使用病症卡证据生成阶段性建议。 |
| `ReasoningDisplayBuilder` | 用户可见证据展示 | 展示“思考过程”形式的诊断证据。 |
| `RagGovernanceService` | RAG chunk 治理 | 扩展为结构化知识资产治理入口。 |
| `/admin/rag/*` | RAG 管理接口 | 扩展导入、审核、发布、回滚 API。 |
| `rag_audit_events` | RAG 审计事件 | 继续记录知识变更审计。 |

## 6. 推荐文件树

本方案建议按以下标准文件树扩展。实际实现时保持现有 `src/vet_agent` 一级包暴露原则，不跨包直接引用内部实现。

```text
docs/
  common-conditions-handbook-implementation-plan.md
    # 本文档：说明结构化临床知识资产完整方案。

src/
  vet_agent/
    api/
      admin_routes.py
        # 承载结构化知识资产的 Admin API 路由。
    services/
      clinical_knowledge.py
        # 负责导入批次、病症卡校验、chunk 生成、发布快照等业务服务。
      rag_governance.py
        # 保留 chunk 启停、审核、统计等治理能力，可被 clinical_knowledge 复用。
    repositories/
      clinical_knowledge.py
        # 负责病症卡、导入批次、发布快照的数据访问。
      knowledge.py
        # 继续负责 Agent 运行时 RAG 检索。
    agents/
      rag_question_planner.py
        # 增强对 condition_card / followup_questions 类型知识的消费。
      composer.py
        # 增强最终回答对病症卡字段级证据的使用。
    db/
      models.py
        # 新增结构化病症卡、导入批次、发布快照模型。

alembic/
  versions/
    0005_clinical_knowledge_assets.py
      # 新增结构化临床知识资产相关表与索引。

scripts/
  import_clinical_conditions.py
    # 可选：仅作为 bootstrap 或离线灾备，不作为生产主入口。

tests/
  test_clinical_knowledge_admin_api.py
    # 覆盖导入、校验、审核、发布、回滚。
  test_common_conditions_rag_followup.py
    # 覆盖病症特异追问与非决策树行为。
```

## 7. 数据来源策略

优先使用结构化 JSON：

```text
tmp/feature/data/vet_conditions.json
```

该文件比 Markdown 更适合作为机器入库源，因为它已经包含稳定字段：

| 字段 | 用途 |
| --- | --- |
| `system` | 疾病所属系统，用于筛选、统计和召回增强。 |
| `condition` | 病症名称和用户常见表达，用于标题、召回和展示。 |
| `presentation` | 典型表现，用于召回和最终回答依据。 |
| `differentials` | 鉴别方向，用于最终回答中的“可能方向与依据”。 |
| `followupQuestions` | 最高价值字段，用于动态追问生成。 |
| `triage` | 分诊建议，用于阶段性建议和线下兜底。 |
| `redFlagsEscalate` | 红旗升级，用于安全层补强和回答兜底。 |
| `medicationDirection` | 用药方向，仅允许方向性描述，不允许剂量。 |
| `homeAdvice` | 居家护理，用于非急症情况下的安全建议。 |
| `source` | 来源追溯，用于 `references` 和临床审核。 |

Markdown 文件继续作为人工审查材料和来源说明，不建议运行时直接解析 Markdown。

## 8. 数据模型方案

### 8.1 短期兼容方案

短期可以继续复用 `knowledge_chunks` 表，不新增主表，仅通过 `metadata` 保存病症卡结构。

优点：

- 改造成本低。
- 当前 `PostgresKnowledgeRepository` 可继续使用。
- 当前 `/admin/rag/chunks` 可继续治理。

缺点：

- 病症卡完整结构不够清晰。
- 审核、发布、回滚只能围绕 chunk 操作，难以围绕“病症卡版本”操作。
- 后台运维不容易查看一张完整病症卡。

适用场景：快速验证 RAG 追问质量。

### 8.2 生产推荐方案

生产建议新增结构化病症卡主表，并继续将派生 chunk 写入 `knowledge_chunks`。

#### 8.2.1 `clinical_condition_cards`

保存每张病症卡完整结构。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键。 |
| `condition_key` | 稳定业务键，建议由 `system + condition` 归一化生成。 |
| `system` | 所属系统。 |
| `condition` | 病症标题。 |
| `presentation` | 典型表现。 |
| `differentials` | 鉴别诊断。 |
| `followup_questions` | 病症特异追问。 |
| `triage` | 分诊建议。 |
| `red_flags_escalate` | 升级红旗。 |
| `medication_direction` | 用药方向。 |
| `home_advice` | 居家护理。 |
| `source` | 来源。 |
| `source_document` | 来源文档，例如 `common_conditions_handbook.md`。 |
| `source_version` | 来源版本。 |
| `content_hash` | 内容 hash，用于去重和变更识别。 |
| `review_status` | `draft / pending_review / approved / rejected / quarantined`。 |
| `enabled` | 是否启用。 |
| `quality_score` | 质量分。 |
| `clinical_review_required` | 是否需要临床审核。 |
| `reviewer_id` | 审核人。 |
| `last_reviewed_at` | 审核时间。 |
| `ingestion_batch` | 导入批次。 |
| `metadata` | 扩展信息。 |
| `created_at` | 创建时间。 |
| `updated_at` | 更新时间。 |

#### 8.2.2 `clinical_knowledge_ingestion_batches`

记录每次导入。

| 字段 | 说明 |
| --- | --- |
| `batch_id` | 导入批次 ID。 |
| `source_type` | `upload / oss_url / local_seed / api_payload`。 |
| `source_ref` | 文件名、OSS 地址或来源描述。 |
| `source_hash` | 原始文件 hash。 |
| `status` | `created / validating / vectorizing / pending_review / failed / completed / published`。 |
| `total_cards` | 病症卡总数。 |
| `valid_cards` | 校验通过数量。 |
| `invalid_cards` | 校验失败数量。 |
| `chunk_count` | 生成 chunk 数量。 |
| `embedding_model` | embedding 模型。 |
| `embedding_dimension` | 向量维度。 |
| `created_by` | 操作人。 |
| `error_report` | 错误报告。 |
| `created_at` | 创建时间。 |
| `updated_at` | 更新时间。 |

#### 8.2.3 `clinical_knowledge_publication_snapshots`

记录线上发布版本。

| 字段 | 说明 |
| --- | --- |
| `snapshot_id` | 发布快照 ID。 |
| `batch_id` | 来源导入批次。 |
| `status` | `active / inactive / rolled_back`。 |
| `published_by` | 发布人。 |
| `published_at` | 发布时间。 |
| `rollback_from_snapshot_id` | 若为回滚产生，记录来源快照。 |
| `notes` | 发布说明。 |

### 8.3 `knowledge_chunks` 的增强约定

无论是否新增病症卡主表，最终都应生成字段级 RAG chunk。

建议 `knowledge_chunks.metadata` 使用如下结构：

```yaml
type: clinical_condition_card
  # 知识资产类型。运行时可据此识别这是病症卡，而不是普通文档摘要。

condition_key: gastrointestinal__acute_vomiting
  # 病症卡稳定业务键。用于把多个字段 chunk 聚合回同一张病症卡。

condition_id: 123
  # 如果新增 clinical_condition_cards 表，则记录主表 ID。

condition_title: 急性呕吐
  # 面向后台和 reasoning_display 的标题。

system: 肠胃消化
  # 所属系统，用于后台筛选和检索增强。

field: followup_questions
  # 当前 chunk 来自哪个字段。追问规划应优先消费 followup_questions。

field_priority: 10
  # 字段优先级。followup_questions、red_flags、triage 通常优先级更高。

source_document: tmp/feature/docs/common_conditions_handbook.md
  # 人工审查文档来源。

source_data: tmp/feature/data/vet_conditions.json
  # 机器入库源。

clinical_review_required: true
  # 临床审核标记。未审核内容不能发布到生产召回。
```

## 9. Chunk 拆分策略

一张病症卡不建议作为一个大 chunk 入库。应按字段拆分，以提高召回精度。

推荐拆分：

| chunk 类型 | 内容 | 主要消费者 |
| --- | --- | --- |
| `condition_overview` | `condition + presentation + differentials` | 召回候选病症方向、最终回答。 |
| `followup_questions` | `condition + followupQuestions` | `RagQuestionPlannerAgent`。 |
| `triage` | `condition + triage` | `ResponseComposer`、分诊建议。 |
| `red_flags` | `condition + redFlagsEscalate` | 安全补强、兜底提醒。 |
| `medication_direction` | `condition + medicationDirection` | 用药方向，但必须经过输出安全审查。 |
| `home_advice` | `condition + homeAdvice` | 非急症居家护理。 |

每个 chunk 的标题建议格式：

```text
常见病症手册 / 肠胃消化 / 急性呕吐 / followup_questions
```

这样后台列表、引用展示和审计都更清楚。

## 10. Admin API 方案

结构化临床知识资产应作为内部 Admin API 实现，供后台运维和审核系统调用。该 API 应复用当前 `AccessControlService` 的鉴权能力，不面向普通用户开放。

### 10.1 API 总览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/admin/clinical-knowledge/condition-batches` | 创建病症卡导入批次。 |
| `GET` | `/admin/clinical-knowledge/condition-batches/{batch_id}` | 查询导入批次详情。 |
| `POST` | `/admin/clinical-knowledge/condition-batches/{batch_id}/validate` | 执行 schema 和临床安全校验。 |
| `GET` | `/admin/clinical-knowledge/condition-batches/{batch_id}/preview` | 预览病症卡与 chunk 拆分结果。 |
| `POST` | `/admin/clinical-knowledge/condition-batches/{batch_id}/vectorize` | 执行 embedding 生成。 |
| `POST` | `/admin/clinical-knowledge/condition-batches/{batch_id}/submit-review` | 提交临床审核。 |
| `POST` | `/admin/clinical-knowledge/conditions/{condition_id}/review` | 审核单张病症卡。 |
| `POST` | `/admin/clinical-knowledge/publications` | 发布一个审核通过的批次或快照。 |
| `POST` | `/admin/clinical-knowledge/publications/{snapshot_id}/rollback` | 回滚到指定发布快照。 |
| `GET` | `/admin/clinical-knowledge/conditions` | 分页查询病症卡。 |
| `PATCH` | `/admin/clinical-knowledge/conditions/{condition_id}` | 启用、禁用、隔离或更新病症卡元信息。 |
| `GET` | `/admin/clinical-knowledge/stats` | 查询覆盖率、审核状态、chunk 数量和发布状态。 |

### 10.2 创建导入批次

```http
POST /admin/clinical-knowledge/condition-batches
Content-Type: application/json
```

```yaml
source_type: oss_url
  # 支持 oss_url、upload、api_payload、local_seed。生产推荐 oss_url 或 upload。

source_ref: oss://infra-prod-file-storage/rag/vet_conditions_20260718.json
  # 源文件地址。若来自后台上传，建议先落 OSS，再传 OSS 地址给 Agent。

source_document: tmp/feature/docs/common_conditions_handbook.md
  # 人工审查对应文档，用于追溯。

source_version: v1-20260718
  # 临床知识版本。发布和回滚时用它标识本批内容。

clinical_review_required: true
  # 生产必须为 true。未审核批次不得进入线上召回。

metadata:
  uploader: backend_admin
  # 后台传入的补充信息，便于审计。
```

响应：

```json
{
  "batch_id": "ckb_20260718_001",
  "status": "created",
  "source_type": "oss_url",
  "source_ref": "oss://infra-prod-file-storage/rag/vet_conditions_20260718.json"
}
```

### 10.3 校验批次

校验应包含两类。

第一类是结构校验：

- JSON 必须包含 `conditions` 数组。
- 每条必须包含 `system`、`condition`、`presentation`、`followupQuestions`、`triage`、`redFlagsEscalate`、`source`。
- `followupQuestions` 不应为空。
- `source` 不应为空。
- 重复 `condition_key` 应标记为冲突。

第二类是临床安全校验：

- `medicationDirection` 中不允许出现具体剂量建议。
- 红旗字段为空的病症卡不得自动通过。
- 出现“自行催吐”“人药退烧”“具体剂量”等危险表达必须隔离。
- 标注 `clinical_review_required=true` 的内容必须经审核才能发布。

校验响应示例：

```json
{
  "batch_id": "ckb_20260718_001",
  "status": "validated",
  "total_cards": 105,
  "valid_cards": 103,
  "invalid_cards": 2,
  "warnings": [
    {
      "condition": "示例病症",
      "field": "medicationDirection",
      "message": "疑似包含剂量表达，需要人工审核"
    }
  ]
}
```

### 10.4 预览 chunk

预览接口用于后台在真正写入线上检索前确认拆分效果。

```http
GET /admin/clinical-knowledge/condition-batches/{batch_id}/preview
```

响应中应展示：

- 病症卡数量。
- 每张病症卡生成的 chunk 数量。
- 每个 chunk 的标题、字段类型、内容预览。
- 是否会复用旧卡、更新旧卡或新增卡。
- 是否存在待隔离内容。

### 10.5 向量化

```http
POST /admin/clinical-knowledge/condition-batches/{batch_id}/vectorize
```

```yaml
embedding_model: text-embedding-v4
  # 由服务端配置决定，外部请求只能作为期望值或审计说明，不能绕过服务端白名单。

embedding_dimension: 1024
  # 必须与 pgvector collection / 字段维度一致。此前 Mem0 已按 1024 维重建，应保持一致。

rebuild_existing: false
  # false 表示仅对新增或内容 hash 变化的 chunk 重新向量化，降低成本。

dry_run: false
  # true 仅计算计划，不写入 embedding。
```

向量化应是异步任务，不建议让 HTTP 请求长时间阻塞。API 可以返回任务状态，由后台轮询。

### 10.6 审核与发布

审核动作：

```http
POST /admin/clinical-knowledge/conditions/{condition_id}/review
Content-Type: application/json
```

```yaml
review_status: approved
  # 可选 approved、rejected、quarantined。只有 approved 才允许发布。

quality_score: 0.92
  # 临床质量评分，用于检索排序或后台治理。

reason: 持证兽医审核通过，来源和用药口径已确认。
  # 审核说明，写入 rag_audit_events。
```

发布动作：

```http
POST /admin/clinical-knowledge/publications
Content-Type: application/json
```

```yaml
batch_id: ckb_20260718_001
  # 发布哪个导入批次。发布前必须确认该批次全部必要条目已 approved。

publication_mode: replace_current
  # replace_current 表示以本批次作为新的线上快照；append 表示增量追加，需谨慎使用。

notes: 发布常见病症手册 v1，用于 RAG 动态追问。
  # 发布说明，便于回滚和审计。
```

发布后，Agent 运行时只读取当前 active snapshot 内的知识。

### 10.7 回滚

```http
POST /admin/clinical-knowledge/publications/{snapshot_id}/rollback
Content-Type: application/json
```

```yaml
reason: 新版本追问质量异常，临时回滚到上一稳定快照。
  # 回滚原因必须写入审计日志。

disable_rolled_back_batch: true
  # 是否同时禁用被回滚批次，避免继续被检索。
```

## 11. Agent 运行时消费链路

### 11.1 第一轮信息不足

```text
用户输入
  -> SafetyAgent 输入安全分诊
  -> PetContextProvider 读取宠物资料
  -> MemoryService 读取历史记忆
  -> ConsultationSemanticExtractorAgent 抽取本轮事实
  -> ConsultationStateAgent 更新问诊状态
  -> 判断信息不足
  -> KnowledgeService 检索病症卡字段级 chunk
  -> RagQuestionPlannerAgent 生成病症特异追问
  -> ReasoningDisplayBuilder 生成用户可见依据
  -> 返回 requires_followup
```

关键要求：

- 知识检索应使用“用户输入 + 宠物资料 + 已知槽位 + 语义抽取结果 + 当前缺失证据”构造 query。
- 召回应保留多个候选病症方向，避免 top-1 误导。
- 追问应优先来自 `followup_questions` 类型 chunk。
- LLM 只能生成追问，不能在该阶段给诊断或处理方案。

### 11.2 用户下一轮补充

```text
用户继续使用同一 user_id + pet_id + session_id
  -> 系统读取 consultation_states
  -> 跳过或弱化任务拆分
  -> 将本轮回答合并进 slots / semantic_extraction
  -> 重新评估是否 ready
  -> 如果仍缺高价值信息：继续 RAG 追问
  -> 如果信息足够：进入最终回答
```

关键要求：

- 不重复询问已确认事实。
- 不因用户换一种表达就丢失上一轮上下文。
- 若达到最大追问轮数，且已有最低可回答上下文，应给阶段性回答，并明确不确定性。

### 11.3 最终回答

```text
ConsultationStateAgent ready=true
  -> KnowledgeService 检索相关病症卡
  -> ResponseComposer 使用 presentation / differentials / triage / homeAdvice / redFlags
  -> SafetyAgent 输出安全审查
  -> ReasoningDisplayBuilder 生成诊断证据展示
  -> MemoryExtractionAgent 抽取事实记忆
  -> 返回 completed
```

最终回答应包含：

- 分诊/紧急度。
- 可能方向与依据。
- 当前可以观察和记录什么。
- 哪些情况需要尽快就医或急诊。
- 用药只给方向，不给剂量。
- 不替代线下兽医诊断。

## 12. RAG 检索策略

检索不应只依赖用户原始输入。建议查询构造包含：

```yaml
user_text: 用户本轮原文
  # 捕捉用户自然表达。

pet_context_summary: 物种、年龄、体重、品种、性别、绝育等
  # 物种和年龄会显著影响风险分层。

consultation_state:
  # 多轮累积状态，避免每轮都像第一次问诊。
  chief_complaint: 首轮主诉
  slots: 已确认事实
  missing_slots: 当前仍缺高价值证据

semantic_extraction:
  # LLM 抽取出的归一化事实，例如“夜间活动改变”“饮水增多”“无呕吐”。

retrieval_intent: followup_planning
  # 检索目的。追问阶段优先召回 followup_questions 和 red_flags。
```

推荐检索策略：

1. 向量召回 top 12。
2. 过滤 `enabled=true`、`review_status=approved`、`published_snapshot=active`。
3. 按字段类型重排，追问阶段提高 `followup_questions`、`red_flags` 权重。
4. 同一病症卡多个 chunk 命中时聚合，避免候选过度重复。
5. 传给 LLM 的候选知识控制在 4 到 8 张病症卡，避免上下文污染。

## 13. 追问生成策略

`RagQuestionPlannerAgent` 应在 prompt 中明确：

- 只生成追问，不回答诊断。
- 每个问题必须面向宠物主人。
- 问题要口语化、具体、可回答。
- 优先询问会影响分诊等级的信息。
- 不重复询问已确认事实。
- 不照搬固定槽位模板。
- 不询问与候选病症无关的低价值信息。
- 最多输出 `max_followup_questions` 个问题。

输出结构建议：

```json
{
  "questions": [
    {
      "slot": "domain_specific",
      "question": "它最近排尿的次数、尿团大小，和平时相比有没有明显变化？",
      "reason": "该信息有助于区分单纯行为变化与泌尿或全身性疾病风险。",
      "evidence_titles": ["常见病症手册 / 泌尿系统 / 多饮多尿 / followup_questions"],
      "priority": 10
    }
  ]
}
```

## 14. reasoning_display 展示策略

`reasoning_display` 应展示用户可理解的诊断证据摘要，而不是隐藏链式推理。

追问阶段可展示：

```text
我先核对了本轮主诉、宠物资料和已知问诊状态。
目前已知：猫、4 岁、体重 4.6kg、最近躲藏和夜间活动节奏变化。
参考知识包括：行为异常、疼痛/活动异常、泌尿异常、慢性全身性问题等候选病症卡。
这些候选方向需要先确认排尿、饮水、食欲和疼痛线索，因此本轮先补充高价值追问，避免直接下结论。
```

最终回答阶段可展示：

```text
我先做安全分诊，未发现需要立刻中断普通问诊流程的安全信号。
随后结合用户补充的精神、食欲、排尿、活动变化和系统已知宠物资料。
本轮可展示依据包括用户回答、常见病症手册中的候选病症卡和公开来源摘要。
基于这些信息，回复只给阶段性方向、观察要点和就医触发条件，不替代线下兽医诊断。
```

## 15. 安全层关系

`common_conditions_handbook.md` 不是安全层的唯一来源。安全层仍应以 `clinical_safety_reference.md` 和 `vet_safety_reference.json` 为主。

病症卡中的 `redFlagsEscalate` 应用于：

1. 最终回答中的就医触发条件。
2. RAG 追问时优先确认高风险变量。
3. 安全层语义识别的补充训练/测试材料。
4. reasoning_display 中的风险依据展示。

安全层原则：

- 命中 L1/L2 急症时，不进入普通追问路径。
- 未命中不等于安全，信息不足时从严。
- 药物剂量、人药、毒物、催吐禁忌必须继续由安全层硬拦截。
- `medicationDirection` 只能作为方向性材料，输出仍必须经过 `SafetyAgent.sanitize_output`。

## 16. 避免退化为决策树

应避免以下实现：

```text
用户说“耳朵臭”
  -> 字符串命中“外耳炎”
  -> 固定追问“单耳还是双耳、分泌物什么颜色”
  -> 固定回答外耳炎
```

这种方式的问题：

- 容易忽略耳螨、马拉色菌、细菌性外耳炎、中耳炎等相近候选。
- 难处理否定表达和多任务。
- 难处理跨轮补充。
- 用户换个说法就可能漏召回。
- 召回错误会直接导致下游错误。

推荐实现：

```text
用户说“耳朵臭、甩头”
  -> 向量和文本召回多个候选病症卡
  -> 聚合外耳炎、耳螨、马拉色菌、细菌性外耳炎等候选
  -> LLM 根据候选卡选择最能区分风险和方向的问题
  -> 用户补充后更新状态
  -> 信息足够时给阶段性判断，而不是确诊
```

也就是说：

- 字符串匹配可作为召回增强。
- 向量相似度可作为候选排序。
- LLM 可作为追问和回答生成器。
- 状态机负责多轮收敛。
- 安全层负责从严兜底。
- 不让任何单个匹配结果直接决定诊断或固定路径。

## 17. 配置建议

新增配置应放入 env 文件，由 compose 只负责拓扑。示例：

```env
# 是否启用结构化临床知识资产 API。
ENABLE_CLINICAL_KNOWLEDGE_ADMIN=true

# 是否要求病症卡经过审核后才允许发布到线上召回。
CLINICAL_KNOWLEDGE_REQUIRE_REVIEW=true

# 线上运行时只读取 active publication snapshot。
CLINICAL_KNOWLEDGE_REQUIRE_ACTIVE_SNAPSHOT=true

# 默认 embedding 模型。实际调用仍应由服务端白名单控制，不能信任外部请求透传。
CLINICAL_KNOWLEDGE_EMBEDDING_MODEL=text-embedding-v4

# embedding 维度，需与 pgvector 字段和 Mem0 collection 维度保持一致。
CLINICAL_KNOWLEDGE_EMBEDDING_DIMENSION=1024

# 单批导入最大病症卡数量，防止误上传超大文件拖垮服务。
CLINICAL_KNOWLEDGE_MAX_CONDITIONS_PER_BATCH=500

# 单张病症卡拆分后的最大 chunk 数量，用于限制异常数据。
CLINICAL_KNOWLEDGE_MAX_CHUNKS_PER_CONDITION=8

# 是否允许本地 seed 脚本直接写入 approved 数据。生产建议 false。
CLINICAL_KNOWLEDGE_ALLOW_SEED_APPROVED=false
```

## 18. 发布策略

建议分三阶段落地。

### 18.1 第一阶段：离线入库验证

目标：

- 使用 `vet_conditions.json` 生成字段级 chunk。
- 写入 `knowledge_chunks`。
- 使用当前 `/admin/rag/chunks` 查看。
- 验证 `RagQuestionPlannerAgent` 追问不再模板化。

适合开发环境。

### 18.2 第二阶段：Admin API 导入与审核

目标：

- 新增导入批次 API。
- 新增校验、预览、审核接口。
- 所有导入默认为 `pending_review`。
- 审核通过后才可 `approved`。

适合预生产环境。

### 18.3 第三阶段：发布快照与回滚

目标：

- 新增 active snapshot。
- Agent 运行时只读取 active snapshot。
- 支持一键回滚。
- 后台展示当前线上版本。

适合生产环境。

## 19. 测试方案

### 19.1 单元测试

覆盖：

- `vet_conditions.json` schema 校验。
- `condition_key` 生成与去重。
- 字段级 chunk 拆分。
- 剂量风险检测。
- `review_status` 状态流转。
- 发布快照选择。
- 禁用病症卡后不再召回。

### 19.2 集成测试

覆盖：

- Admin API 导入、校验、预览、审核、发布全链路。
- pgvector 检索仅返回 `approved + enabled + active_snapshot`。
- RAG 追问优先使用 `followup_questions`。
- 最终回答包含知识证据和线下兜底。
- `reasoning_display` 包含用户回答、宠物资料、知识卡来源。

### 19.3 业务回归测试

至少覆盖：

| 场景 | 验收点 |
| --- | --- |
| 耳朵臭、甩头 | 追问单耳/双耳、分泌物颜色气味、是否歪头，而不是只问精神食欲。 |
| 猫夜间行为改变 | 能追问排尿饮水、疼痛、食欲体重、环境变化等高价值信息。 |
| 慢性消瘦 | 能追问食欲趋势、饮水尿量、体重曲线、年龄。 |
| 呕吐 | 能区分呕吐/反流、次数、内容、误食、能否喝水。 |
| 腹胀 | 能优先排查 GDV 红旗。 |
| 公猫排尿异常 | 进入安全升级或优先确认是否能排尿。 |
| 多任务输入 | 无未完成状态时可拆分；已有未完成状态时优先作为追问回答处理。 |
| LLM 不可用 | 回退保守追问，并在 metadata 标明 fallback。 |

## 20. 验收标准

功能验收：

- 后台可以导入 `vet_conditions.json`。
- 系统能生成字段级 RAG chunk。
- 审核前知识不进入线上召回。
- 发布后 Agent 能检索新病症卡。
- 回滚后 Agent 不再使用被回滚版本。
- 单条病症卡或 chunk 可被禁用。

问诊验收：

- 第一轮信息不足时返回 `requires_followup`。
- 下一轮回答后能累积上下文。
- 追问明显具有病症特异性。
- 不重复问已确认事实。
- 达到追问轮数上限后能给阶段性回答。

安全验收：

- 急症、毒物、人药风险仍由安全层优先处理。
- 输出不含具体用药剂量。
- 不把 RAG 命中结果当作确诊。
- `reasoning_display` 只展示用户可见诊断证据，不展示隐藏推理链。

运维验收：

- 每次导入、审核、发布、禁用、回滚均有审计记录。
- 后台能查询当前 active snapshot。
- 出现错误知识时能在不重新部署代码的情况下禁用或回滚。

## 21. 风险与控制

| 风险 | 控制方式 |
| --- | --- |
| 错误知识进入生产 | 默认 `pending_review`，必须审核后发布。 |
| RAG 召回错误 | top-k 候选 + LLM 证据约束 + 不输出确诊。 |
| 追问仍模板化 | 提高 `followup_questions` chunk 权重，并在 prompt 中禁止模板腔。 |
| 用药越界 | 入库校验 + 输出安全审查双层控制。 |
| 版本发布后质量下降 | 发布快照 + 快速回滚。 |
| 后台误操作 | 审计日志 + actor_id + reason 必填。 |
| 大批量向量化失败 | 异步任务 + 批次状态 + 可重试。 |
| Markdown 与 JSON 不一致 | 以 JSON 为机器源，Markdown 为人工审查源，并记录 source_version。 |

## 22. 与后端/BFF 的边界

后端/BFF 不需要理解病症卡内部推理逻辑，只需要对接 Admin API 和主问诊 API。

后端/BFF 推荐承担：

- 上传临床知识 JSON 到 OSS。
- 调用 Admin API 创建导入批次。
- 展示校验结果和 chunk 预览。
- 承接审核人员操作。
- 发起发布或回滚。
- 在问诊主链路中展示 `output_text`、`segments`、`reasoning_display`。

Agent 推荐承担：

- 结构化知识校验。
- chunk 拆分。
- embedding。
- RAG 检索。
- 动态追问。
- 最终回答生成。
- 安全审查。
- 审计记录。

## 23. 最终交付形态

完成后，`common_conditions_handbook.md` 对应能力应表现为：

```text
后台运维侧：
  可以导入、校验、审核、发布、回滚常见病症知识。

Agent 运行侧：
  可以根据用户输入召回候选病症卡。
  可以基于病症卡反推更自然、更有鉴别价值的追问。
  可以根据用户下一轮回答逐步构建上下文。
  可以在最终回答中展示用户可见诊断证据。

安全侧：
  急症和毒物继续优先拦截。
  病症卡只增强追问和回答依据，不替代安全层。
```

该方案能满足 PRD 中“根据用户问题反问，并根据用户下一轮回答逐步构建上下文，避免一次性武断回答”的要求，同时减少代码硬编码语料，便于后续由后台和临床人员持续维护知识资产。
