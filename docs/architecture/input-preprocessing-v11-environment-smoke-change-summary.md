<!--
=============================================================================
文件: input-preprocessing-v11-environment-smoke-change-summary.md
作用: 记录第十一轮 input-preprocessing candidate view / reranking 实验的
      远程环境基线、模型 snapshot、基础冒烟与兼容性验证结果。
范围: 覆盖独立 Python 环境、Torch CPU、NetworkX、BGE reranker、LiteLLM、
      qwen-plus、text-embedding-v4、BAML 与 V8/V9/V10 兼容测试。
说明: 本文证明 V11 环境与工具链可执行；不证明 candidate view、reranking、
      macro、relation 或 early-exit 质量，不解除 V8 live phase gate。
维护: 当 V11 依赖版本、模型 snapshot、环境脚本或冒烟结果变化时同步更新。
=============================================================================
-->

# Input preprocessing V11 环境基线与基础冒烟记录

> **结论**：远程开发服务器已具备 V11 candidate view / reranking 探索性 shadow 实验所需的基础环境。独立 `.venv-v11`、Torch CPU、NetworkX、`BAAI/bge-reranker-base` 离线 snapshot、LiteLLM `qwen-plus`、`text-embedding-v4` 与 BAML schema / generated client 均通过基础冒烟。使用 `.venv-v11` 执行 V8/V9/V10 测试为 `28 passed`，V8 strict environment smoke 亦通过。
>
> **限制**：本文是环境与工具链记录，不是 V11 质量结论。reranker 冒烟只验证离线加载、tokenizer offset 和有限 logits 输出，不评估排序质量；`SNAP-INTEGRITY`、`VIEW-COVERAGE`、`RANK-CROSS`、`MACRO-*`、`REL-COLD3` 和 `EARLY-*` 均尚未执行。所有 V11 结果继续要求 `diagnostic_only=true`、`can_unblock_v8_phase=false`。

## 1. 环境基线

V11 使用独立虚拟环境，避免 reranker 与 graph 实验依赖污染既有环境：

```text
<remote-repository-root>/.venv-v11
```

当前版本：

| 组件 | 版本 / 状态 |
|---|---|
| Python | `3.12.0` |
| CPU / GPU | 4 vCPU，无 GPU |
| Torch | `2.8.0+cpu` |
| CUDA | `cuda_available=false`，无 NVIDIA Python 包 |
| Transformers | `4.55.4` |
| tokenizers | `0.21.4` |
| safetensors | `0.8.0` |
| NetworkX | `3.6.1` |
| GLiNER | `0.2.28` |
| Hugging Face Hub | `0.36.0` |
| seqeval | `1.2.2` |
| SpanMarker | `1.8.1` |
| LiteLLM | `1.96.2` |
| Instructor | `1.15.4` |
| OpenAI SDK | `2.54.0` |
| BAML Python | `baml-py==0.226.1` |
| BAML npm / CLI | `@boundaryml/baml==0.226.1` |
| Node.js / npm | `v22.23.2` / `10.9.8` |
| httpx / socksio | `0.28.1` / `1.0.0` |
| sentence-transformers | 未安装 |

`sentence-transformers` 未安装是当前基线的有意选择。V11 baseline reranker adapter 使用：

```text
transformers.AutoTokenizer
transformers.AutoModelForSequenceClassification
```

因此不需要额外 wrapper，也避免最新 `sentence-transformers` 要求更高版本 Transformers 而破坏 V8/V10 固定依赖栈。

依赖已固化为 optional extra：

```toml
v11-rerank = [
    "networkx==3.6.1",
]
```

完整同步命令：

```bash
uv sync --extra v8 --extra v10-refine --extra v11-rerank
```

远程环境文件：

```text
<remote-repository-root>/.env.v11.local
```

该文件包含 LiteLLM virtual key，当前权限为 `600`，不得提交仓库、复制到正式文档或在日志中输出明文。

VPN / mihomo 环境下，LiteLLM loopback 调用继续显式绕过 proxy：

