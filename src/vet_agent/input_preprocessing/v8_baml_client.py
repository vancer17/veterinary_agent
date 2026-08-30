"""Project-specific BAML adapter for the V8 macro experiment.

The generated BAML client is deliberately kept behind a small adapter so the
experiment runner can preserve the stable V8 Pydantic contract while BAML owns
prompt/schema versioning and its bounded retry policy.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .v8_contracts import V8MacroSemanticRawOutput

INTERNAL_RETRY_LIMIT = 0
BAML_MODEL = "qwen-plus"


async def extract_v8_macro(
    *,
    messages: list[dict[str, Any]],
    model: str,
) -> V8MacroSemanticRawOutput:
    """Run the generated BAML function and convert it to the stable contract."""

    if model != BAML_MODEL:
        raise ValueError(f"v8_baml_model_not_configured:{model}")
    # BAML's default function log includes the complete prompt and parsed user
    # text. V8 reports must contain audited projections only, not raw prompt
    # dumps.
    os.environ.setdefault("BAML_LOG", "OFF")
    os.environ.setdefault("BAML_LOG_MAX_CHUNK_LENGTH", "0")
    try:
        from .baml_client import b
    except Exception as exc:  # pragma: no cover - generated optional module
        raise ValueError("v8_baml_client_unavailable") from exc

    messages_json = json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    raw = await b.ExtractV8Macro(messages_json=messages_json)
    payload = raw.model_dump(mode="json")
    for act in payload.get("acts", []):
        act["act_type"] = str(act["act_type"]).lower()
    for claim in payload.get("claims", []):
        claim["statement_type"] = str(claim["statement_type"]).lower()
        claim["coarse_type"] = str(claim["coarse_type"]).lower()
    return V8MacroSemanticRawOutput.model_validate(payload)
