<!--
=============================================================================
文件: docs/architecture/clinical-safety-emergency-code-response-change-summary.md
作用: 记录临床安全阶段 4 的急诊资产身份治理、OPA 主信号投影与用户响应收敛契约。
范围: 适用于 scripts/clinical_safety 原始资产源、发布态静态资产、OPA 策略、
      Python 策略客户端、evaluator、orchestrator 与 reasoning display。
说明: 本文档只描述已迁移的稳定边界和审计映射；不把自然语言准入条目枚举化，
      也不定义最终临床模式等价治理。
维护: 当急诊资产 code、primary_signal 契约、数据库约束或用户投影边界变化时更新。
=============================================================================
-->

# 临床安全急诊 code 与响应投影变更总结

> **文档状态**：阶段 4 迁移后稳定契约
>
> **适用范围**：临床安全静态资产治理、OPA 裁决、临床安全 evaluator、用户安全分诊响应
>
> **不适用范围**：完整医学模式归一化、症状标准词表、RAG 知识问答、问诊状态机、基础输入安全

## 1. 迁移目标

阶段 4 解决两个残余问题：

1. 多个不同急诊资产复用 `EMERGENCY_RED_FLAG`，导致策略信号、审计与 evaluator 去重无法区分具体资产。
2. 用户可见文本虽已只取一个信号，但主信号选择缺少显式 tie-break，`matched_terms` 和 reasoning display
   仍可能绕过“只展示一个主信号”的边界。

迁移后的稳定形态是：

```text
一条 enabled 急诊资产
→ 一个 opaque 资产级 code
→ OPA 返回全部 signals
→ OPA 另行返回唯一 primary_signal
→ 用户 output_text / segment / reasoning display 只消费 primary_signal
→ 全部候选继续保留在 safety_signals 与 metadata
```

## 2. 急诊 code 策略

### 2.1 资产级 opaque 身份

`emergency_red_flag` 资产的 `code` 使用：

```text
EMERGENCY_MODE_[A-Z0-9]{10}
```

例如：

```text
EMERGENCY_MODE_M5GBPYMACD
```

该 code 是资产级信号身份，不承载医学语义，不用于 Python 或 OPA 的疾病分支，也不参与召回正文。

人可读含义继续由以下字段承担：

| 字段 | 职责 |
|---|---|
| `canonical_name` | 资产规范名称 |
| `category` | 原始分类 |
| `asset_type` | 资产大类 |
| `severity` | 资产默认严重级别 |
| `action_class` | 资产默认动作分类 |
| `required_context.symptoms` | 自然语言准入前提 |
| `triage_message` | 对外分诊处置口径 |

### 2.2 一条资产一个 code

阶段 4 不判断多条资产是否代表同一临床模式。因此当前规则是：

```text
一条 enabled emergency_red_flag 资产对应一个独立 code。
```

即使两条资产在医学上高度相似，也先保留两个资产身份和两个 code，供完整审计。
临床模式等价、合并或共享 code 属于后续资产治理域，不在本阶段实现。

### 2.3 不把自然语言准入条目枚举化

`required_context.symptoms` 继续保持自然语言，例如：

```text
反复进砂盆使劲但尿不出
下腹硬胀
呕吐
```

本次迁移不新增：

1. Python 急诊 code 枚举；
2. 症状标准词表；
3. 自然语言准入条目到枚举值的映射；
4. 运行时按具体 code 的医学 if/else；
5. 原始文本关键词回退。

## 3. 资产源与发布契约

### 3.1 资产源显式声明

`scripts/clinical_safety/assets/vet_safety_reference.json` 已为全部原始条目显式补充 `code`。

其中：

1. `emergencyRedFlags` 使用 opaque 资产级 code；
2. `emergencyRedFlags` 显式声明 `code_governance.strategy` 与
   `code_governance.legacy_code`；
3. 其他资产类型保持既有显式 code；
4. 转换器只读取显式 code 与显式 code 治理信息；
5. 缺失 code 或 code 治理信息不完整时离线转换继续快速失败；
6. 转换器不根据标题、别名、数组序号或内容 hash 推导 code。

