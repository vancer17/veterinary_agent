#!/usr/bin/env bash
# =============================================================================
# 文件: verify-input-preprocessing-v11-environment.sh
# 作用: 验证远程开发服务器上的 V11 candidate view / reranking 实验环境。
# 范围: Python/Torch CPU、NetworkX、BGE reranker、LiteLLM、embedding 与 BAML。
# 说明: 本脚本只做环境与工具链冒烟，不评估 candidate view、reranking、macro
#       或 relation 质量，不读取 held-out，不解除 V8 live phase gate。
# =============================================================================

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: verify-input-preprocessing-v11-environment.sh [options]

Options:
  --skip-model       Skip local BGE reranker loading and CPU inference.
  --skip-llm         Skip LiteLLM readiness, qwen-plus, and embedding smoke.
  --skip-baml        Skip BAML generated-client and schema-check smoke.
  --skip-artifacts   Do not write environment manifests or JSON report.
  -h, --help         Show this help.
EOF
}

skip_model=false
skip_llm=false
skip_baml=false
skip_artifacts=false

while (($# > 0)); do
    case "$1" in
        --skip-model) skip_model=true ;;
        --skip-llm) skip_llm=true ;;
        --skip-baml) skip_baml=true ;;
        --skip-artifacts) skip_artifacts=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${INPUT_PREPROCESSING_V11_PYTHON:-$ROOT/.venv-v11/bin/python}"
ENV_FILE="$ROOT/.env.v11.local"
cd "$ROOT"

if [[ ! -x "$PYTHON" ]]; then
    echo "Python interpreter not found: $PYTHON" >&2
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    echo "Environment file not found: $ENV_FILE" >&2
    exit 1
fi

env_mode="$(stat -c '%a' "$ENV_FILE")"
if [[ "$env_mode" != "600" ]]; then
    echo "[FAIL] env_file_mode|expected=600|actual=$env_mode" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# LiteLLM runs on loopback while the developer host may route external traffic
# through VPN/mihomo. Never send local service credentials through that proxy.
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-4}"
export BAML_LOG="${BAML_LOG:-OFF}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

node_bin_dir="${INPUT_PREPROCESSING_V11_NODE_BIN_DIR:-$HOME/.local/opt/node-v22.23.2-linux-x64/bin}"
if [[ -d "$node_bin_dir" ]]; then
    export PATH="$node_bin_dir:$PATH"
fi

export INPUT_PREPROCESSING_V11_SKIP_MODEL="$skip_model"
export INPUT_PREPROCESSING_V11_SKIP_LLM="$skip_llm"
export INPUT_PREPROCESSING_V11_SKIP_BAML="$skip_baml"
export INPUT_PREPROCESSING_V11_SKIP_ARTIFACTS="$skip_artifacts"

echo "[INFO] repository=$ROOT"
echo "[INFO] python=$PYTHON"
echo "[INFO] env_file=$ENV_FILE"

"$PYTHON" - <<'PY'
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
OUTPUT_DIR = ROOT / ".data/evaluations/input-preprocessing-v11/environment"
SKIP_MODEL = os.getenv("INPUT_PREPROCESSING_V11_SKIP_MODEL") == "true"
SKIP_LLM = os.getenv("INPUT_PREPROCESSING_V11_SKIP_LLM") == "true"
SKIP_BAML = os.getenv("INPUT_PREPROCESSING_V11_SKIP_BAML") == "true"
SKIP_ARTIFACTS = os.getenv("INPUT_PREPROCESSING_V11_SKIP_ARTIFACTS") == "true"
checks: list[dict[str, Any]] = []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record(
    name: str,
    function: Callable[[], dict[str, Any]],
    *,
    skipped: bool = False,
) -> None:
    started = time.perf_counter()
    if skipped:
        checks.append(
            {
                "name": name,
                "status": "skipped",
                "latency_ms": 0,
                "metadata": {},
            }
        )
        print(f"[SKIP] {name}")
        return
    try:
        metadata = function()
        checks.append(
            {
                "name": name,
                "status": "passed",
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "metadata": metadata,
            }
        )
        print(f"[PASS] {name}")
    except Exception as exc:  # noqa: BLE001 - preserve every smoke failure
        error = f"{type(exc).__name__}:{exc}"[:1200]
        checks.append(
            {
                "name": name,
                "status": "failed",
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "metadata": {"error_type": type(exc).__name__, "error": error},
            }
        )
        print(f"[FAIL] {name}|{error}", flush=True)


