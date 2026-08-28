<!--
=============================================================================
文件: docs/architecture/input-preprocessing-shadow-validation-change-summary.md
作用: 记录输入前置预处理 quick validation 的实现边界、运行方式、验证结果和后续动作。
范围: 覆盖 ideal structured injection、两阶段 shadow analyzer、质量门禁、领域投影和行为模拟。
说明: 本链路仅用于离线/显式集成验证，不接入生产响应、问诊状态或临床安全主路径。
维护: 当 shadow 契约、A/B/C/D/E 样本、质量门禁或远程冒烟脚本调整时同步更新本文。
=============================================================================
-->

# 输入前置预处理 Shadow 快速验证变更总结

> **文档状态**：快速验证链路已实现；真实模型 shadow 结果仍需迭代，不进入生产消费
>
> **架构结论入口**：[agent-input-preprocessing-shadow-experiment-architecture-guidance.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-shadow-experiment-architecture-guidance.md)

## 1. 验证定位

本阶段回答两个独立问题：

1. **Ideal Input Injection**：如果上游已经产出语义正确、主体可信、断言分明的结构化事实，现有问诊状态合并、回答充分性 OPA 和多轮收敛链路是否能正常消费。
2. **Shadow Extraction Feasibility**：在真实 LiteLLM `qwen-plus` 与 embedding 服务上，新的两阶段结构化前置分析是否能产出接近期望的证据图，并由质量门禁暴露失败。

本阶段不验证完整兽医 canonical 词表、生产问诊语义抽取替换、临床安全主路径切换、在线 trace 平台建设或长期记忆候选抽取。

## 2. 实现内容

新增包：

```text
src/vet_agent/input_preprocessing/
```

核心组件：

| 组件 | 职责 |
|---|---|
| `contracts.py` | TurnContext、Segment、SubjectBinding、AssertionObservation、Temporal/Measurement、QualityGateResult 等稳定契约 |
| `analyzer.py` | 两阶段结构化分析：segmentation / rewrite 与 evidence verifier |
| `vocabulary.py` | 加载评估版 canonical vocabulary |
| `gates.py` | Contract、suspicious empty、subject、assertion 同步质量门禁 |
| `projection.py` | 显式问诊投影与临床安全 shadow 投影 |
| `evaluation.py` | Ideal injection、真实模型 shadow、OPA 行为模拟与 JSON 报告 |

新增评估资产：

```text
assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json
tests/fixtures/input_preprocessing/quick_validation.json
scripts/integration/run-input-preprocessing-shadow-smoke.sh
```

## 3. 数据流

```text
A/B/C/D/E fixture
→ TurnContext
→ qwen-plus claim-level segmentation / discourse role / intent
→ text-embedding-v4 canonical candidate recall
→ qwen-plus assertion / subject / temporal / canonical verifier
→ Quality Gates
→ Consultation projection
→ Clinical safety shadow projection
→ ConsultationStateService
→ OPA consultation answerability
→ JSON report
```

批量 evidence verifier 失败时，允许一次同契约、有界的 per-segment 重试；重试次数写入 `stage_attempts`。该重试不改变契约、不解析自由 JSON、不使用关键词补事实。

## 4. 质量门禁

当前同步门禁覆盖：

1. TurnContext 主体引用；
2. segment 原文锚定；
3. evidence 原文锚定；
4. canonical ID 存在性；
5. subject 引用、类型和解析方法；
6. temporal / measurement 所属 segment；
7. 非空事实输入的可疑空结果；
8. 同一 subject + canonical 的断言冲突。

Blocking gate 失败时不进入问诊投影、临床安全投影或行为模拟，并在报告中标记 failed。

多宠物歧义输出 `subject_ambiguous`，不会被默认为当前宠物；`normal`、`denied`、`unknown` 在投影 metadata 中分离保留。

## 5. 运行方式

Ideal injection：

```bash
uv run python -m vet_agent.input_preprocessing.evaluation \
  --mode ideal \
  --repeat 1 \
  --policy local
```

远程真实服务 shadow：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
INPUT_PREPROCESSING_MODE=shadow \
scripts/integration/run-input-preprocessing-shadow-smoke.sh
```

脚本通过 SSH 隧道访问远程 LiteLLM 与 OPA，并同步当前分支的问诊回答充分性策略。可用 `--only <sample_id>` 过滤样本。脚本只读取显式环境变量，不把开发密钥写入仓库。

## 6. 当前验证结果

### 本地 ideal injection

结果：

```text
A/B/C/D/E：5/5 passed
```

关键行为：

1. A/B 低风险软便输入进入 `answer` / `slot_complete`；
2. C 的 `answer_now=true` 被 OPA 接受，返回 `answer` / `user_requested_answer_now`；
3. D 第二轮 unknown slot 从 3 降到 0；
4. 已回答槽位重复追问数为 0；
5. `normal` 未投影为 `denied`；
6. `denied` 未投影为 `present`。

这证明当前下游问诊状态与回答充分性链路具备消费正确结构化事实的能力。

### 远程 qwen-plus shadow

完整报告：

```text
.data/evaluations/input-preprocessing-remote/input-preprocessing-69695673e467.json
```

结果：

```text
A：passed
B：passed
C：passed
D：failed
E：failed
```

后续 D 单样本迭代显示：

1. 两轮行为模拟均已通过；
2. 第二轮进入 `answer` / `slot_complete`；
3. unknown slot 从 3 降为 0；
4. 已回答槽位重复追问数为 0；
5. 但并列否定中仍漏抽 `bloody_stool denied`，semantic precision / recall 未达全量匹配。

后续 E 单样本迭代显示：

1. 模糊分诊曾输出 `not_found` 占位或发明 `problem_onset` / `multi_pet_context`；
2. 主体歧义绑定存在字段不一致；
3. 这些结果被 `evidence_contract` blocking gate 拦截；
4. 未进入领域投影或策略消费。

结论：下游消费链路可用；新前置分析方向有效，但 canonical 约束、多事实并列否定和主体歧义提示仍需迭代。质量门禁能够暴露这些失败，没有静默降级。

## 7. 明确不做事项

1. 不把该链路接入 `VetOrchestrator`；
2. 不写 `consultation_states`；
3. 不替换现有问诊语义抽取器；
4. 不把临床安全投影接入现有 evaluator；
5. 不让 OPA 扫描原始文本；
6. 不使用关键词、正则或短语匹配补事实；
7. 不使用宽松文本 JSON 解析；
8. 不用默认追问掩盖失败。

## 8. 后续建议

1. 扩充 canonical verifier 的并列否定样本；
2. 将 Stage 1 输出的 claim-level segmentation 进一步约束为单事实片段；
3. 对 `subject_ambiguous` 使用更明确的 schema 示例和离线评估；
4. 将 `not_found` 占位输出固化为负例测试；
5. 增加每个样本 3 次重复运行的漂移报告；
6. 在 A/B/C/D/E 稳定后，才评估 API metadata shadow。
