"""CLI orchestration for ninth-round V8 attribution diagnostics.

The runner intentionally keeps V8 stage admission unchanged.  V9 reports are
diagnostic evidence only and cannot unblock a live phase or production gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .v8_experiments import load_v8_matrix
from .v8_macro_analyzer import V8_PROMPT_VERSION, V8_SCHEMA_VERSION
from .v8_span_extractors import V8GlinerSpanExtractor
from .v9_attribution import (
    V9_REPORT_VERSION,
    evaluate_v9_span_pool,
    gold_integrity_report,
    run_v9_adapter_cold,
    run_v9_canonical_gold,
    run_v9_macro_attribution,
    run_v9_participant_gold,
    run_v9_relation_gold,
    run_v9_repeat_attribution,
    v9_safety_boundary,
)
from .vocabulary import CanonicalVocabulary

DEFAULT_MATRIX = Path("tests/fixtures/input_preprocessing/eighth_round_span_macro_matrix.json")
DEFAULT_VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)
DEFAULT_RELATION_CALIBRATION = Path(
    "tests/fixtures/input_preprocessing/seventh_round_attribution_matrix.json"
)
DEFAULT_OUTPUT_DIR = Path(".data/evaluations/input-preprocessing-v9-attribution")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gliner_model_name() -> str:
    model_name = os.getenv("INPUT_PREPROCESSING_V8_GLINER_MODEL", "")
    if not model_name:
        raise ValueError("v9_gliner_model_required")
    return model_name


def _build_gliner_variant(
    *,
    label_mode: str,
    threshold: float,
) -> V8GlinerSpanExtractor:
    return V8GlinerSpanExtractor(
        model_name=_gliner_model_name(),
        threshold=threshold,
        label_profile=os.getenv(
            "INPUT_PREPROCESSING_V8_GLINER_LABEL_PROFILE",
            "staged",
        ),
        model_revision=os.getenv(
            "INPUT_PREPROCESSING_V8_GLINER_REVISION",
            "unpinned",
        ),
        label_mode=label_mode,
    )


def _span_reports(
    *,
    matrix: dict[str, Any],
    label_modes: list[str],
    thresholds: list[float],
) -> list[dict[str, Any]]:
    if len(label_modes) != 1 and len(thresholds) != 1:
        raise ValueError("v9_span_attribution_requires_one_fixed_axis")
    reports: list[dict[str, Any]] = []
    for label_mode in label_modes:
        for threshold in thresholds:
            extractor = _build_gliner_variant(
                label_mode=label_mode,
                threshold=threshold,
            )
            reports.append(
                evaluate_v9_span_pool(
                    matrix,
                    extractor=extractor,
                )
            )
    return reports


async def _async_main(args: argparse.Namespace) -> int:
    if "held_out" in args.matrix.name:
        raise ValueError("v9_attribution_held_out_forbidden")
    matrix = load_v8_matrix(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    reports: list[dict[str, Any]] = []
    changed_variables: list[str] = []

    if args.suite in {"gold", "interface", "all"}:
        reports.append(gold_integrity_report(matrix))
        changed_variables.append("none_gold_integrity_control")

    if args.suite == "interface":
        reports.append(
            await run_v9_relation_gold(
                matrix=matrix,
                mode="quick",
                cache_path=None,
            )
        )
        reports.append(
            run_v9_canonical_gold(
                matrix=matrix,
                vocabulary=vocabulary,
                mode="quick",
            )
        )
        reports.append(run_v9_participant_gold(matrix))

    if args.suite in {"span-label", "span-threshold", "all"}:
        label_modes = (
            list(dict.fromkeys(args.label_mode))
            if args.suite in {"span-label", "all"}
            else [args.label_mode[0]]
        )
        thresholds = (
            list(dict.fromkeys(args.threshold))
            if args.suite in {"span-threshold", "all"}
            else [args.threshold[0]]
        )
        if args.suite == "all":
            # The default all-suite control keeps one axis fixed: labels use
            # the first threshold; the threshold sweep uses the first label.
            label_modes = label_modes[:1]
            thresholds = thresholds[:1]
        reports.extend(
            _span_reports(
                matrix=matrix,
                label_modes=label_modes,
                thresholds=thresholds,
            )
        )
        changed_variables.append(
            "gliner_label_mode" if args.suite == "span-label" else "gliner_threshold"
        )

    llm_selected = args.suite in {
        "macro",
        "downstream",
        "relation-calibration",
        "rep",
        "adapter",
        "all",
    }
    if llm_selected and not args.allow_llm:
        raise ValueError("v9_llm_attribution_requires_allow_llm")

    if args.suite in {"macro", "all"}:
        for id_mode in dict.fromkeys(args.span_id_mode):
            reports.append(
                await run_v9_macro_attribution(
                    matrix=matrix,
                    adapter=args.macro_adapter,
                    id_mode=id_mode,
                    unit_ids=args.unit,
                    cache_path=None if args.no_cache else args.cache_path,
                )
            )
        changed_variables.append("ideal_span_id_mode")

    if args.suite in {"downstream", "all"}:
        reports.append(
            await run_v9_relation_gold(
                matrix=matrix,
                mode=args.mode,
                cache_path=None if args.no_cache else args.cache_path,
            )
        )
        reports.append(
            run_v9_canonical_gold(
                matrix=matrix,
                vocabulary=vocabulary,
                mode=args.mode,
            )
        )
        reports.append(run_v9_participant_gold(matrix))
        changed_variables.append("none_gold_injection_control")

    if args.suite == "relation-calibration":
        calibration_document = json.loads(
            args.relation_calibration_matrix.read_text(encoding="utf-8")
        )
        reports.append(
            await run_v9_relation_gold(
                matrix=matrix,
                mode=args.mode,
                cache_path=None if args.no_cache else args.cache_path,
                calibration_units=list(calibration_document["relation_units"]),
            )
        )
        changed_variables.append("v7_relation_calibration_context")

    if args.suite in {"rep", "all"}:
        reports.append(
            await run_v9_repeat_attribution(
                matrix=matrix,
                unit_id=args.rep_unit,
                adapter=args.macro_adapter,
                run_count=args.rep_runs,
            )
        )
        changed_variables.append("cold_run_index")

    if args.suite == "adapter":
        reports.append(
            await run_v9_adapter_cold(
                matrix=matrix,
                adapters=args.adapter,
                unit_id=args.rep_unit,
                run_count=args.rep_runs,
            )
        )
        changed_variables.append("adapter")

    if not reports:
        raise ValueError("no_v9_attribution_report_generated")

    document = {
        "schema_version": V9_REPORT_VERSION,
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "suite": args.suite,
        "mode": args.mode,
        "changed_variables": list(dict.fromkeys(changed_variables)),
        "matrix": str(args.matrix),
        "matrix_sha256": _sha256(args.matrix),
        "vocabulary": str(args.vocabulary),
        "vocabulary_version": vocabulary.version,
        "model": os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        "prompt_version": V8_PROMPT_VERSION,
        "schema_contract_version": V8_SCHEMA_VERSION,
        "cache_enabled": not args.no_cache,
        "reports": reports,
        "safety_boundary": v9_safety_boundary(),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / (
        f"v9-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
    )
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(output_path)
    for report in reports:
        print(
            report["experiment_id"],
            report["status"],
            json.dumps(report.get("metrics", {}), ensure_ascii=False, sort_keys=True),
        )
    return 1 if any(report.get("status") == "failed" for report in reports) else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        choices=(
            "gold",
            "interface",
            "span-label",
            "span-threshold",
            "macro",
            "downstream",
            "relation-calibration",
            "adapter",
            "rep",
            "all",
        ),
        required=True,
    )
    parser.add_argument("--mode", choices=("quick", "shadow"), default="shadow")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument(
        "--relation-calibration-matrix",
        type=Path,
        default=DEFAULT_RELATION_CALIBRATION,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-path", type=Path)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--label-mode",
        action="append",
        choices=("english", "bilingual", "descriptive"),
        default=None,
    )
    parser.add_argument("--threshold", action="append", type=float, default=None)
    parser.add_argument(
        "--span-id-mode",
        action="append",
        choices=("role-hinted", "opaque"),
        default=None,
    )
    parser.add_argument("--macro-adapter", choices=("base", "instructor", "baml"), default="base")
    parser.add_argument(
        "--adapter",
        action="append",
        choices=("base", "instructor", "baml"),
        default=None,
    )
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--rep-unit", default="macro-answer-fact")
    parser.add_argument("--rep-runs", type=int, default=3)
    parser.add_argument("--allow-llm", action="store_true")
    args = parser.parse_args()
    if args.label_mode is None:
        args.label_mode = ["english"]
    if args.span_id_mode is None:
        args.span_id_mode = ["opaque"]
    if args.adapter is None:
        args.adapter = ["base"]
    if args.threshold is None:
        args.threshold = [0.3]
    if any(not 0.0 <= value <= 1.0 for value in args.threshold):
        raise ValueError("v9_threshold_out_of_range")
    return args


def main() -> int:
    args = _parse_args()
    import asyncio

    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
