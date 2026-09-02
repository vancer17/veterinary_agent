<!--
=============================================================================
文件: input-preprocessing-v8-environment-smoke-change-summary.md
作用: 记录第八轮 input-preprocessing 实验依赖、远程开发环境基础冒烟与基线修复。
范围: Python/Torch/GLiNER、LiteLLM、Instructor、embedding、BAML schema/runtime/CLI、
      V8 adapter、deterministic span governance 与 V8 quick/shadow runner。
说明: 本文证明实验环境与工具链可执行，不证明 SPAN-GOLDEN、STRUCT、MACRO
      或任何 V8 实验质量，也不构成生产消费准入。
维护: 当远程 V8 实验环境、依赖版本、模型 snapshot、BAML schema 或冒烟脚本变化时更新。
=============================================================================
-->

# Input preprocessing V8 环境基线与修复记录

> **结论**：远程开发服务器已具备第八轮实验的基础环境。Python/Torch CPU、GLiNER 本地模型、LiteLLM `qwen-plus`、Instructor、`text-embedding-v4`、BAML schema/runtime/CLI、V8 base / Instructor / BAML adapter 和 deterministic quote resolution 均已通过基础冒烟。初始冒烟暴露的 claim gate、bounded retry、BAML 项目定义与 V8 runner 缺口已修复；环境脚本 `--strict-v8-gates` 已通过。
>
> **限制**：本文是环境与工具链记录，不是 V8 质量结论。当前 GLiNER `staged` profile 在 development fixture 上的 required field coverage 仍为 `0.075`，SPAN-GOLDEN 未达标。该质量问题不能用关键词、正则或宽松匹配补齐。
> 后续全阶段 runner、live stage gate、quick ideal control 与 held-out / DSPy 防护见 [input-preprocessing-v8-shadow-runner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v8-shadow-runner-change-summary.md)。

## 1. 环境基线

远程开发服务器使用独立虚拟环境：

```text
<remote-repository-root>/.venv-v8
```

| 组件 | 版本 / 状态 |
|---|---|
| Python | 3.12.0 |
| CPU / GPU | 4 vCPU，无 GPU |
| Torch | `2.8.0+cpu` |
| CUDA | 无 NVIDIA Python 包，`cuda_available=false` |
| Transformers | `4.55.4` |
| GLiNER | `0.2.28` |
| Hugging Face Hub | `0.36.0` |
| seqeval | `1.2.2` |
| LiteLLM | `1.96.2` |
| Instructor | `1.15.4` |
| OpenAI SDK | `2.54.0` |
| BAML Python | `baml-py==0.226.1` |
| BAML npm / CLI | `@boundaryml/baml==0.226.1` |
| Node.js / npm | `v22.23.2` / `10.9.8` |
| httpx / socksio | `0.28.1` / `1.0.0` |

依赖已固化为 Python optional extra：

```text
uv sync --extra v8
```

`torch` 通过 PyTorch CPU index 固定为 `2.8.0+cpu`，避免默认解析拉入 CUDA/NVIDIA 包。DSPy 单独放在 `v8-optimize` extra，Phase 4 前不安装。

VPN / mihomo 环境下，LiteLLM loopback 调用显式绕过 proxy：

```text
NO_PROXY=127.0.0.1,localhost
no_proxy=127.0.0.1,localhost
```

远程环境文件：

```text
<remote-repository-root>/.env.v8.local
```

该文件包含 LiteLLM virtual key，必须保持 `600`，不得提交仓库或复制到正式文档。

## 2. 模型与外部服务

GLiNER snapshot：

```text
repo=gliner-community/gliner_small-v2.5
revision=f227d3cd637bd4e6757ae143935316d062393341
local_path=<remote-model-cache>/v8-models/gliner-community__gliner_small-v2.5
weight_sha256=f3aa07b0bbd2c7d551e935fe998ceaaaa1d387f7d92f05ec70396c1264f41d22
```

模型目录约 `645MiB`。

