# P1 Agent Hardening

本次 P1 改造已覆盖以下模块，暂不包含健康算法 v2.0 集成。

## Agent 编排

当前使用项目内置的确定性编排逻辑串联安全分诊、宠物上下文、记忆读取、任务拆分、问诊、RAG、回答合成、安全复核、事实记忆写入和 trace 记录。

已去除未实际接管执行流的外部图编排 facade，不再提供对应的开关配置和后台规格接口。

## OCR / 报告解析

接口：

```bash
curl -X POST http://127.0.0.1:8000/reports/parse \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/report_lab.json"
```

支持输入：

- `oss_image_url`: 后端传递的 OSS 图片文件地址
- 兼容别名：`image_url`、`storage_ref`、`oss_url`、`file_url`
- 支持 `oss://infra-dev-file-storage/uploads/...jpg`、OSS HTTPS URL，或同桶内对象键 `uploads/...jpg`

OSS 约束来自 `docs/开发环境介绍-业务部署指南.md`：

- Dev 桶：`infra-dev-file-storage`
- Endpoint：`oss-cn-hangzhou-internal.aliyuncs.com`
- Prefix：默认空，不再使用 `dev/` 前缀
- 不在代码或 `.env` 中配置 AK/SK，线上访问由 ECS RAM 角色承担
- 私有桶真实调用 Qwen 视觉模型时，后端应通过 RAM 角色生成短期签名 HTTPS OSS URL；Agent 只校验/转发给模型，并在存储中脱敏为 `oss://bucket/key`

安全策略：

- `xray`、`ct`、`mri`、`ultrasound`、`radiology` 报告会返回 `blocked`
- 不对影像类资料做线上判读
- 检验报告图片会通过 Qwen 视觉模型解析为 `items[]`，包括项目名、数值、单位、参考范围、异常标记和置信度

查询：

```bash
curl "http://127.0.0.1:8000/reports?user_id=dev_user_report&session_id=dev_session_report&pet_id=dev_pet_report"
```

## RAG 治理

新增 `knowledge_chunks` 治理字段：

- `review_status`: `approved` / `pending` / `rejected` / `quarantined`
- `quality_score`
- `last_reviewed_at`
- `disabled_reason`
- `ingestion_batch`

检索层只会使用：

```text
enabled = true
review_status = approved
```

后台接口：

```bash
curl http://127.0.0.1:8000/admin/rag/stats
curl "http://127.0.0.1:8000/admin/rag/chunks?limit=5"

curl -X PATCH http://127.0.0.1:8000/admin/rag/chunks/1 \
  -H "Content-Type: application/json" \
  -d '{"review_status":"quarantined","enabled":false,"reason":"manual review"}'
```

## Docker Dev 检查

```bash
make request-report-parse
make request-rag-stats
make request-rag-chunks
```

没有 `make` 时，直接使用 `docker compose -f docker-compose.dev.yml exec -T app python scripts/dev_request.py <scenario>`。

## 新增迁移

```text
alembic/versions/0004_p1_reports_and_rag_governance.py
```

新增表：

- `pet_reports`
- `pet_report_items`
- `rag_audit_events`

调整表：

- `knowledge_chunks`
