<!--
=============================================================================
文件: docs/standards/clinical-safety/template-operations-manual.md
作用: 为运维和资产维护人员说明临床安全静态资产模板的含义、用法与创建步骤。
范围: 适用于模板填写、人工审核、发布前 dry-run 和导入前校验。
说明: 本文档只解释如何正确创建发布态静态资产，不替代程序化契约校验。
=============================================================================
-->

# 临床安全静态资产模板操作手册

本手册面向运维、资产维护与审核人员，说明如何使用临床安全静态资产模板创建可发布资产。

## 1. 模板用途

模板用于统一临床安全资产的外部结构、受控枚举和发布态字段要求，目的是：

1. 降低资产构造歧义。
2. 让人工审核人员能快速判断字段是否完整。
3. 让 CI 与导入流程使用同一套标准。
4. 避免运行时根据医学文本临时补齐 `code`、枚举或上下文字段。

模板不是草稿宽松格式，也不是运行时兜底格式。只有通过发布态严格契约校验的资产，才允许进入数据库发布态。

## 2. 模板文件

本目录提供以下文件：

1. `clinical-safety-assets.publish.example.json`
2. `clinical-safety-chunks.publish.example.json`
3. `clinical-safety-assets.publish.schema.json`
4. `clinical-safety-chunks.publish.schema.json`
5. `README.md`

其中：

- `.example.json` 是完整示例，可直接作为新资产填写参考。
- `.schema.json` 是机器校验用结构定义。
- `README.md` 说明允许的枚举值与审核边界。

## 3. 字段理解

### 3.1 资产主文档

资产主文档描述一条临床安全资产的权威信息，重点字段如下：

- `asset_id`：资产稳定标识。
- `code`：对外安全信号编码，必须稳定且非空。
- `asset_type`：资产类型，只能使用受控枚举。
- `canonical_name`：资产规范名称。
- `severity`：资产默认风险级别。
- `action_class`：资产默认动作分类。
- `recognition_phrases`：用于离线向量文本生成的召回短语。
- `required_context`：结构化上下文提示。
- `decision_hints`：结构化策略提示，不能写成自由格式规则。
- `source`：来源追踪信息。
- `review_status`：发布态必须为 `approved`。
- `enabled`：发布态必须为 `true`。
- `published_at`：发布态必须存在。

### 3.2 chunk 文档

chunk 文档描述一个资产对应的向量检索片段。每个资产至少应包含三类 chunk：

- `recognition`：用于识别和召回。
- `clinical_risk`：用于解释风险。
- `triage_action`：用于说明分诊口径。

chunk 文档重点字段如下：

- `chunk_id`：chunk 稳定标识。
- `asset_id`：关联资产标识。
- `chunk_type`：chunk 类型，只能使用受控枚举。
- `embedding_text`：用于生成向量的标准文本。
- `embedding_model`：embedding 模型名。
- `embedding_dimension`：embedding 维度。
- `content_hash`：文本内容哈希。
- `metadata`：冗余审计字段，只能与权威资产一致。

## 4. 当前推荐默认值

以下值用于当前模板示例和运维联调口径，属于推荐默认值，不是宽松校验规则。

| 字段 | 推荐值 | 说明 |
| --- | --- | --- |
| `embedding_model` | `text-embedding-v4` | 与仓库默认配置一致；`openai/text-embedding-v4` 仅作为外部兼容别名出现，不建议作为模板主值。 |
| `embedding_dimension` | `1024` | 与当前向量模型输出维度一致。 |
| `schema_version` | `1.0.0` | 当前发布态契约版本。 |
| `review_status` | `approved` | 发布态模板必须使用。 |
| `enabled` | `true` | 发布态模板必须使用。 |
| `published_at` | 发布时刻 | 必须填写真实时间，不能留空。 |

## 5. 正确创建步骤

### 5.1 选择模板

新建资产时，先复制 `.example.json` 的结构，再替换具体内容。不要删减必填字段，也不要新增未经契约允许的字段。

### 5.2 填写资产主文档

建议按以下顺序填写：

1. `asset_id`
2. `code`
3. `asset_type`
4. `canonical_name`
5. `category`
6. `species_scope`
7. `sex_scope`
8. `age_scope`
9. `severity`
10. `action_class`
11. `aliases`
12. `carriers`
13. `user_expressions`
14. `symptoms`
15. `recognition_phrases`
16. `required_context`
17. `decision_hints`
18. `clinical_risk_summary`
19. `triage_message`
20. `source`
21. `review_status`
22. `version`
23. `enabled`
24. `published_at`

### 5.3 填写 chunk 文档

每条资产至少创建 3 个 chunk，并保证：

1. `asset_id` 一致。
2. `metadata.asset_id` 与资产一致。
3. `metadata.code` 与资产一致。
4. `chunk_type` 分别为 `recognition`、`clinical_risk`、`triage_action`。
5. `content_hash` 与 `embedding_text` 匹配。

### 5.4 生成校验结果

在提交前执行严格契约校验：

- `scripts/clinical_safety/convert_safety_reference.py --review-status approved --validate-publish`
- 或 `scripts/seed_database.py --clinical-safety-dry-run`

如果资产缺少 embedding 元信息，开发阶段可临时加 `--allow-missing-clinical-safety-embeddings`，但正式发布前不得保留这种状态。

## 6. 允许与禁止

### 6.1 允许

1. 使用模板复制创建新资产。
2. 使用 schema 文件做编辑器校验。
3. 使用严格契约做发布前校验。
4. 使用人工审核记录补充医学含义说明。

### 6.2 禁止

1. 不允许把 JSON 解析成功视为可发布。
2. 不允许缺少 `code` 时让程序自动补齐。
3. 不允许把非法枚举静默替换成默认值。
4. 不允许把 `recognition_phrases` 当作运行时关键词规则。
5. 不允许让 chunk metadata 与权威资产字段不一致。
6. 不允许使用模板示例直接冒充真实生产资产。

## 7. 审核建议

人工审核时建议重点关注：

1. 资产名称是否准确。
2. `code` 是否由资产治理域确认。
3. `severity` 与 `action_class` 是否匹配临床风险级别。
4. `decision_hints` 是否只是结构化提示，而不是规则表达式。
5. 来源文本是否可回溯。
6. chunk 是否覆盖识别、风险和分诊三类信息。
7. 发布态字段是否全部就绪。

## 8. 参考命令

生成或校验模板文件时，可参考：

```bash
uv run python scripts/clinical_safety/convert_safety_reference.py --review-status approved --validate-publish
uv run python scripts/seed_database.py --clinical-safety-dry-run
```

## 9. 结果判断

如果严格校验通过，说明资产结构、枚举、引用关系和发布态状态满足当前标准。

如果严格校验失败，应根据错误信息修正模板，而不是绕过校验。
