# 集成能力映射（附录 B）

> **用途：** 产品能力 → 建议集成落点（对应 **PRD v1.0**）。**非架构绑定**；具体表名 / 模型由后端契约定义，变更时更新本文档即可，无需改 FR 编号。

| 产品能力 | 要求 | 建议落点（非绑定） |
| --- | --- | --- |
| 宠物画像读取 | 对话开始时按 **`pet_id`** 拉取附录 A 字段 | 宠物 / 用户数据 API |
| 多宠列表与默认宠 | `pets[]`、`default_pet_id`（FR-MEM-02、§5.2.7） | 主人档案 / 偏好 API |
| 对话持久化 | 会话与消息可读写；**消息带 `pet_id`** | `apps/ai`（`AIChatSession` / `AIDialogMessage` 等） |
| 多宠鉴权 | `user_id` 仅访问授权 `pet_id` | Gateway / 后端 |
| 主人级记忆写入 | 跨会话 CRUD（FR-MEM-02、FR-MEM-04） | 主人档案 / 偏好 API；历史实现或曾用 `aiuserprofile` |
| 宠物级 / 会话级记忆 | 主诉、转归、接续上下文；**宠物级 scoped by `pet_id`**；会话槽位 **`(session_id, pet_id)`** | 对话域 + 结构化记忆存储 |
| 定宠后上下文装配 | FR-DATA-01、FR-MEM-06；只读 | Agent 编排 + Context Builder（`context-builder.md`） |
| 上传资料 | OCR 输入与合规存储；**按 `pet_id`** | 对象存储 + FR-VIS 流水线 |
| RAG 检索 | `standard` / `education` 医学接地 | 知识库服务（KB-MVP / KB-Full，见 PRD §6.8） |
| 审计留痕 | `audit_tier` 分级入库；含 `pet_id`、`pet_disambiguation_method` 等 | 审计库（独立于 RAG 索引，FR-KB-06） |

**原则：** FR 与验收只写**能力**；本表仅供集成研发对齐，不作为 P0 安全约束的替代。