def optional_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def check_environment_file() -> dict[str, Any]:
    path = ROOT / ".env.v11.local"
    mode = oct(path.stat().st_mode & 0o777)
    required = {
        "INPUT_PREPROCESSING_LITELLM_BASE_URL",
        "INPUT_PREPROCESSING_LITELLM_API_KEY",
        "INPUT_PREPROCESSING_V11_RERANKER_ADAPTER",
        "INPUT_PREPROCESSING_V11_RERANKER_REPO",
        "INPUT_PREPROCESSING_V11_RERANKER_REVISION",
        "INPUT_PREPROCESSING_V11_RERANKER_WEIGHT_SHA256",
        "INPUT_PREPROCESSING_V11_RERANKER_MODEL",
    }
    text = path.read_text(encoding="utf-8")
    missing = sorted(name for name in required if f"export {name}=" not in text)
    if mode != "0o600":
        raise ValueError(f"env_file_mode_expected_600_actual_{mode}")
    if missing:
        raise ValueError("env_fields_missing:" + ",".join(missing))
    return {"path": str(path), "mode": mode, "field_count": len(required)}


def check_python_and_packages() -> dict[str, Any]:
    expected = {
        "torch": "2.8.0+cpu",
        "transformers": "4.55.4",
        "tokenizers": "0.21.4",
        "networkx": "3.6.1",
        "gliner": "0.2.28",
        "huggingface-hub": "0.36.0",
        "seqeval": "1.2.2",
        "span_marker": "1.8.1",
        "sentencepiece": "0.2.2",
        "instructor": "1.15.4",
        "openai": "2.54.0",
        "litellm": "1.96.2",
        "baml-py": "0.226.1",
        "httpx": "0.28.1",
        "socksio": "1.0.0",
    }
    actual: dict[str, str] = {}
    for package_name, expected_version in expected.items():
        try:
            actual_version = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"package_missing:{package_name}") from exc
        actual[package_name] = actual_version
        if actual_version != expected_version:
            raise ValueError(
                f"package_version_mismatch:{package_name}:"
                f"expected={expected_version},actual={actual_version}"
            )

    import gliner  # noqa: F401
    import httpx  # noqa: F401
    import instructor  # noqa: F401
    import litellm  # noqa: F401
    import networkx  # noqa: F401
    import openai  # noqa: F401
    import socksio  # noqa: F401
    import span_marker  # noqa: F401
    import torch
    import transformers  # noqa: F401

    if platform.python_version_tuple()[:2] != ("3", "12"):
        raise ValueError("python_version_expected_3.12")
    if torch.__version__ != "2.8.0+cpu":
        raise ValueError(f"torch_version_invalid:{torch.__version__}")
    if torch.cuda.is_available():
        raise ValueError("torch_cuda_available_true")
    nvidia_packages = sorted(
        {
            (distribution.metadata.get("Name") or "")
            for distribution in importlib.metadata.distributions()
            if (distribution.metadata.get("Name") or "")
            .lower()
            .startswith("nvidia-")
        }
    )
    if nvidia_packages:
        raise ValueError("nvidia_packages_present:" + ",".join(nvidia_packages))
    return {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cuda_available": False,
        "package_count": len(expected),
        "packages": actual,
        "sentence_transformers": optional_version("sentence-transformers"),
        "nvidia_packages": nvidia_packages,
    }


