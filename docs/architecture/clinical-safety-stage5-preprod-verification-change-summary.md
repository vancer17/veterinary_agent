<!--
=============================================================================
文件: docs/architecture/clinical-safety-stage5-preprod-verification-change-summary.md
作用: 总结临床安全阶段 5 回归验证与预发布观察的执行契约、部署顺序、验收边界和回滚策略。
范围: 覆盖预发布 core 镜像部署、阶段 4 资产契约迁移、真实 HTTP 黑盒冒烟、观察窗口与后续指标边界。
说明: 本文记录工程验证边界，不扩展医学规则，不把 Guardrails 输出增强纳入本阶段完成判定。
维护: 当预发布服务器、Release 策略、镜像变体、数据库迁移顺序或阶段 5 验收口径调整时同步更新。
-->

# 临床安全阶段 5 预发布回归验证变更总结

> **文档状态**：工程实现已就绪，待 Release 合并、镜像发布与预发布观察后关闭

## 1. 阶段目标

阶段 5 不再修改临床安全医学判断逻辑。本阶段把阶段 0 至阶段 4 的结果收敛为可重复执行的预发布验证链路：

1. 本地全量 CI 与真实依赖集成测试先行。
2. GitHub Release 生成 core 应用镜像与 OPA 策略镜像。
3. 预发布环境先导入已审核临床安全资产，再执行 `0021` 数据库迁移。
4. 通过真实 HTTP API 验证四类残余问题没有回退。
5. 进入显式观察窗口，记录行为指标和失败样本。
6. 保留运行时镜像回滚与数据库备份，不在现场手写医学修复逻辑。

## 2. 关键结论

### 2.1 预发布使用 core 应用镜像

阶段 5 预发布部署使用：

```text
veterinary_agent:<release-tag>
```

不使用：

```text
veterinary_agent-guardrails:<release-tag>
```

原因：

1. 临床安全阶段 5 验收依赖 LiteLLM、pgvector、`required_context` 与 OPA，不依赖 Guardrails 输出检测器。
2. 输出安全 `rewrite` 动作尚未就绪，携带 Guardrails 重依赖不能形成完整闭环。
3. 当前预发布输出安全配置是 `observe`，且 `ENABLE_OUTPUT_SAFETY_GUARDRAILS=false`。
4. core 镜像有明确启动校验；如果误开启 Guardrails 开关，会 Fail Fast 而不是静默跳过。
5. 减小镜像体积可以降低推送、拉取、seed、迁移和回滚的不确定性。

阶段 5 保持：

```text
VET_AGENT_IMAGE_VARIANT=core
ENABLE_OUTPUT_SAFETY=true
OUTPUT_SAFETY_MODE=observe
ENABLE_INPUT_SAFETY_GUARDRAILS=false
ENABLE_OUTPUT_SAFETY_GUARDRAILS=false
```

### 2.2 OPA 策略版本以镜像为准

正式 Compose 不再把宿主机 `./opa/policies` 挂载到容器内策略目录。OPA 的策略来源是：

```text
veterinary_agent-opa:<release-tag>
```

这样可以避免以下漂移：

```text
新 app 镜像 + 旧宿主机 clinical_safety.rego
```

部署脚本会校验：

1. OPA 镜像 revision 与 Release tag 对应 commit 一致。
2. OPA 镜像内 `clinical_safety.rego` 哈希与仓库 Release 策略一致。

### 2.3 预发布迁移顺序固定为 seed → migrate

当前预发布存量库可能处于：

```text
0019_followup_rag_miss_governance
```

并仍存在旧 `EMERGENCY_RED_FLAG` 编码。`0021` 迁移按设计拒绝自动生成或修复 code，因此不能使用常规 migrate-first 流程。

阶段 5 固定顺序为：

```text
备份
→ 同步编排
→ 拉取 Release 镜像
→ 启动 PostgreSQL / LiteLLM / Mem0 / OPA
→ 使用 Release app 镜像执行 seed
→ 执行 alembic upgrade head
→ 校验数据库契约
→ 重建 app / worker
→ /health 与 /ready 验证
```

该过程会显式停止旧 app 与 worker，形成短暂维护窗口。预发布不以旧应用持续在线为优先目标；
避免“旧 app + 新 OPA”或“新 app + 旧数据库契约”的跨版本组合更重要。

不允许：

1. 用 SQL 手工批量改 code。
2. 修改 `0021` 迁移自动修复存量资产。
3. 在 seed 失败后继续迁移。
4. 在数据库契约失败后启动新 app。

