# =============================================================================
# Architecture Guard — veterinary_agent 架构压制门禁
# =============================================================================
#
# 【文件信息】
#   路径：tools/ci/architecture_guard.py
#   类型：CI 工具脚本
#
# 【作用】
#   读取 tools/ci/architecture_guard.toml，统计代码结构指标并执行架构门禁：
#   代码体积预算、公共导出预算、DTO/模型转换预算、导入边界、循环依赖、
#   import smoke、CI 文件头注释检查等。脚本只依赖 Python 标准库，便于在
#   本地与 GitHub Actions 中复用。
#
# 【维护原则】
#   1. 本脚本只检查工程结构，不承载业务规则。
#   2. 新增检查应优先配置化，避免把项目策略散落在 workflow YAML 中。
#   3. 阈值应随分层整改逐步收紧，让 CI 持续压住重复设计和过度分层。
#
# =============================================================================

from __future__ import annotations

import argparse
import ast
import importlib
import os
import sys
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ROOT = Path(__file__).resolve().parents[2]
PROJECT_PACKAGE = "veterinary_agent"
FINDING_LEVELS = ("error", "warning")

FindingLevel = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    """CI finding emitted by an architecture check."""

    level: FindingLevel
    check: str
    message: str
    path: Path | None = None
    line: int | None = None


@dataclass
class FileAnalysis:
    """Static analysis facts collected from one Python file."""

    path: Path
    top_module: str
    module_path: str
    physical_lines: int
    imports: set[str] = field(default_factory=set)
    direct_root_import_lines: list[int] = field(default_factory=list)
    class_names: list[str] = field(default_factory=list)
    call_counts: Counter[str] = field(default_factory=Counter)
    all_export_count: int = 0
    syntax_error: str | None = None


@dataclass
class ArchitectureMetrics:
    """Aggregated project metrics used by CI summaries and budgets."""

    source_files: int
    test_files: int
    source_physical_lines: int
    test_physical_lines: int
    module_physical_lines: Counter[str]
    module_file_counts: Counter[str]
    total_all_exports: int
    root_all_exports: int
    root_init_physical_lines: int
    model_dump_calls: int
    model_validate_calls: int
    dto_classes: int
    error_status_todo_classes: int
    template_files: int
    direct_root_imports: int
    dependency_cycles: list[tuple[str, ...]]


