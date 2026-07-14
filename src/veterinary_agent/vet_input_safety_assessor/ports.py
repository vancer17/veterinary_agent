##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/ports.py
# 作用: 定义 VetInputSafetyAssessor 的词库匹配、语义路由与本地结构化抽取端口及 TODO 空壳。
# 边界: 只声明和提供弱依赖占位能力，不实现跨领域业务、不调用 LLM、不写入 trace。
##################################################################################################

from typing import Protocol

from veterinary_agent.vet_input_safety_assessor.dto import (
    InputSafetySignalDto,
    SemanticRouteCandidateDto,
    StructuredSignalExtractionSummaryDto,
    VetInputAssessmentRequestDto,
)

TODO_SEMANTIC_ROUTER_VERSION = "todo-semantic-router.unavailable"
TODO_LOCAL_EXTRACTOR_VERSION = "todo-local-extractor.unavailable"


class LexicalSignalMatcher(Protocol):
    """输入安全词库匹配端口。"""

    def is_ready(self) -> bool:
        """判断词库匹配器是否具备执行条件。

        :return: 若词库或 last-known-good 实例可用则返回 True。
        """

        ...

    def match(
        self, request: VetInputAssessmentRequestDto
    ) -> list[InputSafetySignalDto]:
        """匹配单个子任务中的输入侧安全信号。

        :param request: 单个子任务输入安全评估请求。
        :return: 当前子任务检出的安全信号列表。
        """

        ...


class SemanticRouteClassifier(Protocol):
    """语义路由候选端口。"""

    def is_ready(self) -> bool:
        """判断语义路由器是否具备执行条件。

        :return: 若语义路由器可用则返回 True。
        """

        ...

    async def classify(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> list[SemanticRouteCandidateDto]:
        """对单个子任务进行语义路由候选召回。

        :param request: 单个子任务输入安全评估请求。
        :return: 语义路由候选列表。
        """

        ...


class StructuredSignalExtractor(Protocol):
    """本地结构化信号抽取端口。"""

    def is_ready(self) -> bool:
        """判断本地结构化抽取器是否具备执行条件。

        :return: 若本地抽取器可用则返回 True。
        """

        ...

    async def extract(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> StructuredSignalExtractionSummaryDto:
        """抽取单个子任务中的结构化症状、时间和程度线索。

        :param request: 单个子任务输入安全评估请求。
        :return: 本地结构化抽取摘要。
        """

        ...


class TodoStructuredSignalExtractor:
    """本地结构化抽取器尚未接入时使用的 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO 本地抽取器是否就绪。

        :return: TODO 空壳固定返回 False。
        """

        return False

    async def extract(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> StructuredSignalExtractionSummaryDto:
        """返回本地结构化抽取器不可用摘要。

        :param request: 单个子任务输入安全评估请求。
        :return: 标记抽取器不可用的结构化摘要。
        """

        del request
        return StructuredSignalExtractionSummaryDto(
            extractor_version=TODO_LOCAL_EXTRACTOR_VERSION,
            extracted_concept_types=[],
            confidence=0.0,
            unavailable=True,
        )


__all__: tuple[str, ...] = (
    "LexicalSignalMatcher",
    "SemanticRouteClassifier",
    "StructuredSignalExtractor",
    "TODO_LOCAL_EXTRACTOR_VERSION",
    "TODO_SEMANTIC_ROUTER_VERSION",
    "TodoStructuredSignalExtractor",
)