急诊资产 metadata 中的稳定治理信息为：

```json
"code_governance": {
  "strategy": "opaque_asset_identity_v1",
  "legacy_code": "EMERGENCY_RED_FLAG"
}
```

该信息来自原始资产源，重新执行离线转换不会丢失。

### 3.2 发布态防退化

发布态契约新增以下规则：

1. `EMERGENCY_RED_FLAG` 是禁止发布的兜底总标签；
2. `asset_type=emergency_red_flag` 的 code 必须匹配
   `^EMERGENCY_MODE_[A-Z0-9]{10}$`；
3. 同一资产文档内的急诊 code 必须唯一；
4. 非急诊资产不得占用 `EMERGENCY_MODE_` 命名空间；
5. 急诊资产必须携带完整的 `metadata.code_governance`；
6. 非急诊资产不得声明 `metadata.code_governance`；
7. chunk metadata 中的 code 必须与权威资产一致。

### 3.3 静态资产与向量

`assets/clinical_safety/vet_safety_assets.v1.json` 与
`assets/clinical_safety/vet_safety_chunks.v1.json` 已同步更新 code。

code 不参与 `embedding_text`，因此本次迁移不改变召回正文语义，也不要求因 code 变化重建向量。
生产导入仍必须执行既有 embedding 与发布态校验。

## 4. OPA primary_signal 契约

OPA `decision` 新增：

```text
primary_signal
```

动作映射如下：

| action | primary_signal |
|---|---|
| `allow` | 必须为 `null` |
| `observe` | 必须为 `null` |
| `escalate` | 必须存在 |
| `block` | 必须存在 |

`escalate` / `block` 时：

1. `primary_signal` 必须来自有效 `signals` 对应的候选；
2. 必须与其中一条信号按 `asset_id` 对应；
3. `escalate` 主信号必须为 `urgent`；
4. `block` 主信号必须为 `blocked`。

### 4.1 主信号排序

主信号排序只使用结构化通用字段：

```text
1. severity: blocked > urgent
2. action_class: emergency > same_day_visit > urgent_visit
3. asset_id 字典序
```

明确不使用：

1. 向量召回分数；
2. 具体疾病 code；
3. `matched_terms`；
4. 用户原文；
5. Python 响应层本地医学规则。

向量 `score` 只表达召回文本相似度，不能代表临床前提满足或医学优先级，因此不参与主信号选择。

## 5. Python 解析与 evaluator 投影

`ClinicalSafetyPolicyDecision` 新增 `primary_signal`。OPA 客户端严格校验：

1. OPA 响应缺少 `primary_signal` 字段时失败；
2. 升级或阻断动作缺少主信号对象时失败；
3. allow / observe 返回主信号对象时失败；
4. 主信号不能匹配唯一有效信号时失败；
5. 主信号 severity 与动作不匹配时失败；
6. 主信号必须携带 `asset_id` 与 `canonical_name`，用于 opaque code 审计解释。

除 OPA 响应解析外，`ClinicalSafetyPolicyDecision` 在对象构造阶段同样执行主信号契约校验，
自定义策略客户端也不能构造缺失、重复或与信号集脱钩的主信号。

`ClinicalSafetyEvaluationResult` 同步透出 `primary_signal`。

evaluator 的信号去重键从泛化 `code` 收敛为资产身份：

```text
asset_id
```

这样多个急诊资产不会因为历史上共享 code 而被误合并；同一资产重复信号仍会合并审计命中词。
如果主信号在去重后丢失，evaluator 快速失败，不做本地兜底选择。

## 6. 用户响应收敛

用户可见面现在只消费 `primary_signal`：

1. `output_text`；
2. safety triage `segment.content`；
3. `reasoning_display`。

变化包括：

1. 不再拼接多个 urgent 候选；
2. 不再在用户文本中展示 `matched_terms`；
3. reasoning display 不展示 signal code 或命中词；
4. `safety_signals` 仍保留全部策略信号；
5. `metadata.clinical_safety_resolution.policy_decision.signals` 保留全部 OPA 信号；
6. `metadata.clinical_safety_resolution.primary_signal` 显式记录用户主信号。

