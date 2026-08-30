#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/verify-input-preprocessing-v8-environment.sh
# 作用: 验证远程开发服务器上的 V8 input-preprocessing 实验环境基线。
# 范围: Python/Torch/GLiNER、V8 adapter、LiteLLM、Instructor、embedding、
#       BAML runtime/CLI 与 deterministic span governance 的基础可用性。
# 说明: 本脚本只做环境与基础设施冒烟，不证明 SPAN-GOLDEN 或 V8 实验质量。
#       不读取 held-out、不写业务状态、不调用临床安全 evaluator/OPA。
# =============================================================================

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: verify-input-preprocessing-v8-environment.sh [options]

Options:
  --skip-model       Skip local GLiNER model loading and V8 span extractor smoke.
  --skip-llm         Skip LiteLLM qwen-plus, Instructor, and embedding smoke.
  --skip-baml        Skip Node/BAML CLI smoke (Python package import remains checked).
  --strict-v8-gates  Fail when the current V8 governance negative case does not
                     emit a blocking gate. The environment-only default reports
                     this source-level gap as a warning.
  -h, --help         Show this help.
EOF
}

skip_model=false
skip_llm=false
skip_baml=false
strict_v8_gates=false

while (($# > 0)); do
    case "$1" in
        --skip-model)
            skip_model=true
            ;;
        --skip-llm)
            skip_llm=true
            ;;
        --skip-baml)
            skip_baml=true
            ;;
        --strict-v8-gates)
            strict_v8_gates=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${INPUT_PREPROCESSING_V8_PYTHON:-$ROOT/.venv-v8/bin/python}"
ENV_FILE="$ROOT/.env.v8.local"
cd "$ROOT"

if [[ ! -x "$PYTHON" ]]; then
    echo "Python interpreter not found: $PYTHON" >&2
    exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
else
    echo "Environment file not found: $ENV_FILE" >&2
    exit 1
fi

if "$strict_v8_gates"; then
    export INPUT_PREPROCESSING_V8_STRICT_GATES=1
fi

# LiteLLM runs on the remote loopback. The development host may also have a
# VPN/mihomo proxy, so explicitly bypass proxies for local service calls.
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

echo "[INFO] repository=$ROOT"
echo "[INFO] python=$PYTHON"

env_mode="$(stat -c '%a' "$ENV_FILE")"
if [[ "$env_mode" != "600" ]]; then
    echo "[FAIL] env_file_mode|expected=600|actual=$env_mode" >&2
    exit 1
fi
echo "[PASS] env_file_mode|600"

"$PYTHON" - <<'PY'
from importlib.metadata import PackageNotFoundError, version

expected = {
    "torch": "2.8.0+cpu",
    "transformers": "4.55.4",
    "gliner": "0.2.28",
    "huggingface-hub": "0.36.0",
    "seqeval": "1.2.2",
    "instructor": "1.15.4",
    "openai": "2.54.0",
    "baml-py": "0.226.1",
    "litellm": "1.96.2",
    "httpx": "0.28.1",
    "socksio": "1.0.0",
}

for package_name, expected_version in expected.items():
    try:
        actual_version = version(package_name)
    except PackageNotFoundError as exc:
        raise SystemExit(f"[FAIL] package_missing|{package_name}") from exc
    if actual_version != expected_version:
        raise SystemExit(
            f"[FAIL] package_version_mismatch|{package_name}|"
            f"expected={expected_version}|actual={actual_version}"
        )
    print(f"[PASS] package|{package_name}|{actual_version}")

import baml_py  # noqa: F401
import gliner  # noqa: F401
import instructor  # noqa: F401
import litellm  # noqa: F401
import openai  # noqa: F401
import httpx  # noqa: F401
import seqeval  # noqa: F401
import socksio  # noqa: F401
import torch
import transformers  # noqa: F401

if torch.__version__ != "2.8.0+cpu":
    raise SystemExit(
        f"[FAIL] torch_version|expected=2.8.0+cpu|actual={torch.__version__}"
    )
if torch.cuda.is_available():
    raise SystemExit("[FAIL] torch_cuda|cuda_is_available=true")
print(f"[PASS] torch_cpu|{torch.__version__}")

from importlib.metadata import distributions

nvidia_packages = sorted(
    {
        distribution.metadata["Name"]
        for distribution in distributions()
        if (distribution.metadata["Name"] or "").lower().startswith("nvidia-")
    }
)
if nvidia_packages:
    print(f"[FAIL] nvidia_packages_present|{','.join(nvidia_packages)}")
    raise SystemExit(1)
print("[PASS] no_nvidia_cuda_packages")
PY

