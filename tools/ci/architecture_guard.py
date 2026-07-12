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
    with config_path.open("rb") as file:
        return tomllib.load(file)


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def iter_python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    ignored_parts = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
    return sorted(
        path
        for path in root.rglob("*.py")
        if not ignored_parts.intersection(path.relative_to(root).parts)
    )


def count_physical_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def top_module_for(path: Path, source_root: Path) -> str:
    relative_path = path.relative_to(source_root)
    if len(relative_path.parts) == 1:
        return "__root__"
    return relative_path.parts[0]


def module_path_for(path: Path, source_root: Path) -> str:
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
    if isinstance(node, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    return isinstance(node.target, ast.Name) and node.target.id == "__all__"


def analyze_python_file(path: Path, source_root: Path) -> FileAnalysis:
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
            analysis.all_export_count += count_string_constants(node.value)

    return analysis


def internal_target_top(import_name: str, known_modules: set[str]) -> str | None:
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
    body = cycle[:-1]
    rotations = [tuple(body[index:] + body[:index]) for index in range(len(body))]
    return min(rotations)


def find_dependency_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    cycles: set[tuple[str, ...]] = set()
    visited: set[str] = set()
    active: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
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
    return import_name == blocked_import or import_name.startswith(f"{blocked_import}.")


def check_import_rules(
    analyses: list[FileAnalysis],
    config: dict[str, Any],
) -> list[Finding]:
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
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_github_annotations(findings: list[Finding]) -> None:
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
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def metric_rows(
    metrics: ArchitectureMetrics, config: dict[str, Any]
) -> list[list[str]]:
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
    print(summary)
    if not path_text:
        return

    path = Path(path_text)
    with path.open("a", encoding="utf-8") as file:
        file.write(summary)
        file.write("\n")


def main() -> int:
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
