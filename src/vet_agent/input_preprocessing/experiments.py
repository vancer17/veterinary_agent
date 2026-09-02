"""Falsifiable shadow experiments for the remaining input-preprocessing risks."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from statistics import median
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .analyzer import InputPreprocessingAnalyzer
from .evaluation import (
    EvaluationSuite,
    InputPreprocessingEvaluation,
    build_analyzer,
)
from .projection import ClinicalSafetyShadowProjection
from .runtime_helpers import make_runtime_settings
from .vocabulary import CanonicalVocabulary


class ExperimentKind(StrEnum):
    """Finite kinds used to close the remaining shadow-risk hypotheses."""

    D_PARALLEL_NEGATION = "d_parallel_negation"
    E_SUBJECT_AMBIGUITY = "e_subject_ambiguity"
    NEGATIVE_MAPPING = "negative_mapping"
    REPEAT_STABILITY = "repeat_stability"
    CONSULTATION_BRANCH = "consultation_branch"
    CLINICAL_SAFETY_SHADOW = "clinical_safety_shadow"
    CANONICAL_GOVERNANCE = "canonical_governance"
    API_METADATA_SHADOW = "api_metadata_shadow"


class ShadowExperimentSpec(BaseModel):
    """One falsifiable experiment and its exit semantics."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=120)
    kind: ExperimentKind
    hypothesis: str = Field(min_length=1, max_length=500)
    sample_ids: list[str] = Field(default_factory=list, max_length=32)
    repeat_count: int = Field(default=1, ge=1, le=10)
    segmentation_variant: Literal["model", "expected"] = "model"
    expected_outcome: Literal[
        "semantic_exact_match",
        "gate_blocked_as_expected",
        "stable_output",
        "branch_behavior_match",
        "comparison_report_only",
        "governance_report_only",
    ]