if ! "$skip_model"; then
    if [[ -z "${INPUT_PREPROCESSING_V8_GLINER_MODEL:-}" ]]; then
        echo "[FAIL] INPUT_PREPROCESSING_V8_GLINER_MODEL is empty" >&2
        exit 1
    fi
    label_profile="${INPUT_PREPROCESSING_V8_GLINER_LABEL_PROFILE:-staged}"
    case "$label_profile" in
        core|participant|discourse|staged|all) ;;
        *)
            echo "[FAIL] unsupported_gliner_label_profile|$label_profile" >&2
            exit 1
            ;;
    esac
    echo "[PASS] gliner_label_profile|$label_profile"
    if [[ "${INPUT_PREPROCESSING_V8_GLINER_REVISION:-unpinned}" == "unpinned" ]]; then
        echo "[FAIL] gliner_revision_unpinned" >&2
        exit 1
    fi
    echo "[PASS] gliner_revision|${INPUT_PREPROCESSING_V8_GLINER_REVISION}"
    if [[ ! -d "$INPUT_PREPROCESSING_V8_GLINER_MODEL" ]]; then
        echo "[FAIL] GLiNER model directory not found: $INPUT_PREPROCESSING_V8_GLINER_MODEL" >&2
        exit 1
    fi

    "$PYTHON" - <<'PY'
import gc
import os
import time
from pathlib import Path

from gliner import GLiNER


model_path = Path(os.environ["INPUT_PREPROCESSING_V8_GLINER_MODEL"])
required_files = (
    "gliner_config.json",
    "pytorch_model.bin",
    "tokenizer.json",
)
for filename in required_files:
    if not (model_path / filename).is_file():
        raise SystemExit(f"[FAIL] gliner_file_missing|{filename}")
print(f"[PASS] gliner_snapshot_files|{model_path}")

text = "猫前天开始呕吐，一天两次，吐未消化的猫粮。"
started = time.perf_counter()
model = GLiNER.from_pretrained(str(model_path), map_location="cpu")
entities = model.predict_entities(
    text,
    ["target_mention", "temporal_expression", "measurement_expression"],
    threshold=0.3,
)
print(
    "[PASS] gliner_inference|"
    f"entities={len(entities)}|latency_ms={(time.perf_counter() - started) * 1000:.0f}"
)
# Entity count is intentionally not asserted: this is an infrastructure smoke,
# not SPAN-GOLDEN evidence.
del model
gc.collect()

from vet_agent.input_preprocessing.v8_span_extractors import build_v8_span_extractor

started = time.perf_counter()
extractor = build_v8_span_extractor()
spans = extractor.extract(
    source_id="environment-smoke-user-turn",
    source_block_id="block-001",
    text=text,
)
print(
    "[PASS] v8_span_extractor|"
    f"extractor={extractor.extractor_version}|spans={len(spans)}|"
    f"latency_ms={(time.perf_counter() - started) * 1000:.0f}"
)
if not spans:
    print(
        "[WARN] v8_span_extractor_empty|all-label/threshold output was empty; "
        "evaluate label subsets and thresholds in SPAN-GOLDEN"
    )
for span in spans:
    if text[span.start : span.end] != span.text or span.end <= span.start:
        raise SystemExit(f"[FAIL] invalid_v8_span_offset|{span.span_id}")
print("[PASS] v8_span_offsets")
PY
else
    echo "[SKIP] GLiNER and V8 span extractor smoke"
fi

"$PYTHON" - <<'PY'
import os

from vet_agent.input_preprocessing.v8_contracts import (
    V8MacroClaimRaw,
    V8MacroDiscourseActRaw,
    V8MacroSemanticRawOutput,
    V8SpanCandidate,
    V8SpanLabel,
)
from vet_agent.input_preprocessing.v8_span_governance import (
    V8SpanGovernance,
    V8SpanPool,
)


