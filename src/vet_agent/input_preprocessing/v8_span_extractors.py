"""Span candidate adapters for the V8 experiment.

The default adapter is deliberately non-semantic: it creates an exhaustive,
offset-backed candidate pool inside physical sentence blocks.  GLiNER is an
optional mature extractor adapter and must be explicitly configured; there is
no keyword, regex, or semantic fallback.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .v8_contracts import V8SpanCandidate, V8SpanLabel

NGRAM_EXTRACTOR_VERSION = "v8-ngram-candidate-pool-20260827-1"
_GLINER_ADAPTER_VERSION = "v8-gliner-adapter-20260828-2"
_SENTENCE_BOUNDARY = re.compile(r"[。！？!?\n；;]+")
_BLOCK_TRIM = re.compile(r"^\s+|\s+$")

_GLINER_LABEL_PROFILES: dict[str, tuple[V8SpanLabel, ...]] = {
    "core": (
        V8SpanLabel.TARGET_MENTION,
        V8SpanLabel.STATE_MENTION,
        V8SpanLabel.ACTION_EVENT,
        V8SpanLabel.TEMPORAL_EXPRESSION,
        V8SpanLabel.MEASUREMENT_EXPRESSION,
        V8SpanLabel.RELATION_EXPRESSION,
    ),
    "participant": (
        V8SpanLabel.AGENT_MENTION,
        V8SpanLabel.RECIPIENT_MENTION,
        V8SpanLabel.SUBJECT_MENTION,
        V8SpanLabel.OBJECT_MENTION,
    ),
    "discourse": (
        V8SpanLabel.CONTROL_INTENT_EXPRESSION,
        V8SpanLabel.QUESTION_EXPRESSION,
    ),
}
_GLINER_LABEL_PROFILES["staged"] = (
    *_GLINER_LABEL_PROFILES["core"],
    *_GLINER_LABEL_PROFILES["participant"],
    *_GLINER_LABEL_PROFILES["discourse"],
)
_GLINER_LABEL_PROFILES["all"] = tuple(
    label for label in V8SpanLabel if label is not V8SpanLabel.CANDIDATE_SPAN
)
_GLINER_LABEL_MODES: dict[str, dict[V8SpanLabel, str]] = {
    "english": {
        V8SpanLabel.TARGET_MENTION: "target_mention",
        V8SpanLabel.STATE_MENTION: "state_mention",
        V8SpanLabel.ACTION_EVENT: "action_event",
        V8SpanLabel.AGENT_MENTION: "agent_mention",
        V8SpanLabel.RECIPIENT_MENTION: "recipient_mention",
        V8SpanLabel.SUBJECT_MENTION: "subject_mention",
        V8SpanLabel.OBJECT_MENTION: "object_mention",
        V8SpanLabel.TEMPORAL_EXPRESSION: "temporal_expression",
        V8SpanLabel.MEASUREMENT_EXPRESSION: "measurement_expression",
        V8SpanLabel.RELATION_EXPRESSION: "relation_expression",
        V8SpanLabel.CONTROL_INTENT_EXPRESSION: "control_intent_expression",
        V8SpanLabel.QUESTION_EXPRESSION: "question_expression",
    },
    # Chinese aliases remain generic linguistic prompts. They are intentionally
    # not veterinary symptoms, diagnoses, risk classes, or treatment rules.
    "bilingual": {
        V8SpanLabel.TARGET_MENTION: "目标现象或事物 target mention",
        V8SpanLabel.STATE_MENTION: "状态表述 state mention",
        V8SpanLabel.ACTION_EVENT: "动作事件 action event",
        V8SpanLabel.AGENT_MENTION: "动作发出者 agent mention",
        V8SpanLabel.RECIPIENT_MENTION: "动作承受者 recipient mention",
        V8SpanLabel.SUBJECT_MENTION: "陈述主体 subject mention",
        V8SpanLabel.OBJECT_MENTION: "涉及对象 object mention",
        V8SpanLabel.TEMPORAL_EXPRESSION: "时间表达 temporal expression",
        V8SpanLabel.MEASUREMENT_EXPRESSION: "数量或频率表达 measurement expression",
        V8SpanLabel.RELATION_EXPRESSION: "状态关系表达 relation expression",
        V8SpanLabel.CONTROL_INTENT_EXPRESSION: "控制意图表达 control intent",
        V8SpanLabel.QUESTION_EXPRESSION: "问题表达 question",
    },
    "descriptive": {
        V8SpanLabel.TARGET_MENTION: "被讨论的目标短语，不是完整句子 target phrase",
        V8SpanLabel.STATE_MENTION: "用户描述的状态短语，不是完整句子 state phrase",
        V8SpanLabel.ACTION_EVENT: "完整动作或事件短语 action event phrase",
        V8SpanLabel.AGENT_MENTION: "发出动作的人或动物短 mention agent",
        V8SpanLabel.RECIPIENT_MENTION: "承受动作的人或动物短 mention recipient",
        V8SpanLabel.SUBJECT_MENTION: "陈述所指的人或动物短 mention subject",
        V8SpanLabel.OBJECT_MENTION: "动作涉及的物体或物品短 mention object",
        V8SpanLabel.TEMPORAL_EXPRESSION: "时间、持续时间或起点表达 temporal",
        V8SpanLabel.MEASUREMENT_EXPRESSION: "数量、频率、剂量或度量表达 measurement",
        V8SpanLabel.RELATION_EXPRESSION: "状态变化、正常或否认关系短语 relation",
        V8SpanLabel.CONTROL_INTENT_EXPRESSION: "用户要求或控制意图短语 control intent",
        V8SpanLabel.QUESTION_EXPRESSION: "用户提问短语 question",
    },
}

_GLINER_PROFILE_ALIASES = {
    "focused": "core",
    "macro": "staged",
}


class V8SpanExtractor(Protocol):
    @property
    def extractor_version(self) -> str: ...

    def extract(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
    ) -> list[V8SpanCandidate]: ...


@dataclass(frozen=True)
class V8NgramSpanExtractor:
    """Generate all bounded character spans inside physical sentence blocks."""

    max_span_length: int = 32
    extractor_version: str = NGRAM_EXTRACTOR_VERSION

    def extract(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
    ) -> list[V8SpanCandidate]:
        if not text:
            raise ValueError("v8_source_text_empty")
        candidates: list[V8SpanCandidate] = []
        for block_start, block in self._blocks(text):
            limit = min(len(block), self.max_span_length)
            for length in range(1, limit + 1):
                for start in range(len(block) - length + 1):
                    end = start + length
                    candidates.append(
                        V8SpanCandidate(
                            span_id=f"{source_id}:span-{block_start + start:06d}-{block_start + end:06d}",
                            source_id=source_id,
                            source_block_id=source_block_id,
                            start=block_start + start,
                            end=block_start + end,
                            text=block[start:end],
                            label=V8SpanLabel.CANDIDATE_SPAN,
                            score=1.0,
                            extractor_version=self.extractor_version,
                        )
                    )
        return candidates

    @staticmethod
    def _blocks(text: str) -> list[tuple[int, str]]:
        blocks: list[tuple[int, str]] = []
        cursor = 0
        for match in _SENTENCE_BOUNDARY.finditer(text):
            stop = match.end()
            block = text[cursor:stop]
            block = _BLOCK_TRIM.sub("", block)
            relative = len(text[cursor:stop]) - len(text[cursor:stop].lstrip())
            if block:
                blocks.append((cursor + relative, block))
            cursor = stop
        block = text[cursor:]
        relative = len(block) - len(block.lstrip())
        block = _BLOCK_TRIM.sub("", block)
        if block:
            blocks.append((cursor + relative, block))
        return blocks


@dataclass(frozen=True)
class V8GlinerSpanExtractor:
    """Optional GLiNER adapter; loaded lazily and never used as a fallback."""

    model_name: str
    label_profile: str = "staged"
    threshold: float = 0.5
    model_revision: str = "unpinned"
    label_mode: str = "english"
    extractor_version: str = _GLINER_ADAPTER_VERSION

    def __post_init__(self) -> None:
        try:
            from gliner import GLiNER  # type: ignore[import-untyped]
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("v8_gliner_unavailable") from exc
        if self.label_profile not in _GLINER_LABEL_PROFILES:
            raise ValueError(
                f"unsupported_v8_gliner_label_profile:{self.label_profile}"
            )
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("v8_gliner_threshold_out_of_range")
        if self.label_mode not in _GLINER_LABEL_MODES:
            raise ValueError(f"unsupported_v8_gliner_label_mode:{self.label_mode}")
        self.__dict__["model"] = GLiNER.from_pretrained(self.model_name)
        self.__dict__["extractor_version"] = (
            f"{_GLINER_ADAPTER_VERSION}:{self.label_profile}:"
            f"threshold-{self.threshold:.3f}:{self.label_mode}:"
            f"{self.model_revision}"
        )

    @property
    def _label_groups(self) -> list[tuple[str, list[str]]]:
        labels = _GLINER_LABEL_PROFILES[self.label_profile]
        model_labels = _GLINER_LABEL_MODES[self.label_mode]
        if self.label_profile in {"staged", "all"}:
            if self.label_profile == "all":
                return [("all", [model_labels[label] for label in labels])]
            return [
                (
                    name,
                    [model_labels[label] for label in _GLINER_LABEL_PROFILES[name]],
                )
                for name in ("core", "participant", "discourse")
            ]
        return [(self.label_profile, [model_labels[label] for label in labels])]

    def _canonical_label(self, raw_label: str) -> V8SpanLabel:
        model_labels = _GLINER_LABEL_MODES[self.label_mode]
        for canonical, model_label in model_labels.items():
            if model_label == raw_label:
                return canonical
        raise ValueError(
            f"unsupported_v8_gliner_output_label:{self.label_mode}:{raw_label}"
        )

    def extract(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
    ) -> list[V8SpanCandidate]:
        model: Any = self.__dict__["model"]
        result: list[V8SpanCandidate] = []
        for group_name, labels in self._label_groups:
            entities = model.predict_entities(text, labels, threshold=self.threshold)
            for index, entity in enumerate(entities, start=1):
                start = int(entity["start"])
                end = int(entity["end"])
                label = self._canonical_label(str(entity["label"]))
                if not 0 <= start < end <= len(text):
                    continue
                result.append(
                    V8SpanCandidate(
                        span_id=(f"{source_id}:gliner-{group_name}-{index:06d}"),
                        source_id=source_id,
                        source_block_id=source_block_id,
                        start=start,
                        end=end,
                        text=text[start:end],
                        label=label,
                        score=float(entity.get("score", 0.0)),
                        extractor_version=self.extractor_version,
                    )
                )
        return self._deduplicate(result)

    @staticmethod
    def _deduplicate(candidates: list[V8SpanCandidate]) -> list[V8SpanCandidate]:
        by_boundary: dict[tuple[int, int, str], V8SpanCandidate] = {}
        for candidate in candidates:
            key = (candidate.start, candidate.end, candidate.label)
            previous = by_boundary.get(key)
            if previous is None or candidate.score > previous.score:
                by_boundary[key] = candidate
        return [
            by_boundary[key]
            for key in sorted(by_boundary, key=lambda item: (item[0], item[1], item[2]))
        ]


def build_v8_span_extractor() -> V8SpanExtractor:
    kind = os.getenv("INPUT_PREPROCESSING_V8_SPAN_EXTRACTOR", "ngram").strip().lower()
    if kind == "ngram":
        max_length = int(os.getenv("INPUT_PREPROCESSING_V8_MAX_SPAN_LENGTH", "32"))
        return V8NgramSpanExtractor(max_span_length=max_length)
    if kind == "gliner":
        model_name = os.getenv("INPUT_PREPROCESSING_V8_GLINER_MODEL", "")
        if not model_name:
            raise ValueError("v8_gliner_model_required")
        threshold = float(os.getenv("INPUT_PREPROCESSING_V8_GLINER_THRESHOLD", "0.5"))
        profile = (
            os.getenv(
                "INPUT_PREPROCESSING_V8_GLINER_LABEL_PROFILE",
                "staged",
            )
            .strip()
            .lower()
        )
        profile = _GLINER_PROFILE_ALIASES.get(profile, profile)
        model_revision = os.getenv(
            "INPUT_PREPROCESSING_V8_GLINER_REVISION",
            "unpinned",
        )
        label_mode = (
            os.getenv("INPUT_PREPROCESSING_V8_GLINER_LABEL_MODE", "english")
            .strip()
            .lower()
        )
        return V8GlinerSpanExtractor(
            model_name=model_name,
            label_profile=profile,
            threshold=threshold,
            model_revision=model_revision,
            label_mode=label_mode,
        )
    raise ValueError(f"unsupported_v8_span_extractor:{kind}")
