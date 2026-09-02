<!--
=============================================================================
文件: input-preprocessing-v10-environment-smoke-change-summary.md
作用: 记录第十轮 input-preprocessing 边界校准与早退实验的远程环境基线、
      模型 snapshot、基础冒烟与兼容性验证结果。
范围: 覆盖 V10 独立 Python 环境、Torch CPU、GLiNER、generic tokenizer、
      SpanMarker 依赖与中文底座、LiteLLM、embedding、BAML 和 V8/V9 兼容测试。
说明: 本文证明 V10 实验环境可执行；不证明 boundary calibration、macro、
      relation adapter 或 early-exit 质量，不解除 V8 phase gate。
维护: 当 V10 依赖版本、模型 snapshot、离线加载契约或冒烟脚本变化时同步更新。
=============================================================================
-->

# Input preprocessing V10 环境基线与基础冒烟记录

> **结论**：远程开发服务器已具备 V10 exploratory shadow 实验所需的基础环境。独立 `.venv-v10`、CPU Torch、GLiNER small / multilingual snapshot、中文 generic tokenizer、SpanMarker 依赖与三个中文底座、LiteLLM `qwen-plus`、`text-embedding-v4`、BAML schema / CLI 均通过基础冒烟。使用 `.venv-v10` 执行 V8/V9 测试为 `20 passed`，V8 strict environment smoke 亦通过。
>
> **限制**：本文是环境与工具链记录，不是 V10 质量结论。`SPANMARKER-CHINESE` 尚未训练或评估；`SPAN-CALIBRATE`、`MACRO-*`、`REL-*` 和 early-exit 均未执行。所有结果保持 `diagnostic_only=true`、`can_unblock_v8_phase=false`。

## 1. 环境基线

V10 使用独立虚拟环境，避免 SpanMarker 训练依赖污染已验证的 V8 环境：

```text
<remote-repository-root>/.venv-v10
```

当前版本：

| 组件 | 版本 / 状态 |
|---|---|
| Python | 3.12.0 |
| CPU / GPU | 4 vCPU，无 GPU |
| Torch | `2.8.0+cpu` |
| CUDA | `cuda_available=false`，无 NVIDIA Python 包 |
| Transformers | `4.55.4` |
| tokenizers | `0.21.4` |
| GLiNER | `0.2.28` |
| Hugging Face Hub | `0.36.0` |
| seqeval | `1.2.2` |
| scikit-learn | `1.9.0` |
| SpanMarker | `span-marker==1.8.1` |
| accelerate | `1.14.0` |
| datasets | `5.0.1` |
| evaluate | `0.4.6` |
| LiteLLM | `1.96.2` |
| Instructor | `1.15.4` |
| OpenAI SDK | `2.54.0` |
| BAML Python | `baml-py==0.226.1` |
| BAML npm / CLI | `@boundaryml/baml==0.226.1` |
| Node.js / npm | `v22.23.2` / `10.9.8` |

`span-marker` 已固化为 optional extra：

```bash
uv sync --extra v8 --extra v10-refine
```

V10 环境文件：

```text
<remote-repository-root>/.env.v10.local
```

该文件包含 LiteLLM virtual key，当前权限为 `600`，不得提交仓库或复制到正式文档。

VPN / mihomo 环境下，LiteLLM loopback 调用继续显式绕过 proxy：

```text
NO_PROXY=127.0.0.1,localhost
no_proxy=127.0.0.1,localhost
```

## 2. 模型 snapshot

模型根目录：

```text
<remote-model-cache>/v10-models
```

目录约 `2.3GiB`。现有 V8 GLiNER small 以 symlink 复用，未重复占用存储。