## 3. 部署入口

### 3.1 默认环境

| 项目 | 默认值 |
|---|---|
| SSH 主机 | `121.41.58.20` |
| SSH 用户 | `deploy` |
| SSH 私钥 | `/home/vancer17/.ssh/infra-ci-deploy` |
| 部署根目录 | `/opt/vancer-saas/veterinary_agent-preprod` |
| Compose 项目 | `vet-agent-preprod` |
| 应用端口 | `18000` |
| 应用数据库 | `vet_agent` |

真实密钥、数据库口令和 API key 继续保存在预发布服务器本地 env 文件中，部署脚本不读取、复制或回显这些值。

### 3.2 执行部署

```bash
make preprod-deploy-clinical-safety-stage5 \
  CLINICAL_SAFETY_STAGE5_RELEASE_TAG=v0.1.0-rc.3
```

如需显式注入私有仓库凭据：

```bash
CLINICAL_SAFETY_STAGE5_REGISTRY_USERNAME=... \
CLINICAL_SAFETY_STAGE5_REGISTRY_PASSWORD=... \
make preprod-deploy-clinical-safety-stage5 \
  CLINICAL_SAFETY_STAGE5_RELEASE_TAG=v0.1.0-rc.3
```

若预发布 Docker 已保存 Registry 登录态，可以不注入凭据。

### 3.3 执行黑盒冒烟

```bash
make smoke-clinical-safety-stage5-preprod
```

等价 try-run 入口：

```bash
CI_TRY_RUN_SCOPE=clinical-safety-stage5-preprod \
  bash scripts/ci/try-run.sh
```

显式 pytest 入口：

```bash
RUN_CLINICAL_SAFETY_STAGE5_PREPROD_TEST=true \
  uv run pytest \
    tests/integration/test_clinical_safety_stage5_preprod.py \
    -m integration \
    -q
```

### 3.4 运行时回滚

部署脚本会为每次执行生成备份目录：

```text
/opt/vancer-saas/veterinary_agent-preprod/stage5-backups/<run-id>
```

备份包含：

1. 部署前 app 与 OPA 镜像。
2. 部署前 Alembic 版本。
3. `vet_agent` 数据库逻辑备份。
4. 部署前 Compose 状态。

回滚运行时镜像：

```bash
make preprod-rollback-clinical-safety-stage5 \
  CLINICAL_SAFETY_STAGE5_ROLLBACK_RUN_ID=<run-id>
```

该回滚只回滚 app、worker 和 OPA 镜像，不自动降级数据库。如需恢复数据库，必须单独评审备份内容、备份后新增数据和下游影响后执行，不允许部署脚本隐式恢复。

## 4. 部署过程中的 Fail Fast 契约

以下情况直接终止部署：

1. Release tag 不存在。
2. 远程缺少 Compose v2 或真实 env 文件。
3. 目标数据库不存在。
4. app 或 OPA 镜像拉取失败。
5. app 镜像不是 `core` 变体。
6. app 或 OPA 镜像 revision 与 Release commit 不一致。
7. seed 或 embedding 生成失败。
8. Alembic 迁移失败。
9. Alembic 版本不是 `0021_clinical_safety_emergency_asset_codes`。
10. 已发布急诊资产数量、唯一 code、治理 metadata 或 chunk code 不符合 Release manifest。
11. 已发布 chunk 缺失向量、向量模型或维度信息。
12. OPA 镜像内策略哈希与仓库不一致。
13. app 容器 Guardrails 开关与 core 镜像契约冲突。
14. 新 app 或 worker 无法健康启动。
15. `/ready` 失败。

脚本不会在失败后降级为关键词规则、文本 JSON 检索、本地主信号选择或手工资产修复。

## 5. 数据库契约验收

部署完成后，已发布急诊资产必须满足：

```text
asset_count = Release manifest 中 approved emergency 数量
distinct_code_count = asset_count
invalid_code_count = 0
governance_missing_count = 0
duplicate_code_count = 0
chunk_code_mismatch_count = 0
invalid_embedding_count = 0
```

其中 code 必须满足：

```text
^EMERGENCY_MODE_[A-Z0-9]{10}$
```

资产治理 metadata 必须保留：

```text
code_governance.strategy = opaque_asset_identity_v1
code_governance.legacy_code 非空
```

## 6. 黑盒回归场景

阶段 5 最终验收通过真实预发布 HTTP API 执行，不使用本地 `TestClient` Mock 结果作为完成依据。

### 6.1 模糊分诊只追问

输入形态：

