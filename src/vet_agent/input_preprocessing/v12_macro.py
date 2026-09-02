"""One-call macro adapter over V12 support-first seed views."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from .runtime_helpers import make_runtime_settings
from .v7_run_cache import V7RunCache, V7RunUnitKey, digest_value
from .v8_macro_analyzer import V8BaseStructuredClient
from .v11_contracts import V11MacroSemanticRawOutput, V11RoleMenuRecord
from .v11_macro import V11MacroExecution, compact_menu, seed_payload
from .v11_seeds import V11ClaimSeed
from .v12_contracts import V12_MACRO_PROMPT_VERSION, V12_MACRO_SCHEMA_VERSION


class V12MacroAnalyzer:
    """Run one seeded macro call; no runtime act/claim/binding model split."""

    def __init__(
        self,
        *,
        model: str = "qwen-plus",
        cache: V7RunCache | None = None,
        max_attempts: int = 2,
    ) -> None:
        from vet_agent.runtime import QwenClient

        retries = int(os.getenv("INPUT_PREPROCESSING_V10_BASE_INTERNAL_RETRIES", "0"))
        if retries not in {0, 1}:
            raise ValueError("v12_macro_internal_retry_limit_must_be_0_or_1")
        settings = replace(make_runtime_settings(), qwen_max_retries=retries)
        self.client = V8BaseStructuredClient(
            QwenClient(settings),
            internal_retry_limit=retries,
        )
        self.model = model
        self.cache = cache
        if max_attempts not in {1, 2}:
            raise ValueError("v12_macro_attempt_limit_must_be_1_or_2")
        self.max_attempts = max_attempts

    async def run(
        self,
        *,
        experiment_id: str,
        user_text: str,
        seeds: list[V11ClaimSeed],
        act_menu: list[V11RoleMenuRecord],
        turn_context: dict[str, Any],
    ) -> V11MacroExecution:
        payload = {
            "user_text": user_text,
            "turn_context": turn_context,
            "act_candidate_menu": compact_menu(act_menu),
            "claim_seed_views": [seed_payload(seed) for seed in seeds],
            "contract_rules": [
                "Each act_type occurs at most once per unit.",
                "Claims may only use supplied seed_id values and role-menu span_id values.",
                "Use null when an optional role menu is empty.",
                "Every usable supplied seed produces exactly one claim; use review_required when suspicious.",
                "Empty acts require a non-empty no_act_reason.",
                "Empty claims require coverage_gap_suspected=true and a non-empty reason.",
            ],
        }
        key = V7RunUnitKey(
            experiment_id=experiment_id,
            model=self.model,
            prompt_version=V12_MACRO_PROMPT_VERSION,
            schema_version=V12_MACRO_SCHEMA_VERSION,
            input_digest=digest_value(payload),
            turn_context_digest=digest_value(turn_context),
            adapter=self.client.adapter_name,
        )
        if self.cache is not None:
            cached = self.cache.get(
                key,
                response_model_name=V11MacroSemanticRawOutput.__name__,
            )
            if cached is not None:
                return V11MacroExecution(
                    output=V11MacroSemanticRawOutput.model_validate(cached),
                    adapter=self.client.adapter_name,
                    attempt_count=0,
                    first_attempt_status="cache_hit",
                    cache_hit=True,
                    model_call_count=0,
                )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是 V12 结构化实验器。只输出 JSON；所有证据和绑定只能引用菜单 span_id，"
                    "不得输出 quote、新增 seed 或跨 role 借用 span。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ]
        started = time.perf_counter()
        first_status = "ok"
        first_error = ""
        attempts = 0
        output: V11MacroSemanticRawOutput | None = None
        for offset in range(self.max_attempts):
            attempts += 1
            try:
                output = V11MacroSemanticRawOutput.model_validate(
                    await self.client.run_structured(
                        messages=messages,
                        response_model=V11MacroSemanticRawOutput,
                        model=self.model,
                    )
                )
                break
            except ValidationError as exc:
                first_status = "schema_invalid"
                first_error = str(exc)[:1200]
                if offset == 0:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                first_status = "dependency_failed"
                first_error = f"{type(exc).__name__}:{exc}"[:1200]
                if offset == 0:
                    continue
                break
        if output is None:
            raise RuntimeError(f"v12_macro_adapter_failed:{first_status}:{first_error}")
        if self.cache is not None:
            self.cache.put(
                key,
                response_model_name=V11MacroSemanticRawOutput.__name__,
                output=output.model_dump(mode="json"),
                attempt_count=attempts,
            )
        return V11MacroExecution(
            output=output,
            adapter=self.client.adapter_name,
            attempt_count=attempts,
            first_attempt_status=first_status,
            first_attempt_error=first_error,
            latency_ms=int((time.perf_counter() - started) * 1000),
            model_call_count=attempts * (1 + self.client.internal_retry_limit),
        )
