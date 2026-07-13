##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/blocks.py
# 作用: 将任务、事实账本、session 状态、近期消息和确认摘要编译为受控 VetPromptBlock。
# 边界: 只编译和估算候选块，不决定最终 token 预算、不读取外部来源或写入逻辑链。
##################################################################################################

from dataclasses import dataclass
from functools import partial
from hashlib import sha256
import json

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import (
    count_tokens_approximately,
    trim_messages,
)
from veterinary_agent.config import VetContextBuilderSettings
from veterinary_agent.vet_context_builder.dto import (
    ContextMessageDto,
    ContextSourceReadResultDto,
    ContextSourceRefDto,
    JsonMap,
    ResolvedContextFactDto,
    SessionContextStateDto,
    SlotCoverageDto,
    VetContextBuildRequestDto,
    VetPromptBlockDto,
)
from veterinary_agent.vet_context_builder.enums import (
    ContextCompressionStrategy,
    ContextSourceFreshness,
    ContextSourceStatus,
    ContextSourceType,
    VetGenerationProfile,
    VetPromptBlockPriority,
    VetPromptBlockType,
)


@dataclass(frozen=True, slots=True)
class BlockCompilationResult:
    """prompt 候选块编译结果。"""

    blocks: list[VetPromptBlockDto]
    truncated_block_ids: list[str]


