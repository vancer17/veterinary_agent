##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/service.py
# 作用: 实现 VetContextBuilder 多源并行读取、宠物隔离、事实合并、块编译、裁剪、校验与观测。
# 边界: 只编排公开端口并输出只读上下文，不调用 LLM、不写记忆、不执行 RAG 或用户可见回复生成。
##################################################################################################

import asyncio
from time import perf_counter
from typing import Protocol

from pydantic import JsonValue

from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
    VetContextBuilderSettings,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.vet_context_builder.blocks import VetPromptBlockCompiler
from veterinary_agent.vet_context_builder.compression import ContextBudgetManager
from veterinary_agent.vet_context_builder.dto import (
    ContextFactDto,
    ContextSourceLoadRequestDto,
    ContextSourceReadResultDto,
    ContextSourceRefDto,
    ContextTraceRecordDto,
    ContextTraceWriteResultDto,
    JsonMap,
    VetContextBuildRequestDto,
    VetContextBundleDto,
    VetPromptBlockDto,
)
from veterinary_agent.vet_context_builder.enums import (
    ContextBuildStatus,
    ContextCompressionStrategy,
    ContextSourceFreshness,
    ContextSourceStatus,
    ContextSourceType,
    ContextTraceWriteStatus,
    VetContextBuilderErrorCode,
    VetContextBuilderOperation,
    VetPromptBlockType,
)
from veterinary_agent.vet_context_builder.errors import VetContextBuilderError
from veterinary_agent.vet_context_builder.facts import (
    evaluate_slot_coverage,
    facts_from_session_state,
    resolve_context_facts,
)
from veterinary_agent.vet_context_builder.ports import ContextSourcePort
from veterinary_agent.vet_context_builder.trace import (
    TodoVetContextTraceSink,
    VetContextTraceSink,
)

_COMPONENT_NAME = "vet_context_builder"


class VetContextBuilder(Protocol):
    """VetContextBuilder 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断上下文构建服务是否具备执行条件。

        :return: 若 RuntimeConfig 可用且组件已启用则返回 True。
        """

        ...

    async def build(
        self,
        request: VetContextBuildRequestDto,
    ) -> VetContextBundleDto:
        """为单个兽医子任务构建生成前领域上下文。

        :param request: 单个子任务上下文构建请求。
        :return: 经宠物隔离、槽位计算和预算裁剪的上下文 bundle。
        :raises VetContextBuilderError: 当契约、配置或必需上下文不满足执行条件时抛出。
        """

        ...