source_id = "environment-governance-smoke"
text = "猫呕吐两天"
spans = [
    V8SpanCandidate(
        span_id=f"{source_id}:support",
        source_id=source_id,
        source_block_id="block-001",
        start=0,
        end=len(text),
        text=text,
        label=V8SpanLabel.STATE_MENTION,
        score=1.0,
        extractor_version="environment-smoke",
    ),
    V8SpanCandidate(
        span_id=f"{source_id}:target",
        source_id=source_id,
        source_block_id="block-001",
        start=1,
        end=3,
        text=text[1:3],
        label=V8SpanLabel.TARGET_MENTION,
        score=1.0,
        extractor_version="environment-smoke",
    ),
]
governance = V8SpanGovernance(
    V8SpanPool(sources={source_id: text}, spans=spans)
)
valid = V8MacroSemanticRawOutput(
    acts=[
        V8MacroDiscourseActRaw(
            unit_id="u1",
            act_type="fact_statement",
            evidence_span_ids=[f"{source_id}:support"],
            confidence=1.0,
        )
    ],
    claims=[
        V8MacroClaimRaw(
            unit_id="u1",
            claim_id="c1",
            statement_type="reports",
            coarse_type="symptom",
            support_span_ids=[f"{source_id}:support"],
            target_span_ids=[f"{source_id}:target"],
            confidence=1.0,
        )
    ],
)
result = governance.govern(valid)
claim = result.governed_claims[0]
if claim.support.quote != text or claim.target.quote != "呕吐":
    raise SystemExit("[FAIL] v8_governance_quote_resolution")
if not claim.projection_ready:
    raise SystemExit("[FAIL] v8_governance_projection_not_ready")
print(
    "[PASS] v8_governance_quote_resolution|"
    f"support={claim.support.quote}|target={claim.target.quote}"
)

invalid = valid.model_copy(deep=True)
invalid.claims[0].support_span_ids = [f"{source_id}:missing"]
blocked = governance.govern(invalid)
missing_ref = f"{source_id}:missing"
if blocked.governed_claims or missing_ref not in blocked.invalid_span_references:
    raise SystemExit("[FAIL] v8_governance_did_not_block_invalid_reference")
blocking_gate = any(
    gate.status == "failed" and gate.severity == "blocking"
    for gate in blocked.gates
)
if blocking_gate:
    print("[PASS] v8_governance_invalid_reference_blocking_gate")
else:
    message = (
        "[WARN] v8_governance_invalid_reference_no_blocking_gate|"
        "claim is blocked from projection, but the current implementation does "
        "not summarize claim-level invalid references as a failed gate"
    )
    if os.environ.get("INPUT_PREPROCESSING_V8_STRICT_GATES") == "1":
        raise SystemExit(message)
    print(message)
PY

if ! "$skip_llm"; then
    if [[ -z "${INPUT_PREPROCESSING_LITELLM_BASE_URL:-}" ]]; then
        echo "[FAIL] INPUT_PREPROCESSING_LITELLM_BASE_URL is empty" >&2
        exit 1
    fi
    if [[ -z "${INPUT_PREPROCESSING_LITELLM_API_KEY:-}" ]]; then
        echo "[FAIL] INPUT_PREPROCESSING_LITELLM_API_KEY is empty" >&2
        exit 1
    fi

    litellm_root="${INPUT_PREPROCESSING_LITELLM_BASE_URL%/v1}"
    curl -fsS --max-time 10 "$litellm_root/litellm/health/readiness" | jq -e '.status == "healthy"' >/dev/null
    echo "[PASS] litellm_readiness|$litellm_root"

    "$PYTHON" - <<'PY'
import asyncio
import os
import time
from typing import Literal

import httpx
from pydantic import BaseModel

from vet_agent.input_preprocessing.v8_macro_analyzer import (
    V8InstructorStructuredClient,
    build_v8_structured_client,
)


class Probe(BaseModel):
    answer: Literal["ok"]


async def main() -> None:
    base_url = os.environ["INPUT_PREPROCESSING_LITELLM_BASE_URL"]
    api_key = os.environ["INPUT_PREPROCESSING_LITELLM_API_KEY"]
    messages = [{"role": "user", "content": "请只返回 answer=ok。"}]

    base_client = build_v8_structured_client("base")
    started = time.perf_counter()
    base_result = await base_client.run_structured(
        messages=messages,
        response_model=Probe,
        model="qwen-plus",
    )
    print(
        "[PASS] v8_base_structured_client|"
        f"answer={base_result.answer}|latency_ms={(time.perf_counter() - started) * 1000:.0f}"
    )

    instructor_client = V8InstructorStructuredClient(
        base_url=base_url,
        api_key=api_key,
    )
    started = time.perf_counter()
    instructor_result = await instructor_client.run_structured(
        messages=messages,
        response_model=Probe,
        model="qwen-plus",
    )
    print(
        "[PASS] v8_instructor_structured_client|"
        f"answer={instructor_result.answer}|latency_ms={(time.perf_counter() - started) * 1000:.0f}"
    )

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=45, trust_env=False) as http:
        response = await http.post(
            f"{base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "text-embedding-v4",
                "input": ["V8 canonical recall environment smoke"],
            },
        )
        response.raise_for_status()
        payload = response.json()
    dimension = len(payload["data"][0]["embedding"])
    if dimension != 1024:
        raise SystemExit(
            f"[FAIL] embedding_dimension|expected=1024|actual={dimension}"
        )
    print(
        "[PASS] litellm_embedding|text-embedding-v4|dimension=1024|"
        f"latency_ms={(time.perf_counter() - started) * 1000:.0f}"
    )


