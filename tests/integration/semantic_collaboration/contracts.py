"""
=============================================================================
文件：tests/integration/semantic_collaboration/contracts.py
作用：定义受限语义协作 DAG Pre-M11 集成测试的配置、fixture 与报告契约。
范围：覆盖显式环境配置加载、人工审核测试用例装载、测试级报告记录和
      JSON 报告落盘；不访问 LiteLLM、Temporal、PostgreSQL 或下游业务领域。
说明：本模块只属于 integration 测试层，不能被生产包引用，也不能作为生产
      fallback 或 M11 Artifact Store 替代实现。
=============================================================================
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

SemanticCasePriority = Literal["P0", "P1", "P2"]
SemanticCaseGroup = Literal[
    "engineering",
    "generation",
    "coverage_review",
    "faithfulness_review",
    "repair_plan",
    "repair",
    "negative",
    "isolation",
]


class SemanticCaseFixture(BaseModel):
    """表示一条人工审核的 Pre-M11 集成测试输入。

    :return: 无返回值；该对象只描述测试语义预期，不是生产事实契约。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str = Field(description="集测设计文档中的稳定用例标识。")
    priority: SemanticCasePriority = Field(description="用例优先级。")
    group: SemanticCaseGroup = Field(description="用例所属测试分组。")
    current_turn: str = Field(min_length=1, description="当前回合用户原文。")
    claims: tuple[str, ...] = Field(
        default=(),
        description="用于直接校准 M08 的固定 claim 集合。",
    )
    expected_intent: dict[str, bool] = Field(
        default_factory=dict,
        description="Turn Intent 固定布尔字段的期望值。",
    )
    expected_dimensions: tuple[str, ...] = Field(
        default=(),
        description="期望 M08 识别出的中文 true dimension 集合。",
    )
    expected_any_dimensions: tuple[str, ...] = Field(
        default=(),
        description="至少一个必须出现的 M08 true dimension 集合。",
    )
    forbidden_dimensions: tuple[str, ...] = Field(
        default=(),
        description="该用例禁止出现的 M08 true dimension 集合。",
    )
    expected_route: str | None = Field(
        default=None,
        description="期望 M09 输出的确定性路由。",
    )
    coverage_true_dimensions: tuple[str, ...] = Field(
        default=(),
        description="确定性构造 M08 Coverage 输入时置为 true 的维度。",
    )
    faithfulness_true_dimensions: tuple[str, ...] = Field(
        default=(),
        description="确定性构造 M08 Faithfulness 输入时置为 true 的维度。",
    )
    minimum_claim_count: int | None = Field(
        default=None,
        ge=0,
        le=8,
        description="生成语义用例最少应产出的自包含 proposition 数量。",
    )
    repair_payload: dict[str, object] | None = Field(
        default=None,
        description="真实 M10 调用时预留的测试上下文；真实模型仍需自行输出 proposal。",
    )


