"""Runtime configuration helpers shared by shadow evaluation entry points."""

from __future__ import annotations

import os

from vet_agent import Settings


def make_runtime_settings() -> Settings:
    """Build an explicit LiteLLM-only settings object for shadow runners."""

    api_key = os.getenv("INPUT_PREPROCESSING_LITELLM_API_KEY") or os.getenv(
        "LITELLM_API_KEY"
    )
    base_url = os.getenv("INPUT_PREPROCESSING_LITELLM_BASE_URL") or os.getenv(
        "LITELLM_BASE_URL"
    )
    if not api_key or not base_url:
        raise ValueError("LiteLLM API key and base URL are required for shadow mode")
    return Settings(
        litellm_api_key=api_key,
        litellm_base_url=base_url.rstrip("/"),
        request_timeout_seconds=float(
            os.getenv("INPUT_PREPROCESSING_TIMEOUT_SECONDS", "45")
        ),
        qwen_max_retries=int(os.getenv("INPUT_PREPROCESSING_MAX_RETRIES", "1")),
    )
