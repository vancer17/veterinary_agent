##################################################################################################
# 文件: tests/architecture/test_static_boundaries.py
# 作用: 验证 veterinary_agent 的静态架构边界，包括导入方向、根包 facade、核心 import smoke
#       与循环依赖基线，防止分层整改过程中再次引入结构漂移。
# 边界: 复用 tools/ci/architecture_guard.py 的分析能力与配置；不重复实现 CI 指标统计，
#       不断言内部 DTO、Error、Status、TODO 壳或 __all__ 的精确实现形状。
##################################################################################################

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from tools.ci.architecture_guard import (
    ArchitectureMetrics,
    FileAnalysis,
    Finding,
    ROOT,
    check_import_rules,
    collect_metrics,
    load_config,
    run_import_smoke,
)


ARCHITECTURE_CONFIG_PATH = ROOT / "tools/ci/architecture_guard.toml"


@dataclass(frozen=True)
class ArchitectureContext:
    """架构测试上下文。"""

    config: dict[str, Any]
    source_root: Path
    test_root: Path
    metrics: ArchitectureMetrics
    analyses: list[FileAnalysis]


def _config_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    """读取配置中的必需 section。

    :param config: 已加载的架构门禁配置。
    :param name: section 名称。
    :return: section 对应的字典配置。
    :raises AssertionError: 当 section 缺失或不是字典时抛出。
    """

    value = config.get(name)
    assert isinstance(value, dict), f"architecture config section missing: {name}"
    return cast(dict[str, Any], value)


def _string_list(config: dict[str, Any], name: str) -> list[str]:
    """读取配置中的字符串列表。

    :param config: section 或规则配置。
    :param name: 配置项名称。
    :return: 字符串列表。
    :raises AssertionError: 当配置项不是字符串列表时抛出。
    """

    value = config.get(name, [])
    assert isinstance(value, list), f"architecture config value must be list: {name}"
    assert all(isinstance(item, str) for item in value), (
        f"architecture config list must only contain strings: {name}"
    )
    return cast(list[str], value)


def _int_budget(config: dict[str, Any], name: str) -> int:
    """读取整数型架构预算。

    :param config: budgets section 配置。
    :param name: 预算项名称。
    :return: 预算整数值。
    :raises AssertionError: 当预算项缺失或不是整数时抛出。
    """

    value = config.get(name)
    assert isinstance(value, int), f"architecture budget must be int: {name}"
    return value


def _import_rules(config: dict[str, Any]) -> list[dict[str, Any]]:
    """读取导入边界规则列表。

    :param config: 已加载的架构门禁配置。
    :return: 导入边界规则列表。
    :raises AssertionError: 当 import_rules 不是字典列表时抛出。
    """

    value = config.get("import_rules", [])
    assert isinstance(value, list), "architecture config import_rules must be a list"
    assert all(isinstance(item, dict) for item in value), (
        "architecture config import_rules must only contain tables"
    )
    return cast(list[dict[str, Any]], value)


def _format_findings(findings: list[Finding]) -> str:
    """格式化架构检查失败项。

    :param findings: 架构检查产生的失败项列表。
    :return: 适合 pytest 断言输出的多行文本。
    """

    if not findings:
        return "no findings"

    lines: list[str] = []
    for finding in findings:
        location = "-"
        if finding.path is not None:
            location = finding.path.relative_to(ROOT).as_posix()
            if finding.line is not None:
                location = f"{location}:{finding.line}"
        lines.append(
            f"[{finding.level}] {finding.check} {location} - {finding.message}"
        )
    return "\n".join(lines)


def _known_top_level_modules(architecture_context: ArchitectureContext) -> set[str]:
    """收集源码中的已知一级模块。

    :param architecture_context: 架构测试上下文。
    :return: 源码包下已存在的一级模块集合。
    """

    return {
        analysis.top_module
        for analysis in architecture_context.analyses
        if analysis.top_module != "__root__"
    }


@pytest.fixture(scope="module")
def architecture_context() -> ArchitectureContext:
    """构建架构测试上下文。

    :return: 已加载配置、源码分析结果与聚合指标。
    """

    config = load_config(ARCHITECTURE_CONFIG_PATH)
    paths_config = _config_section(config, "paths")
    source_root = ROOT / str(paths_config.get("source_root", "src/veterinary_agent"))
    test_root = ROOT / str(paths_config.get("test_root", "tests"))
    metrics, analyses = collect_metrics(source_root, test_root, config)
    return ArchitectureContext(
        config=config,
        source_root=source_root,
        test_root=test_root,
        metrics=metrics,
        analyses=analyses,
    )


def test_configured_import_boundaries_are_respected(
    architecture_context: ArchitectureContext,
) -> None:
    """验证源码导入边界符合架构门禁配置。

    :param architecture_context: 架构测试上下文。
    :return: None。
    """

    findings = [
        finding
        for finding in check_import_rules(
            architecture_context.analyses,
            architecture_context.config,
        )
        if finding.level == "error"
    ]

    assert not findings, _format_findings(findings)


def test_core_import_smoke_entries_are_importable(
    architecture_context: ArchitectureContext,
) -> None:
    """验证核心包入口保持可导入状态。

    :param architecture_context: 架构测试上下文。
    :return: None。
    """

    findings = [
        finding
        for finding in run_import_smoke(architecture_context.config)
        if finding.level == "error"
    ]

    assert not findings, _format_findings(findings)


def test_dependency_cycles_do_not_exceed_configured_baseline(
    architecture_context: ArchitectureContext,
) -> None:
    """验证顶层模块循环依赖不超过配置基线。

    :param architecture_context: 架构测试上下文。
    :return: None。
    """

    budgets_config = _config_section(architecture_context.config, "budgets")
    max_cycles = _int_budget(budgets_config, "max_dependency_cycles")
    cycles = architecture_context.metrics.dependency_cycles
    formatted_cycles = "\n".join(" -> ".join([*cycle, cycle[0]]) for cycle in cycles)

    assert len(cycles) <= max_cycles, formatted_cycles


def test_source_code_does_not_use_root_package_as_internal_facade(
    architecture_context: ArchitectureContext,
) -> None:
    """验证生产代码不通过根包 facade 引用内部对象。

    :param architecture_context: 架构测试上下文。
    :return: None。
    """

    budgets_config = _config_section(architecture_context.config, "budgets")
    max_direct_root_imports = _int_budget(
        budgets_config,
        "max_direct_root_imports",
    )

    assert architecture_context.metrics.direct_root_imports <= max_direct_root_imports


def test_architecture_guard_configuration_references_existing_modules(
    architecture_context: ArchitectureContext,
) -> None:
    """验证架构门禁配置没有引用不存在的一级模块。

    :param architecture_context: 架构测试上下文。
    :return: None。
    """

    known_modules = _known_top_level_modules(architecture_context)
    missing_modules: list[str] = []

    for rule in _import_rules(architecture_context.config):
        configured_modules = [
            *_string_list(rule, "from_modules"),
            *_string_list(rule, "except_modules"),
        ]
        missing_modules.extend(
            module
            for module in configured_modules
            if module != "*" and module not in known_modules
        )

    assert not missing_modules, sorted(set(missing_modules))
