<!--
=============================================================================
文件: docs/architecture/clinical-safety-asset-contract.md
作用: 定义临床安全静态资产、向量 chunk、发布态严格校验与运行时消费边界。
范围: 适用于 assets/clinical_safety、离线转换脚本、数据库导入 dry-run、PostgreSQL 发布态资产表和候选召回链路。
说明: 本文档只定义临床安全资产契约和治理边界，不定义最终临床安全裁决策略。
维护: 当资产字段、发布流程、数据库约束、embedding 模型治理或 OPA 输入契约调整时，应同步更新本文档。
=============================================================================
-->

# 临床安全资产发布态契约

> **文档状态**：发布态资产治理基线
>
> **适用范围**：临床安全静态资产、离线导入、发布前 dry-run、PostgreSQL/pgvector 生产召回资产
>
> **不适用范围**：临床安全最终策略裁决、问诊状态机、RAG 知识问答、长期记忆、报告解析

## 1. 核心原则

临床安全资产不允许存在“基础结构校验通过”的可信状态。静态 JSON 可以被解析为草稿或历史参考，但只有通过发布态严格校验的资产，才允许进入数据库发布态和生产召回链路。

因此，资产治理链路只承认以下状态：

1. 草稿或历史参考资产：可以被离线工具读取、转换和人工审阅，不进入运行时召回。
2. 发布态严格校验通过资产：可以导入 PostgreSQL，并在具备 embedding 后进入 pgvector 召回。

不存在以下状态：

1. JSON 可以解析，因此允许进入生产。
2. 字段大致存在，因此允许运行时补齐。
3. 缺少 `code`，因此根据 `canonical_name` 推导。
4. 枚举非法，因此静默默认成 `danger_pattern`、`caution` 或 `safety_warning`。

## 2. 发布态资产要求

发布态资产必须满足：

1. `code` 必填、非空、稳定，并由资产治理域确认。
2. `code` 不允许为 `CLINICAL_SAFETY_UNKNOWN`，也不允许为 `CLINICAL_SAFETY_2_3` 这类生成兜底编码。
3. `asset_type`、`severity`、`action_class` 必须是受控枚举。
4. `review_status` 必须为 `approved`。
5. `enabled` 必须为 `true`。
6. `published_at` 必须非空。
7. `source` 必须包含 `source_file`、`source_path` 和 `source_text`。
8. `recognition_phrases`、`clinical_risk_summary` 和 `triage_message` 必须非空。
9. `decision_hints` 如存在，只能使用枚举化键和值，不得作为自由格式策略 DSL。
10. `required_context` 如存在，只能使用受控上下文字段。

`recognition_phrases` 和 `user_expressions` 仅用于离线生成 embedding 文本，不允许作为运行时关键词规则。

## 3. 发布态 Chunk 要求

发布态 chunk 必须满足：

1. `chunk_id`、`asset_id`、`chunk_type`、`title`、`embedding_text` 必填。
2. `chunk_type` 必须是 `recognition`、`clinical_risk` 或 `triage_action`。
3. `review_status` 必须为 `approved`。
4. `enabled` 必须为 `true`。
5. 生产发布要求 `embedding_model`、`embedding_dimension` 和 `content_hash` 非空。
6. chunk 必须引用已通过发布态校验的资产。
7. 每个资产至少应具备一个 `recognition` chunk。
8. chunk `metadata` 中冗余保存的 `code`、`asset_type`、`severity`、`action_class` 等字段不得与权威资产字段冲突。

chunk metadata 只能作为审计冗余，不是第二个可信资产源。

## 4. 工具链边界

当前发布态严格契约由 `src/vet_agent/clinical_safety/asset_contract.py` 定义，并通过 `vet_agent.clinical_safety` 包顶层暴露。

允许的工具链用法：

1. 离线转换脚本可以生成草稿资产，默认不视为发布态。
2. `scripts/clinical_safety/convert_safety_reference.py --validate-publish` 可执行发布态严格校验。
3. `scripts/seed_database.py --clinical-safety-dry-run` 可在写库前执行发布态严格校验。
4. 发布态导入数据库前必须通过 `validate_clinical_safety_publish_contract()`。
5. 标准模板、示例文件和枚举说明位于 `docs/standards/clinical-safety/`，其内容必须与包内契约保持一致。

不允许的工具链用法：

1. 不允许用 JSON 解析成功替代发布态严格校验。
2. 不允许在导入时根据医学名称补齐 `code`。
3. 不允许导入时把非法枚举静默修复为默认值。
4. 不允许把文件资产作为生产运行时兜底召回源。

## 5. 数据库边界

PostgreSQL 层应作为发布态资产的最终运行时可信源。数据库约束应保证：

1. `clinical_safety_assets.code` 非空、稳定，且不允许生成兜底编码；是否唯一由后续资产治理策略另行定义，当前以 `asset_id` 作为单条资产的稳定定位标识。
2. `clinical_safety_assets.review_status=approved` 时，必须同时满足 `enabled=true` 和 `published_at IS NOT NULL`。
3. 非 approved 资产必须保持 `enabled=false` 且 `published_at IS NULL`。
4. `clinical_safety_chunks.review_status=approved` 时，必须同时满足 `enabled=true`、`embedding IS NOT NULL`、`embedding_model IS NOT NULL`、`embedding_dimension IS NOT NULL` 和 `content_hash` 非空。
5. 非 approved chunk 必须保持 `enabled=false`。

运行时召回只读取已发布、已启用、已向量化的 chunk，并通过仓储协议访问数据库；业务层不得直接操作数据表模型。

## 6. 与候选召回边界的关系

临床安全候选召回只负责从已发布 pgvector chunk 中召回候选。资产契约负责保证这些候选来自已治理数据，而不是运行时补齐的半成品。

因此：

1. `ClinicalSafetyRetriever` 只消费已发布资产和 chunk。
2. `ClinicalSafetyEvaluator` 不应根据资产字段缺失执行补齐或裁决。
3. `ClinicalSafetyPolicyInput` 只透传资产治理域提供的 `code`。
4. OPA 只消费结构化候选、结构化语义、可信上下文和降级状态。

资产契约失败时，应在导入、发布或仓储读取阶段 Fail Fast，不应推迟到主业务对话过程中由运行时代码猜测修复。