class ShadowExperimentDocument(BaseModel):
    """Experiment matrix plus the ordinary evaluation suite it filters."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"]
    suite: EvaluationSuite
    experiments: list[ShadowExperimentSpec] = Field(min_length=1)


@dataclass(frozen=True)
class AsyncShadowTurnSnapshot:
    """A bounded, scope-aware snapshot for asynchronous API shadow analysis."""

    request_id: str
    trace_id: str
    user_id: str
    pet_id: str
    session_id: str
    task_key: str
    user_text: str
    turn_context: Any


@dataclass
class InMemoryAsyncShadowQueue:
    """In-memory queue used to prove API shadow can be failure-isolated."""

    max_size: int = 100
    _items: list[AsyncShadowTurnSnapshot] = field(default_factory=list)

    def submit(self, snapshot: AsyncShadowTurnSnapshot) -> bool:
        """Enqueue one snapshot without blocking or rejecting the main turn."""

        if len(self._items) >= self.max_size:
            return False
        self._items.append(snapshot)
        return True

    def pop(self) -> AsyncShadowTurnSnapshot | None:
        """Pop the oldest snapshot for a worker."""

        if not self._items:
            return None
        return self._items.pop(0)


class ClinicalSafetyBaselineAgent(Protocol):
    """Minimal existing clinical-safety semantic extractor interface."""

    async def extract(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        model: str,
    ) -> Any: ...


@dataclass
class ShadowExperimentRunner:
    """Run the experiment matrix without consuming any result in production."""

    document: ShadowExperimentDocument
    vocabulary: CanonicalVocabulary
    policy: Literal["local", "opa"] = "local"
    opa_base_url: str = ""
    analyzer: InputPreprocessingAnalyzer | None = None
    clinical_baseline: ClinicalSafetyBaselineAgent | None = None
    model: str = "qwen-plus"
    vocabulary_audit: dict[str, Any] | None = None

    async def run(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        only_experiment_ids: set[str] | None = None,
        repeat_override: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run selected experiments and return one report per experiment."""

        reports: list[dict[str, Any]] = []
        for spec in self.document.experiments:
            if only_experiment_ids and spec.experiment_id not in only_experiment_ids:
                continue
            reports.append(
                await self._run_experiment(
                    spec,
                    mode=mode,
                    repeat_override=repeat_override,
                )
            )
        return reports

    async def _run_experiment(
        self,
        spec: ShadowExperimentSpec,
        *,
        mode: Literal["ideal", "shadow"],
        repeat_override: int | None = None,
    ) -> dict[str, Any]:
        sample_ids = set(spec.sample_ids)
        if not sample_ids:
            if spec.kind != ExperimentKind.CANONICAL_GOVERNANCE:
                raise ValueError(f"experiment {spec.experiment_id} has no sample ids")
            passed = bool(self.vocabulary_audit)
            return {
                "experiment_id": spec.experiment_id,
                "kind": spec.kind.value,
                "hypothesis": spec.hypothesis,
                "mode": mode,
                "expected_outcome": spec.expected_outcome,
                "segmentation_variant": spec.segmentation_variant,
                "repeat_count": spec.repeat_count,
                "status": "passed" if passed else "failed",
                "failures": [] if passed else ["vocabulary_audit_missing"],
                "exit_criteria": {
                    "audit_present": passed,
                    "review_required": bool(
                        (self.vocabulary_audit or {}).get("review_required")
                    ),
                },
                "summary": {},
                "reports": [],
                "clinical_safety_comparison": None,
            }

        suite = self.document.suite.model_copy(
            update={
                "cases": [
                    case
                    for case in self.document.suite.cases
                    if case.sample_id in sample_ids
                ]
            }
        )
        if not suite.cases:
            raise ValueError(f"experiment {spec.experiment_id} has no matching cases")

        evaluation = InputPreprocessingEvaluation(
            suite=suite,
            vocabulary=self.vocabulary,
            policy=self.policy,
            opa_base_url=self.opa_base_url,
            analyzer=self.analyzer,
        )
        raw_reports = await evaluation.run(
            mode=mode,
            repeat=repeat_override or spec.repeat_count,
            segmentation_variant=spec.segmentation_variant,
        )
        summary = summarize_reports(raw_reports)
        kind_result = self._evaluate_kind(spec, raw_reports, summary)
        clinical_comparison = None
        if spec.kind == ExperimentKind.CLINICAL_SAFETY_SHADOW:
            clinical_comparison = await self._clinical_structural_comparison(suite)

        return {
            "experiment_id": spec.experiment_id,
            "kind": spec.kind.value,
            "hypothesis": spec.hypothesis,
            "mode": mode,
            "expected_outcome": spec.expected_outcome,
            "segmentation_variant": spec.segmentation_variant,
            "repeat_count": spec.repeat_count,
            "effective_repeat_count": repeat_override or spec.repeat_count,
            "status": kind_result["status"],
            "failures": kind_result["failures"],
            "exit_criteria": kind_result["exit_criteria"],
            "summary": summary,
            "reports": raw_reports,
            "clinical_safety_comparison": clinical_comparison,
        }

    def _evaluate_kind(
        self,
        spec: ShadowExperimentSpec,
        reports: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        failures: list[str] = []
        if spec.expected_outcome == "semantic_exact_match":
            if summary["semantic_match_count"] != len(reports):
                failures.append("semantic_exact_match_failed")
            if summary["denied_as_present_count"]:
                failures.append("denied_as_present_regression")
            if summary["normal_as_denied_count"]:
                failures.append("normal_as_denied_regression")
        elif spec.expected_outcome == "gate_blocked_as_expected":
            gate_blocked = sum(
                any(
                    turn.get("status") == "gate_blocked_as_expected"
                    for turn in report.get("turns", [])
                )
                for report in reports
            )
            if gate_blocked != len(reports):
                failures.append("expected_gate_block_not_observed")
        elif spec.expected_outcome == "stable_output":
            if summary["stable_sample_count"] != len(spec.sample_ids):
                failures.append("repeat_output_unstable")
            if summary["semantic_match_count"] != len(reports):
                failures.append("semantic_match_failed")
        elif spec.expected_outcome == "branch_behavior_match":
            for report in reports:
                for turn in report.get("turns", []):
                    behavior = turn.get("behavior_simulation") or {}
                    branches = behavior.get("branches") or {}
                    if not branches:
                        failures.append("decision_branches_missing")
                        continue
                    if (
                        behavior.get("answer_now")
                        and branches.get("answer_now_only", {}).get("action")
                        != "answer"
                    ):
                        failures.append("answer_now_only_branch_did_not_answer")
                    if branches.get("baseline_empty_success", {}).get(
                        "action"
                    ) == branches.get("answer_now_only", {}).get(
                        "action"
                    ) and behavior.get("answer_now"):
                        failures.append("answer_now_branch_has_no_behavior_delta")
                    if turn.get("status") != "passed":
                        failures.append("full_projection_branch_failed")
        elif spec.expected_outcome in {
            "comparison_report_only",
            "governance_report_only",
        }:
            if not reports and spec.expected_outcome == "comparison_report_only":
                failures.append("comparison_reports_missing")
            if (
                spec.expected_outcome == "governance_report_only"
                and not self.vocabulary_audit
            ):
                failures.append("vocabulary_audit_missing")

        passed = not failures
        return {
            "status": "passed" if passed else "failed",
            "failures": failures,
            "exit_criteria": {
                "all_reports_passed": summary["passed_count"] == len(reports),
                "semantic_exact": summary["semantic_match_count"] == len(reports),
                "safety_regressions": (
                    summary["denied_as_present_count"]
                    + summary["normal_as_denied_count"]
                ),
                "stable_sample_count": summary["stable_sample_count"],
            },
        }

    async def _clinical_structural_comparison(
        self,
        suite: EvaluationSuite,
    ) -> list[dict[str, Any]]:
        """Compare structural counts without feeding either result to policy."""

        comparisons: list[dict[str, Any]] = []
        if self.clinical_baseline is None or self.analyzer is None:
            for case in suite.cases:
                for turn in case.turns:
                    comparisons.append(
                        {
                            "sample_id": case.sample_id,
                            "baseline_status": "not_configured",
                            "comparison_status": "not_applicable",
                            "reason_code": "clinical_baseline_extractor_missing",
                        }
                    )
            return comparisons

        for case in suite.cases:
            for turn_index, turn in enumerate(case.turns, start=1):
                evaluation = InputPreprocessingEvaluation(
                    suite=suite,
                    vocabulary=self.vocabulary,
                    policy=self.policy,
                    opa_base_url=self.opa_base_url,
                    analyzer=self.analyzer,
                )
                context = evaluation._turn_context(case, turn_index=turn_index)
                baseline = await self.clinical_baseline.extract(
                    user_text=turn.user_text,
                    pet_context_summary="宠物画像: 物种=猫。",
                    model=self.model,
                )
                shadow = await self.analyzer.analyze(
                    user_text=turn.user_text,
                    turn_context=context,
                )
                from .projection import project_clinical_safety

                projection = project_clinical_safety(shadow, vocabulary=self.vocabulary)
                comparisons.append(
                    compare_clinical_safety_structures(baseline, projection)
                    | {"sample_id": case.sample_id}
                )
        return comparisons


def summarize_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate semantic, gate, safety, latency, and stability observations."""

    semantic_matches = 0
    denied_as_present = 0
    normal_as_denied = 0
    latencies: list[int] = []
    gate_reasons: Counter[str] = Counter()
    signatures_by_sample: dict[str, Counter[str]] = {}

    for report in reports:
        latencies.append(int(report.get("latency_ms") or 0))
        report_semantic_match = True
        signature_items: list[str] = []
        for turn in report.get("turns", []):
            comparison = turn.get("semantic_comparison")
            if comparison is not None:
                missing = bool(comparison.get("missing"))
                unexpected = bool(comparison.get("unexpected"))
                if missing or unexpected:
                    report_semantic_match = False
            else:
                expected = {tuple(item) for item in turn.get("expected_semantics", [])}
                actual = {tuple(item) for item in turn.get("actual_semantics", [])}
                report_semantic_match = report_semantic_match and expected == actual
            signature_items.extend(
                str(item) for item in turn.get("actual_semantics", [])
            )
            metrics = turn.get("metrics") or {}
            denied_as_present += int(bool(metrics.get("denied_as_present")))
            normal_as_denied += int(bool(metrics.get("normal_as_denied")))
            for gate in turn.get("gates", []):
                if gate.get("status") == "failed":
                    gate_reasons[str(gate.get("reason_code"))] += 1
        if report_semantic_match and report.get("status") == "passed":
            semantic_matches += 1
        signatures_by_sample.setdefault(str(report.get("sample_id")), Counter())[
            json.dumps(sorted(signature_items), ensure_ascii=False)
        ] += 1

    stable_samples = sum(
        1 for signatures in signatures_by_sample.values() if len(signatures) == 1
    )
    return {
        "report_count": len(reports),
        "passed_count": sum(item.get("status") == "passed" for item in reports),
        "semantic_match_count": semantic_matches,
        "denied_as_present_count": denied_as_present,
        "normal_as_denied_count": normal_as_denied,
        "gate_failed_count": sum(gate_reasons.values()),
        "gate_failed_reasons": dict(gate_reasons),
        "latency_ms": {
            "median": int(median(latencies)) if latencies else 0,
            "max": max(latencies) if latencies else 0,
        },
        "stable_sample_count": stable_samples,
        "sample_stability": {
            sample_id: {
                "unique_output_count": len(signatures),
                "majority_agreement": max(signatures.values())
                / sum(signatures.values()),
            }
            for sample_id, signatures in signatures_by_sample.items()
        },
    }


def compare_clinical_safety_structures(
    baseline: Any,
    projection: ClinicalSafetyShadowProjection,
) -> dict[str, Any]:
    """Produce a non-medical structural comparison between two shadow outputs."""

    baseline_present = [
        feature.normalized_text
        for feature in baseline.observed_features
        if feature.state == "present"
    ]
    baseline_denied = list(baseline.negated_terms)
    new_present = [item["canonical_id"] for item in projection.current_pet_symptoms]
    new_denied = [item["canonical_id"] for item in projection.denied_evidence]
    return {
        "baseline_status": baseline.strategy,
        "comparison_status": "report_only",
        "baseline_present_count": len(baseline_present),
        "new_present_count": len(new_present),
        "baseline_denied_count": len(baseline_denied),
        "new_denied_count": len(new_denied),
        "coverage_gap": (
            bool(baseline_present or new_present)
            and len(baseline_present) != len(new_present)
        ),
        "denied_coverage_gap": len(baseline_denied) != len(new_denied),
        "downstream_evaluation": projection.downstream_evaluation,
    }


def audit_vocabulary(
    vocabulary: CanonicalVocabulary,
    embeddings: Any,
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    """Audit alias recall and collisions without treating neighbors as facts."""

    alias_vectors: list[tuple[str, str, list[float]]] = []
    for term in vocabulary.terms:
        for alias in term.aliases:
            alias_vectors.append((term.canonical_id, alias, embeddings.embed(alias)))

    alias_counts: Counter[str] = Counter(alias for _, alias, _ in alias_vectors)
    records: list[dict[str, Any]] = []
    recall_hits = 0
    for expected_id, alias, vector in alias_vectors:
        scored: list[tuple[float, str]] = []
        for candidate_id, _, candidate_vector in alias_vectors:
            if candidate_id == expected_id and candidate_vector == vector:
                continue
            scored.append((_cosine(vector, candidate_vector), candidate_id))
        ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
        top_ids = [candidate_id for _, candidate_id in ranked[:top_k]]
        hit = expected_id in top_ids
        recall_hits += int(hit)
        records.append(
            {
                "canonical_id": expected_id,
                "alias": alias,
                "top_ids": top_ids,
                "recall_hit": hit,
                "ambiguous_alias": alias_counts[alias] > 1,
            }
        )
    return {
        "vocabulary_version": vocabulary.version,
        "alias_count": len(records),
        "recall_hit_rate": recall_hits / len(records) if records else 0.0,
        "ambiguous_alias_count": sum(item["ambiguous_alias"] for item in records),
        "records": records,
        "review_required": any(not item["recall_hit"] for item in records),
    }


def audit_vocabulary_static(vocabulary: CanonicalVocabulary) -> dict[str, Any]:
    """Run an offline structural vocabulary audit without embedding dependencies."""

    alias_counts: Counter[str] = Counter(
        alias for term in vocabulary.terms for alias in term.aliases
    )
    duplicate_aliases = sorted(
        alias for alias, count in alias_counts.items() if count > 1
    )
    return {
        "vocabulary_version": vocabulary.version,
        "term_count": len(vocabulary.terms),
        "alias_count": sum(alias_counts.values()),
        "unique_alias_count": len(alias_counts),
        "duplicate_aliases": duplicate_aliases,
        "review_required": bool(duplicate_aliases),
    }


def load_experiment_document(path: Path) -> ShadowExperimentDocument:
    """Load and validate the unproven-risk experiment matrix."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["suite"] = _suite_from_raw(raw["suite"])
        return ShadowExperimentDocument.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid shadow experiment document: {path}") from exc


def _suite_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate nested suite fields while preserving serializable input."""

    EvaluationSuite.model_validate(raw)
    return raw


def write_experiment_report(
    *,
    output_dir: Path,
    reports: list[dict[str, Any]],
    vocabulary_audit: dict[str, Any] | None = None,
) -> Path:
    """Write a versioned experiment report."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-experiments-{uuid4().hex[:12]}.json"
    passed = sum(item["status"] == "passed" for item in reports)
    payload = {
        "schema_version": "v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "experiment_count": len(reports),
            "passed": passed,
            "failed": len(reports) - passed,
        },
        "vocabulary_audit": vocabulary_audit,
        "experiments": reports,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return path


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding_dimension_mismatch")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _json_default(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("tests/fixtures/input_preprocessing/unproven_shadow_matrix.json"),
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(
            "assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json"
        ),
    )
    parser.add_argument("--mode", choices=("ideal", "shadow", "both"), default="ideal")
    parser.add_argument("--policy", choices=("local", "opa"), default="local")
    parser.add_argument(
        "--opa-base-url", default=os.getenv("INPUT_PREPROCESSING_OPA_BASE_URL", "")
    )
    parser.add_argument(
        "--model", default=os.getenv("INPUT_PREPROCESSING_MODEL", "qwen-plus")
    )
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--repeat-override", type=int, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-experiments"),
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    if args.repeat_override is not None and not 1 <= args.repeat_override <= 10:
        raise ValueError("--repeat-override must be between 1 and 10")
    document = load_experiment_document(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    modes: list[Literal["ideal", "shadow"]] = (
        ["ideal", "shadow"] if args.mode == "both" else [args.mode]
    )
    analyzer = (
        build_analyzer(model=args.model, vocabulary=vocabulary)
        if "shadow" in modes
        else None
    )
    vocabulary_audit: dict[str, Any] | None = audit_vocabulary_static(vocabulary)
    clinical_baseline = None
    if "shadow" in modes:
        from vet_agent.clinical_safety import ClinicalSafetySemanticExtractorAgent
        from vet_agent.runtime import QwenClient, QwenEmbeddingClient

        settings = make_runtime_settings()
        clinical_baseline = ClinicalSafetySemanticExtractorAgent(
            QwenClient(settings),
            settings,
        )
        vocabulary_audit = audit_vocabulary(
            vocabulary,
            QwenEmbeddingClient(settings),
        )
    runner = ShadowExperimentRunner(
        document=document,
        vocabulary=vocabulary,
        policy=args.policy,
        opa_base_url=args.opa_base_url,
        analyzer=analyzer,
        clinical_baseline=clinical_baseline,
        model=args.model,
        vocabulary_audit=vocabulary_audit,
    )
    reports: list[dict[str, Any]] = []
    only = set(args.experiment) if args.experiment else None
    for mode in modes:
        reports.extend(
            await runner.run(
                mode=mode,
                only_experiment_ids=only,
                repeat_override=args.repeat_override,
            )
        )
    path = write_experiment_report(output_dir=args.output_dir, reports=reports)
    failed = [item for item in reports if item["status"] != "passed"]
    print(f"report={path}")
    print(
        f"experiments={len(reports)} passed={len(reports) - len(failed)} failed={len(failed)}"
    )
    for item in failed:
        print(
            f"FAILED experiment={item['experiment_id']} mode={item['mode']} "
            f"failures={','.join(item['failures'])}"
        )
    return 1 if failed else 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