asyncio.run(main())
PY
else
    echo "[SKIP] LiteLLM, Instructor, and embedding smoke"
fi

if ! "$skip_baml"; then
    case "${BAML_LOG:-OFF}" in
        OFF|ERROR) ;;
        *)
            echo "[FAIL] baml_log_must_be_off_or_error|actual=${BAML_LOG}" >&2
            exit 1
            ;;
    esac
    echo "[PASS] baml_log_policy|${BAML_LOG:-OFF}"
    node_bin_dir="${INPUT_PREPROCESSING_V8_NODE_BIN_DIR:-$HOME/.local/opt/node-v22.23.2-linux-x64/bin}"
    if [[ -d "$node_bin_dir" ]]; then
        export PATH="$node_bin_dir:$PATH"
    fi
    if ! command -v node >/dev/null 2>&1; then
        echo "[FAIL] node_not_found" >&2
        exit 1
    fi
    if ! command -v npx >/dev/null 2>&1; then
        echo "[FAIL] npx_not_found" >&2
        exit 1
    fi
    node_version="$(node --version)"
    if [[ "$node_version" != v22.* ]]; then
        echo "[FAIL] node_major_version|expected=22.x|actual=$node_version" >&2
        exit 1
    fi
    echo "[PASS] node|$node_version"
    echo "[PASS] npm|$(npm --version)"

    for baml_file in \
        baml_src/generators.baml \
        baml_src/clients.baml \
        baml_src/v8_macro.baml; do
        if [[ ! -f "$ROOT/$baml_file" ]]; then
            echo "[FAIL] baml_source_missing|${baml_file}" >&2
            exit 1
        fi
    done
    npx --no-install baml-cli check --from "$ROOT/baml_src" >/dev/null
    echo "[PASS] baml_schema_check"

    "$PYTHON" - <<'PY'
import json
from pathlib import Path

from vet_agent.input_preprocessing.baml_client import b as baml_client
from vet_agent.input_preprocessing.v8_baml_client import INTERNAL_RETRY_LIMIT
from vet_agent.input_preprocessing.v8_macro_analyzer import (
    V8BamlStructuredClient,
    V8InstructorStructuredClient,
)

package = json.loads(Path("package.json").read_text(encoding="utf-8"))
actual = package.get("devDependencies", {}).get("@boundaryml/baml")
if actual != "0.226.1":
    raise SystemExit(
        f"[FAIL] baml_npm_version|expected=0.226.1|actual={actual}"
    )
print("[PASS] baml_npm|@boundaryml/baml|0.226.1")
if not hasattr(baml_client, "ExtractV8Macro"):
    raise SystemExit("[FAIL] baml_generated_function_missing")
print("[PASS] baml_generated_client|ExtractV8Macro")

instructor = V8InstructorStructuredClient(
    base_url="http://127.0.0.1:4000/v1",
    api_key="not-used",
    internal_retry_limit=0,
)
if instructor.internal_retry_limit != 0:
    raise SystemExit("[FAIL] instructor_internal_retry_not_bounded")
print("[PASS] instructor_bounded_retry|limit=0")

baml_adapter = V8BamlStructuredClient()
if baml_adapter.internal_retry_limit != INTERNAL_RETRY_LIMIT:
    raise SystemExit("[FAIL] baml_internal_retry_not_bounded")
print(f"[PASS] baml_bounded_retry|limit={INTERNAL_RETRY_LIMIT}")
PY

    baml_version="$(npx --no-install baml-cli --version)"
    if [[ "$baml_version" != "baml-cli 0.226.1" ]]; then
        echo "[FAIL] baml_cli_version|actual=$baml_version" >&2
        exit 1
    fi
    echo "[PASS] baml_cli|$baml_version"
else
    echo "[SKIP] Node and BAML CLI smoke"
fi

manifest="$ROOT/.data/evaluations/input-preprocessing-v8/model-baseline.txt"
if [[ -f "$manifest" ]]; then
    for field in repo revision local_path weight_sha256; do
        if ! grep -q "^${field}=" "$manifest"; then
            echo "[WARN] model_manifest_missing_field|${field}"
        fi
    done
    echo "[PASS] model_manifest|$manifest"
else
    echo "[WARN] model_manifest_missing|$manifest"
fi

echo "[PASS] v8_environment_smoke_complete"