远程 LiteLLM：

```text
http://127.0.0.1:4000/v1
```

已验证：

| 能力 | 结果 |
|---|---|
| LiteLLM readiness | healthy，数据库 connected |
| `qwen-plus` + response_format / V8 base adapter | PASS |
| `qwen-plus` + Instructor / V8 Instructor adapter | PASS |
| `qwen-plus` + BAML typed schema / V8 wrapper | PASS |
| `text-embedding-v4` | PASS，维度 1024 |

本次没有调用 Mem0、OPA、PostgreSQL 业务库、clinical safety evaluator 或 required context。

## 3. 初始问题与修复结果

### 3.1 GLiNER all-label 输出为空

初始实现一次传入全部 V8 标签。基础文本在 threshold `0.5` 下返回空 span；focused labels 在 threshold `0.3` 下能返回候选，说明标签组与阈值敏感。

修复：

1. 增加 `core / participant / discourse / staged / all` label profiles；
2. 默认使用 `staged`，按三组分别预测并合并去重；
3. `extractor_version` 包含 adapter、profile、threshold 与模型 revision；
4. 所有候选仍必须携带 offset，并按原文反查验证；
5. 不做关键词、正则或语义兜底。

当前 development fixture 在 threshold `0.5` 下的 `SPAN-POOL-COVERAGE` 结果：

```text
required_field_count = 80
boundary_match_count = 6
predicted_span_count = 4
precision = 1.0
recall = 0.075
f1 = 0.1395
label_accuracy = 0.0
required_field_coverage = 0.075
```

结论：工具链可执行，但 recall、F1 与 label accuracy 未达标。后续只能在 development set 上继续做标签组、threshold、候选模型与边界策略对照。

### 3.2 Claim-level invalid reference gate 缺口

初始 governance 能阻断无效 claim 进入 projection，但没有生成 claim-level failed blocking gate；缺失 support 时还会把有效 target 一起计入 invalid attribution。

修复：

1. act 与 claim 的 invalid reference 分别输出 gate；
2. 缺失 support 只归因缺失 support span，不误标有效 target；
3. 全部 ID 存在但无法组成连续 binding 时，单独归因 `invalid_span_binding`；
4. target outside support 输出 `target_binding_error` blocking gate；
5. free quote 字段由 strict Pydantic schema 直接阻断；
6. `NEG-V8` 当前 `false_pass=0`、`model_free_quote_output=0`。

远程环境脚本执行：

```text
verify-input-preprocessing-v8-environment.sh --strict-v8-gates
```

结果为 PASS。

### 3.3 Instructor bounded retry 未闭环

初始尝试将 `max_retries=0` 传给 `instructor.from_openai()` 后，当前 Instructor / OpenAI SDK 组合在 patched create 调用中出现重复参数。

修复：

1. `AsyncOpenAI(max_retries=0)` 固定 transport retry；
2. Instructor 调用点显式传 `max_retries=0`；
3. V8 base / Instructor / BAML 内部 retry limit 默认均为 `0`；
4. `V8ModelExecution` 记录 `model_call_count` 与 `internal_retry_limit`；
5. run cache key 增加 adapter，避免不同 adapter 共享缓存；
6. cache 写入使用 `model_dump(mode="json")`，读取后重新验证为 V8 contract；
7. cache hit 的 `model_call_count=0`，冷调用按 attempt 与 internal retry 估算；
8. V8 analyzer 外层仍默认最多两次，并记录 first attempt status。

远程 STRUCT-INSTRUCTOR 冒烟：

```text
attempt_count=1
first_attempt_status=ok
model_call_count=1
internal_retry_limit=0
```

### 3.4 BAML 只有 runtime / CLI

初始环境只有 `baml-py` 与 BAML CLI，没有项目 schema、generated client 或 V8 wrapper。

修复后新增：

```text
baml_src/generators.baml
baml_src/clients.baml
baml_src/v8_macro.baml
src/vet_agent/input_preprocessing/baml_client/
src/vet_agent/input_preprocessing/v8_baml_client.py
```