| 用途 | repo | revision | local path |
|---|---|---|---|
| GLiNER baseline | `gliner-community/gliner_small-v2.5` | `f227d3cd637bd4e6757ae143935316d062393341` | `gliner/gliner-community__gliner_small-v2.5` |
| GLiNER 对照 | `urchade/gliner_multi-v2.1` | `443d26d654e0324125a96bebd8e796c14ff2efe6` | `gliner/urchade__gliner_multi-v2.1` |
| GLiNER 对照 tokenizer / base config | `microsoft/mdeberta-v3-base` | `a0484667b22365f84929a935b5e50a51f71f159d` | `tokenizers/microsoft__mdeberta-v3-base` |
| generic tokenizer | `bert-base-chinese` | `8f23c25b06e129b6c986331a13d8d025a92cf0ea` | `tokenizers/bert-base-chinese` |
| generic tokenizer 对照 | `hfl/chinese-roberta-wwm-ext` | `5c58d0b8ec1d9014354d691c538661bf00bfdb44` | `tokenizers/hfl__chinese-roberta-wwm-ext` |
| generic tokenizer 对照 | `hfl/chinese-macbert-base` | `a986e004d2a7f2a1c2f5a3edef4e20604a974ed1` | `tokenizers/hfl__chinese-macbert-base` |
| SpanMarker 底座 | `bert-base-chinese` | `8f23c25b06e129b6c986331a13d8d025a92cf0ea` | `spanmarker/bert-base-chinese` |
| SpanMarker 底座 | `hfl/chinese-roberta-wwm-ext` | `5c58d0b8ec1d9014354d691c538661bf00bfdb44` | `spanmarker/hfl__chinese-roberta-wwm-ext` |
| SpanMarker 底座 | `hfl/chinese-macbert-base` | `a986e004d2a7f2a1c2f5a3edef4e20604a974ed1` | `spanmarker/hfl__chinese-macbert-base` |

关键权重哈希：

```text
gliner_small-v2.5/pytorch_model.bin
  f3aa07b0bbd2c7d551e935fe998ceaaaa1d387f7d92f05ec70396c1264f41d22

gliner_multi-v2.1/model.safetensors
  2100142f31627531497850659dcb3821c99d5e71c08a8e01a98e4b11ef32a199

bert-base-chinese/model.safetensors
  3404a1ffd8da507042e8161013ba2a4fc49858b4e3f8fbf5ce5724f94883aec3

chinese-roberta-wwm-ext/pytorch_model.bin
  1ded5a5a1c7841dee6e47942f7b5bf2bcf6f73ff19197580f852f7f638f86b35

chinese-macbert-base/pytorch_model.bin
  db0506d985574b80c33eec1cf13bd4c130585568753175871095ca13dbad9e23
```

### 2.1 multilingual GLiNER 离线加载契约

`urchade/gliner_multi-v2.1` 的源 `gliner_config.json` 使用 `microsoft/mdeberta-v3-base` 作为 encoder config source。在 `HF_HUB_OFFLINE=1` 下，直接加载会尝试访问 Hugging Face。

本地 snapshot 保留原始配置：

```text
gliner_config.source.json
```

并生成离线配置：

```text
gliner_config.json
```

离线配置仅做两处部署级修改：

```text
model_name -> <remote-model-cache>/v10-models/tokenizers/microsoft__mdeberta-v3-base
vocab_size -> 250105
```

加载该 snapshot 时必须关闭 embedding resize：

```python
GLiNER.from_pretrained(
    path,
    map_location="cpu",
    local_files_only=True,
    resize_token_embeddings=False,
)
```

原因：checkpoint embedding 行数为 `250105`；默认 tokenizer resize 会先把 shell 调整到本地 tokenizer 长度，导致 state dict size mismatch。该契约只影响离线加载，不修改权重，也不表示该模型质量胜出。

## 3. 基础冒烟结果

报告：

```text
.data/evaluations/input-preprocessing-v10/environment/v10-environment-smoke-20260828-194253.json
sha256=efff931f3b95c1797d532a8f6866c846881279a9c27d1e29df8f78efed20abd9
```

结果：

```text
passed_count=13
failed_count=0
```

| 检查项 | 结果 |
|---|---|
| 依赖版本锁定 | PASS |
| Torch CPU / 无 NVIDIA 包 | PASS |
| `span_marker` import | PASS |
| 三个中文 tokenizer offline load + offset 反查 | PASS |
| GLiNER small offline load + inference | PASS |
| GLiNER multilingual offline load + inference | PASS |
| BERT Chinese base weights load | PASS |
| Chinese RoBERTa base weights load | PASS |
| MacBERT base weights load | PASS |
| LiteLLM readiness | PASS |
| `qwen-plus` chat | PASS |
| `text-embedding-v4` | PASS，维度 1024 |
| BAML schema check | PASS |