`matched_terms` 仍仅作为审计信息存在，不作为用户可见事实解释或临床判断依据。

输出安全复核后的信号去重键包含临床安全 `asset_id` 与 `canonical_name`；
即使两个资产复用同一历史编码或分诊文案，也不会在输出安全阶段被误合并。

## 7. API 兼容性

本次为有意不兼容变更：

1. 急诊 `safety_signals[].code` 从少量泛化或语义 code 变为 opaque code；
2. 临床安全策略信号增加 `asset_id` 与 `canonical_name` 审计字段；
3. 用户输出不再包含 `matched_terms` 相关线索。

客户端不应硬编码旧 code。需要定位具体资产时应使用：

```text
asset_id + canonical_name
```

历史 code 映射保留在本文档和资产迁移 metadata 中，不作为双信号输出。

顶层 `safety_signals` 是结构化审计集合，不是用户展示列表：

```text
前端不得遍历 safety_signals 拼接安全建议；
用户可见内容只消费 output_text / segments / reasoning_display；
主信号审计入口是 metadata.clinical_safety_resolution.primary_signal。
```

## 8. 数据库契约

数据库新增阶段 4 约束：

1. `review_status=approved` 且 `enabled=true` 的 `emergency_red_flag` 资产，
   code 必须匹配
   `^EMERGENCY_MODE_[A-Z0-9]{10}$`；
2. 上述已发布急诊资产 code 在数据库内必须唯一。

迁移不自动修复存量数据，并会先列出仍不合规的已发布资产编码。若预发布库仍存在：

```text
EMERGENCY_RED_FLAG
```

或已发布状态下的重复急诊 code，数据库迁移会失败，必须先导入本次资产治理结果。

推荐发布顺序：

```text
1. 执行临床安全静态资产发布校验；
2. 先导入或更新已发布临床安全资产 code；
3. 再执行 Alembic 0021；
4. 迁移失败时按预检输出的 asset_id / code 回到资产治理流程修复。
```

业务层继续通过临床安全仓储访问数据库，不直接操作 SQLAlchemy 表模型。

## 9. 验收基线

### 9.1 资产治理

1. 原始资产缺 code 时转换失败；
2. 急诊资产使用泛化 code 时发布契约失败；
3. 急诊 code 格式非法时发布契约失败；
4. 同批急诊 code 重复时发布契约失败；
5. chunk metadata 与资产 code 不一致时发布契约失败。

### 9.2 OPA 与 Python

1. 多个 urgent 候选时全部进入 `signals`；
2. `primary_signal` 有且只有一个；
3. `blocked` 优先于 `urgent`；
4. `emergency` 动作分类优先于 `same_day_visit`；
5. 高向量分数不能使 `same_day_visit` 覆盖 `emergency` 主信号；
6. allow / observe 不返回主信号；
7. Python 对缺失或不匹配主信号快速失败。

### 9.3 用户响应

1. 多个候选时用户文本只包含主信号 message；
2. `matched_terms` 不进入用户文本；
3. reasoning display 不展示候选 code 或命中词；
4. `safety_signals` 与 metadata 保留全部候选；
5. 缺失主信号时响应层失败，不回退为“当前信息需要进一步确认”。

## 10. 真实服务集成验证

阶段 4 使用显式集成的真实服务测试，不把内存 Mock 结果作为预发布验收依据。

执行入口：

```bash
bash scripts/integration/run-clinical-safety-api-smoke.sh
```

脚本会通过远程开发服务器建立 PostgreSQL、LiteLLM 与 OPA 隧道，并按需完成：

1. 同步本地 `docker/opa/policies/*.rego` 到远程 OPA；
2. 检测远程已发布急诊资产是否满足阶段 4 契约；
3. 在需要时导入本地发布态临床安全资产并生成真实 embedding；
4. 执行 Alembic 迁移；
5. 运行阶段 3 / 阶段 4 真实服务集成测试。