def _stable_json(value: object) -> str:
    """将结构化值渲染为稳定 JSON 文本。

    :param value: 待渲染的结构化值。
    :return: UTF-8 字符、稳定 key 顺序和紧凑分隔符组成的 JSON 文本。
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _content_hash(content: str) -> str:
    """计算 prompt 块正文 hash。

    :param content: 待计算 hash 的块正文。
    :return: 带 sha256 前缀的十六进制摘要。
    """

    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"


def _bounded_text(value: str, *, max_chars: int) -> tuple[str, bool]:
    """对超长文本执行保留首尾的确定性有界裁剪。

    :param value: 原始文本。
    :param max_chars: 允许保留的最大字符数。
    :return: 有界文本与是否发生裁剪的标记。
    """

    if len(value) <= max_chars:
        return value, False
    marker = "\n...[上下文输入已按字符上限裁剪]...\n"
    available_chars = max(max_chars - len(marker), 2)
    prefix_chars = available_chars // 2
    suffix_chars = available_chars - prefix_chars
    return f"{value[:prefix_chars]}{marker}{value[-suffix_chars:]}", True


def _deduplicate_source_refs(
    source_refs: list[ContextSourceRefDto],
) -> list[ContextSourceRefDto]:
    """按来源类型和来源 ID 去重来源引用。

    :param source_refs: 原始来源引用列表。
    :return: 保持首次出现顺序的来源引用列表。
    """

    deduplicated: dict[tuple[ContextSourceType, str], ContextSourceRefDto] = {}
    for source_ref in source_refs:
        deduplicated.setdefault(
            (source_ref.source_type, source_ref.source_id),
            source_ref,
        )
    return list(deduplicated.values())


def _message_sequence_key(message: ContextMessageDto) -> int:
    """读取近期消息排序使用的会话序号。

    :param message: 待排序的标准近期消息。
    :return: 消息在当前会话中的单调递增序号。
    """

    return message.sequence_no


class VetPromptBlockCompiler:
    """确定性 VetPromptBlock 候选编译器。"""

    def __init__(self, *, settings: VetContextBuilderSettings) -> None:
        """初始化 prompt 块编译器。

        :param settings: 当前 VetContextBuilder 配置。
        :return: None。
        """

        self._settings = settings

    def compile(
        self,
        *,
        request: VetContextBuildRequestDto,
        fact_ledger: list[ResolvedContextFactDto],
        slot_coverage: SlotCoverageDto,
        source_results: list[ContextSourceReadResultDto],
    ) -> BlockCompilationResult:
        """编译当前任务的全部 prompt 候选块。

        :param request: 单个子任务上下文构建请求。
        :param fact_ledger: 已完成来源合并的事实账本。
        :param slot_coverage: 当前子任务槽位覆盖。
        :param source_results: 已通过宠物边界过滤的来源结果。
        :return: 候选块和块内有界裁剪摘要。
        """

        blocks: list[VetPromptBlockDto] = []
        truncated_block_ids: list[str] = []
        task_block, task_truncated = self._build_task_input_block(request=request)
        blocks.append(task_block)
        if task_truncated:
            truncated_block_ids.append(task_block.block_id)

        p0_block = self._build_p0_block(request=request, fact_ledger=fact_ledger)
        if p0_block is not None:
            blocks.append(p0_block)
        safety_block = self._build_safety_block(request=request)
        if safety_block is not None:
            blocks.append(safety_block)
        core_block = self._build_fact_block(
            request=request,
            fact_ledger=fact_ledger,
            source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
            block_type=VetPromptBlockType.CORE_FACT_SNAPSHOT,
            priority=VetPromptBlockPriority.P1,
        )
        if core_block is not None:
            blocks.append(core_block)
        session_state = self._find_session_state(source_results=source_results)
        if session_state is not None:
            blocks.extend(
                self._build_session_blocks(
                    request=request,
                    session_state=session_state,
                )
            )
        recent_block = self._build_recent_messages_block(
            request=request,
            source_results=source_results,
        )
        if recent_block is not None:
            blocks.append(recent_block)
        lab_block = self._build_summary_block(
            request=request,
            source_results=source_results,
            source_type=ContextSourceType.CONFIRMED_LAB,
            block_type=VetPromptBlockType.CONFIRMED_LAB_SUMMARY,
            priority=VetPromptBlockPriority.P1,
        )
        if lab_block is not None:
            blocks.append(lab_block)
        owner_block = self._build_fact_block(
            request=request,
            fact_ledger=fact_ledger,
            source_type=ContextSourceType.OWNER_PREFERENCE,
            block_type=VetPromptBlockType.OWNER_PREFERENCE,
            priority=VetPromptBlockPriority.P3,
        )
        if owner_block is not None:
            blocks.append(owner_block)
        blocks.append(
            self._build_slot_coverage_block(
                request=request,
                slot_coverage=slot_coverage,
            )
        )
        return BlockCompilationResult(
            blocks=blocks,
            truncated_block_ids=truncated_block_ids,
        )

    def _build_block(
        self,
        *,
        request: VetContextBuildRequestDto,
        block_type: VetPromptBlockType,
        priority: VetPromptBlockPriority,
        required: bool,
        payload: object,
        source_refs: list[ContextSourceRefDto],
        metadata: JsonMap | None = None,
    ) -> VetPromptBlockDto:
        """根据结构化负载构建单个稳定 prompt 块。

        :param request: 当前上下文构建请求。
        :param block_type: 受控块类型。
        :param priority: 块保留优先级。
        :param required: 当前策略下是否必须保留该块。
        :param payload: 待渲染的受控结构化负载。
        :param source_refs: 块关联的来源引用。
        :param metadata: 可选普通块元信息。
        :return: 带稳定 ID、hash 和 token 估算的 prompt 块。
        """

        content = _stable_json(payload)
        token_estimate = count_tokens_approximately(
            [content],
            chars_per_token=self._settings.chars_per_token,
            extra_tokens_per_message=0,
        )
        return VetPromptBlockDto(
            block_id=f"{request.task_id}:{block_type.value}",
            block_type=block_type,
            priority=priority,
            required=required,
            content_ref_or_text=content,
            content_hash=_content_hash(content),
            token_estimate=token_estimate,
            source_refs=_deduplicate_source_refs(source_refs),
            metadata=metadata or {},
        )

    def _build_task_input_block(
        self,
        *,
        request: VetContextBuildRequestDto,
    ) -> tuple[VetPromptBlockDto, bool]:
        """构建当前子任务输入块。

        :param request: 当前上下文构建请求。
        :return: 必需 task_input 块和是否执行字符裁剪的标记。
        """

        bounded_query, truncated = _bounded_text(
            request.normalized_query,
            max_chars=self._settings.max_task_input_chars,
        )
        source_ref = ContextSourceRefDto(
            source_type=ContextSourceType.CURRENT_TASK,
            source_id=request.task_id,
            pet_id=request.current_pet_id,
            freshness=ContextSourceFreshness.FRESH,
            status=ContextSourceStatus.AVAILABLE,
        )
        block = self._build_block(
            request=request,
            block_type=VetPromptBlockType.TASK_INPUT,
            priority=VetPromptBlockPriority.P0,
            required=True,
            payload={
                "task_id": request.task_id,
                "task_type": request.task_type,
                "normalized_query": bounded_query,
                "truncated": truncated,
            },
            source_refs=[source_ref],
            metadata={"truncated": truncated},
        )
        return block, truncated

    def _build_p0_block(
        self,
        *,
        request: VetContextBuildRequestDto,
        fact_ledger: list[ResolvedContextFactDto],
    ) -> VetPromptBlockDto | None:
        """构建宠物 P0 基础事实块。

        :param request: 当前上下文构建请求。
        :param fact_ledger: 当前事实账本。
        :return: P0 基础事实块；纯科普且无任何 P0 事实时返回 None。
        """

        by_key = {fact.key: fact for fact in fact_ledger}
        selected = {
            key: {
                "value": by_key[key].value,
                "state": by_key[key].state.value,
            }
            for key in self._settings.p0_fields
            if key in by_key
        }
        missing = [key for key in self._settings.p0_fields if key not in by_key]
        required = request.compression_strategy in {
            ContextCompressionStrategy.SINGLE_FULL,
            ContextCompressionStrategy.SAFETY_MINIMAL,
        }
        if not selected and not required:
            return None
        source_refs = [
            source_ref for key in selected for source_ref in by_key[key].source_refs
        ]
        return self._build_block(
            request=request,
            block_type=VetPromptBlockType.PET_PROFILE_P0,
            priority=VetPromptBlockPriority.P0,
            required=required,
            payload={"facts": selected, "missing_fields": missing},
            source_refs=source_refs,
        )

    def _build_safety_block(
        self,
        *,
        request: VetContextBuildRequestDto,
    ) -> VetPromptBlockDto | None:
        """构建输入安全评估摘要块。

        :param request: 当前上下文构建请求。
        :return: 受控安全摘要块；无剖面且无摘要时返回 None。
        """

        if request.generation_profile is None and not request.assessment_summary:
            return None
        allowed_keys = {
            "signals",
            "intent",
            "intent_confidence",
            "risk_level",
            "disambiguation_method",
            "fallback_used",
        }
        assessment = {
            key: value
            for key, value in request.assessment_summary.items()
            if key in allowed_keys
        }
        source_ref = ContextSourceRefDto(
            source_type=ContextSourceType.CURRENT_TASK,
            source_id=f"{request.task_id}:assessment",
            pet_id=request.current_pet_id,
            freshness=ContextSourceFreshness.FRESH,
            status=ContextSourceStatus.AVAILABLE,
        )
        return self._build_block(
            request=request,
            block_type=VetPromptBlockType.SAFETY_ASSESSMENT,
            priority=VetPromptBlockPriority.P0,
            required=(
                request.generation_profile is VetGenerationProfile.SAFETY_TRIGGER
            ),
            payload={
                "route": request.route,
                "generation_profile": (
                    request.generation_profile.value
                    if request.generation_profile is not None
                    else None
                ),
                "executor_key": request.executor_key.value,
                "assessment": assessment,
            },
            source_refs=[source_ref],
        )

    def _build_fact_block(
        self,
        *,
        request: VetContextBuildRequestDto,
        fact_ledger: list[ResolvedContextFactDto],
        source_type: ContextSourceType,
        block_type: VetPromptBlockType,
        priority: VetPromptBlockPriority,
    ) -> VetPromptBlockDto | None:
        """构建指定权威来源的事实块。

        :param request: 当前上下文构建请求。
        :param fact_ledger: 当前事实账本。
        :param source_type: 需要选择的权威来源类型。
        :param block_type: 输出块类型。
        :param priority: 输出块优先级。
        :return: 命中事实时返回 prompt 块，否则返回 None。
        """

        selected = [
            fact
            for fact in fact_ledger
            if fact.source_refs[0].source_type is source_type
            and fact.key not in self._settings.p0_fields
        ]
        if not selected:
            return None
        payload = {
            fact.key: {"value": fact.value, "state": fact.state.value}
            for fact in selected
        }
        source_refs = [
            source_ref for fact in selected for source_ref in fact.source_refs
        ]
        return self._build_block(
            request=request,
            block_type=block_type,
            priority=priority,
            required=False,
            payload=payload,
            source_refs=source_refs,
        )

    def _find_session_state(
        self,
        *,
        source_results: list[ContextSourceReadResultDto],
    ) -> SessionContextStateDto | None:
        """从来源结果中选择 session 状态。

        :param source_results: 已通过宠物边界过滤的来源结果。
        :return: 首个可用 session 状态；不存在时返回 None。
        """

        return next(
            (
                result.session_state
                for result in source_results
                if result.session_state is not None
            ),
            None,
        )

    def _build_session_blocks(
        self,
        *,
        request: VetContextBuildRequestDto,
        session_state: SessionContextStateDto,
    ) -> list[VetPromptBlockDto]:
        """构建 session 状态与 rolling summary 引用块。

        :param request: 当前上下文构建请求。
        :param session_state: 当前 session 状态快照。
        :return: 一个 session_state 块和可选 rolling_summary 块。
        """

        blocks = [
            self._build_block(
                request=request,
                block_type=VetPromptBlockType.SESSION_STATE,
                priority=VetPromptBlockPriority.P1,
                required=False,
                payload={
                    "current_complaint_type": session_state.current_complaint_type,
                    "slot_progress": session_state.slot_progress,
                    "checkpoint_id": session_state.checkpoint_id,
                    "checkpoint_version": session_state.checkpoint_version,
                },
                source_refs=[session_state.source_ref],
            )
        ]
        if session_state.rolling_summary_ref is not None:
            blocks.append(
                self._build_block(
                    request=request,
                    block_type=VetPromptBlockType.ROLLING_SUMMARY,
                    priority=VetPromptBlockPriority.P2,
                    required=False,
                    payload={
                        "rolling_summary_ref": session_state.rolling_summary_ref,
                    },
                    source_refs=[session_state.source_ref],
                )
            )
        return blocks

    def _to_langchain_message(self, message: ContextMessageDto) -> BaseMessage:
        """将标准近期消息转换为 LangChain 消息。

        :param message: 标准近期消息。
        :return: 保留消息 ID 和角色的 LangChain 消息。
        """

        if message.role == "user":
            return HumanMessage(content=message.content, id=message.message_id)
        if message.role == "assistant":
            return AIMessage(content=message.content, id=message.message_id)
        return SystemMessage(content=message.content, id=message.message_id)

    def _build_recent_messages_block(
        self,
        *,
        request: VetContextBuildRequestDto,
        source_results: list[ContextSourceReadResultDto],
    ) -> VetPromptBlockDto | None:
        """构建经 LangChain 保持角色结构裁剪的近期消息块。

        :param request: 当前上下文构建请求。
        :param source_results: 已通过宠物边界过滤的来源结果。
        :return: 非空近期消息块；无可用消息时返回 None。
        """

        messages = sorted(
            (message for result in source_results for message in result.messages),
            key=_message_sequence_key,
        )
        if not messages:
            return None
        langchain_messages = [
            self._to_langchain_message(message) for message in messages
        ]
        token_counter = partial(
            count_tokens_approximately,
            chars_per_token=self._settings.chars_per_token,
        )
        trimmed = trim_messages(
            langchain_messages,
            max_tokens=self._settings.recent_message_token_budget,
            token_counter=token_counter,
            strategy="last",
            allow_partial=False,
            start_on="human",
            include_system=False,
        )
        retained_ids = {message.id for message in trimmed if message.id is not None}
        retained = [
            message for message in messages if message.message_id in retained_ids
        ]
        if not retained:
            return None
        payload = [
            {
                "message_id": message.message_id,
                "role": message.role,
                "content": message.content,
                "sequence_no": message.sequence_no,
            }
            for message in retained
        ]
        return self._build_block(
            request=request,
            block_type=VetPromptBlockType.RECENT_MESSAGES,
            priority=VetPromptBlockPriority.P2,
            required=False,
            payload=payload,
            source_refs=[message.source_ref for message in retained],
        )

    def _build_summary_block(
        self,
        *,
        request: VetContextBuildRequestDto,
        source_results: list[ContextSourceReadResultDto],
        source_type: ContextSourceType,
        block_type: VetPromptBlockType,
        priority: VetPromptBlockPriority,
    ) -> VetPromptBlockDto | None:
        """构建指定来源类型的已确认摘要块。

        :param request: 当前上下文构建请求。
        :param source_results: 已通过宠物边界过滤的来源结果。
        :param source_type: 需要选择的摘要来源类型。
        :param block_type: 输出块类型。
        :param priority: 输出块优先级。
        :return: 已确认摘要非空时返回 prompt 块，否则返回 None。
        """

        summaries = [
            summary
            for result in source_results
            if result.source_type is source_type
            for summary in result.summaries
            if summary.confirmed
        ]
        if not summaries:
            return None
        payload = [
            {
                "summary_id": summary.summary_id,
                "summary_type": summary.summary_type,
                "content": summary.content,
            }
            for summary in summaries
        ]
        return self._build_block(
            request=request,
            block_type=block_type,
            priority=priority,
            required=False,
            payload=payload,
            source_refs=[summary.source_ref for summary in summaries],
        )

    def _build_slot_coverage_block(
        self,
        *,
        request: VetContextBuildRequestDto,
        slot_coverage: SlotCoverageDto,
    ) -> VetPromptBlockDto:
        """构建由 bundle 同源生成的 slot_coverage prompt 块。

        :param request: 当前上下文构建请求。
        :param slot_coverage: 当前子任务槽位覆盖。
        :return: 受控 slot_coverage 块。
        """

        required = request.generation_profile is VetGenerationProfile.STANDARD
        return self._build_block(
            request=request,
            block_type=VetPromptBlockType.SLOT_COVERAGE,
            priority=(
                VetPromptBlockPriority.P0 if required else VetPromptBlockPriority.P2
            ),
            required=required,
            payload=slot_coverage.model_dump(mode="json"),
            source_refs=[],
        )


__all__: tuple[str, ...] = (
    "BlockCompilationResult",
    "VetPromptBlockCompiler",
)