冒烟只验证模型能离线加载、offset 能反查、服务能调用和 schema 可检查；不评估实体抽取质量，也不把输出数量作为通过条件。

在部分 Python 进程退出阶段会出现 `multiprocess.resource_tracker` destructor 警告。该警告发生在所有检查完成之后，不影响上述结果；若出现在检查过程中，应单独归因，不得忽略。

## 4. 兼容性验证

使用 `.venv-v10` 执行既有 V8/V9 测试：

```text
tests/test_input_preprocessing_v8.py
tests/test_input_preprocessing_v9.py

20 passed
```

使用 `.venv-v10` 执行 V8 strict environment smoke：

```text
INPUT_PREPROCESSING_V8_PYTHON=<remote-repository-root>/.venv-v10/bin/python
scripts/integration/verify-input-preprocessing-v8-environment.sh --strict-v8-gates
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

## 5. 运行产物

```text
.data/evaluations/input-preprocessing-v10/environment/model-baseline.txt
.data/evaluations/input-preprocessing-v10/environment/python-freeze.txt
.data/evaluations/input-preprocessing-v10/environment/model-manifest.sha256
.data/evaluations/input-preprocessing-v10/environment/env-file.sha256
.data/evaluations/input-preprocessing-v10/environment/v10-environment-smoke-latest.json
.data/evaluations/input-preprocessing-v10/environment/environment-smoke-manifest.sha256
```

关键 manifest 哈希：

```text
python-freeze.txt
  46d1d408288c6bab7006cc4cf810c1374b21996e6646ea9cf897cb5201dead0d

model-baseline.txt
  17f7f66d92bf66dcab0cebd892c1f6f351d83f1df33a9e5b94ca3df7f2cba5c9

model-manifest.sha256
  d7927d88de5bdfd60deb0903dcd73470ed05a16e50833407058f6352213b7529

v10-environment-smoke-latest.json
  efff931f3b95c1797d532a8f6866c846881279a9c27d1e29df8f78efed20abd9
```

## 6. 复现命令

同步依赖定义：

```bash
uv sync --extra v8 --extra v10-refine
```

基础环境检查：

```bash
cd <remote-repository-root>
set -a
source .env.v10.local
set +a

.venv-v10/bin/python - <<'PY'
import torch
import transformers
import span_marker
from importlib.metadata import version

assert torch.__version__ == "2.8.0+cpu"
assert torch.cuda.is_available() is False
assert transformers.__version__ == "4.55.4"
assert version("span-marker") == "1.8.1"
print("v10 dependency smoke: PASS")
PY
```

Tokenizer offset 检查：

```bash
.venv-v10/bin/python - <<'PY'
from transformers import AutoTokenizer

path = "<remote-model-cache>/v10-models/tokenizers/bert-base-chinese"
text = "猫前天开始呕吐，一天两次，吐未消化的猫粮。"
tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
encoded = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
for start, end in encoded["offset_mapping"]:
    assert text[start:end] != ""
print("tokenizer offset smoke: PASS")
PY
```

Multilingual GLiNER offline 检查：

```bash
.venv-v10/bin/python - <<'PY'
from gliner import GLiNER

path = "<remote-model-cache>/v10-models/gliner/urchade__gliner_multi-v2.1"
model = GLiNER.from_pretrained(
    path,
    map_location="cpu",
    local_files_only=True,
    resize_token_embeddings=False,
)
print(type(model).__name__)
PY
```

既有 V8/V9 兼容测试：

```bash
.venv-v10/bin/python -m pytest \
  tests/test_input_preprocessing_v8.py \
  tests/test_input_preprocessing_v9.py \
  -q
```

## 7. 安全边界

本轮环境验证保持：

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

未新增：

```text
Redis / Kafka / RabbitMQ
业务数据库表
生产 queue
生产 projection
```

SpanMarker 依赖和底座权重可用不表示 supervised refinement winner；必须等待 `SPAN-CALIBRATE` 与 `SPAN-BUDGET` 结果，并在 development explicit-offset fixture 上做 cross-validation 后才能评估。
