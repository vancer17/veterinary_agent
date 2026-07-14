##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/matchers.py
# 作用: 实现 VetInputSafetyAssessor 的本地关键词 SAF 信号匹配和轻量语义路由兜底。
# 边界: 仅使用本组件内置受控词表产出结构化候选，不执行模型推理、不访问长期记忆或外部知识库。
##################################################################################################

from dataclasses import dataclass
from hashlib import sha256

from veterinary_agent.vet_input_safety_assessor.dto import (
    InputSafetySignalDto,
    SemanticRouteCandidateDto,
    VetInputAssessmentRequestDto,
)
from veterinary_agent.vet_input_safety_assessor.enums import (
    SafetySignalCode,
    SignalSource,
    SignalStrength,
)
from veterinary_agent.vet_task_decomposer import VetTaskType

_ROUTER_VERSION = "keyword-semantic-router.v1"


@dataclass(frozen=True, slots=True)
class KeywordSignalRule:
    """关键词安全信号规则。"""

    code: SafetySignalCode
    strength: SignalStrength
    concept: str
    keywords: tuple[str, ...]


def _hash_text(text: str) -> str:
    """构建命中文本的稳定 hash。

    :param text: 需要计算摘要的文本片段。
    :return: 带 sha256 前缀的十六进制摘要。
    """

    digest = sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _normalize_text(text: str) -> str:
    """归一化用于关键词匹配的文本。

    :param text: 原始子任务文本。
    :return: 小写化并裁剪首尾空白后的匹配文本。
    """

    return text.strip().lower()


def _build_signal_id(
    *,
    task_id: str,
    code: SafetySignalCode,
    concept: str,
    index: int,
) -> str:
    """构建确定性安全信号 ID。

    :param task_id: 子任务 ID。
    :param code: 安全信号码。
    :param concept: 归一化风险概念。
    :param index: 当前子任务内的信号序号。
    :return: 稳定安全信号 ID。
    """

    raw_id = f"{task_id}:{code.value}:{concept}:{index}"
    digest = sha256(raw_id.encode("utf-8")).hexdigest()[:16]
    return f"sig_{digest}"


