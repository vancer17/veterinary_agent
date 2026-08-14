"""
文件：src/vet_agent/config.py
作用：提供兽医 Agent 项目的业务实现。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ANSWER_RAG_ALLOWED_CHUNK_TYPES: tuple[str, ...] = (
    "condition_overview",
    "triage",
    "red_flags",
    "medication_direction",
    "home_advice",
)


def _bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量。

    :param name: 环境变量名称。
    :param default: 变量不存在时使用的默认值。
    :return: 返回解析后的布尔值。
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> tuple[str, ...]:
    """读取逗号分隔环境变量。

    :param name: 环境变量名称。
    :return: 返回去空白、去空项后的字符串元组。
    """
    raw = os.getenv(name, "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(values)


@dataclass(frozen=True)
class Settings:
    app_name: str = "Vet Agent"
    default_model: str = "qwen-plus"
    qwen_embedding_model: str = "text-embedding-v4"
    qwen_vision_model: str = "qwen-vl-plus"
    litellm_api_key: str | None = None
    litellm_base_url: str = "http://127.0.0.1:4000/v1"
    request_timeout_seconds: float = 30.0
    data_dir: Path = Path(".data")
    seed_dir: Path = Path("assets/seeds")
    clinical_safety_vector_min_score: float = 0.35
    followup_rag_top_k: int = 4
    followup_rag_vector_min_score: float = 0.35
    answer_rag_top_k: int = 5
    answer_rag_vector_min_score: float = 0.35
    answer_rag_allowed_chunk_types: tuple[str, ...] = DEFAULT_ANSWER_RAG_ALLOWED_CHUNK_TYPES
    answer_rag_filter_by_domain: bool = False
    database_url: str | None = None
    enable_rag_embeddings: bool = False
    task_routing_max_task_count: int = 5
    task_routing_opa_base_url: str = "http://opa:8181/v1"
    task_routing_opa_package_path: str = "vet_agent.task_routing"
    task_routing_opa_rule_name: str = "decision"
    task_routing_opa_auth_token: str | None = None
    consultation_answerability_opa_base_url: str = "http://opa:8181/v1"
    consultation_answerability_opa_package_path: str = "vet_agent.consultation_state"
    consultation_answerability_opa_rule_name: str = "decision"
    consultation_answerability_opa_auth_token: str | None = None
    enable_llm_semantic_extraction: bool = True
    semantic_extraction_min_confidence: float = 0.65
    consultation_max_followup_rounds: int = 2
    enable_mem0: bool = True
    mem0_base_url: str = "http://127.0.0.1:8001"
    mem0_api_key: str | None = None
    memory_read_session_turn_limit: int = 20
    memory_read_pet_episode_limit: int = 10
    memory_read_semantic_limit: int = 5
    memory_read_allow_semantic_degraded: bool = False
    memory_prompt_max_chars: int = 5000
    api_keys: tuple[str, ...] = ()
    require_api_auth: bool = False
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
    enable_input_safety: bool = True
    input_safety_policy_backend: str = "opa"
    input_safety_policy_always_call: bool = True
    input_safety_opa_base_url: str = "http://opa:8181/v1"
    input_safety_opa_package_path: str = "vet_agent.input_safety"
    input_safety_opa_rule_name: str = "decision"
    input_safety_opa_auth_token: str | None = None
    clinical_safety_opa_base_url: str = "http://opa:8181/v1"
    clinical_safety_opa_package_path: str = "vet_agent.clinical_safety"
    clinical_safety_opa_rule_name: str = "decision"
    clinical_safety_opa_auth_token: str | None = None
    enable_input_safety_guardrails: bool = False
    input_safety_guardrails_model: str = "openai/qwen-plus"
    input_safety_prompt_injection_threshold: float = 0.8
    oss_bucket: str = "infra-dev-file-storage"
    oss_prefix: str = ""
    oss_endpoint: str = "oss-cn-hangzhou-internal.aliyuncs.com"

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构造应用配置。

        :return: 返回完整的应用配置对象。
        """
        return cls(
            default_model=os.getenv("QWEN_MODEL", "qwen-plus"),
            qwen_embedding_model=os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
            qwen_vision_model=os.getenv("QWEN_VISION_MODEL", "qwen-vl-plus"),
            litellm_api_key=os.getenv("LITELLM_API_KEY") or os.getenv("LITELLM_MASTER_KEY"),
            litellm_base_url=os.getenv("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1").rstrip("/"),
            request_timeout_seconds=float(os.getenv("LITELLM_TIMEOUT_SECONDS", os.getenv("QWEN_TIMEOUT_SECONDS", "30"))),
            data_dir=Path(os.getenv("VET_AGENT_DATA_DIR", ".data")),
            seed_dir=Path(os.getenv("VET_AGENT_SEED_DIR", "assets/seeds")),
            clinical_safety_vector_min_score=float(os.getenv("CLINICAL_SAFETY_VECTOR_MIN_SCORE", "0.35")),
            followup_rag_top_k=int(os.getenv("FOLLOWUP_RAG_TOP_K", "4")),
            followup_rag_vector_min_score=float(os.getenv("FOLLOWUP_RAG_VECTOR_MIN_SCORE", "0.35")),
            answer_rag_top_k=int(os.getenv("ANSWER_RAG_TOP_K", "5")),
            answer_rag_vector_min_score=float(os.getenv("ANSWER_RAG_VECTOR_MIN_SCORE", "0.35")),
            answer_rag_allowed_chunk_types=(
                _csv_env("ANSWER_RAG_ALLOWED_CHUNK_TYPES") or DEFAULT_ANSWER_RAG_ALLOWED_CHUNK_TYPES
            ),
            answer_rag_filter_by_domain=_bool_env("ANSWER_RAG_FILTER_BY_DOMAIN", False),
            database_url=os.getenv("DATABASE_URL"),
            enable_rag_embeddings=_bool_env("ENABLE_RAG_EMBEDDINGS", False),
            task_routing_max_task_count=int(os.getenv("TASK_ROUTING_MAX_TASK_COUNT", "5")),
            task_routing_opa_base_url=os.getenv(
                "TASK_ROUTING_OPA_BASE_URL",
                "http://opa:8181/v1",
            ).strip().rstrip("/"),
            task_routing_opa_package_path=os.getenv(
                "TASK_ROUTING_OPA_PACKAGE_PATH",
                "vet_agent.task_routing",
            ).strip(),
            task_routing_opa_rule_name=os.getenv("TASK_ROUTING_OPA_RULE_NAME", "decision").strip(),
            task_routing_opa_auth_token=os.getenv("TASK_ROUTING_OPA_AUTH_TOKEN") or None,
            consultation_answerability_opa_base_url=os.getenv(
                "CONSULTATION_ANSWERABILITY_OPA_BASE_URL",
                "http://opa:8181/v1",
            ).strip().rstrip("/"),
            consultation_answerability_opa_package_path=os.getenv(
                "CONSULTATION_ANSWERABILITY_OPA_PACKAGE_PATH",
                "vet_agent.consultation_state",
            ).strip(),
            consultation_answerability_opa_rule_name=os.getenv(
                "CONSULTATION_ANSWERABILITY_OPA_RULE_NAME",
                "decision",
            ).strip(),
            consultation_answerability_opa_auth_token=os.getenv("CONSULTATION_ANSWERABILITY_OPA_AUTH_TOKEN")
            or None,
            enable_llm_semantic_extraction=_bool_env("ENABLE_LLM_SEMANTIC_EXTRACTION", True),
            semantic_extraction_min_confidence=float(os.getenv("SEMANTIC_EXTRACTION_MIN_CONFIDENCE", "0.65")),
            consultation_max_followup_rounds=int(os.getenv("CONSULTATION_MAX_FOLLOWUP_ROUNDS", "2")),
            enable_mem0=_bool_env("ENABLE_MEM0", True),
            mem0_base_url=os.getenv("MEM0_BASE_URL", "http://127.0.0.1:8001").rstrip("/"),
            mem0_api_key=os.getenv("MEM0_API_KEY"),
            memory_read_session_turn_limit=int(os.getenv("MEMORY_READ_SESSION_TURN_LIMIT", "20")),
            memory_read_pet_episode_limit=int(os.getenv("MEMORY_READ_PET_EPISODE_LIMIT", "10")),
            memory_read_semantic_limit=int(os.getenv("MEMORY_READ_SEMANTIC_LIMIT", "5")),
            memory_read_allow_semantic_degraded=_bool_env("MEMORY_READ_ALLOW_SEMANTIC_DEGRADED", False),
            memory_prompt_max_chars=int(os.getenv("MEMORY_PROMPT_MAX_CHARS", "5000")),
            api_keys=_csv_env("VET_AGENT_API_KEYS"),
            require_api_auth=_bool_env("REQUIRE_API_AUTH", False),
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
            enable_input_safety=_bool_env("ENABLE_INPUT_SAFETY", True),
            input_safety_policy_backend=os.getenv("INPUT_SAFETY_POLICY_BACKEND", "opa").strip().lower(),
            input_safety_policy_always_call=_bool_env("INPUT_SAFETY_POLICY_ALWAYS_CALL", True),
            input_safety_opa_base_url=os.getenv("INPUT_SAFETY_OPA_BASE_URL", "http://opa:8181/v1").strip().rstrip("/"),
            input_safety_opa_package_path=os.getenv("INPUT_SAFETY_OPA_PACKAGE_PATH", "vet_agent.input_safety").strip(),
            input_safety_opa_rule_name=os.getenv("INPUT_SAFETY_OPA_RULE_NAME", "decision").strip(),
            input_safety_opa_auth_token=os.getenv("INPUT_SAFETY_OPA_AUTH_TOKEN") or None,
            clinical_safety_opa_base_url=os.getenv(
                "CLINICAL_SAFETY_OPA_BASE_URL",
                os.getenv("INPUT_SAFETY_OPA_BASE_URL", "http://opa:8181/v1"),
            ).strip().rstrip("/"),
            clinical_safety_opa_package_path=os.getenv(
                "CLINICAL_SAFETY_OPA_PACKAGE_PATH",
                "vet_agent.clinical_safety",
            ).strip(),
            clinical_safety_opa_rule_name=os.getenv("CLINICAL_SAFETY_OPA_RULE_NAME", "decision").strip(),
            clinical_safety_opa_auth_token=(
                os.getenv("CLINICAL_SAFETY_OPA_AUTH_TOKEN")
                or os.getenv("INPUT_SAFETY_OPA_AUTH_TOKEN")
                or None
            ),
            enable_input_safety_guardrails=_bool_env("ENABLE_INPUT_SAFETY_GUARDRAILS", False),
            input_safety_guardrails_model=os.getenv("INPUT_SAFETY_GUARDRAILS_MODEL", "openai/qwen-plus").strip(),
            input_safety_prompt_injection_threshold=float(os.getenv("INPUT_SAFETY_PROMPT_INJECTION_THRESHOLD", "0.8")),
            oss_bucket=os.getenv("OSS_BUCKET", "infra-dev-file-storage").strip(),
            oss_prefix=os.getenv("OSS_PREFIX", "").strip().strip("/"),
            oss_endpoint=os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com").strip().rstrip("/"),
        )

    @property
    def litellm_configured(self) -> bool:
        """判断 LiteLLM 服务配置是否完整。

        :return: API 地址和密钥均存在时返回 True。
        """
        return bool(self.litellm_api_key and self.litellm_base_url)

    @property
    def postgres_configured(self) -> bool:
        """判断 PostgreSQL 连接是否已配置。

        :return: 数据库连接串存在时返回 True。
        """
        return bool(self.database_url)
