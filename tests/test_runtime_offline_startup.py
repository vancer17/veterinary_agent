"""
文件：tests/test_runtime_offline_startup.py
作用：验证生产镜像离线启动自检对 core 与 guardrails 变体的分流逻辑。
范围：覆盖 NLTK、Guardrails 运行时模块、LiteLLM 本地模型成本表以及导入边界检查的调用收敛。
说明：本测试不触发真实第三方模块导入，仅通过替身确认不同镜像变体的检查开关行为。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import vet_agent.runtime.offline_startup as offline_startup
from vet_agent.runtime import OfflineStartupCheckResult, run_offline_startup_checks


def _track_result(
    name: str,
    calls: list[str],
) -> Callable[..., OfflineStartupCheckResult]:
    """构造离线自检替身结果生成器。

    :param name: 检查项名称。
    :param calls: 用于记录调用顺序的列表。
    :return: 返回一个可替换原检查函数的替身。
    """

    def _result(*args: object, **kwargs: object) -> OfflineStartupCheckResult:
        """返回预置检查结果并记录调用顺序。

        :param args: 位置参数。
        :param kwargs: 关键字参数。
        :return: 返回通过状态的检查结果。
        """
        del args, kwargs
        calls.append(name)
        return OfflineStartupCheckResult(name=name, ok=True, detail=name)

    return _result


def test_offline_startup_core_variant_skips_guardrails_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 core 变体默认不检查 NLTK 与 Guardrails 运行时模块。

    :param monkeypatch: pytest 环境变量与对象替换工具。
    :return: 无返回值；断言通过表示 core 镜像不会携带 Guardrails 重依赖门槛。
    """
    calls: list[str] = []
    monkeypatch.setenv("VET_AGENT_IMAGE_VARIANT", "core")
    monkeypatch.delenv("ENABLE_INPUT_SAFETY_GUARDRAILS", raising=False)
    monkeypatch.delenv("ENABLE_OUTPUT_SAFETY_GUARDRAILS", raising=False)
    monkeypatch.setattr(offline_startup, "check_nltk_resources", _track_result("nltk_data", calls))
    monkeypatch.setattr(offline_startup, "check_guardrails_runtime", _track_result("guardrails_runtime", calls))
    monkeypatch.setattr(
        offline_startup,
        "check_litellm_local_model_cost_map",
        _track_result("litellm_model_cost_map", calls),
    )
    monkeypatch.setattr(offline_startup, "check_import_boundary", _track_result("import_boundary", calls))

    results = run_offline_startup_checks()

    assert [result.name for result in results] == ["litellm_model_cost_map", "import_boundary"]
    assert calls == ["litellm_model_cost_map", "import_boundary"]


def test_offline_startup_guardrails_variant_enables_extra_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 guardrails 变体会额外启用 NLTK 与 Guardrails 运行时检查。

    :param monkeypatch: pytest 环境变量与对象替换工具。
    :return: 无返回值；断言通过表示增强镜像会被更严格地验证。
    """
    calls: list[str] = []
    monkeypatch.setenv("VET_AGENT_IMAGE_VARIANT", "guardrails")
    monkeypatch.setenv("ENABLE_INPUT_SAFETY_GUARDRAILS", "false")
    monkeypatch.setenv("ENABLE_OUTPUT_SAFETY_GUARDRAILS", "false")
    monkeypatch.setattr(offline_startup, "check_nltk_resources", _track_result("nltk_data", calls))
    monkeypatch.setattr(offline_startup, "check_guardrails_runtime", _track_result("guardrails_runtime", calls))
    monkeypatch.setattr(
        offline_startup,
        "check_litellm_local_model_cost_map",
        _track_result("litellm_model_cost_map", calls),
    )
    monkeypatch.setattr(offline_startup, "check_import_boundary", _track_result("import_boundary", calls))

    results = run_offline_startup_checks()

    assert [result.name for result in results] == [
        "nltk_data",
        "guardrails_runtime",
        "litellm_model_cost_map",
        "import_boundary",
    ]
    assert calls == [
        "nltk_data",
        "guardrails_runtime",
        "litellm_model_cost_map",
        "import_boundary",
    ]


def test_offline_startup_guardrails_flags_force_extra_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 guardrails 开关开启时即便 core 变体也会触发额外检查。

    :param monkeypatch: pytest 环境变量与对象替换工具。
    :return: 无返回值；断言通过表示 guardrails 开关不会被 core 变体静默忽略。
    """
    calls: list[str] = []
    monkeypatch.setenv("VET_AGENT_IMAGE_VARIANT", "core")
    monkeypatch.setenv("ENABLE_OUTPUT_SAFETY_GUARDRAILS", "true")
    monkeypatch.delenv("ENABLE_INPUT_SAFETY_GUARDRAILS", raising=False)
    monkeypatch.setattr(offline_startup, "check_nltk_resources", _track_result("nltk_data", calls))
    monkeypatch.setattr(offline_startup, "check_guardrails_runtime", _track_result("guardrails_runtime", calls))
    monkeypatch.setattr(
        offline_startup,
        "check_litellm_local_model_cost_map",
        _track_result("litellm_model_cost_map", calls),
    )
    monkeypatch.setattr(offline_startup, "check_import_boundary", _track_result("import_boundary", calls))

    results = run_offline_startup_checks()

    assert [result.name for result in results] == [
        "nltk_data",
        "guardrails_runtime",
        "litellm_model_cost_map",
        "import_boundary",
    ]
    assert calls == [
        "nltk_data",
        "guardrails_runtime",
        "litellm_model_cost_map",
        "import_boundary",
    ]
