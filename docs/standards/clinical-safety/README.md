<!--
=============================================================================
文件: docs/standards/clinical-safety/README.md
作用: 说明临床安全静态资产发布态模板、枚举值和人工审核边界。
范围: 适用于临床安全静态资产构造、导入、人工审核和 CI 约束。
说明: 本目录只提供发布态模板与受控枚举说明，不作为运行时裁决依据。
=============================================================================
-->

# 临床安全静态资产标准

本目录定义临床安全静态资产的统一对外标准，用于资产构造、人工审核、静态校验与 CI 门禁。

## 1. 约束原则

1. 只保留发布态模板，不提供宽松基础模板。
2. 模板字段必须与 `vet_agent.clinical_safety.asset_contract` 保持一致。
3. 模板示例必须可以通过发布态严格契约校验。
4. 模板中的枚举值不得自由扩展。

## 2. 允许的枚举值

### asset_type

- `toxin`
- `human_drug`
- `plant_toxin`
- `chemical_toxin`
- `emergency_red_flag`
- `danger_pattern`

### severity

- `info`
- `caution`
- `urgent`
- `blocked`

### action_class

- `emergency`
- `same_day_visit`
- `urgent_visit`
- `safety_warning`

### chunk_type

- `recognition`
- `clinical_risk`
- `triage_action`

### species_scope

- `dog`
- `cat`

### sex_scope

- `male`
- `female`

### age_scope

- `juvenile`
- `adult`
- `senior`

### required_context

- `species`
- `sex`
- `age`
- `symptoms`

### decision_hints

- `actual_exposure`
- `possible_exposure`
- `active_symptom`
- `possible_symptom`
- `historical_context`
- `knowledge_question`
- `prevention_question`

### decision_hints 值

- `safety_escalated`
- `clinical_caution`
- `completed_with_safety_warning`
- `record_as_history`

## 3. 当前推荐默认值

以下默认值与当前仓库实现、模板示例和远程开发环境口径保持一致。这里给出的是推荐写法，不是放宽契约。

| 字段 | 当前推荐值 | 说明 |
| --- | --- | --- |
| `embedding_model` | `text-embedding-v4` | 与 `src/vet_agent/config.py` 的默认值一致；在当前 LiteLLM/OpenAI 兼容入口中，`openai/text-embedding-v4` 只是外部可见别名，不建议作为模板持久化主值。 |
| `embedding_dimension` | `1024` | 必须与向量模型输出维度和 pgvector 维度一致。 |
| `schema_version` | `1.0.0` | 与当前发布态契约版本一致。 |
| `version` | `v1` | 这是当前示例模板版本；真实资产批次可按治理版本调整。 |
| `review_status` | `approved` | 发布态示例只允许 `approved`。 |
| `enabled` | `true` | 发布态示例只允许 `true`。 |
| `published_at` | 发布时刻 | 必须真实存在，不能留空。 |

如果需要在联调说明里提及外部兼容名称，可以同时写明 `openai/text-embedding-v4`，但资产模板文件和落盘持久化字段应保持 `text-embedding-v4` 作为当前 canonical 值。

## 4. 模板文件

- `clinical-safety-assets.publish.example.json`
- `clinical-safety-chunks.publish.example.json`
- `clinical-safety-assets.publish.schema.json`
- `clinical-safety-chunks.publish.schema.json`
- `template-operations-manual.md`

## 5. 审核要点

1. `code` 必须稳定且非空，不允许兜底生成。
2. `emergency_red_flag` 资产必须使用唯一的 `EMERGENCY_MODE_[A-Z0-9]{10}` opaque code。
3. 非急诊资产不得占用 `EMERGENCY_MODE_` 命名空间。
4. `review_status` 必须为 `approved`。
5. `enabled` 必须为 `true`。
6. `published_at` 必须存在。
7. chunk 必须包含 embedding 元信息。
8. chunk metadata 不得与权威资产字段冲突。
9. 每个资产必须至少有一个 `recognition` chunk。