def check_reranker_snapshot() -> dict[str, Any]:
    model_path = Path(
        os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_MODEL", "")
    ).resolve()
    required_files = (
        "README.md",
        "config.json",
        "model.safetensors",
        "sentencepiece.bpe.model",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    )
    sizes: dict[str, int] = {}
    for filename in required_files:
        path = model_path / filename
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"reranker_file_missing_or_empty:{filename}")
        sizes[filename] = path.stat().st_size
    weight_hash = sha256_file(model_path / "model.safetensors")
    expected_hash = os.environ.get(
        "INPUT_PREPROCESSING_V11_RERANKER_WEIGHT_SHA256",
        "",
    )
    if expected_hash and weight_hash != expected_hash:
        raise ValueError(
            f"reranker_weight_sha_mismatch:expected={expected_hash},actual={weight_hash}"
        )
    config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    return {
        "adapter": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_ADAPTER", ""),
        "repo": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_REPO", ""),
        "revision": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_REVISION", ""),
        "local_path": str(model_path),
        "weight_sha256": weight_hash,
        "weight_bytes": sizes["model.safetensors"],
        "model_type": config.get("model_type", ""),
        "files": sizes,
    }


def check_networkx_offset_graph() -> dict[str, Any]:
    import networkx as nx

    graph = nx.DiGraph()
    graph.add_node("span:target", kind="span", start=8, end=10)
    graph.add_node("span:temporal", kind="span", start=1, end=5)
    graph.add_node("region:action", kind="claim_region", start=0, end=21)
    graph.add_edge("span:target", "region:action", relation="CONTAINED_IN")
    graph.add_edge("span:temporal", "region:action", relation="CONTAINED_IN")
    if not graph.has_edge("span:target", "region:action"):
        raise ValueError("networkx_offset_edge_missing")
    return {
        "version": nx.__version__,
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
    }


def check_reranker_inference() -> dict[str, Any]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_path = Path(os.environ["INPUT_PREPROCESSING_V11_RERANKER_MODEL"])
    threads = int(os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_THREADS", "4"))
    max_length = int(
        os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_MAX_LENGTH", "256")
    )
    torch.set_num_threads(threads)

    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_path,
        local_files_only=True,
    )
    model.eval()
    load_ms = round((time.perf_counter() - started) * 1000, 3)

    text = "猫前天开始呕吐，一天两次，吐未消化的猫粮。"
    encoded_offsets = tokenizer(
        text,
        return_offsets_mapping=True,
        add_special_tokens=False,
    )
    for start, end in encoded_offsets["offset_mapping"]:
        if start != end and not text[start:end]:
            raise ValueError("reranker_tokenizer_offset_mismatch")

    pairs = [
        (
            "当前陈述中的时间、起点或频率表达",
            "猫前天开始呕吐，一天两次。候选 span：前天开始",
        ),
        (
            "当前陈述中的核心目标对象 mention",
            "猫前天开始呕吐，一天两次。候选 span：呕吐",
        ),
        (
            "当前陈述中的动作承受者 mention",
            "我前天开始给它换新猫粮。候选 span：它",
        ),
    ]
    encoded = tokenizer(
        pairs,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    started = time.perf_counter()
    with torch.inference_mode():
        logits = model(**encoded).logits.view(-1).float()
    inference_ms = round((time.perf_counter() - started) * 1000, 3)
    if logits.shape[0] != len(pairs) or not bool(torch.isfinite(logits).all()):
        raise ValueError("reranker_logits_invalid")
    return {
        "device": "cpu",
        "threads": threads,
        "max_length": max_length,
        "tokenizer": type(tokenizer).__name__,
        "model": type(model).__name__,
        "load_ms": load_ms,
        "inference_ms": inference_ms,
        "pair_count": len(pairs),
        "scores": logits.tolist(),
        "quality_asserted": False,
    }


async def check_lite_llm_async() -> dict[str, Any]:
    import httpx

    base_url = os.environ["INPUT_PREPROCESSING_LITELLM_BASE_URL"].rstrip("/")
    api_key = os.environ["INPUT_PREPROCESSING_LITELLM_API_KEY"]
    service_root = base_url.removesuffix("/v1")
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=45, trust_env=False) as http:
        started = time.perf_counter()
        health = await http.get(f"{service_root}/health/readiness")
        health.raise_for_status()
        health_payload = health.json()
        health_ms = round((time.perf_counter() - started) * 1000, 3)

        started = time.perf_counter()
        chat = await http.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": "qwen-plus",
                "messages": [
                    {
                        "role": "user",
                        "content": "V11 environment smoke: reply with OK only.",
                    }
                ],
                "temperature": 0.0,
                "max_tokens": 8,
            },
        )
        chat.raise_for_status()
        chat_payload = chat.json()
        chat_ms = round((time.perf_counter() - started) * 1000, 3)

        started = time.perf_counter()
        embedding = await http.post(
            f"{base_url}/embeddings",
            headers=headers,
            json={
                "model": "text-embedding-v4",
                "input": ["V11 candidate reranking environment smoke"],
            },
        )
        embedding.raise_for_status()
        embedding_payload = embedding.json()
        embedding_ms = round((time.perf_counter() - started) * 1000, 3)

    dimension = len(embedding_payload["data"][0]["embedding"])
    content = str(chat_payload["choices"][0]["message"].get("content", ""))
    if dimension != 1024:
        raise ValueError(f"embedding_dimension_invalid:{dimension}")
    if "OK" not in content.upper():
        raise ValueError("qwen_plus_smoke_answer_invalid")
    return {
        "readiness": health_payload,
        "readiness_ms": health_ms,
        "qwen_plus_model": chat_payload.get("model", "qwen-plus"),
        "qwen_plus_latency_ms": chat_ms,
        "embedding_model": "text-embedding-v4",
        "embedding_dimension": dimension,
        "embedding_latency_ms": embedding_ms,
    }