阶段 4 真实验证覆盖：

1. `allow` 响应显式包含 `primary_signal: null`；
2. 多候选 `escalate` 响应包含唯一 `primary_signal`；
3. `emergency` 主信号优先于高召回分的 `same_day_visit` 候选；
4. 远程数据库已发布急诊资产使用唯一 opaque code；
5. 远程资产保留 `code_governance` 历史映射；
6. 远程 chunk metadata 与权威资产 code 一致；
7. 真实 API 的 `safety_signals` 保留全部审计信号；
8. 真实 API 的 `output_text` 与 reasoning display 只展示主信号。

如需强制重新导入静态资产：

```bash
CLINICAL_SAFETY_SMOKE_SEED_REMOTE_ASSETS=always \
  bash scripts/integration/run-clinical-safety-api-smoke.sh
```

## 11. 有意保留 TODO

| 事项 | 当前边界 | 后续归属 |
|---|---|---|
| 临床模式等价治理 | 一条资产一个 code，不判断模式是否相同 | 独立资产治理流程 |
| 急诊 code 语义化命名 | 使用 opaque code 降低维护成本 | 如临床运营需要再评估 |
| 全量症状标准化 | `required_context.symptoms` 保持自然语言 | 医学资产治理域 |
| 主信号医学优先级模型 | 仅通用 severity / action_class / asset_id 排序 | 需要独立临床审核契约后才可引入 |
| 历史线上日志解释 | 使用本文映射表与资产 metadata | 观测与资产治理域 |

## 12. 阶段 4 code 审计映射

下表记录旧 code 到新 opaque code 的迁移映射。该表用于历史日志解释，不表示新旧 code 可同时输出。