BAML 定义：

1. `ExtractV8Macro` typed output；
2. LiteLLM OpenAI-compatible client；
3. `qwen-plus`；
4. `max_retries 0`；
5. enum alias 与稳定 V8 contract 对齐。

wrapper 负责 generated Pydantic 类型到 V8 stable contract 的转换，并默认关闭 BAML prompt 明文日志，避免 raw user text 进入控制台或报告。

远程真实 `STRUCT-BAML` 冒烟：

```text
schema_valid=1
invalid_span_reference_count=0
invalid_span_binding_count=0
model_free_quote_output=0
governed_claim_count=4
projection_ready_count=4
```

该结果只证明 BAML 工具链可用，不代表 BAML 质量胜出。

### 3.5 V8 runner 缺失

新增：

```text
src/vet_agent/input_preprocessing/v8_experiments.py
scripts/integration/run-input-preprocessing-v8-remote-runner.sh
```

环境基线完成时支持：

```text
SPAN-GOLDEN
SPAN-POOL-COVERAGE
NEG-V8
STRUCT-BASE
STRUCT-INSTRUCTOR
STRUCT-BAML
```

截至 2026-08-28，runner 已扩展到文档定义的全部 V8 实验 ID；live Phase 1+ 会在 Phase 0 未达标时输出 `upstream_blocked`。

报告统一记录 model、prompt version、schema version、metrics、execution audit 与 safety boundary。默认 quick 模式只执行本地 Phase 0；不读取 held-out，不写业务状态。

STRUCT adapter 初始化或调用失败时，runner 输出 `status=failed`、`failure_attribution=schema_adapter_failure` 和截断后的错误摘要，不用空结果或 fallback 掩盖依赖失败。

## 4. 复现命令

远程环境 strict 冒烟：

```bash
ssh -i <ssh-key-path> <remote-user>@<remote-host> \
  "cd <remote-repository-root> && scripts/integration/verify-input-preprocessing-v8-environment.sh --strict-v8-gates"
```

远程 Phase 0 quick validation：

```bash
scripts/integration/run-input-preprocessing-v8-remote-runner.sh \
  --mode quick \
  --experiment SPAN-GOLDEN \
  --experiment SPAN-POOL-COVERAGE \
  --experiment NEG-V8
```

远程 STRUCT adapter shadow：

```bash
scripts/integration/run-input-preprocessing-v8-remote-runner.sh \
  --mode shadow \
  --experiment STRUCT-BASE \
  --adapter base
```

可将 `STRUCT-BASE/base` 替换为：

```text
STRUCT-INSTRUCTOR/instructor
STRUCT-BAML/baml
```

环境脚本可选：

```text
--skip-model
--skip-llm
--skip-baml
--strict-v8-gates
```

## 5. 运行产物与 fixture

远程基线产物：

```text
.data/evaluations/input-preprocessing-v8/model-baseline.txt
.data/evaluations/input-preprocessing-v8/python-freeze.txt
.data/evaluations/input-preprocessing-v8/fixture-manifest.sha256
.data/evaluations/input-preprocessing-v8/env-file.sha256
.data/evaluations/input-preprocessing-v8/environment-smoke-manifest.sha256
```

Development fixture：

```text
be7e388f625b3c4095998a742265f09202c2e21e8fe0652cfb058cadd54728ba
tests/fixtures/input_preprocessing/eighth_round_span_macro_matrix.json
```

Held-out fixture：

```text
4d30b4021a81f969e1b5cbdebc46cbf0a99b892c306774f93fff9fb7204e900e
tests/fixtures/input_preprocessing/eighth_round_span_macro_held_out.json
```

Held-out 只允许在 `HELD-OUT-V8` 使用；DSPy 优化不得读取。

## 6. 安全边界

所有环境验证与 runner 保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

未接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

环境、BAML、runner 或 adapter 可用不改变 V8 report-only 边界，也不构成生产消费准入。