def check_lite_llm() -> dict[str, Any]:
    return asyncio.run(check_lite_llm_async())


def check_baml() -> dict[str, Any]:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    npm_version = package.get("devDependencies", {}).get("@boundaryml/baml")
    if npm_version != "0.226.1":
        raise ValueError(f"baml_npm_version_invalid:{npm_version}")
    from vet_agent.input_preprocessing.baml_client import b as baml_client

    if not hasattr(baml_client, "ExtractV8Macro"):
        raise ValueError("baml_generated_function_missing")
    if os.environ.get("BAML_LOG", "OFF").upper() not in {"OFF", "ERROR"}:
        raise ValueError("baml_log_policy_invalid")
    result = subprocess.run(
        [
            "npx",
            "--no-install",
            "baml-cli",
            "check",
            "--from",
            str(ROOT / "baml_src"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            "baml_schema_check_failed:" + (result.stderr or result.stdout)[:600]
        )
    return {
        "npm_version": npm_version,
        "generated_function": "ExtractV8Macro",
        "schema_check": "passed",
        "log_policy": os.environ.get("BAML_LOG", "OFF").upper(),
    }


record("environment_file", check_environment_file)
record("python_and_packages", check_python_and_packages)
record("reranker_snapshot", check_reranker_snapshot)
record("networkx_offset_graph", check_networkx_offset_graph)
record(
    "reranker_cpu_inference",
    check_reranker_inference,
    skipped=SKIP_MODEL,
)
record("litellm_qwen_embedding", check_lite_llm, skipped=SKIP_LLM)
record("baml_schema_and_client", check_baml, skipped=SKIP_BAML)

failed = [item for item in checks if item["status"] == "failed"]
passed_count = sum(item["status"] == "passed" for item in checks)
skipped_count = sum(item["status"] == "skipped" for item in checks)
report: dict[str, Any] = {
    "report_version": "v11-environment-smoke-1",
    "created_at": datetime.now(UTC).isoformat(),
    "repository": str(ROOT),
    "diagnostic_only": True,
    "can_unblock_v8_phase": False,
    "summary": {
        "passed_count": passed_count,
        "failed_count": len(failed),
        "skipped_count": skipped_count,
        "status": "passed" if not failed else "failed",
    },
    "checks": checks,
    "safety_boundary": {
        "consultation_state_written": False,
        "clinical_safety_evaluator_called": False,
        "clinical_safety_opa_called": False,
        "required_context_called": False,
        "held_out_read": False,
        "dspy_used": False,
    },
    "limitations": [
        "reranker output quality is not asserted",
        "candidate view coverage is not evaluated",
        "macro and relation quality is not evaluated",
        "V8 live phase gate remains unchanged",
    ],
}

if not SKIP_ARTIFACTS:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    report_path = OUTPUT_DIR / f"v11-environment-smoke-{timestamp}.json"
    report_text = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    report_path.write_text(report_text, encoding="utf-8")
    (OUTPUT_DIR / "v11-environment-smoke-latest.json").write_text(
        report_text,
        encoding="utf-8",
    )

    freeze_lines = sorted(
        f"{distribution.metadata.get('Name', 'unknown')}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    )
    freeze_path = OUTPUT_DIR / "python-freeze.txt"
    freeze_path.write_text("\n".join(freeze_lines) + "\n", encoding="utf-8")

    model_path = Path(os.environ["INPUT_PREPROCESSING_V11_RERANKER_MODEL"])
    model_baseline = {
        "adapter": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_ADAPTER", ""),
        "repo": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_REPO", ""),
        "revision": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_REVISION", ""),
        "local_path": str(model_path),
        "weight": "model.safetensors",
        "weight_sha256": os.environ.get(
            "INPUT_PREPROCESSING_V11_RERANKER_WEIGHT_SHA256",
            "",
        ),
        "device": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_DEVICE", "cpu"),
        "batch_size": os.environ.get(
            "INPUT_PREPROCESSING_V11_RERANKER_BATCH_SIZE",
            "4",
        ),
        "max_length": os.environ.get(
            "INPUT_PREPROCESSING_V11_RERANKER_MAX_LENGTH",
            "256",
        ),
        "threads": os.environ.get("INPUT_PREPROCESSING_V11_RERANKER_THREADS", "4"),
    }
    baseline_path = OUTPUT_DIR / "model-baseline.txt"
    baseline_path.write_text(
        "\n".join(f"{key}={value}" for key, value in model_baseline.items()) + "\n",
        encoding="utf-8",
    )

    model_files = (
        "README.md",
        "config.json",
        "model.safetensors",
        "sentencepiece.bpe.model",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    )
    model_manifest = OUTPUT_DIR / "model-manifest.sha256"
    model_manifest.write_text(
        "\n".join(
            f"{sha256_file(model_path / filename)}  {model_path / filename}"
            for filename in model_files
        )
        + "\n",
        encoding="utf-8",
    )
    env_hash = hashlib.sha256((ROOT / ".env.v11.local").read_bytes()).hexdigest()
    env_manifest = OUTPUT_DIR / "env-file.sha256"
    env_manifest.write_text(f"{env_hash}  .env.v11.local\n", encoding="utf-8")

    artifact_paths = (
        freeze_path,
        baseline_path,
        model_manifest,
        env_manifest,
        report_path,
    )
    manifest_path = OUTPUT_DIR / "environment-smoke-manifest.sha256"
    manifest_path.write_text(
        "\n".join(
            f"{sha256_file(path)}  {path.relative_to(ROOT)}" for path in artifact_paths
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[PASS] v11_environment_report|{report_path.relative_to(ROOT)}")
    print(f"[PASS] v11_environment_manifest|{manifest_path.relative_to(ROOT)}")

print(
    f"[{'PASS' if not failed else 'FAIL'}] "
    "v11_environment_smoke_complete|"
    f"passed={passed_count}|failed={len(failed)}|skipped={skipped_count}"
)
raise SystemExit(1 if failed else 0)
PY
