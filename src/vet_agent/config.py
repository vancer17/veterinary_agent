from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(values)


@dataclass(frozen=True)
class Settings:
    app_name: str = "Vet Agent"
    default_model: str = "qwen-plus"
    qwen_embedding_model: str = "text-embedding-v4"
    qwen_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    request_timeout_seconds: float = 30.0
    allow_mock_llm: bool = True
    data_dir: Path = Path(".data")
    seed_dir: Path = Path("data/seeds")
    database_url: str | None = None
    enable_rag_embeddings: bool = False
    enable_llm_task_splitter: bool = True
    enable_mem0: bool = False
    mem0_api_key: str | None = None
    enable_langgraph_runtime: bool = False
    api_keys: tuple[str, ...] = ()
    require_api_auth: bool = False
    pet_authorization_mode: str = "permissive"
    session_policy_mode: str = "permissive"
    require_auth_user_match: bool = False
    idempotency_wait_seconds: float = 10.0
    idempotency_processing_ttl_seconds: float = 300.0
    qwen_max_concurrent_requests: int = 8
    qwen_min_interval_seconds: float = 0.0
    qwen_max_retries: int = 2
    qwen_retry_base_delay_seconds: float = 0.5
    qwen_circuit_breaker_failure_threshold: int = 5
    qwen_circuit_breaker_cooldown_seconds: float = 30.0
    qwen_fallback_models: tuple[str, ...] = ()
    enable_memory_extraction: bool = True
    enable_llm_memory_extraction: bool = True
    memory_extraction_min_confidence: float = 0.72
    max_attachments: int = 8
    max_input_chars: int = 12_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            default_model=os.getenv("QWEN_MODEL", "qwen-plus"),
            qwen_embedding_model=os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
            qwen_api_key=os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY"),
            qwen_base_url=os.getenv(
                "QWEN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).rstrip("/"),
            request_timeout_seconds=float(os.getenv("QWEN_TIMEOUT_SECONDS", "30")),
            allow_mock_llm=_bool_env("ALLOW_MOCK_LLM", True),
            data_dir=Path(os.getenv("VET_AGENT_DATA_DIR", ".data")),
            seed_dir=Path(os.getenv("VET_AGENT_SEED_DIR", "data/seeds")),
            database_url=os.getenv("DATABASE_URL"),
            enable_rag_embeddings=_bool_env("ENABLE_RAG_EMBEDDINGS", False),
            enable_llm_task_splitter=_bool_env("ENABLE_LLM_TASK_SPLITTER", True),
            enable_mem0=_bool_env("ENABLE_MEM0", False),
            mem0_api_key=os.getenv("MEM0_API_KEY"),
            enable_langgraph_runtime=_bool_env("ENABLE_LANGGRAPH_RUNTIME", False),
            api_keys=_csv_env("VET_AGENT_API_KEYS"),
            require_api_auth=_bool_env("REQUIRE_API_AUTH", False),
            pet_authorization_mode=os.getenv("PET_AUTHORIZATION_MODE", "permissive").strip().lower(),
            session_policy_mode=os.getenv("SESSION_POLICY_MODE", "permissive").strip().lower(),
            require_auth_user_match=_bool_env("REQUIRE_AUTH_USER_MATCH", False),
            idempotency_wait_seconds=float(os.getenv("IDEMPOTENCY_WAIT_SECONDS", "10")),
            idempotency_processing_ttl_seconds=float(os.getenv("IDEMPOTENCY_PROCESSING_TTL_SECONDS", "300")),
            qwen_max_concurrent_requests=int(os.getenv("QWEN_MAX_CONCURRENT_REQUESTS", "8")),
            qwen_min_interval_seconds=float(os.getenv("QWEN_MIN_INTERVAL_SECONDS", "0")),
            qwen_max_retries=int(os.getenv("QWEN_MAX_RETRIES", "2")),
            qwen_retry_base_delay_seconds=float(os.getenv("QWEN_RETRY_BASE_DELAY_SECONDS", "0.5")),
            qwen_circuit_breaker_failure_threshold=int(os.getenv("QWEN_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")),
            qwen_circuit_breaker_cooldown_seconds=float(os.getenv("QWEN_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30")),
            qwen_fallback_models=_csv_env("QWEN_FALLBACK_MODELS"),
            enable_memory_extraction=_bool_env("ENABLE_MEMORY_EXTRACTION", True),
            enable_llm_memory_extraction=_bool_env("ENABLE_LLM_MEMORY_EXTRACTION", True),
            memory_extraction_min_confidence=float(os.getenv("MEMORY_EXTRACTION_MIN_CONFIDENCE", "0.72")),
            max_attachments=int(os.getenv("MAX_ATTACHMENTS", "8")),
            max_input_chars=int(os.getenv("MAX_INPUT_CHARS", "12000")),
        )

    @property
    def qwen_configured(self) -> bool:
        return bool(self.qwen_api_key)

    @property
    def postgres_configured(self) -> bool:
        return bool(self.database_url)