| # | asset_id | 规范名称 | 旧 code | 新 code |
|---|---|---|---|---|
| 1 | `safety_emergency_red_flag_001_ebca9c6b2f` | 公猫尿道完全梗阻 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_M5GBPYMACD` |
| 2 | `safety_emergency_red_flag_002_e0fec7fe4a` | 公猫尿道梗阻【早期/部分梗阻】预警 | `PARTIAL_URINARY_OBSTRUCTION_RISK` | `EMERGENCY_MODE_4K4P5HXWNX` |
| 3 | `safety_emergency_red_flag_003_03adfca49c` | 胃扩张扭转 | `GDV_RISK_PATTERN` | `EMERGENCY_MODE_BDHMCEB4RZ` |
| 4 | `safety_emergency_red_flag_004_e273f780ab` | 线状异物 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_VW5VX623TH` |
| 5 | `safety_emergency_red_flag_005_09bb9d24f8` | 误食尖锐物 / 中毒物 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_2XRW8G48E2` |
| 6 | `safety_emergency_red_flag_006_c92e47d515` | 胃肠道异物梗阻 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_3XR84H2E8N` |
| 7 | `safety_emergency_red_flag_007_ae9b2a4be6` | 频繁剧烈呕吐 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_V7ZR6FYSP2` |
| 8 | `safety_emergency_red_flag_008_2fabd46de2` | 呕血 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_XWGHBV274N` |
| 9 | `safety_emergency_red_flag_009_db1b074330` | 黑便 / 柏油样便 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_45ME3KCDWR` |
| 10 | `safety_emergency_red_flag_010_943708294e` | 血便 / 便血 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_JRRNA4FSWA` |
| 11 | `safety_emergency_red_flag_011_54017eb543` | 急性出血性腹泻综合征 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_P37PGAY5ZQ` |
| 12 | `safety_emergency_red_flag_012_3f51ca1c15` | 剧烈腹泻致脱水 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_3EADW4XWXW` |
| 13 | `safety_emergency_red_flag_013_a612c84a06` | 剧烈腹痛 / 拱背祈祷姿势 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_YBD6WP9JGH` |
| 14 | `safety_emergency_red_flag_014_0a17b95c71` | 猫张口呼吸 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_7VSY82P2MF` |
| 15 | `safety_emergency_red_flag_015_fdf4bcbd51` | 舌/牙龈发绀发紫 | `CYANOSIS_RISK_PATTERN` | `EMERGENCY_MODE_VAXXZTWVAE` |
| 16 | `safety_emergency_red_flag_016_116476e920` | 呼吸急促费力 / 腹式呼吸 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_HFH29MSWSY` |
| 17 | `safety_emergency_red_flag_017_55e767aae3` | 静息/睡眠呼吸频率持续>30 次/分 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_5W9V7VYZ87` |
| 18 | `safety_emergency_red_flag_018_a7de7ba324` | 持续咳嗽伴呼吸困难 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_WXG24SPGJA` |
| 19 | `safety_emergency_red_flag_019_1762812824` | 颈部前伸 / 肘外展蹲伏、拒绝躺下的呼吸姿势 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_TY4P59J6YM` |
| 20 | `safety_emergency_red_flag_020_38bbbdeeff` | 虚脱倒地 / 突然瘫软无力 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_C2R6XC2T57` |
| 21 | `safety_emergency_red_flag_021_7b7fd51c69` | 晕厥后自行恢复 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_SMSGNKAYRE` |
| 22 | `safety_emergency_red_flag_022_cd5b6e77e0` | 牙龈苍白 / 发白 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_2FTFRMTXDN` |
| 23 | `safety_emergency_red_flag_023_3558456503` | 牙龈灰白 / 泥灰 / 暗砖红 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_D2RD3F9NCH` |
| 24 | `safety_emergency_red_flag_024_5a4a9573ce` | 毛细血管再充盈时间延长 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_NRDKEFJV4T` |
| 25 | `safety_emergency_red_flag_025_abbe3c72ee` | 四肢/耳尖冰凉伴虚弱 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_BVAEKV9JQV` |
| 26 | `safety_emergency_red_flag_026_448d0fff92` | 濒死呼吸 / 喘息样呼吸 / 呼吸暂停 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_SCEQ7Y5NC9` |
| 27 | `safety_emergency_red_flag_027_f60a6a2566` | 癫痫持续状态 / 群集性抽搐 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_KMEGY4WK97` |
| 28 | `safety_emergency_red_flag_028_aff9a790f2` | 突发后躯瘫痪/无力 — 椎间盘疾病 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_ZTEFMP534H` |
| 29 | `safety_emergency_red_flag_029_f6835045bb` | 急性前庭综合征 — 歪头/转圈/眼球震颤 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_85HZC7Y4SS` |
| 30 | `safety_emergency_red_flag_030_49deeb4d0f` | 意识改变 / 头部外伤 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_8GS9QZPM6A` |
| 31 | `safety_emergency_red_flag_031_56e21afb25` | 车祸/高坠等严重外伤 + 创伤性休克 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_AS7C33C6FP` |
| 32 | `safety_emergency_red_flag_032_8ef5c08e96` | 难产 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_FH4R5SH3TW` |
| 33 | `safety_emergency_red_flag_033_9751942180` | 眼球脱出/突出 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_6BENX5K7CJ` |
| 34 | `safety_emergency_red_flag_034_84f1d11b75` | 中暑 / 热射病 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_TXBBC7SGKN` |
| 35 | `safety_emergency_red_flag_035_3f4574145f` | 过敏性休克 / 严重过敏反应 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_5JRR8G4DKB` |
| 36 | `safety_emergency_red_flag_036_f20a5d590a` | 误食异物 / 胃肠道梗阻 | `EMERGENCY_RED_FLAG` | `EMERGENCY_MODE_F2FVT5M2Z6` |
| 37 | `safety_emergency_red_flag_037_f50138da9e` | 胃扩张扭转 / 胃扭转 | `GDV_RISK_PATTERN` | `EMERGENCY_MODE_5FW63BED4Y` |

## 13. 相关文档

1. [临床安全待迁移问题与分阶段治理方案](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-open-issues-migration-plan.md)
2. [临床安全资产发布态契约](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-asset-contract.md)
3. [临床安全前置上下文裁决变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-required-context-change-summary.md)
4. [临床安全补齐测试基线变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-test-baseline-change-summary.md)