```text
NO_PROXY=127.0.0.1,localhost
no_proxy=127.0.0.1,localhost
```

## 2. Reranker snapshot

V11 baseline reranker：

```text
repo=BAAI/bge-reranker-base
revision=2cfc18c9415c912f9d8155881c133215df768a70
local_path=<remote-model-cache>/v11-models/reranker/BAAI__bge-reranker-base
weight=model.safetensors
weight_sha256=ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd
weight_bytes=1112206140
```

关键 tokenizer 文件 SHA256：

```text
sentencepiece.bpe.model
  cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865

tokenizer.json
  9eb652ac4e40cc093272bbbe0f55d521cf67570060227109b5cdc20945a4489e
```

运行配置：

```text
adapter=transformers-auto-sequence-classification
device=cpu
batch_size=4
max_length=256
threads=4
```

该模型只作为 role-conditioned candidate reranker。它不得：

```text
生成 span
修改 offset / text
输出 quote
输出 canonical
判断医学事实
生成 claim
```

## 3. 冒烟脚本

新增：

```text
scripts/integration/verify-input-preprocessing-v11-environment.sh
```

支持：

```text
--skip-model
--skip-llm
--skip-baml
--skip-artifacts
```

脚本检查：

1. `.env.v11.local` 权限与必需字段；
2. Python / Torch CPU / 关键依赖版本；
3. 无 NVIDIA Python 包；
4. reranker snapshot 文件完整性与权重 SHA256；
5. NetworkX offset graph；
6. reranker tokenizer offset 反查；
7. reranker CPU 离线加载与有限 logits 输出；
8. LiteLLM readiness；
9. `qwen-plus` chat；
10. `text-embedding-v4` 维度；
11. BAML npm 版本、generated client、schema check 与日志策略。

脚本输出报告版本：

```text
v11-environment-smoke-1
```

报告显式包含：

```text
diagnostic_only=true
can_unblock_v8_phase=false
safety_boundary
limitations
```

## 4. 基础冒烟结果

报告：

```text
.data/evaluations/input-preprocessing-v11/environment/v11-environment-smoke-20260831-135618-946626.json
sha256=911f0b5d43e3f9258fb9f7d2c6b35634ff9fdeee170ab9d80530746a706a03f3
```

结果：

```text
passed_count=7
failed_count=0
skipped_count=0
status=passed
```

| 检查项 | 结果 |
|---|---|
| 环境文件权限与字段 | PASS |
| Python / Torch CPU / 依赖版本 / 无 NVIDIA 包 | PASS |
| BGE reranker snapshot 完整性与权重哈希 | PASS |
| NetworkX offset graph | PASS |
| BGE reranker CPU offline load + inference | PASS |
| LiteLLM readiness / `qwen-plus` / `text-embedding-v4` | PASS |
| BAML schema / generated client / log policy | PASS |

关键运行数据：

```text
reranker:
  model=XLMRobertaForSequenceClassification
  tokenizer=XLMRobertaTokenizerFast
  load_ms=1545.052
  inference_ms=2981.586
  pair_count=3
  finite_logits=true
  quality_asserted=false

LiteLLM:
  readiness=healthy
  db=connected
  qwen_plus_latency_ms=543.243
  embedding_dimension=1024
  embedding_latency_ms=193.061
```

冒烟中的三个 reranker score 均为有限值，但数值本身不作为质量结论：

```text
-10.19 左右
```

原因：本冒烟没有校准阈值，也没有对照 gold candidate 排名；只验证 CPU 推理链路可执行。

## 5. 兼容性验证

使用 `.venv-v11` 执行：

```text
tests/test_input_preprocessing_v8.py
tests/test_input_preprocessing_v9.py
tests/test_input_preprocessing_v10.py
```

结果：

```text
28 passed
```

使用 `.venv-v11` 执行 V8 strict environment smoke：