def _saf01_rules() -> tuple[KeywordSignalRule, ...]:
    """构建内置 SAF-01 毒物和高危物质规则。

    :return: SAF-01 关键词规则集合。
    """

    return (
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="布洛芬",
            keywords=("布洛芬", "ibuprofen", "芬必得"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="对乙酰氨基酚",
            keywords=("对乙酰氨基酚", "扑热息痛", "泰诺", "acetaminophen"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="葡萄或葡萄干",
            keywords=("葡萄干", "葡萄", "raisin", "grape"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="木糖醇",
            keywords=("木糖醇", "xylitol"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="巧克力",
            keywords=("巧克力", "chocolate", "可可"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="葱蒜类",
            keywords=("洋葱", "大蒜", "葱", "韭菜", "onion", "garlic"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="百合",
            keywords=("百合", "lily"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
            strength=SignalStrength.L3,
            concept="灭鼠药",
            keywords=("灭鼠药", "老鼠药", "rat poison"),
        ),
    )


def _saf03_rules() -> tuple[KeywordSignalRule, ...]:
    """构建内置 SAF-03 急症红线规则。

    :return: SAF-03 关键词规则集合。
    """

    return (
        KeywordSignalRule(
            code=SafetySignalCode.SAF_03_ACUTE_RED_FLAG,
            strength=SignalStrength.L3,
            concept="抽搐",
            keywords=("抽搐", "癫痫", "痉挛"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_03_ACUTE_RED_FLAG,
            strength=SignalStrength.L3,
            concept="呼吸困难",
            keywords=("呼吸困难", "喘不上气", "张口呼吸", "呼吸很费劲"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_03_ACUTE_RED_FLAG,
            strength=SignalStrength.L3,
            concept="意识异常",
            keywords=("昏迷", "休克", "叫不醒", "失去意识"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_03_ACUTE_RED_FLAG,
            strength=SignalStrength.L3,
            concept="排尿阻塞",
            keywords=("尿不出来", "尿闭", "一直蹲猫砂盆"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_03_ACUTE_RED_FLAG,
            strength=SignalStrength.L2,
            concept="持续呕吐",
            keywords=("持续呕吐", "频繁呕吐", "一直吐", "吐个不停"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.SAF_03_ACUTE_RED_FLAG,
            strength=SignalStrength.L2,
            concept="便血",
            keywords=("便血", "拉血", "血便"),
        ),
    )


def _marker_rules() -> tuple[KeywordSignalRule, ...]:
    """构建实况、科普、假设和跨域信号规则。

    :return: 标记类关键词规则集合。
    """

    return (
        KeywordSignalRule(
            code=SafetySignalCode.REALTIME_MARKER,
            strength=SignalStrength.NOT_APPLICABLE,
            concept="实况时间",
            keywords=("正在", "现在", "刚刚", "刚才", "突然", "一直", "持续"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.EDUCATION_MARKER,
            strength=SignalStrength.NOT_APPLICABLE,
            concept="科普问法",
            keywords=("为什么", "有哪些原因", "什么原因", "科普", "原理", "是什么"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.HYPOTHETICAL_MARKER,
            strength=SignalStrength.NOT_APPLICABLE,
            concept="假设问法",
            keywords=("如果", "假如", "万一", "会不会"),
        ),
        KeywordSignalRule(
            code=SafetySignalCode.CROSS_DOMAIN_SYMPTOM,
            strength=SignalStrength.L1,
            concept="轻量症状线索",
            keywords=("拉稀", "软便", "呕吐", "吐了", "咳嗽", "打喷嚏", "没精神"),
        ),
    )


class KeywordLexicalSignalMatcher:
    """基于内置关键词规则的输入安全信号匹配器。"""

    def __init__(self, *, dictionary_version: str) -> None:
        """初始化关键词信号匹配器。

        :param dictionary_version: 本地词库版本。
        :return: None。
        :raises ValueError: 当词库版本为空时抛出。
        """

        if not dictionary_version.strip():
            raise ValueError("dictionary_version 不得为空")
        self._dictionary_version = dictionary_version.strip()
        self._rules = (*_saf01_rules(), *_saf03_rules(), *_marker_rules())

    def is_ready(self) -> bool:
        """判断关键词信号匹配器是否具备执行条件。

        :return: 若本地规则已加载则返回 True。
        """

        return bool(self._rules and self._dictionary_version)

    def match(
        self, request: VetInputAssessmentRequestDto
    ) -> list[InputSafetySignalDto]:
        """匹配单个子任务中的输入侧安全信号。

        :param request: 单个子任务输入安全评估请求。
        :return: 当前子任务检出的安全信号列表。
        """

        normalized_text = _normalize_text(request.task.normalized_query)
        signals: list[InputSafetySignalDto] = []
        seen_keys: set[tuple[SafetySignalCode, str]] = set()
        for rule in self._rules:
            matched_keyword = self._first_matched_keyword(
                normalized_text=normalized_text,
                rule=rule,
            )
            if matched_keyword is None:
                continue
            dedupe_key = (rule.code, rule.concept)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            signals.append(
                InputSafetySignalDto(
                    signal_id=_build_signal_id(
                        task_id=request.task.task_id,
                        code=rule.code,
                        concept=rule.concept,
                        index=len(signals),
                    ),
                    code=rule.code,
                    strength=rule.strength,
                    matched_text_hash=_hash_text(matched_keyword),
                    normalized_concept=rule.concept,
                    source=SignalSource.LEXICAL,
                    confidence=0.95,
                    dictionary_version=self._dictionary_version,
                )
            )
        return signals

    def _first_matched_keyword(
        self,
        *,
        normalized_text: str,
        rule: KeywordSignalRule,
    ) -> str | None:
        """读取当前规则在文本中的首个命中关键词。

        :param normalized_text: 已归一化的待匹配文本。
        :param rule: 当前关键词规则。
        :return: 命中的关键词；未命中时返回 None。
        """

        for keyword in rule.keywords:
            if keyword.lower() in normalized_text:
                return keyword
        return None


class KeywordSemanticRouteClassifier:
    """基于任务类型和关键词的轻量语义路由兜底分类器。"""

    def is_ready(self) -> bool:
        """判断轻量语义路由器是否具备执行条件。

        :return: 本地兜底分类器固定返回 True。
        """

        return True

    async def classify(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> list[SemanticRouteCandidateDto]:
        """对单个子任务进行轻量语义路由候选召回。

        :param request: 单个子任务输入安全评估请求。
        :return: 语义路由候选列表。
        """

        task_type = request.task.task_type
        text = _normalize_text(request.task.normalized_query)
        if task_type is VetTaskType.EDUCATION_QA or self._looks_educational(text):
            return [self._candidate(label="education", score=0.78, margin=0.2)]
        if task_type in {VetTaskType.NUTRITION, VetTaskType.BEHAVIOR, VetTaskType.CARE}:
            return [
                self._candidate(label="nonmedical_pet_care", score=0.76, margin=0.18)
            ]
        if task_type in {VetTaskType.REPORT_OCR, VetTaskType.RECORD_PARSE}:
            return [
                self._candidate(
                    label="lab_report_interpretation",
                    score=0.82,
                    margin=0.22,
                )
            ]
        if task_type is VetTaskType.TRIAGE:
            return [
                self._candidate(label="standard_consultation", score=0.74, margin=0.16)
            ]
        return [self._candidate(label="general_qa", score=0.52, margin=0.08)]

    def _looks_educational(self, text: str) -> bool:
        """判断文本是否呈现科普或通识问法。

        :param text: 已归一化的子任务文本。
        :return: 若文本包含科普问法标记则返回 True。
        """

        return any(
            marker in text for marker in ("为什么", "有哪些原因", "科普", "是什么")
        )

    def _candidate(
        self,
        *,
        label: str,
        score: float,
        margin: float,
    ) -> SemanticRouteCandidateDto:
        """构建轻量语义路由候选。

        :param label: 候选路由标签。
        :param score: 候选分数。
        :param margin: 首位候选间隔。
        :return: 语义路由候选 DTO。
        """

        return SemanticRouteCandidateDto(
            route_label=label,
            score=score,
            margin=margin,
            router_version=_ROUTER_VERSION,
        )


__all__: tuple[str, ...] = (
    "KeywordLexicalSignalMatcher",
    "KeywordSemanticRouteClassifier",
    "KeywordSignalRule",
)
