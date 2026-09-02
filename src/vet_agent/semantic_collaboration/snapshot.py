"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/snapshot.py
作用：实现受限语义协作 DAG M02 TurnSnapshot 构建器与受限投影器。
范围：覆盖来源端口协议、显式来源读取、确定性预算计量、硬预算门禁、
      canonical digest 构建、snapshot 组装以及按 SkillSpec 的资源投影。
说明：本文件不调用 LLM、不访问数据库、不读取问诊状态或临床安全结果，
      也不通过截断、丢弃、摘要或压缩上下文来绕过预算失败。
维护：生产来源适配器由后续 Plan / Scheduler 生产组合根显式接入；在
      M11 verified artifact 与生产接入完成前，不提供默认空实现或隐式回退。
=============================================================================
"""

from __future__ import annotations

from typing import Any, Protocol

from .contracts import (
    DOMAIN_ISOLATED_CONTEXT_RESOURCES,
    TURN_SNAPSHOT_CONTEXT_RESOURCES,
    ContextContract,
    SkillContextResource,
)
from .errors import (
    SemanticCollaborationError,
    TurnSnapshotBudgetExceededError,
    TurnSnapshotContextPolicyViolationError,
    TurnSnapshotSourceUnavailableError,
    UnsupportedTurnInputError,
)
from .snapshot_contracts import (
    BoundedHistoryReadResult,
    OriginalUserText,
    TrustedPetContext,
    TurnSnapshot,
    TurnSnapshotBudget,
    TurnSnapshotBuildRequest,
    TurnSnapshotBuildResult,
    TurnSnapshotProjection,
    TurnSnapshotSourceRequest,
    TurnSnapshotUsage,
    VerifiedPriorFactSummary,
    canonical_turn_snapshot_json,
    compute_turn_snapshot_digest,
)


class BoundedConversationHistoryReader(Protocol):
    """表示上一轮受限历史的读取端口。

    :return: 无返回值；实现必须自行保证不读取问诊状态或长期记忆。
    TODO: Phase 7 前由会话回合仓储提供适配器；禁止用默认追问模板替代。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> BoundedHistoryReadResult:
        """读取上一轮助手追问的显式结果。

        :param request: 不携带当前原文的来源读取请求。
        :return: 返回成功读取后的受限历史结果。
        """


class VerifiedPriorFactReader(Protocol):
    """表示已验证历史事实摘要的读取端口。

    :return: 无返回值；实现只能消费已验证语义 artifact，不得现场推断事实。
    TODO: M11 verified claim artifact 就绪前保持空端口，不伪造空事实。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> VerifiedPriorFactSummary:
        """读取已验证历史事实摘要。

        :param request: 不携带当前原文的来源读取请求。
        :return: 返回成功读取后的事实摘要状态。
        """


class TrustedPetContextReader(Protocol):
    """表示服务端可信宠物上下文读取端口。

    :return: 无返回值；实现必须排除请求侧自报宠物资料。
    TODO: Phase 7 前由现有服务端可信宠物上下文边界提供白名单适配器。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> TrustedPetContext:
        """读取服务端可信宠物上下文。

        :param request: 不携带当前原文的来源读取请求。
        :return: 返回白名单可信宠物上下文投影。
        """


def _context_resource_sort_key(resource: SkillContextResource) -> str:
    """读取上下文资源枚举的稳定排序键。

    :param resource: 待排序的受限上下文资源。
    :return: 返回资源枚举值字符串。
    """
    return resource.value


