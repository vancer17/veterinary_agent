"""
文件：src/vet_agent/runtime/offline_startup.py
作用：提供生产镜像离线启动前置自检能力。
范围：校验 NLTK 运行数据、Guardrails 运行时模块、LiteLLM 本地模型成本表配置，以及主应用与 worker 导入链路。
说明：本模块只执行无业务副作用的导入和资源检查，不连接数据库、模型网关、Mem0 或 OPA；core 变体默认跳过 Guardrails 重依赖检查。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Final


DEFAULT_IMPORT_MODULES: Final[tuple[str, ...]] = (
    "vet_agent.main",
    "vet_agent.background_tasks.worker",
    "vet_agent.input_safety",
    "vet_agent.output_safety",
)
DEFAULT_GUARDRAILS_IMPORT_MODULES: Final[tuple[str, ...]] = (
    "guardrails_ai.prompt_injection_detector.main",
    "guardrails_ai.detect_pii.main",
    "guardrails_ai.detect_system_prompt_leakage.main",
    "guardrails_ai.mentions_drugs.main",
    "guardrails_ai.regex_match.main",
    "guardrails_ai.restricttotopic.main",
    "guardrails_ai.secrets_present.main",
    "guardrails_ai.valid_length.main",
)
DEFAULT_NLTK_RESOURCES: Final[tuple[str, ...]] = (
    "tokenizers/punkt",
    "tokenizers/punkt_tab",
)
DEFAULT_IMAGE_VARIANT: Final[str] = "core"
GUARDRAILS_IMAGE_VARIANT: Final[str] = "guardrails"
TRUTHY_ENV_VALUES: Final[set[str]] = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OfflineStartupCheckResult:
    """表示单项离线启动检查结果。

    :param name: 检查项名称。
    :param ok: 检查是否通过。
    :param detail: 检查结果说明。
    :return: 无返回值。
    """

    name: str
    ok: bool
    detail: str


def run_offline_startup_checks(
    *,
    check_imports: bool = True,
    check_nltk: bool | None = None,
    check_guardrails: bool | None = None,
    check_litellm: bool = True,
) -> tuple[OfflineStartupCheckResult, ...]:
    """执行生产镜像离线启动检查。

    :param check_imports: 是否检查主应用和 worker 导入链路。
    :param check_nltk: 是否检查 NLTK 本地数据资源；为空时按镜像变体和 guardrails 开关自动判断。
    :param check_guardrails: 是否检查 Guardrails 运行时模块；为空时按镜像变体和 guardrails 开关自动判断。
    :param check_litellm: 是否检查 LiteLLM 本地模型成本表配置。
    :return: 返回所有检查项结果。
    """
    if check_nltk is None:
        check_nltk = _should_check_guardrails_runtime()
    if check_guardrails is None:
        check_guardrails = _should_check_guardrails_runtime()
    results: list[OfflineStartupCheckResult] = []
    if check_nltk:
        results.append(check_nltk_resources(DEFAULT_NLTK_RESOURCES))
    if check_guardrails:
        results.append(check_guardrails_runtime(DEFAULT_GUARDRAILS_IMPORT_MODULES))
    if check_litellm:
        results.append(check_litellm_local_model_cost_map())
    if check_imports:
        results.append(check_import_boundary(DEFAULT_IMPORT_MODULES))
    return tuple(results)


def check_nltk_resources(resources: tuple[str, ...]) -> OfflineStartupCheckResult:
    """检查 NLTK 本地资源是否可加载。

    :param resources: 需要通过 nltk.data.find 定位的资源路径。
    :return: 返回 NLTK 资源检查结果。
    """
    data_paths = _nltk_data_paths()
    if not data_paths:
        return OfflineStartupCheckResult(
            name="nltk_data",
            ok=False,
            detail="NLTK_DATA 未配置，生产容器不得在启动阶段依赖默认用户目录。",
        )
    missing_paths = [str(path) for path in data_paths if not path.exists()]
    if missing_paths:
        return OfflineStartupCheckResult(
            name="nltk_data",
            ok=False,
            detail=f"NLTK_DATA 指向的目录不存在: {', '.join(missing_paths)}",
        )

    try:
        from nltk import data as nltk_data
        from nltk.tokenize import sent_tokenize

        # 显式定位 punkt 与 punkt_tab，避免只存在空目录时误判通过。
        for resource in resources:
            nltk_data.find(resource)
        sent_tokenize("The cat vomited twice. The owner called the veterinarian.")
    except Exception as exc:
        return OfflineStartupCheckResult(
            name="nltk_data",
            ok=False,
            detail=f"NLTK 本地 tokenizer 资源不可用: {exc}",
        )
    return OfflineStartupCheckResult(
        name="nltk_data",
        ok=True,
        detail=f"NLTK 本地 tokenizer 资源可用: {', '.join(str(path) for path in data_paths)}",
    )


def check_litellm_local_model_cost_map() -> OfflineStartupCheckResult:
    """检查 LiteLLM 是否使用本地模型成本表。

    :return: 返回 LiteLLM 离线配置检查结果。
    """
    if not _bool_env("LITELLM_LOCAL_MODEL_COST_MAP"):
        return OfflineStartupCheckResult(
            name="litellm_model_cost_map",
            ok=False,
            detail="LITELLM_LOCAL_MODEL_COST_MAP 未启用，生产容器可能在导入阶段访问 GitHub Raw。",
        )
    try:
        import_module("litellm")
    except Exception as exc:
        return OfflineStartupCheckResult(
            name="litellm_model_cost_map",
            ok=False,
            detail=f"LiteLLM 在本地成本表模式下仍无法导入: {exc}",
        )
    return OfflineStartupCheckResult(
        name="litellm_model_cost_map",
        ok=True,
        detail="LiteLLM 本地模型成本表模式已启用并可导入。",
    )


def check_guardrails_runtime(modules: tuple[str, ...]) -> OfflineStartupCheckResult:
    """检查 Guardrails 运行时模块是否可导入。

    :param modules: 需要在离线环境中导入的 Guardrails 模块路径。
    :return: 返回 Guardrails 运行时检查结果。
    """
    imported: list[str] = []
    try:
        for module_name in modules:
            import_module(module_name)
            imported.append(module_name)
    except Exception as exc:
        return OfflineStartupCheckResult(
            name="guardrails_runtime",
            ok=False,
            detail=f"Guardrails 运行模块离线导入失败: {exc}",
        )
    return OfflineStartupCheckResult(
        name="guardrails_runtime",
        ok=True,
        detail=f"Guardrails 运行模块离线导入通过: {', '.join(imported)}",
    )


def check_import_boundary(modules: tuple[str, ...]) -> OfflineStartupCheckResult:
    """检查主运行入口导入阶段是否存在明显副作用。

    :param modules: 需要在离线环境中导入的模块路径。
    :return: 返回导入链路检查结果。
    """
    imported: list[str] = []
    try:
        for module_name in modules:
            import_module(module_name)
            imported.append(module_name)
    except Exception as exc:
        return OfflineStartupCheckResult(
            name="import_boundary",
            ok=False,
            detail=f"模块离线导入失败: {exc}",
        )
    return OfflineStartupCheckResult(
        name="import_boundary",
        ok=True,
        detail=f"模块离线导入通过: {', '.join(imported)}",
    )


def _nltk_data_paths() -> tuple[Path, ...]:
    """读取 NLTK_DATA 指定的本地数据目录。

    :return: 返回 NLTK 数据目录元组。
    """
    raw_value = os.getenv("NLTK_DATA", "")
    return tuple(Path(item.strip()) for item in raw_value.split(os.pathsep) if item.strip())


def _image_variant() -> str:
    """读取当前镜像变体。

    :return: 返回镜像变体名称。
    """
    return os.getenv("VET_AGENT_IMAGE_VARIANT", DEFAULT_IMAGE_VARIANT).strip().lower() or DEFAULT_IMAGE_VARIANT


def _guardrails_enabled() -> bool:
    """判断当前运行时是否启用了 Guardrails 相关功能。

    :return: 任何 Guardrails 开关启用时返回 True。
    """
    return _bool_env("ENABLE_INPUT_SAFETY_GUARDRAILS") or _bool_env("ENABLE_OUTPUT_SAFETY_GUARDRAILS")


def _should_check_guardrails_runtime() -> bool:
    """判断是否需要在离线启动阶段验证 Guardrails 运行时。

    :return: guardrails 变体或 guardrails 开关启用时返回 True。
    """
    return _image_variant() == GUARDRAILS_IMAGE_VARIANT or _guardrails_enabled()


def _bool_env(name: str) -> bool:
    """读取布尔环境变量。

    :param name: 环境变量名称。
    :return: 环境变量为启用值时返回 True。
    """
    return os.getenv(name, "").strip().lower() in TRUTHY_ENV_VALUES


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析离线启动检查命令行参数。

    :param argv: 命令行参数序列；为空时由 argparse 读取 sys.argv。
    :return: 返回解析后的命令行参数对象。
    """
    parser = argparse.ArgumentParser(description="执行兽医 Agent 生产镜像离线启动检查。")
    parser.add_argument("--skip-imports", action="store_true", help="跳过主应用和 worker 导入链路检查。")
    parser.add_argument("--skip-nltk", action="store_true", help="跳过 NLTK 本地数据检查。")
    parser.add_argument("--skip-guardrails", action="store_true", help="跳过 Guardrails 运行时模块检查。")
    parser.add_argument("--skip-litellm", action="store_true", help="跳过 LiteLLM 本地模型成本表检查。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """执行离线启动检查命令行入口。

    :param argv: 命令行参数序列；为空时由 argparse 读取 sys.argv。
    :return: 检查全部通过时返回 0，否则返回 1。
    """
    args = _parse_args(argv)
    results = run_offline_startup_checks(
        check_imports=not args.skip_imports,
        check_nltk=False if args.skip_nltk else None,
        check_guardrails=False if args.skip_guardrails else None,
        check_litellm=not args.skip_litellm,
    )
    failed = False
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}", file=sys.stderr)
        failed = failed or not result.ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