class DefaultVetContextBuilder:
    """VetContextBuilder 默认确定性实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        source_ports: tuple[ContextSourcePort, ...],
        trace_sink: VetContextTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 VetContextBuilder 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param source_ports: 已规范化的上下文来源端口元组。
        :param trace_sink: 可选上下文构建摘要写入端口。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        :raises ValueError: 当来源端口类型重复或包含 current_task 时抛出。
        """

        source_port_map: dict[ContextSourceType, ContextSourcePort] = {}
        for source_port in source_ports:
            if source_port.source_type is ContextSourceType.CURRENT_TASK:
                raise ValueError("current_task 由 Builder 内部构建，不允许注册来源端口")
            if source_port.source_type in source_port_map:
                raise ValueError(f"上下文来源端口重复: {source_port.source_type.value}")
            source_port_map[source_port.source_type] = source_port
        self._runtime_config_provider = runtime_config_provider
        self._source_ports = source_port_map
        self._trace_sink = trace_sink or TodoVetContextTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断上下文构建服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 VetContextBuilder 已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.vet_context_builder.enabled

    async def build(
        self,
        request: VetContextBuildRequestDto,
    ) -> VetContextBundleDto:
        """为单个兽医子任务构建生成前领域上下文。

        :param request: 单个子任务上下文构建请求。
        :return: 经宠物隔离、槽位计算和预算裁剪的上下文 bundle。
        :raises VetContextBuilderError: 当契约、配置或必需上下文不满足执行条件时抛出。
        """

        started_monotonic = perf_counter()
        bundle: VetContextBundleDto | None = None
        try:
            snapshot = self._load_config_snapshot(request=request)
            settings = snapshot.vet_context_builder
            source_results = await self._load_sources(
                request=request,
                settings=settings,
            )
            filtered_results, degraded_reasons = self._enforce_pet_boundary(
                request=request,
                source_results=source_results,
            )
            observed_facts, observed_degraded = self._filter_observed_facts(
                request=request,
            )
            degraded_reasons.extend(observed_degraded)
            facts = list(observed_facts)
            for source_result in filtered_results:
                facts.extend(source_result.facts)
                if source_result.session_state is not None:
                    facts.extend(facts_from_session_state(source_result.session_state))
            fact_ledger = resolve_context_facts(facts)
            slot_coverage = evaluate_slot_coverage(
                task_id=request.task_id,
                task_type=request.task_type,
                fact_ledger=fact_ledger,
            )
            compiler = VetPromptBlockCompiler(settings=settings)
            compilation = compiler.compile(
                request=request,
                fact_ledger=fact_ledger,
                slot_coverage=slot_coverage,
                source_results=filtered_results,
            )
            compression = ContextBudgetManager(settings=settings).compress(
                request=request,
                candidates=compilation.blocks,
                truncated_block_ids=compilation.truncated_block_ids,
            )
            source_refs = self._collect_source_refs(
                source_results=filtered_results,
                prompt_blocks=compression.prompt_blocks,
            )
            status = self._resolve_build_status(
                source_results=filtered_results,
                degraded_reasons=degraded_reasons,
            )
            bundle = VetContextBundleDto(
                task_id=request.task_id,
                current_pet_id=request.current_pet_id,
                generation_profile=request.generation_profile,
                executor_key=request.executor_key,
                prompt_blocks=compression.prompt_blocks,
                fact_ledger=fact_ledger,
                slot_coverage=slot_coverage,
                source_refs=source_refs,
                compression_audit=compression.audit,
                status=status,
                degraded_reasons=list(dict.fromkeys(degraded_reasons)),
                core_fact_snapshot_version=self._core_snapshot_version(
                    source_refs=source_refs
                ),
                trace_delivery_status=ContextTraceWriteStatus.SKIPPED,
            )
            self._validate_bundle(request=request, bundle=bundle)
            trace_result = await self._write_trace_safely(
                request=request,
                bundle=bundle,
            )
            bundle = bundle.model_copy(
                update={"trace_delivery_status": trace_result.status}
            )
            return bundle
        except VetContextBuilderError:
            raise
        except RuntimeConfigError as exc:
            raise VetContextBuilderError(
                code=(VetContextBuilderErrorCode.CONTEXT_RUNTIME_CONFIG_UNAVAILABLE),
                operation=VetContextBuilderOperation.BUILD_CONTEXT,
                message="VetContextBuilder 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except Exception as exc:
            raise VetContextBuilderError(
                code=VetContextBuilderErrorCode.CONTEXT_INTERNAL_ERROR,
                operation=VetContextBuilderOperation.BUILD_CONTEXT,
                message="VetContextBuilder 发生未映射内部错误",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"exception_type": type(exc).__name__},
            ) from exc
        finally:
            self._record_observability(
                request=request,
                bundle=bundle,
                duration_seconds=perf_counter() - started_monotonic,
            )

    def _load_config_snapshot(
        self,
        *,
        request: VetContextBuildRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前构建请求使用的配置快照。

        :param request: 当前上下文构建请求。
        :return: 与请求版本一致且启用 VetContextBuilder 的配置快照。
        :raises VetContextBuilderError: 当组件未启用或请求版本与当前快照不一致时抛出。
        """

        snapshot = self._runtime_config_provider.current_snapshot()
        if not snapshot.vet_context_builder.enabled:
            raise VetContextBuilderError(
                code=VetContextBuilderErrorCode.CONTEXT_NOT_READY,
                operation=VetContextBuilderOperation.BUILD_CONTEXT,
                message="RuntimeConfig 已禁用 VetContextBuilder",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if (
            request.config_snapshot_id != snapshot.config_snapshot_id
            or request.params_version != snapshot.params_version
        ):
            raise VetContextBuilderError(
                code=VetContextBuilderErrorCode.CONTEXT_INVALID_REQUEST,
                operation=VetContextBuilderOperation.BUILD_CONTEXT,
                message="上下文构建请求与当前 RuntimeConfig 快照版本不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "expected_config_snapshot_id": snapshot.config_snapshot_id,
                    "actual_config_snapshot_id": request.config_snapshot_id,
                    "expected_params_version": snapshot.params_version,
                    "actual_params_version": request.params_version,
                },
            )
        return snapshot

    def _source_plan(
        self,
        *,
        request: VetContextBuildRequestDto,
    ) -> tuple[ContextSourceType, ...]:
        """根据压缩策略生成确定性来源读取计划。

        :param request: 当前上下文构建请求。
        :return: 按当前策略需要读取的来源类型元组。
        """

        if request.compression_strategy is ContextCompressionStrategy.SAFETY_MINIMAL:
            return (
                ContextSourceType.CORE_FACT_SNAPSHOT,
                ContextSourceType.PET_PROFILE,
            )
        if request.compression_strategy is ContextCompressionStrategy.EDUCATION_LIGHT:
            return (
                ContextSourceType.PET_PROFILE,
                ContextSourceType.CONVERSATION,
                ContextSourceType.OWNER_PREFERENCE,
            )
        sources = [
            ContextSourceType.CORE_FACT_SNAPSHOT,
            ContextSourceType.PET_PROFILE,
            ContextSourceType.CONVERSATION,
            ContextSourceType.CONFIRMED_LAB,
            ContextSourceType.OWNER_PREFERENCE,
        ]
        if request.session_state_snapshot is None:
            sources.append(ContextSourceType.CHECKPOINT)
        return tuple(sources)

    async def _load_sources(
        self,
        *,
        request: VetContextBuildRequestDto,
        settings: VetContextBuilderSettings,
    ) -> list[ContextSourceReadResultDto]:
        """在总超时和单来源超时内并行读取上下文来源。

        :param request: 当前上下文构建请求。
        :param settings: 当前 VetContextBuilder 配置。
        :return: 包含显式空、不可用或超时状态的来源结果列表。
        """

        plan = self._source_plan(request=request)
        source_request = ContextSourceLoadRequestDto(
            request_id=request.request_id,
            trace_id=request.trace_id,
            session_id=request.session_id,
            user_id=request.user_id,
            current_pet_id=request.current_pet_id,
            task_id=request.task_id,
            params_version=request.params_version,
            recent_message_limit=settings.recent_message_limit,
        )
        safety_path = (
            request.compression_strategy is ContextCompressionStrategy.SAFETY_MINIMAL
        )
        total_timeout = (
            settings.timeouts.safety_total_seconds
            if safety_path
            else settings.timeouts.total_seconds
        )
        source_timeout = (
            settings.timeouts.safety_source_seconds
            if safety_path
            else settings.timeouts.source_seconds
        )
        results: dict[ContextSourceType, ContextSourceReadResultDto] = {}
        try:
            async with asyncio.timeout(total_timeout):
                async with asyncio.TaskGroup() as task_group:
                    for source_type in plan:
                        task_group.create_task(
                            self._load_source_into(
                                source_type=source_type,
                                request=source_request,
                                timeout_seconds=source_timeout,
                                results=results,
                            ),
                            name=f"vet-context-source:{source_type.value}",
                        )
        except TimeoutError:
            pass
        for source_type in plan:
            results.setdefault(
                source_type,
                self._build_source_degraded_result(
                    source_type=source_type,
                    request=source_request,
                    status=ContextSourceStatus.TIMEOUT,
                    error_code="CONTEXT_SOURCE_TOTAL_TIMEOUT",
                    detail="上下文来源读取超过总超时",
                ),
            )
        if request.session_state_snapshot is not None:
            results[ContextSourceType.CHECKPOINT] = ContextSourceReadResultDto(
                source_type=ContextSourceType.CHECKPOINT,
                status=ContextSourceStatus.AVAILABLE,
                source_refs=[request.session_state_snapshot.source_ref],
                session_state=request.session_state_snapshot,
            )
        return [results[source_type] for source_type in sorted(results, key=str)]

    async def _load_source_into(
        self,
        *,
        source_type: ContextSourceType,
        request: ContextSourceLoadRequestDto,
        timeout_seconds: float,
        results: dict[ContextSourceType, ContextSourceReadResultDto],
    ) -> None:
        """读取单个来源并将结果写入当前请求局部结果表。

        :param source_type: 待读取的来源类型。
        :param request: 统一来源读取请求。
        :param timeout_seconds: 单来源读取超时。
        :param results: 当前请求局部来源结果表。
        :return: None。
        """

        source_port = self._source_ports.get(source_type)
        if source_port is None:
            results[source_type] = self._build_source_degraded_result(
                source_type=source_type,
                request=request,
                status=ContextSourceStatus.UNAVAILABLE,
                error_code="CONTEXT_SOURCE_PORT_MISSING",
                detail="上下文来源端口未注册",
            )
            return
        try:
            result = await asyncio.wait_for(
                source_port.load(request),
                timeout=timeout_seconds,
            )
            if result.source_type is not source_type:
                results[source_type] = self._build_source_degraded_result(
                    source_type=source_type,
                    request=request,
                    status=ContextSourceStatus.UNAVAILABLE,
                    error_code="CONTEXT_SOURCE_TYPE_MISMATCH",
                    detail="上下文来源端口返回了错误来源类型",
                )
            else:
                results[source_type] = result
        except TimeoutError:
            results[source_type] = self._build_source_degraded_result(
                source_type=source_type,
                request=request,
                status=ContextSourceStatus.TIMEOUT,
                error_code="CONTEXT_SOURCE_TIMEOUT",
                detail="上下文来源读取超时",
            )
        except Exception as exc:
            results[source_type] = self._build_source_degraded_result(
                source_type=source_type,
                request=request,
                status=ContextSourceStatus.UNAVAILABLE,
                error_code="CONTEXT_SOURCE_UNMAPPED_ERROR",
                detail=f"来源端口抛出 {type(exc).__name__}",
            )

    def _build_source_degraded_result(
        self,
        *,
        source_type: ContextSourceType,
        request: ContextSourceLoadRequestDto,
        status: ContextSourceStatus,
        error_code: str,
        detail: str,
    ) -> ContextSourceReadResultDto:
        """构建不包含业务正文的来源降级结果。

        :param source_type: 降级来源类型。
        :param request: 当前统一来源读取请求。
        :param status: 来源降级状态。
        :param error_code: 稳定来源错误码。
        :param detail: 不含业务正文的降级说明。
        :return: 标准来源降级结果。
        """

        source_ref = ContextSourceRefDto(
            source_type=source_type,
            source_id=f"degraded:{source_type.value}",
            pet_id=(
                None
                if source_type is ContextSourceType.OWNER_PREFERENCE
                else request.current_pet_id
            ),
            freshness=ContextSourceFreshness.UNKNOWN,
            status=status,
        )
        return ContextSourceReadResultDto(
            source_type=source_type,
            status=status,
            source_refs=[source_ref],
            error_code=error_code,
            detail=detail,
        )

    def _source_ref_matches_pet(
        self,
        *,
        source_ref: ContextSourceRefDto,
        current_pet_id: str,
    ) -> bool:
        """判断来源引用是否满足当前宠物边界。

        :param source_ref: 待校验的来源引用。
        :param current_pet_id: PetSessionPolicy 确认的当前宠物 ID。
        :return: 宠物级来源匹配，或主人偏好来源明确无宠物 ID 时返回 True。
        """

        if source_ref.source_type is ContextSourceType.OWNER_PREFERENCE:
            return source_ref.pet_id is None or source_ref.pet_id == current_pet_id
        return source_ref.pet_id == current_pet_id

    def _enforce_pet_boundary(
        self,
        *,
        request: VetContextBuildRequestDto,
        source_results: list[ContextSourceReadResultDto],
    ) -> tuple[list[ContextSourceReadResultDto], list[str]]:
        """过滤所有与 current_pet_id 不一致的来源内容。

        :param request: 当前上下文构建请求。
        :param source_results: 原始标准来源结果。
        :return: 过滤后的来源结果和稳定降级原因列表。
        """

        filtered_results: list[ContextSourceReadResultDto] = []
        degraded_reasons: list[str] = []
        for result in source_results:
            mismatch_detected = False
            source_refs: list[ContextSourceRefDto] = []
            for source_ref in result.source_refs:
                if self._source_ref_matches_pet(
                    source_ref=source_ref,
                    current_pet_id=request.current_pet_id,
                ):
                    source_refs.append(source_ref)
                else:
                    mismatch_detected = True
                    source_refs.append(
                        source_ref.model_copy(
                            update={"status": ContextSourceStatus.PET_MISMATCH}
                        )
                    )
            facts = [
                fact
                for fact in result.facts
                if self._source_ref_matches_pet(
                    source_ref=fact.source_ref,
                    current_pet_id=request.current_pet_id,
                )
            ]
            messages = [
                message
                for message in result.messages
                if message.pet_id == request.current_pet_id
                and self._source_ref_matches_pet(
                    source_ref=message.source_ref,
                    current_pet_id=request.current_pet_id,
                )
            ]
            summaries = [
                summary
                for summary in result.summaries
                if self._source_ref_matches_pet(
                    source_ref=summary.source_ref,
                    current_pet_id=request.current_pet_id,
                )
                and (
                    result.source_type is not ContextSourceType.CONFIRMED_LAB
                    or summary.confirmed
                )
            ]
            session_state = result.session_state
            if (
                session_state is not None
                and session_state.pet_id != request.current_pet_id
            ):
                mismatch_detected = True
                session_state = None
            if len(facts) != len(result.facts) or len(messages) != len(result.messages):
                mismatch_detected = True
            if len(summaries) != len(result.summaries) and any(
                not self._source_ref_matches_pet(
                    source_ref=summary.source_ref,
                    current_pet_id=request.current_pet_id,
                )
                for summary in result.summaries
            ):
                mismatch_detected = True
            status = result.status
            if mismatch_detected:
                degraded_reasons.append(f"{result.source_type.value}:pet_mismatch")
                if (
                    not facts
                    and not messages
                    and not summaries
                    and session_state is None
                ):
                    status = ContextSourceStatus.PET_MISMATCH
            if status in {
                ContextSourceStatus.UNAVAILABLE,
                ContextSourceStatus.TIMEOUT,
                ContextSourceStatus.PET_MISMATCH,
            }:
                reason = f"{result.source_type.value}:{status.value}"
                if result.error_code is not None:
                    reason = f"{reason}:{result.error_code}"
                degraded_reasons.append(reason)
            if any(
                source_ref.freshness is ContextSourceFreshness.STALE
                for source_ref in source_refs
            ):
                degraded_reasons.append(f"{result.source_type.value}:stale")
            filtered_results.append(
                result.model_copy(
                    update={
                        "status": status,
                        "source_refs": source_refs,
                        "facts": facts,
                        "messages": messages,
                        "summaries": summaries,
                        "session_state": session_state,
                    }
                )
            )
        return filtered_results, list(dict.fromkeys(degraded_reasons))

    def _filter_observed_facts(
        self,
        *,
        request: VetContextBuildRequestDto,
    ) -> tuple[list[ContextFactDto], list[str]]:
        """校验前置结构化抽取器提供的本轮事实边界。

        :param request: 当前上下文构建请求。
        :return: 可参与合并的本轮事实和稳定降级原因列表。
        """

        facts: list[ContextFactDto] = []
        degraded_reasons: list[str] = []
        for fact in request.observed_facts:
            if (
                fact.source_ref.source_type is ContextSourceType.CURRENT_TASK
                and fact.source_ref.pet_id == request.current_pet_id
            ):
                facts.append(fact)
            else:
                degraded_reasons.append("current_task:observed_fact_scope_invalid")
        return facts, degraded_reasons

    def _collect_source_refs(
        self,
        *,
        source_results: list[ContextSourceReadResultDto],
        prompt_blocks: list[VetPromptBlockDto],
    ) -> list[ContextSourceRefDto]:
        """汇总并去重构建过程涉及的来源引用。

        :param source_results: 已过滤来源结果。
        :param prompt_blocks: 最终保留的 prompt 块。
        :return: 按首次出现顺序去重的来源引用列表。
        """

        references = [
            *(
                source_ref
                for result in source_results
                for source_ref in result.source_refs
            ),
            *(
                source_ref
                for block in prompt_blocks
                for source_ref in block.source_refs
            ),
        ]
        deduplicated: dict[tuple[ContextSourceType, str], ContextSourceRefDto] = {}
        for source_ref in references:
            deduplicated.setdefault(
                (source_ref.source_type, source_ref.source_id),
                source_ref,
            )
        return list(deduplicated.values())

    def _resolve_build_status(
        self,
        *,
        source_results: list[ContextSourceReadResultDto],
        degraded_reasons: list[str],
    ) -> ContextBuildStatus:
        """根据来源可用性和降级原因解析构建状态。

        :param source_results: 已过滤来源结果。
        :param degraded_reasons: 当前稳定降级原因列表。
        :return: full、degraded 或 minimal 构建状态。
        """

        if source_results and not any(
            result.status is ContextSourceStatus.AVAILABLE for result in source_results
        ):
            return ContextBuildStatus.MINIMAL
        if degraded_reasons:
            return ContextBuildStatus.DEGRADED
        return ContextBuildStatus.FULL

    def _core_snapshot_version(
        self,
        *,
        source_refs: list[ContextSourceRefDto],
    ) -> str | None:
        """读取最终上下文命中的核心事实快照版本。

        :param source_refs: 当前构建涉及的全部来源引用。
        :return: 首个可用核心事实快照版本；不存在时返回 None。
        """

        return next(
            (
                source_ref.version
                for source_ref in source_refs
                if source_ref.source_type is ContextSourceType.CORE_FACT_SNAPSHOT
                and source_ref.status is ContextSourceStatus.AVAILABLE
                and source_ref.version is not None
            ),
            None,
        )

    def _validate_bundle(
        self,
        *,
        request: VetContextBuildRequestDto,
        bundle: VetContextBundleDto,
    ) -> None:
        """执行 VetContextBundle 最终领域不变量校验。

        :param request: 当前上下文构建请求。
        :param bundle: 待校验的上下文 bundle。
        :return: None。
        :raises VetContextBuilderError: 当 bundle 为空、缺少必需块、块 ID 重复或来源越界时抛出。
        """

        block_ids = [block.block_id for block in bundle.prompt_blocks]
        block_types = {block.block_type for block in bundle.prompt_blocks}
        required_types = {VetPromptBlockType.TASK_INPUT}
        if request.compression_strategy in {
            ContextCompressionStrategy.SINGLE_FULL,
            ContextCompressionStrategy.SAFETY_MINIMAL,
        }:
            required_types.add(VetPromptBlockType.PET_PROFILE_P0)
        if request.compression_strategy is ContextCompressionStrategy.SINGLE_FULL:
            required_types.add(VetPromptBlockType.SLOT_COVERAGE)
        if request.compression_strategy is ContextCompressionStrategy.SAFETY_MINIMAL:
            required_types.add(VetPromptBlockType.SAFETY_ASSESSMENT)
        invalid_refs = [
            source_ref
            for source_ref in bundle.source_refs
            if source_ref.status is not ContextSourceStatus.PET_MISMATCH
            and not self._source_ref_matches_pet(
                source_ref=source_ref,
                current_pet_id=request.current_pet_id,
            )
        ]
        if (
            not bundle.prompt_blocks
            or len(block_ids) != len(set(block_ids))
            or not required_types.issubset(block_types)
            or invalid_refs
        ):
            missing_block_types: list[JsonValue] = [
                block_type.value
                for block_type in sorted(
                    required_types.difference(block_types),
                    key=str,
                )
            ]
            conflict_summary: JsonMap = {
                "prompt_block_count": len(bundle.prompt_blocks),
                "duplicate_block_ids": len(block_ids) != len(set(block_ids)),
                "missing_block_types": missing_block_types,
                "invalid_source_ref_count": len(invalid_refs),
            }
            raise VetContextBuilderError(
                code=VetContextBuilderErrorCode.CONTEXT_EMPTY_BUNDLE,
                operation=VetContextBuilderOperation.VALIDATE_BUNDLE,
                message="VetContextBundle 最终领域不变量校验失败",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with=conflict_summary,
            )

    async def _write_trace_safely(
        self,
        *,
        request: VetContextBuildRequestDto,
        bundle: VetContextBundleDto,
    ) -> ContextTraceWriteResultDto:
        """写入脱敏上下文摘要并将异常转换为降级结果。

        :param request: 当前上下文构建请求。
        :param bundle: 已通过最终校验的上下文 bundle。
        :return: trace 写入结果或显式降级结果。
        """

        source_types = list(
            dict.fromkeys(source_ref.source_type for source_ref in bundle.source_refs)
        )
        record = ContextTraceRecordDto(
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=request.run_id,
            session_id=request.session_id,
            user_id=request.user_id,
            pet_id=request.current_pet_id,
            task_id=request.task_id,
            audit_tier=request.audit_tier,
            generation_profile=request.generation_profile,
            executor_key=request.executor_key,
            status=bundle.status,
            compression_audit=bundle.compression_audit,
            source_types=source_types,
            block_hashes={
                block.block_id: block.content_hash for block in bundle.prompt_blocks
            },
            degraded_reasons=bundle.degraded_reasons,
            params_version=request.params_version,
            config_snapshot_id=request.config_snapshot_id,
        )
        try:
            return await self._trace_sink.write_context_summary(record)
        except Exception as exc:
            return ContextTraceWriteResultDto(
                status=ContextTraceWriteStatus.DEGRADED,
                error_code="VET_CONTEXT_TRACE_WRITE_FAILED",
                retryable=True,
                detail=f"trace sink 抛出 {type(exc).__name__}",
            )

    def _record_observability(
        self,
        *,
        request: VetContextBuildRequestDto,
        bundle: VetContextBundleDto | None,
        duration_seconds: float,
    ) -> None:
        """记录上下文构建指标和不含业务正文的结构化事件。

        :param request: 当前上下文构建请求。
        :param bundle: 构建成功时的 bundle；失败时为空。
        :param duration_seconds: 本次构建耗时，单位为秒。
        :return: None。
        """

        provider = self._observability_provider
        if provider is None:
            return
        try:
            status = bundle.status.value if bundle is not None else "failed"
            profile = (
                request.generation_profile.value
                if request.generation_profile is not None
                else "none"
            )
            labels = {
                "component": _COMPONENT_NAME,
                "status": status,
                "generation_profile": profile,
                "compression_strategy": request.compression_strategy.value,
            }
            provider.record_metric(
                metric_name="vet_context_builder_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels=labels,
                description="VetContextBuilder 构建总数。",
            )
            provider.record_metric(
                metric_name="vet_context_builder_duration_seconds",
                value=duration_seconds,
                metric_type=MetricType.HISTOGRAM,
                labels=labels,
                description="VetContextBuilder 构建耗时，单位为秒。",
            )
            provider.record_event(
                event_name="vet_context_builder.finished",
                component=_COMPONENT_NAME,
                level=(
                    StructuredLogLevel.INFO
                    if bundle is not None
                    else StructuredLogLevel.ERROR
                ),
                safe_fields={
                    "status": status,
                    "generation_profile": profile,
                    "compression_strategy": request.compression_strategy.value,
                    "prompt_block_count": (
                        len(bundle.prompt_blocks) if bundle is not None else 0
                    ),
                    "estimated_context_units": (
                        bundle.compression_audit.estimated_tokens
                        if bundle is not None
                        else 0
                    ),
                    "degraded_reason_count": (
                        len(bundle.degraded_reasons) if bundle is not None else 0
                    ),
                    "duration_ms": round(duration_seconds * 1000, 3),
                },
            )
        except Exception:
            return


def create_default_vet_context_builder(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    source_ports: tuple[ContextSourcePort, ...],
    trace_sink: VetContextTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> VetContextBuilder:
    """创建默认 VetContextBuilder 应用内服务。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param source_ports: 已规范化的上下文来源端口元组。
    :param trace_sink: 可选上下文构建摘要写入端口。
    :param observability_provider: 可选 Observability provider。
    :return: 已完成依赖装配的默认 VetContextBuilder。
    """

    return DefaultVetContextBuilder(
        runtime_config_provider=runtime_config_provider,
        source_ports=source_ports,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultVetContextBuilder",
    "VetContextBuilder",
    "create_default_vet_context_builder",
)