class TurnSnapshotBuilder:
    """从受限来源端口组装不可变 TurnSnapshot。

    :return: 无返回值；该构建器是 M02 的确定性编排边界。
    """

    def __init__(
        self,
        *,
        history_reader: BoundedConversationHistoryReader,
        prior_fact_reader: VerifiedPriorFactReader,
        pet_context_reader: TrustedPetContextReader,
        budget: TurnSnapshotBudget,
    ) -> None:
        """初始化 TurnSnapshot 构建器和硬预算。

        :param history_reader: 上一轮追问读取端口。
        :param prior_fact_reader: 已验证事实摘要读取端口。
        :param pet_context_reader: 服务端可信宠物上下文读取端口。
        :param budget: TurnSnapshot 全局硬预算。
        :return: 无返回值。
        """
        self.history_reader = history_reader
        self.prior_fact_reader = prior_fact_reader
        self.pet_context_reader = pet_context_reader
        self.budget = budget

    async def build(self, request: TurnSnapshotBuildRequest) -> TurnSnapshotBuildResult:
        """构建一次用户回合的不可变受限上下文。

        :param request: 当前回合构建请求。
        :return: 返回 TurnSnapshot 与确定性预算占用。
        :raises UnsupportedTurnInputError: 当前契约不支持附件输入时抛出。
        :raises TurnSnapshotSourceUnavailableError: 任一受限来源读取失败时抛出。
        :raises TurnSnapshotBudgetExceededError: 任一权威上下文超过硬预算时抛出。
        """
        if request.attachment_count != 0:
            raise UnsupportedTurnInputError(
                "semantic collaboration M02 currently supports text-only turns only",
            )
        source_request = request.source_request()
        history = await self._read_history(source_request)
        prior_facts = await self._read_prior_facts(source_request)
        pet_context = await self._read_pet_context(source_request)
        usage = self._measure_usage(
            original_user_text=request.original_user_text,
            history=history,
            prior_facts=prior_facts,
            pet_context=pet_context,
        )
        self._enforce_budget(usage)
        snapshot = self._build_snapshot(
            request=request,
            history=history,
            prior_facts=prior_facts,
            pet_context=pet_context,
        )
        return TurnSnapshotBuildResult(snapshot=snapshot, usage=usage)

    async def _read_history(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> BoundedHistoryReadResult:
        """读取上一轮受限历史并包装来源失败。

        :param request: 不携带当前原文的来源读取请求。
        :return: 返回上一轮受限历史结果。
        :raises TurnSnapshotSourceUnavailableError: 来源读取或契约校验失败时抛出。
        """
        try:
            return await self.history_reader.read(request)
        except SemanticCollaborationError:
            raise
        except Exception as error:
            raise TurnSnapshotSourceUnavailableError(
                "bounded conversation history source is unavailable",
                source_name="bounded_conversation_history",
            ) from error

    async def _read_prior_facts(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> VerifiedPriorFactSummary:
        """读取已验证历史事实并包装来源失败。

        :param request: 不携带当前原文的来源读取请求。
        :return: 返回已验证事实摘要。
        :raises TurnSnapshotSourceUnavailableError: 来源读取或契约校验失败时抛出。
        """
        try:
            return await self.prior_fact_reader.read(request)
        except SemanticCollaborationError:
            raise
        except Exception as error:
            raise TurnSnapshotSourceUnavailableError(
                "verified prior fact source is unavailable",
                source_name="verified_prior_fact_summary",
            ) from error

    async def _read_pet_context(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> TrustedPetContext:
        """读取可信宠物上下文并包装来源失败。

        :param request: 不携带当前原文的来源读取请求。
        :return: 返回白名单可信宠物上下文。
        :raises TurnSnapshotSourceUnavailableError: 来源读取或契约校验失败时抛出。
        """
        try:
            return await self.pet_context_reader.read(request)
        except SemanticCollaborationError:
            raise
        except Exception as error:
            raise TurnSnapshotSourceUnavailableError(
                "trusted pet context source is unavailable",
                source_name="trusted_pet_context",
            ) from error

    def _measure_usage(
        self,
        *,
        original_user_text: OriginalUserText,
        history: BoundedHistoryReadResult,
        prior_facts: VerifiedPriorFactSummary,
        pet_context: TrustedPetContext,
    ) -> TurnSnapshotUsage:
        """计算各权威上下文的确定性预算占用。

        :param original_user_text: 当前回合原文契约。
        :param history: 上一轮受限历史结果。
        :param prior_facts: 已验证事实摘要。
        :param pet_context: 可信宠物上下文。
        :return: 返回不含用户内容的预算占用对象。
        """
        original_chars = len(original_user_text.text)
        history_chars = sum(
            len(question.text) for question in history.questions
        )
        prior_fact_chars = sum(len(fact.statement) for fact in prior_facts.facts)
        pet_context_chars = len(
            canonical_turn_snapshot_json(pet_context.model_dump(mode="json")),
        )
        total_chars = (
            original_chars
            + history_chars
            + prior_fact_chars
            + pet_context_chars
        )
        return TurnSnapshotUsage(
            budget_unit=self.budget.budget_unit,
            original_user_text_chars=original_chars,
            last_assistant_question_chars=history_chars,
            verified_prior_fact_chars=prior_fact_chars,
            trusted_pet_context_chars=pet_context_chars,
            total_context_chars=total_chars,
        )

    def _enforce_budget(self, usage: TurnSnapshotUsage) -> None:
        """执行 TurnSnapshot 全局硬预算门禁。

        :param usage: 当前构建的确定性预算占用。
        :return: 无返回值。
        :raises TurnSnapshotBudgetExceededError: 任一预算超限时抛出。
        """
        limits = (
            (
                "original_user_text",
                usage.original_user_text_chars,
                self.budget.max_original_user_text_chars,
            ),
            (
                "last_assistant_questions",
                usage.last_assistant_question_chars,
                self.budget.max_last_question_chars,
            ),
            (
                "verified_prior_fact_summary",
                usage.verified_prior_fact_chars,
                self.budget.max_verified_prior_fact_chars,
            ),
            (
                "trusted_pet_context",
                usage.trusted_pet_context_chars,
                self.budget.max_trusted_pet_context_chars,
            ),
            (
                "total_context",
                usage.total_context_chars,
                self.budget.max_total_context_chars,
            ),
        )
        for budget_name, used, limit in limits:
            if used > limit:
                raise TurnSnapshotBudgetExceededError(
                    "turn snapshot context budget exceeded",
                    budget_name=budget_name,
                    used=used,
                    limit=limit,
                )

    def _build_snapshot(
        self,
        *,
        request: TurnSnapshotBuildRequest,
        history: BoundedHistoryReadResult,
        prior_facts: VerifiedPriorFactSummary,
        pet_context: TrustedPetContext,
    ) -> TurnSnapshot:
        """组装带 canonical digest 的不可变 TurnSnapshot。

        :param request: 当前回合构建请求。
        :param history: 上一轮受限历史结果。
        :param prior_facts: 已验证事实摘要。
        :param pet_context: 可信宠物上下文。
        :return: 返回完成 digest 校验的 TurnSnapshot。
        """
        payload: dict[str, Any] = {
            "turn_id": request.turn_id,
            "turn_index": request.turn_index,
            "original_user_text": request.original_user_text.text,
            "original_text_extraction_policy": (
                request.original_user_text.extraction_policy.value
            ),
            "original_text_input_item_count": (
                request.original_user_text.input_item_count
            ),
            "bounded_history_status": history.status.value,
            "last_assistant_questions": [
                question.model_dump(mode="json") for question in history.questions
            ],
            "verified_prior_fact_summary": prior_facts.model_dump(mode="json"),
            "trusted_pet_context": pet_context.model_dump(mode="json"),
            "snapshot_version": "1.0.0",
        }
        return TurnSnapshot.model_validate(
            {**payload, "context_digest": compute_turn_snapshot_digest(payload)},
        )


class TurnSnapshotProjector:
    """按 SkillSpec 上下文契约生成 TurnSnapshot 受限视图。

    :return: 无返回值；该投影器只做访问控制和确定性计量，不渲染 prompt。
    """

    def project(
        self,
        snapshot: TurnSnapshot,
        context_contract: ContextContract,
    ) -> TurnSnapshotProjection:
        """为单个 SKILL 生成受限 TurnSnapshot 投影。

        :param snapshot: 当前回合不可变 TurnSnapshot。
        :param context_contract: SkillSpec 声明的受限上下文契约。
        :return: 返回通过资源和预算校验的受限投影。
        :raises TurnSnapshotContextPolicyViolationError: 声明非法资源时抛出。
        :raises TurnSnapshotBudgetExceededError: SKILL 可见投影超过硬预算时抛出。
        """
        included_resources: set[SkillContextResource] = {
            SkillContextResource.TURN_SNAPSHOT_DIGEST,
        }
        projection_payload: dict[str, Any] = {
            "turn_snapshot_digest": snapshot.context_digest,
        }
        for resource in context_contract.required_resources:
            if resource in DOMAIN_ISOLATED_CONTEXT_RESOURCES:
                raise TurnSnapshotContextPolicyViolationError(
                    "domain-isolated resource cannot be projected from TurnSnapshot",
                )
            if resource not in TURN_SNAPSHOT_CONTEXT_RESOURCES:
                continue
            included_resources.add(resource)
            if resource == SkillContextResource.ORIGINAL_USER_TEXT:
                projection_payload["original_user_text"] = snapshot.original_user_text
            elif resource in {
                SkillContextResource.BOUNDED_CONVERSATION_HISTORY,
                SkillContextResource.LAST_ASSISTANT_QUESTIONS,
            }:
                projection_payload["last_assistant_questions"] = [
                    question.model_dump(mode="json")
                    for question in snapshot.last_assistant_questions
                ]
            elif resource == SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY:
                projection_payload["verified_prior_fact_summary"] = (
                    snapshot.verified_prior_fact_summary.model_dump(mode="json")
                )
            elif resource == SkillContextResource.TRUSTED_PET_CONTEXT:
                projection_payload["trusted_pet_context"] = (
                    snapshot.trusted_pet_context.model_dump(mode="json")
                )
        ordered_resources = tuple(
            sorted(included_resources, key=_context_resource_sort_key),
        )
        projection_payload["included_resources"] = [
            resource.value for resource in ordered_resources
        ]
        context_chars = len(
            canonical_turn_snapshot_json(projection_payload),
        )
        if context_chars > context_contract.max_context_chars:
            raise TurnSnapshotBudgetExceededError(
                "skill context projection budget exceeded",
                budget_name="skill_context",
                used=context_chars,
                limit=context_contract.max_context_chars,
            )
        return TurnSnapshotProjection.model_validate(
            {**projection_payload, "context_chars": context_chars},
        )