```bash
INPUT_PREPROCESSING_V8_PYTHON=<remote-repository-root>/.venv-v11/bin/python \
  scripts/integration/verify-input-preprocessing-v8-environment.sh \
  --strict-v8-gates
```

结果：

```text
PASS
```

覆盖：

```text
V8 GLiNER adapter
span offset 反查
deterministic quote governance
invalid reference blocking gate
base structured adapter
Instructor adapter
BAML adapter
LiteLLM embedding
BAML CLI / generated client
bounded retry limit=0
```

与 V10 环境相同，部分 Python 进程退出阶段可能出现 `multiprocess.resource_tracker` destructor warning。本轮警告发生在所有检查完成之后，脚本退出码为 0，不影响结果。如果未来该警告出现在检查过程中，必须单独归因，不得忽略。

## 6. 运行产物

```text
.data/evaluations/input-preprocessing-v11/environment/python-freeze.txt
.data/evaluations/input-preprocessing-v11/environment/model-baseline.txt
.data/evaluations/input-preprocessing-v11/environment/model-manifest.sha256
.data/evaluations/input-preprocessing-v11/environment/env-file.sha256
.data/evaluations/input-preprocessing-v11/environment/v11-environment-smoke-latest.json
.data/evaluations/input-preprocessing-v11/environment/environment-smoke-manifest.sha256
```

关键哈希：

```text
python-freeze.txt
  678bae94408f4bee6fd53cdce1fa154619fa450b010c10d0ba021a5f7422b9df

model-baseline.txt
  b73e7823492aaad3850189e24fd2cdb0ebd58ee20b6142e3544ca0405701ea06

model-manifest.sha256
  fb381f3c80ab8eb211f099f68647c39d876933f277d08a0a2fb415f8690bc0b8

env-file.sha256
  004ffe6abf6adb08de4054d0187ecca5175c19e5b9e23ab67161948008263b3d

v11-environment-smoke-latest.json
  911f0b5d43e3f9258fb9f7d2c6b35634ff9fdeee170ab9d80530746a706a03f3

environment-smoke-manifest.sha256
  a8f64bae1b5ed7430d8c5f58196d605c3a64cd9099f387de9e2f653e2d003836
```

## 7. 复现命令

依赖同步：

```bash
uv sync --extra v8 --extra v10-refine --extra v11-rerank
```

基础冒烟：

```bash
scripts/integration/verify-input-preprocessing-v11-environment.sh
```

只检查依赖与外部服务，跳过本地 reranker：

```bash
scripts/integration/verify-input-preprocessing-v11-environment.sh --skip-model
```

兼容性测试：

```bash
.venv-v11/bin/python -m pytest \
  tests/test_input_preprocessing_v8.py \
  tests/test_input_preprocessing_v9.py \
  tests/test_input_preprocessing_v10.py \
  -q
```

V8 strict 兼容冒烟：

```bash
INPUT_PREPROCESSING_V8_PYTHON=<remote-repository-root>/.venv-v11/bin/python \
  scripts/integration/verify-input-preprocessing-v8-environment.sh \
  --strict-v8-gates
```

## 8. 边界与限制

V11 环境可用不代表以下结论：

```text
candidate snapshot integrity
view coverage
reranking precision / recall
candidate budget 合理
macro skeleton / binding 质量
relation cold stability
early-exit 路由正确
V8 live phase gate 通过
生产消费准入
```

后续仍必须先执行：

```text
SNAP-INTEGRITY
VIEW-COVERAGE
RANK-BASE
RANK-CROSS
RANK-BUDGET
```

在 candidate view 或 seed 未达标前，不得执行 integration、adapter cold 或 held-out。

## 9. 安全边界

本轮所有实现与远程命令保持：

```text
consultation_state_written=false
clinical_safety_evaluator_called=false
clinical_safety_opa_called=false
required_context_called=false
held_out_read=false
dspy_used=false
```

未接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

未新增：

```text
Redis / Kafka / RabbitMQ
TEI reranker 服务
图数据库
业务数据库表
生产 queue
生产 projection
```