def parse_args() -> argparse.Namespace:
    """解析架构门禁命令行参数。

    :return: 包含配置路径与可选摘要路径的命令行参数命名空间。
    """

    parser = argparse.ArgumentParser(
        description="Run veterinary_agent architecture fitness checks."
    )
    parser.add_argument(
        "--config",
        default="tools/ci/architecture_guard.toml",
        help="Path to the architecture guard TOML config.",
    )
    parser.add_argument(
        "--summary",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="Optional Markdown summary output path.",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> dict[str, Any]:
    """加载架构门禁 TOML 配置。

    :param config_path: 架构门禁配置文件路径。
    :return: 已解析的 TOML 配置字典。
    """

    with config_path.open("rb") as file:
        return tomllib.load(file)


def rel(path: Path) -> str:
    """将路径转换为相对项目根目录的展示文本。

    :param path: 需要格式化的文件系统路径。
    :return: 优先相对项目根目录、否则保持原始形式的 POSIX 路径。
    """

    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def iter_python_files(root: Path) -> list[Path]:
    """列出目录下参与架构分析的 Python 文件。

    :param root: 需要递归扫描的根目录。
    :return: 已排除缓存与虚拟环境目录并按路径排序的文件列表。
    """

    if not root.exists():
        return []
    ignored_parts = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
    return sorted(
        path
        for path in root.rglob("*.py")
        if not ignored_parts.intersection(path.relative_to(root).parts)
    )


def count_physical_lines(path: Path) -> int:
    """统计文件物理行数。

    :param path: 需要统计的 UTF-8 文本文件路径。
    :return: 文件按行拆分后的物理行数。
    """

    return len(path.read_text(encoding="utf-8").splitlines())


def top_module_for(path: Path, source_root: Path) -> str:
    """解析源码文件所属的一级模块。

    :param path: 待分析的源码文件路径。
    :param source_root: veterinary_agent 源码包根目录。
    :return: 一级模块名；根包文件返回 ``__root__``。
    """

    relative_path = path.relative_to(source_root)
    if len(relative_path.parts) == 1:
        return "__root__"
    return relative_path.parts[0]


def module_path_for(path: Path, source_root: Path) -> str:
    """解析源码文件对应的完整 Python 模块路径。

    :param path: 待分析的源码文件路径。
    :param source_root: veterinary_agent 源码包根目录。
    :return: 以 veterinary_agent 开头的完整模块路径。
    """

    relative_path = path.relative_to(source_root).with_suffix("")
    parts = list(relative_path.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return PROJECT_PACKAGE
    return ".".join([PROJECT_PACKAGE, *parts])


def resolve_import_from(
    node: ast.ImportFrom,
    *,
    current_module: str,
    is_package_init: bool,
) -> str | None:
    """解析 ``from`` 导入节点指向的绝对模块路径。

    :param node: AST ``ImportFrom`` 节点。
    :param current_module: 当前文件对应的完整模块路径。
    :param is_package_init: 当前文件是否为包初始化文件。
    :return: 解析后的绝对模块路径；无法确定时返回 None。
    """

    if node.level == 0:
        return node.module

    base_parts = current_module.split(".")
    if not is_package_init:
        base_parts = base_parts[:-1]

    trim = max(node.level - 1, 0)
    if trim:
        base_parts = base_parts[:-trim]

    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def count_string_constants(node: ast.AST) -> int:
    """递归统计 AST 表达式中的字符串常量数量。

    :param node: 需要遍历的 AST 表达式节点。
    :return: 节点及其受支持子节点中的字符串常量数量。
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return 1
    if isinstance(node, ast.Starred):
        return count_string_constants(node.value)
    if isinstance(node, ast.BinOp):
        return count_string_constants(node.left) + count_string_constants(node.right)
    if isinstance(node, ast.Dict):
        return sum(count_string_constants(value) for value in node.values)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return sum(count_string_constants(element) for element in node.elts)
    return 0


def is_all_assignment(node: ast.Assign | ast.AnnAssign) -> bool:
    """判断赋值节点是否写入模块 ``__all__``。

    :param node: 普通赋值或带类型的赋值节点。
    :return: 若任一赋值目标为 ``__all__`` 则返回 True。
    """

    if isinstance(node, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    return isinstance(node.target, ast.Name) and node.target.id == "__all__"


def analyze_python_file(path: Path, source_root: Path) -> FileAnalysis:
    """收集单个源码文件的静态架构事实。

    :param path: 待分析的 Python 源码文件路径。
    :param source_root: veterinary_agent 源码包根目录。
    :return: 文件导入、类名、调用计数与导出数量等分析结果。
    """

    text = path.read_text(encoding="utf-8")
    module_path = module_path_for(path, source_root)
    analysis = FileAnalysis(
        path=path,
        top_module=top_module_for(path, source_root),
        module_path=module_path,
        physical_lines=len(text.splitlines()),
    )

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        analysis.syntax_error = f"{exc.msg} at line {exc.lineno or 0}"
        return analysis

    is_package_init = path.name == "__init__.py"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                analysis.imports.add(alias.name)
                if alias.name == PROJECT_PACKAGE:
                    analysis.direct_root_import_lines.append(node.lineno)
            continue

        if isinstance(node, ast.ImportFrom):
            module = resolve_import_from(
                node,
                current_module=module_path,
                is_package_init=is_package_init,
            )
            if module:
                analysis.imports.add(module)
                if module == PROJECT_PACKAGE and node.level == 0:
                    analysis.direct_root_import_lines.append(node.lineno)
            continue

        if isinstance(node, ast.ClassDef):
            analysis.class_names.append(node.name)
            continue

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            analysis.call_counts[node.func.attr] += 1
            continue

        if isinstance(node, ast.Assign | ast.AnnAssign) and is_all_assignment(node):
            assigned_value = node.value
            if assigned_value is not None:
                analysis.all_export_count += count_string_constants(assigned_value)

    return analysis


def internal_target_top(import_name: str, known_modules: set[str]) -> str | None:
    """解析内部导入指向的已知一级模块。

    :param import_name: 已解析的绝对导入模块名。
    :param known_modules: 当前源码包内已知一级模块集合。
    :return: 匹配的一级模块名；外部或未知模块返回 None。
    """

    if not import_name.startswith(f"{PROJECT_PACKAGE}."):
        return None
    parts = import_name.split(".")
    if len(parts) < 2:
        return None
    target = parts[1]
    if target in known_modules:
        return target
    return None


def build_dependency_graph(
    analyses: list[FileAnalysis],
    known_modules: set[str],
) -> dict[str, set[str]]:
    """根据源码导入构建一级模块依赖图。

    :param analyses: 全部源码文件的静态分析结果。
    :param known_modules: 当前源码包内已知一级模块集合。
    :return: 从一级模块到其内部依赖模块集合的邻接表。
    """

    graph: dict[str, set[str]] = defaultdict(set)
    for analysis in analyses:
        if analysis.top_module == "__root__":
            continue
        graph.setdefault(analysis.top_module, set())
        for import_name in analysis.imports:
            target = internal_target_top(import_name, known_modules)
            if target and target != analysis.top_module:
                graph[analysis.top_module].add(target)
    return dict(graph)


def canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """将依赖环旋转为稳定的规范表示。

    :param cycle: 首尾节点重复的依赖环路径。
    :return: 从字典序最小旋转位置开始且不重复尾节点的元组。
    """

    body = cycle[:-1]
    rotations = [tuple(body[index:] + body[:index]) for index in range(len(body))]
    return min(rotations)


def find_dependency_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """查找一级模块依赖图中的全部稳定依赖环。

    :param graph: 一级模块依赖邻接表。
    :return: 已去重并排序的规范依赖环列表。
    """

    cycles: set[tuple[str, ...]] = set()
    visited: set[str] = set()
    active: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
        """深度优先访问一个模块并记录回边形成的依赖环。

        :param node: 当前访问的一级模块名。
        :return: None。
        """

        visited.add(node)
        active.add(node)
        stack.append(node)

        for target in sorted(graph.get(node, set())):
            if target not in visited:
                visit(target)
            elif target in active:
                index = stack.index(target)
                cycles.add(canonical_cycle([*stack[index:], target]))

        stack.pop()
        active.remove(node)

    for node in sorted(graph):
        if node not in visited:
            visit(node)

    return sorted(cycles)


def collect_metrics(
    source_root: Path,
    test_root: Path,
    config: dict[str, Any],
) -> tuple[ArchitectureMetrics, list[FileAnalysis]]:
    """收集源码、测试和依赖图的聚合架构指标。

    :param source_root: veterinary_agent 源码包根目录。
    :param test_root: 项目测试根目录。
    :param config: 已加载的架构门禁配置。
    :return: 聚合指标与逐文件静态分析结果。
    """

    source_files = iter_python_files(source_root)
    test_files = iter_python_files(test_root)
    analyses = [analyze_python_file(path, source_root) for path in source_files]

    module_lines: Counter[str] = Counter()
    module_files: Counter[str] = Counter()
    for analysis in analyses:
        module_lines[analysis.top_module] += analysis.physical_lines
        module_files[analysis.top_module] += 1

    root_init = source_root / "__init__.py"
    root_analysis = next((item for item in analyses if item.path == root_init), None)
    class_patterns = config.get("class_patterns", {})
    dto_suffixes = tuple(class_patterns.get("dto_suffixes", []))
    error_suffixes = tuple(class_patterns.get("error_status_todo_suffixes", []))
    template_names = set(config.get("template_files", {}).get("names", []))
    known_modules = {
        analysis.top_module
        for analysis in analyses
        if analysis.top_module != "__root__"
    }
    graph = build_dependency_graph(analyses, known_modules)

    metrics = ArchitectureMetrics(
        source_files=len(source_files),
        test_files=len(test_files),
        source_physical_lines=sum(item.physical_lines for item in analyses),
        test_physical_lines=sum(count_physical_lines(path) for path in test_files),
        module_physical_lines=module_lines,
        module_file_counts=module_files,
        total_all_exports=sum(item.all_export_count for item in analyses),
        root_all_exports=root_analysis.all_export_count if root_analysis else 0,
        root_init_physical_lines=count_physical_lines(root_init)
        if root_init.exists()
        else 0,
        model_dump_calls=sum(item.call_counts["model_dump"] for item in analyses),
        model_validate_calls=sum(
            item.call_counts["model_validate"] for item in analyses
        ),
        dto_classes=sum(
            1
            for item in analyses
            for class_name in item.class_names
            if dto_suffixes and class_name.endswith(dto_suffixes)
        ),
        error_status_todo_classes=sum(
            1
            for item in analyses
            for class_name in item.class_names
            if error_suffixes and class_name.endswith(error_suffixes)
        ),
        template_files=sum(1 for path in source_files if path.name in template_names),
        direct_root_imports=sum(
            len(item.direct_root_import_lines) for item in analyses
        ),
        dependency_cycles=find_dependency_cycles(graph),
    )
    return metrics, analyses


def add_limit_finding(
    findings: list[Finding],
    *,
    check: str,
    label: str,
    value: int,
    limit: int,
) -> None:
    """在指标超过预算时追加错误 finding。

    :param findings: 当前架构检查结果列表。
    :param check: 稳定检查项名称。
    :param label: 面向摘要展示的指标名称。
    :param value: 当前指标值。
    :param limit: 配置允许的最大值。
    :return: None。
    """

    if value > limit:
        findings.append(
            Finding(
                level="error",
                check=check,
                message=f"{label} is {value}, above budget {limit}.",
            )
        )


def check_budgets(
    metrics: ArchitectureMetrics,
    analyses: list[FileAnalysis],
    config: dict[str, Any],
) -> list[Finding]:
    """检查聚合指标、文件大小与模块大小预算。

    :param metrics: 已收集的聚合架构指标。
    :param analyses: 全部源码文件的静态分析结果。
    :param config: 已加载的架构门禁配置。
    :return: 所有预算错误和大文件警告列表。
    """

    findings: list[Finding] = []
    budgets = config.get("budgets", {})

    simple_limits = {
        "max_source_physical_lines": (
            "source_physical_lines",
            metrics.source_physical_lines,
            "Production physical lines",
        ),
        "max_test_physical_lines": (
            "test_physical_lines",
            metrics.test_physical_lines,
            "Test physical lines",
        ),
        "max_source_python_files": (
            "source_python_files",
            metrics.source_files,
            "Production Python files",
        ),
        "max_test_python_files": (
            "test_python_files",
            metrics.test_files,
            "Test Python files",
        ),
        "max_root_init_physical_lines": (
            "root_init_physical_lines",
            metrics.root_init_physical_lines,
            "Root __init__.py physical lines",
        ),
        "max_total_all_exports": (
            "total_all_exports",
            metrics.total_all_exports,
            "Total __all__ exports",
        ),
        "max_root_all_exports": (
            "root_all_exports",
            metrics.root_all_exports,
            "Root __all__ exports",
        ),
        "max_model_dump_calls": (
            "model_dump_calls",
            metrics.model_dump_calls,
            "Production model_dump calls",
        ),
        "max_model_validate_calls": (
            "model_validate_calls",
            metrics.model_validate_calls,
            "Production model_validate calls",
        ),
        "max_dto_classes": (
            "dto_classes",
            metrics.dto_classes,
            "DTO-like classes",
        ),
        "max_error_status_todo_classes": (
            "error_status_todo_classes",
            metrics.error_status_todo_classes,
            "Error/Status/TODO-like classes",
        ),
        "max_template_files": (
            "template_files",
            metrics.template_files,
            "Template-style files",
        ),
        "max_dependency_cycles": (
            "dependency_cycles",
            len(metrics.dependency_cycles),
            "Top-level dependency cycles",
        ),
        "max_direct_root_imports": (
            "direct_root_imports",
            metrics.direct_root_imports,
            "Direct imports from veterinary_agent root package",
        ),
    }

    for budget_key, (check, value, label) in simple_limits.items():
        if budget_key in budgets:
            add_limit_finding(
                findings,
                check=check,
                label=label,
                value=value,
                limit=int(budgets[budget_key]),
            )

    warn_file_lines = int(budgets.get("warn_file_physical_lines", 0))
    max_file_lines = int(budgets.get("max_file_physical_lines", 0))
    for analysis in analyses:
        if max_file_lines and analysis.physical_lines > max_file_lines:
            findings.append(
                Finding(
                    level="error",
                    check="file_size",
                    message=(
                        f"File has {analysis.physical_lines} physical lines, "
                        f"above budget {max_file_lines}."
                    ),
                    path=analysis.path,
                    line=1,
                )
            )
        elif warn_file_lines and analysis.physical_lines > warn_file_lines:
            findings.append(
                Finding(
                    level="warning",
                    check="file_size",
                    message=(
                        f"File has {analysis.physical_lines} physical lines; "
                        f"consider splitting or shrinking it during refactor."
                    ),
                    path=analysis.path,
                    line=1,
                )
            )

    for analysis in analyses:
        if analysis.syntax_error:
            findings.append(
                Finding(
                    level="error",
                    check="syntax",
                    message=analysis.syntax_error,
                    path=analysis.path,
                    line=1,
                )
            )

    module_budgets = config.get("module_budgets", {})
    for module_name, limit in module_budgets.items():
        value = metrics.module_physical_lines[module_name]
        if value > int(limit):
            findings.append(
                Finding(
                    level="error",
                    check="module_size",
                    message=(
                        f"Module {module_name} has {value} physical lines, "
                        f"above budget {limit}."
                    ),
                )
            )

    return findings


def import_matches(import_name: str, blocked_import: str) -> bool:
    """判断导入名是否命中被禁止的模块前缀。

    :param import_name: 源码中解析得到的绝对导入名。
    :param blocked_import: 规则配置中的禁止模块名。
    :return: 完全匹配或属于禁止模块子模块时返回 True。
    """

    return import_name == blocked_import or import_name.startswith(f"{blocked_import}.")


def check_import_rules(
    analyses: list[FileAnalysis],
    config: dict[str, Any],
) -> list[Finding]:
    """检查配置声明的跨模块导入边界与根门面导入。

    :param analyses: 全部源码文件的静态分析结果。
    :param config: 已加载的架构门禁配置。
    :return: 所有违反导入边界的 finding 列表。
    """

    findings: list[Finding] = []
    rules = config.get("import_rules", [])

    for rule in rules:
        from_modules = set(rule.get("from_modules", []))
        except_modules = set(rule.get("except_modules", []))
        blocked_imports = tuple(rule.get("blocked_imports", []))
        severity = rule.get("severity", "error")
        if severity not in FINDING_LEVELS:
            severity = "error"

        for analysis in analyses:
            if analysis.top_module == "__root__":
                continue
            if analysis.top_module in except_modules:
                continue
            if "*" not in from_modules and analysis.top_module not in from_modules:
                continue

            for import_name in sorted(analysis.imports):
                if any(import_matches(import_name, item) for item in blocked_imports):
                    findings.append(
                        Finding(
                            level=severity,
                            check="import_boundary",
                            message=(
                                f"{rule.get('name', 'Import rule')} violated: "
                                f"{analysis.top_module} imports {import_name}."
                            ),
                            path=analysis.path,
                            line=1,
                        )
                    )

    for analysis in analyses:
        for line_number in analysis.direct_root_import_lines:
            findings.append(
                Finding(
                    level="error",
                    check="root_import",
                    message=(
                        "Source files must not import internal objects from "
                        "the veterinary_agent root package; import from the "
                        "owning module instead."
                    ),
                    path=analysis.path,
                    line=line_number,
                )
            )

    return findings


def check_header_comments(config: dict[str, Any]) -> list[Finding]:
    """检查 CI 管理文件是否具有规定的顶部注释块。

    :param config: 已加载的架构门禁配置。
    :return: 文件缺失或顶部注释不足的 finding 列表。
    """

    findings: list[Finding] = []
    header_config = config.get("header_comments", {})
    min_comment_lines = int(header_config.get("min_comment_lines", 1))

    for file_name in header_config.get("required_files", []):
        path = ROOT / file_name
        if not path.exists():
            findings.append(
                Finding(
                    level="error",
                    check="file_header",
                    message="Required CI-owned file is missing.",
                    path=path,
                    line=1,
                )
            )
            continue

        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or not lines[0].lstrip().startswith("#"):
            findings.append(
                Finding(
                    level="error",
                    check="file_header",
                    message="File must start with a comment block.",
                    path=path,
                    line=1,
                )
            )
            continue

        comment_lines = 0
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                comment_lines += 1
                continue
            if stripped == "":
                continue
            break

        if comment_lines < min_comment_lines:
            findings.append(
                Finding(
                    level="error",
                    check="file_header",
                    message=(
                        f"Top comment block has {comment_lines} comment lines, "
                        f"below required {min_comment_lines}."
                    ),
                    path=path,
                    line=1,
                )
            )

    return findings


def run_import_smoke(config: dict[str, Any]) -> list[Finding]:
    """导入配置声明的核心模块并收集失败结果。

    :param config: 已加载的架构门禁配置。
    :return: 核心模块导入失败的 finding 列表。
    """

    smoke_config = config.get("import_smoke", {})
    if not smoke_config.get("enabled", False):
        return []

    findings: list[Finding] = []
    for module_name in smoke_config.get("modules", []):
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - CI smoke must report any failure.
            findings.append(
                Finding(
                    level="error",
                    check="import_smoke",
                    message=(
                        f"Cannot import {module_name}: {type(exc).__name__}: {exc}"
                    ),
                )
            )

    return findings


def github_escape(value: str) -> str:
    """转义 GitHub Actions workflow command 特殊字符。

    :param value: 需要写入 workflow command 的原始文本。
    :return: 已转义百分号、回车与换行的文本。
    """

    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_github_annotations(findings: list[Finding]) -> None:
    """将架构检查结果输出为 GitHub Actions annotation。

    :param findings: 需要输出的架构检查结果列表。
    :return: None。
    """

    for finding in findings:
        if finding.level not in FINDING_LEVELS:
            continue
        command = finding.level
        metadata = []
        if finding.path:
            metadata.append(f"file={github_escape(rel(finding.path))}")
        if finding.line:
            metadata.append(f"line={finding.line}")
        metadata_text = f" {','.join(metadata)}" if metadata else ""
        print(
            f"::{command}{metadata_text}::"
            f"{github_escape(f'[{finding.check}] {finding.message}')}"
        )


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """构建简单的 Markdown 表格。

    :param headers: 表头单元格文本列表。
    :param rows: 按行组织的表格单元格文本。
    :return: 可直接写入摘要的 Markdown 表格文本。
    """

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def metric_rows(
    metrics: ArchitectureMetrics, config: dict[str, Any]
) -> list[list[str]]:
    """构建核心指标摘要表的数据行。

    :param metrics: 已收集的聚合架构指标。
    :param config: 已加载的架构门禁配置。
    :return: 指标名称、当前值与预算组成的表格行。
    """

    budgets = config.get("budgets", {})
    rows = [
        [
            "Production physical lines",
            str(metrics.source_physical_lines),
            str(budgets.get("max_source_physical_lines", "-")),
        ],
        [
            "Test physical lines",
            str(metrics.test_physical_lines),
            str(budgets.get("max_test_physical_lines", "-")),
        ],
        [
            "Production Python files",
            str(metrics.source_files),
            str(budgets.get("max_source_python_files", "-")),
        ],
        [
            "Test Python files",
            str(metrics.test_files),
            str(budgets.get("max_test_python_files", "-")),
        ],
        [
            "Root __init__.py lines",
            str(metrics.root_init_physical_lines),
            str(budgets.get("max_root_init_physical_lines", "-")),
        ],
        [
            "Total __all__ exports",
            str(metrics.total_all_exports),
            str(budgets.get("max_total_all_exports", "-")),
        ],
        [
            "Root __all__ exports",
            str(metrics.root_all_exports),
            str(budgets.get("max_root_all_exports", "-")),
        ],
        [
            "model_dump calls",
            str(metrics.model_dump_calls),
            str(budgets.get("max_model_dump_calls", "-")),
        ],
        [
            "model_validate calls",
            str(metrics.model_validate_calls),
            str(budgets.get("max_model_validate_calls", "-")),
        ],
        [
            "DTO-like classes",
            str(metrics.dto_classes),
            str(budgets.get("max_dto_classes", "-")),
        ],
        [
            "Error/Status/TODO-like classes",
            str(metrics.error_status_todo_classes),
            str(budgets.get("max_error_status_todo_classes", "-")),
        ],
        [
            "Template-style files",
            str(metrics.template_files),
            str(budgets.get("max_template_files", "-")),
        ],
        [
            "Dependency cycles",
            str(len(metrics.dependency_cycles)),
            str(budgets.get("max_dependency_cycles", "-")),
        ],
        [
            "Direct root imports",
            str(metrics.direct_root_imports),
            str(budgets.get("max_direct_root_imports", "-")),
        ],
    ]
    return rows


def module_rows(
    metrics: ArchitectureMetrics, config: dict[str, Any]
) -> list[list[str]]:
    """构建一级模块体积摘要表的数据行。

    :param metrics: 已收集的聚合架构指标。
    :param config: 已加载的架构门禁配置。
    :return: 模块名、行数、文件数与预算组成的表格行。
    """

    budgets = config.get("module_budgets", {})
    rows: list[list[str]] = []
    for module_name, line_count in metrics.module_physical_lines.most_common():
        rows.append(
            [
                module_name,
                str(line_count),
                str(metrics.module_file_counts[module_name]),
                str(budgets.get(module_name, "-")),
            ]
        )
    return rows


def finding_rows(findings: list[Finding]) -> list[list[str]]:
    """构建检查结果摘要表的数据行。

    :param findings: 架构检查结果列表。
    :return: 最多八十条详细结果及可选截断摘要组成的表格行。
    """

    rows: list[list[str]] = []
    for finding in findings[:80]:
        location = "-"
        if finding.path:
            location = rel(finding.path)
            if finding.line:
                location = f"{location}:{finding.line}"
        rows.append(
            [
                finding.level,
                finding.check,
                location,
                finding.message.replace("|", "\\|"),
            ]
        )
    if len(findings) > 80:
        rows.append(["warning", "summary", "-", f"{len(findings) - 80} more findings"])
    return rows


def build_markdown_summary(
    metrics: ArchitectureMetrics,
    findings: list[Finding],
    config: dict[str, Any],
) -> str:
    """构建完整的架构门禁 Markdown 摘要。

    :param metrics: 已收集的聚合架构指标。
    :param findings: 架构检查结果列表。
    :param config: 已加载的架构门禁配置。
    :return: 包含指标、模块、依赖环和结果表格的 Markdown 文本。
    """

    error_count = sum(1 for finding in findings if finding.level == "error")
    warning_count = sum(1 for finding in findings if finding.level == "warning")
    lines = [
        "## Architecture Guard Summary",
        "",
        f"- Errors: {error_count}",
        f"- Warnings: {warning_count}",
        "",
        "### Core Metrics",
        "",
        markdown_table(["Metric", "Value", "Budget"], metric_rows(metrics, config)),
        "",
        "### Module Budgets",
        "",
        markdown_table(
            ["Module", "Lines", "Files", "Budget"], module_rows(metrics, config)
        ),
        "",
    ]

    if metrics.dependency_cycles:
        cycle_rows = [
            [" -> ".join([*cycle, cycle[0]])] for cycle in metrics.dependency_cycles
        ]
        lines.extend(
            [
                "### Dependency Cycles",
                "",
                markdown_table(["Cycle"], cycle_rows),
                "",
            ]
        )

    if findings:
        lines.extend(
            [
                "### Findings",
                "",
                markdown_table(
                    ["Level", "Check", "Location", "Message"],
                    finding_rows(findings),
                ),
                "",
            ]
        )

    return "\n".join(lines)


def write_summary(path_text: str | None, summary: str) -> None:
    """输出架构摘要并按需追加到指定文件。

    :param path_text: 可选摘要输出文件路径文本。
    :param summary: 已构建的 Markdown 摘要。
    :return: None。
    """

    print(summary)
    if not path_text:
        return

    path = Path(path_text)
    with path.open("a", encoding="utf-8") as file:
        file.write(summary)
        file.write("\n")


def main() -> int:
    """执行架构门禁并返回进程退出码。

    :return: 存在错误 finding 时返回 1，否则返回 0。
    """

    args = parse_args()
    config_path = ROOT / args.config
    config = load_config(config_path)
    paths_config = config.get("paths", {})
    source_root = ROOT / paths_config.get("source_root", "src/veterinary_agent")
    test_root = ROOT / paths_config.get("test_root", "tests")

    metrics, analyses = collect_metrics(source_root, test_root, config)
    findings: list[Finding] = []
    findings.extend(check_budgets(metrics, analyses, config))
    findings.extend(check_import_rules(analyses, config))
    findings.extend(check_header_comments(config))
    findings.extend(run_import_smoke(config))

    emit_github_annotations(findings)
    summary = build_markdown_summary(metrics, findings, config)
    write_summary(args.summary, summary)

    if any(finding.level == "error" for finding in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