```text
我家猫最近状态有点不对，要不要带它去医院？
```

验收：

1. 不进入 `safety_escalated` 或 `blocked`。
2. `risk_evidence_state != sufficient`。
3. 临床安全召回 `stage = none`。
4. 向量命中数和候选数为 0。
5. 不产生 urgent / blocked 信号。

### 6.2 范围不匹配候选被过滤

输入形态：

```text
狗狗最近尿频尿少，一趟一趟往砂盆跑。
```

该输入与猫专属泌尿急诊表达相似，但可信宠物范围是犬。验收：

1. Release manifest 中的猫专属急诊 code 不进入 OPA signals。
2. 猫专属 code 不进入响应 `safety_signals`。
3. 猫专属 code 不出现在输出、段落或 reasoning display。

测试从 Release 资产 manifest 读取猫专属 code，生产代码不按 code 写医学分支。

### 6.3 `required_context` 不足时进入追问

输入形态：

```text
猫现在呼吸有一点快，但牙龈颜色我不确定。
```

验收：

1. 状态为 `requires_followup`。
2. 不产生 urgent / blocked 信号。
3. `unknown_count >= 1`。
4. `satisfied_count = 0`。
5. `requires_precondition_information = true`。

### 6.4 多候选只投影一个主信号

输入形态：

```text
猫现在牙龈发紫，呼吸很快。
```

验收：

1. 状态为 `safety_escalated`。
2. OPA signals 至少包含两个候选。
3. OPA `primary_signal` 与 evaluator 投影主信号一致。
4. 主信号 code 符合 opaque 命名空间。
5. 非主信号 message 不进入用户可见文本。
6. 内部 `EMERGENCY_MODE_*` code 不进入用户可见文本。
7. 强召回来源为 `clinical_safety_pgvector`。
8. 语义策略来自 LiteLLM response format 路径。

## 7. 观察窗口

冒烟通过只是进入观察窗口的条件，不代表阶段 5 完成。建议观察：

```text
至少 24 小时，优先 48–72 小时
```

观察指标：

| 指标 | 目标 |
|---|---|
| 模糊分诊误升级 urgent | 0 |
| 证据不足触发强召回 | 0 |
| 宠物画像单独引发 urgent 候选 | 0 |
| 范围不匹配资产进入主信号 | 0 |
| required_context unknown 被放大为 satisfied | 0 |
| 多候选文本拼接展示 | 0 |
| policy primary_signal 缺失 | 0 |
| Python 本地主信号兜底 | 0 |

同时观察：

1. LiteLLM 结构化输出成功率。
2. 语义低置信率。
3. pgvector 检索耗时与失败率。
4. 前提评估 satisfied / unknown / denied 分布。
5. OPA 裁决失败率。
6. API p95 / p99 延迟。
7. app、worker、OPA、LiteLLM 和 PostgreSQL 日志。

## 8. 有意保留 TODO

| TODO | 当前边界 | 后续归属 |
|---|---|---|
| 持续指标采集 | 阶段 5 使用黑盒报告和容器日志人工观察 | 独立观测域 |
| Guardrails 输出增强 | 不进入阶段 5 主预发布完成判定 | Rewrite 与输出安全专项 |
| 医学语义 golden set | 当前只覆盖四类回归残余问题 | 医学资产治理与质量评估域 |
| 自动数据库恢复 | 回滚脚本只回滚运行时镜像，不隐式恢复备份 | 数据库运维流程 |
| 资产发布流程 | 静态资产仍由 Release seed 导入 | 独立资产治理平台 |

## 9. 完成判定

阶段 5 只有在以下条件同时满足时才可关闭：

1. Release 已合并并生成 core app 与 OPA 镜像。
2. 预发布部署脚本执行通过。
3. 数据库处于 `0021` 并满足资产契约。
4. 四类黑盒回归场景全部通过。
5. 观察窗口内未出现 P0 / P1 行为回退。
6. 未发现旧规则、硬关键词、文本 JSON 或本地医学分支回退。
7. 回滚入口完成可执行性检查。
8. 观察结果回填本文档或独立发布记录。

## 10. 相关文档

1. [临床安全待迁移问题与分阶段治理方案](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-open-issues-migration-plan.md)
2. [临床安全证据充分性边界变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-evidence-boundary-change-summary.md)
3. [临床安全召回输入变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-retrieval-input-change-summary.md)
4. [临床安全 required context 变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-required-context-change-summary.md)
5. [临床安全急诊 code 与响应投影变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-emergency-code-response-change-summary.md)