class SemanticFixtureFile(BaseModel):
    """表示语义协作集成测试 fixture 文件的稳定结构。

    :return: 无返回值；fixture 不读取实验 held-out，也不承载生产医学规则。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    fixture_version: str = Field(description="fixture 文件版本。")
    cases: tuple[SemanticCaseFixture, ...] = Field(
        min_length=1,
        description="人工审核集成测试用例集合。",
    )


@dataclass(frozen=True)
class ExternalSemanticTestConfig:
    """表示 Pre-M11 外部集成测试的显式运行配置。

    :return: 无返回值；密钥只保留在进程内，不写入测试报告。
    """

    litellm_base_url: str
    litellm_api_key: str
    model: str
    temporal_address: str
    temporal_namespace: str
    temporal_task_queue: str
    database_url: str
    model_timeout_seconds: float


def _required_environment(name: str) -> str:
    """读取非空外部集成测试环境变量。

    :param name: 环境变量名。
    :return: 返回去除首尾空白后的配置值。
    :raises RuntimeError: 配置缺失或为空时抛出。
    """
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"semantic integration environment variable is required: {name}")
    return value


def load_external_semantic_test_config() -> ExternalSemanticTestConfig:
    """加载并校验 Pre-M11 外部集成测试配置。

    :return: 返回可用于构造 QwenClient、Temporal Client 与仓储的配置。
    :raises RuntimeError: 任一必填配置缺失、URL 非法或模型未显式声明时抛出。
    """
    litellm_base_url = _required_environment(
        "EXTERNAL_SEMANTIC_TEST_LITELLM_BASE_URL",
    ).rstrip("/")
    litellm_api_key = _required_environment(
        "EXTERNAL_SEMANTIC_TEST_LITELLM_API_KEY",
    )
    model = _required_environment("EXTERNAL_SEMANTIC_TEST_MODEL")
    temporal_address = _required_environment(
        "EXTERNAL_SEMANTIC_TEST_TEMPORAL_ADDRESS",
    )
    temporal_namespace = _required_environment(
        "EXTERNAL_SEMANTIC_TEST_TEMPORAL_NAMESPACE",
    )
    temporal_task_queue = _required_environment(
        "EXTERNAL_SEMANTIC_TEST_TEMPORAL_TASK_QUEUE",
    )
    database_url = _required_environment(
        "EXTERNAL_SEMANTIC_TEST_DATABASE_URL",
    )
    timeout_raw = os.getenv(
        "EXTERNAL_SEMANTIC_TEST_MODEL_TIMEOUT_SECONDS",
        "60",
    ).strip()

    parsed_litellm = urlparse(litellm_base_url)
    if parsed_litellm.scheme not in {"http", "https"} or not parsed_litellm.netloc:
        raise RuntimeError("EXTERNAL_SEMANTIC_TEST_LITELLM_BASE_URL is invalid")
    if not litellm_api_key.startswith("sk-"):
        raise RuntimeError("EXTERNAL_SEMANTIC_TEST_LITELLM_API_KEY must start with sk-")
    if not temporal_address or not temporal_namespace or not temporal_task_queue:
        raise RuntimeError("Temporal integration configuration is incomplete")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("EXTERNAL_SEMANTIC_TEST_DATABASE_URL is invalid")
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as error:
        raise RuntimeError(
            "EXTERNAL_SEMANTIC_TEST_MODEL_TIMEOUT_SECONDS is invalid",
        ) from error
    if timeout_seconds <= 0:
        raise RuntimeError("EXTERNAL_SEMANTIC_TEST_MODEL_TIMEOUT_SECONDS must be positive")

    return ExternalSemanticTestConfig(
        litellm_base_url=litellm_base_url,
        litellm_api_key=litellm_api_key,
        model=model,
        temporal_address=temporal_address,
        temporal_namespace=temporal_namespace,
        temporal_task_queue=temporal_task_queue,
        database_url=database_url,
        model_timeout_seconds=timeout_seconds,
    )


def load_semantic_fixture_file(path: Path) -> SemanticFixtureFile:
    """从生产集成测试 fixture 文件装载人工审核用例。

    :param path: fixture JSON 文件路径。
    :return: 返回通过严格 schema 校验的 fixture 文件对象。
    :raises RuntimeError: 文件不存在或 JSON 结构非法时抛出。
    """
    if not path.is_file():
        raise RuntimeError(f"semantic integration fixture does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(
            payload.get("cases"),
            list,
        ):
            raise TypeError("fixture root must contain a cases array")
        tuple_fields = (
            "claims",
            "expected_dimensions",
            "expected_any_dimensions",
            "forbidden_dimensions",
            "coverage_true_dimensions",
            "faithfulness_true_dimensions",
        )
        for case in payload["cases"]:
            if not isinstance(case, dict):
                raise TypeError("fixture case must be an object")
            for field in tuple_fields:
                if isinstance(case.get(field), list):
                    case[field] = tuple(case[field])
        payload["cases"] = tuple(payload["cases"])
        return SemanticFixtureFile.model_validate(payload)
    except Exception as error:
        raise RuntimeError(
            f"semantic integration fixture is invalid: {path}",
        ) from error


def require_semantic_fixture(
    fixture_file: SemanticFixtureFile,
    case_id: str,
) -> SemanticCaseFixture:
    """按稳定 case_id 读取语义集成测试 fixture。

    :param fixture_file: 已装载的 fixture 文件对象。
    :param case_id: 集测设计文档中的用例标识。
    :return: 返回匹配的人工审核用例。
    :raises RuntimeError: case_id 缺失或重复时抛出。
    """
    matches = tuple(item for item in fixture_file.cases if item.case_id == case_id)
    if len(matches) != 1:
        raise RuntimeError(f"semantic fixture case is not unique: {case_id}")
    return matches[0]


def _git_revision() -> str:
    """读取当前仓库短 revision。

    :return: 返回当前 HEAD 的短 SHA；无法读取时返回 unknown。
    """
    try:
        return subprocess.check_output(
            ("git", "rev-parse", "--short", "HEAD"),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class SemanticIntegrationReportWriter:
    """记录 Pre-M11 集成测试结果并生成脱敏 JSON 报告。

    :return: 无返回值；报告是测试级观测产物，不是 M14 生产 trace。
    """

    def __init__(
        self,
        *,
        execution_mode: str,
        config: ExternalSemanticTestConfig,
    ) -> None:
        """初始化报告写入器。

        :param execution_mode: 当前测试执行模式。
        :param config: 已显式注入的外部依赖配置；报告只记录非敏感摘要。
        :return: 无返回值。
        """
        self.execution_mode = execution_mode
        self.run_id = uuid4().hex
        self.started_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self.environment: dict[str, object] = {
            "litellm_ready": False,
            "temporal_ready": False,
            "postgres_ready": False,
            "model": config.model,
            "temporal_namespace": config.temporal_namespace,
            "task_queue": config.temporal_task_queue,
        }
        self._records: list[dict[str, object]] = []
        self._boundary = {
            "consultation_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_retrieval_called": False,
            "clinical_safety_opa_called": False,
            "required_context_called": False,
            "long_term_memory_written": False,
            "mem0_called": False,
            "old_semantic_extractor_called": False,
            "input_preprocessing_experiment_called": False,
            "heldout_read_count": 0,
            "dspy_used": False,
            "m11_store_used": False,
            "m11_commit_performed": False,
            "artifact_reference_is_authoritative": False,
        }

    @contextmanager
    def case(
        self,
        *,
        case_id: str,
        lane: str,
        priority: SemanticCasePriority,
    ) -> Iterator[dict[str, object]]:
        """记录单个集成测试 case 的执行结果。

        :param case_id: 集测设计中的稳定用例标识。
        :param lane: 测试分层名称。
        :param priority: 用例优先级。
        :return: 无返回值；生成器上下文退出时写入 passed 或 failed。
        :raises Exception: 原样向 pytest 传播测试异常。
        """
        record: dict[str, object] = {
            "case_id": case_id,
            "lane": lane,
            "priority": priority,
            "status": "failed",
            "duration_ms": 0.0,
            "failure_code": None,
            "failure_message": None,
            "turn_snapshot_digest": None,
            "plan_id": None,
            "task_id": None,
            "skill_id": None,
            "skill_version": None,
            "prompt_hash": None,
            "proposal_digest": None,
            "requested_model": None,
            "response_model": None,
            "response_id": None,
            "finish_reason": None,
            "latency_ms": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "usage_available": None,
            "m07_state": None,
            "review_outcome": None,
            "true_dimensions": [],
            "repair_route": None,
            "repair_lane": None,
            "patch_state": None,
            "preview_claim_count": None,
            "post_patch_probe_state": None,
            "original_dimension_resolved": None,
            "new_dimension_introduced": None,
        }
        self._records.append(record)
        started_at = perf_counter()
        try:
            yield record
        except Exception as error:
            record["duration_ms"] = (perf_counter() - started_at) * 1000
            record["failure_code"] = type(error).__name__
            record["failure_message"] = str(error)[:1000]
            raise
        record["duration_ms"] = (perf_counter() - started_at) * 1000
        record["status"] = "passed"

    def last_record(self) -> dict[str, object]:
        """读取最近一条测试 case 报告记录。

        :return: 返回当前 case 的可变报告字典。
        :raises RuntimeError: 当前还没有 case 记录时抛出。
        """
        if not self._records:
            raise RuntimeError("semantic integration report has no case record")
        return self._records[-1]

    def mark_environment_ready(
        self,
        *,
        litellm: bool,
        temporal: bool,
        postgres: bool,
    ) -> None:
        """记录环境健康检查结果。

        :param litellm: LiteLLM readiness 结果。
        :param temporal: Temporal SDK 连接结果。
        :param postgres: PostgreSQL 查询结果。
        :return: 无返回值。
        """
        self.environment.update(
            {
                "litellm_ready": litellm,
                "temporal_ready": temporal,
                "postgres_ready": postgres,
            },
        )

    def finalize(
        self,
        *,
        output_directory: Path,
    ) -> Path:
        """将测试报告写入本地评估目录。

        :param output_directory: 报告输出目录。
        :return: 返回已写入的 JSON 报告路径。
        """
        self.finished_at = datetime.now(UTC)
        output_directory.mkdir(parents=True, exist_ok=True)
        timestamp = self.started_at.strftime("%Y%m%d-%H%M%S")
        output_path = output_directory / (
            f"semantic-pre-m11-{timestamp}-{self.run_id[:8]}-"
            f"{len(self._records):03d}.json"
        )
        passed = sum(record.get("status") == "passed" for record in self._records)
        failed = sum(record.get("status") == "failed" for record in self._records)
        report: dict[str, object] = {
            "report_version": "semantic-pre-m11-integration-report-v1",
            "run_id": self.run_id,
            "test_design_revision": "1.0.0",
            "code_revision": _git_revision(),
            "execution_mode": self.execution_mode,
            "environment": self.environment,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "summary": {
                "total": len(self._records),
                "passed": passed,
                "failed": failed,
                "skipped": len(self._records) - passed - failed,
            },
            "case_results": self._records,
            "safety_boundary": self._boundary,
        }
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return output_path
