"""
=============================================================================
文件：tests/test_semantic_collaboration_turn_snapshot.py
作用：验证受限语义协作 DAG M02 TurnSnapshot 的生产契约与门禁。
范围：覆盖不可变构建、原文保留、digest 一致性、来源显式失败、附件失败、
      全局预算、受限投影、SKILL 级预算与领域资源隔离。
说明：本测试不依赖数据库、LiteLLM、OPA、Mem0 或任何 input_preprocessing
      历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from vet_agent.semantic_collaboration import (
    CLAIM_INVENTORY_SPEC,
    TURN_INTENT_SPEC,
    BoundedConversationHistoryReader,
    BoundedHistoryReadResult,
    BoundedHistoryReadStatus,
    OriginalTextExtractionPolicy,
    OriginalUserText,
    SkillContextResource,
    SkillFailureCode,
    TrustedPetContext,
    TrustedPetContextReader,
    TrustedPetContextSource,
    TrustedPetProfile,
    TurnSnapshotBudget,
    TurnSnapshotBudgetExceededError,
    TurnSnapshotBudgetUnit,
    TurnSnapshotBuilder,
    TurnSnapshotBuildRequest,
    TurnSnapshotDigestMismatchError,
    TurnSnapshotProjector,
    TurnSnapshotSourceRequest,
    TurnSnapshotSourceScope,
    TurnSnapshotSourceUnavailableError,
    UnsupportedTurnInputError,
    VerifiedPriorFactReader,
    VerifiedPriorFactSummary,
    VerifiedPriorFactSummaryStatus,
    compute_turn_snapshot_digest,
)


def _budget(
    *,
    max_original: int = 1000,
    max_history: int = 1000,
    max_prior_facts: int = 1000,
    max_pet_context: int = 1000,
    max_total: int = 4000,
) -> TurnSnapshotBudget:
    """构造测试用 TurnSnapshot 硬预算。

    :param max_original: 当前原文预算。
    :param max_history: 上一轮追问预算。
    :param max_prior_facts: 已验证事实摘要预算。
    :param max_pet_context: 可信宠物上下文预算。
    :param max_total: 全局合计预算。
    :return: 返回测试用不可变预算对象。
    """
    return TurnSnapshotBudget(
        budget_unit=TurnSnapshotBudgetUnit.UNICODE_CODEPOINTS,
        max_original_user_text_chars=max_original,
        max_last_question_chars=max_history,
        max_verified_prior_fact_chars=max_prior_facts,
        max_trusted_pet_context_chars=max_pet_context,
        max_total_context_chars=max_total,
    )


def _request(text: str = " 前天开始换新猫粮，大便偏软。 ") -> TurnSnapshotBuildRequest:
    """构造测试用当前回合构建请求。

    :param text: 当前回合原文，默认保留首尾空白。
    :return: 返回测试用不可变构建请求。
    """
    return TurnSnapshotBuildRequest(
        scope=TurnSnapshotSourceScope(
            user_id="user-1",
            session_id="session-1",
            pet_id="pet-1",
        ),
        turn_id="turn-1",
        turn_index=0,
        original_user_text=OriginalUserText(
            text=text,
            input_item_count=1,
            extraction_policy=OriginalTextExtractionPolicy.SINGLE_MESSAGE,
        ),
    )


def _history() -> BoundedHistoryReadResult:
    """构造成功读取但上一轮没有追问的结果。

    :return: 返回显式 no_previous_turn 的受限历史结果。
    """
    return BoundedHistoryReadResult(
        status=BoundedHistoryReadStatus.NO_PREVIOUS_TURN,
    )


def _prior_facts() -> VerifiedPriorFactSummary:
    """构造成功读取后没有已验证事实的摘要。

    :return: 返回显式 no_verified_claims 的事实摘要。
    """
    return VerifiedPriorFactSummary(
        status=VerifiedPriorFactSummaryStatus.NO_VERIFIED_CLAIMS,
    )


def _pet_context() -> TrustedPetContext:
    """构造服务端可信宠物上下文。

    :return: 返回只含白名单画像的可信上下文。
    """
    return TrustedPetContext(
        source=TrustedPetContextSource.SCOPE_CONTEXT_SERVICE,
        profile=TrustedPetProfile(
            species="cat",
            breed="British Shorthair",
            age_text="2 years",
            weight_kg=4.5,
            sex="female",
            neutered=True,
        ),
    )


class StubHistoryReader(BoundedConversationHistoryReader):
    """提供固定的上一轮受限历史读取结果。

    :return: 无返回值；该测试替身不访问任何外部服务。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> BoundedHistoryReadResult:
        """返回固定受限历史结果。

        :param request: 来源读取请求。
        :return: 返回显式 no_previous_turn 结果。
        """
        return _history()


class StubPriorFactReader(VerifiedPriorFactReader):
    """提供固定的已验证事实摘要读取结果。

    :return: 无返回值；该测试替身不推断任何新事实。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> VerifiedPriorFactSummary:
        """返回固定事实摘要结果。

        :param request: 来源读取请求。
        :return: 返回显式 no_verified_claims 结果。
        """
        return _prior_facts()


class StubPetContextReader(TrustedPetContextReader):
    """提供固定的服务端可信宠物上下文。

    :return: 无返回值；该测试替身不读取请求侧自报画像。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> TrustedPetContext:
        """返回固定可信宠物上下文。

        :param request: 来源读取请求。
        :return: 返回白名单可信宠物上下文。
        """
        return _pet_context()


class FailingHistoryReader(BoundedConversationHistoryReader):
    """模拟上一轮历史来源读取失败。

    :return: 无返回值；该测试替身用于验证失败不得转为空集合。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> BoundedHistoryReadResult:
        """抛出来源读取异常。

        :param request: 来源读取请求。
        :return: 该方法不会返回。
        :raises RuntimeError: 固定抛出来源底层错误。
        """
        raise RuntimeError("history backend unavailable")


def _builder(
    *,
    history_reader: BoundedConversationHistoryReader | None = None,
    budget: TurnSnapshotBudget | None = None,
) -> TurnSnapshotBuilder:
    """构造测试用 TurnSnapshotBuilder。

    :param history_reader: 可选历史读取端口测试替身。
    :param budget: 可选硬预算覆盖。
    :return: 返回测试用构建器。
    """
    return TurnSnapshotBuilder(
        history_reader=history_reader or StubHistoryReader(),
        prior_fact_reader=StubPriorFactReader(),
        pet_context_reader=StubPetContextReader(),
        budget=budget or _budget(),
    )


def test_snapshot_build_preserves_text_and_is_immutable() -> None:
    """验证 Snapshot 构建保留原文、digest 一致且对象不可变。

    :return: 无返回值。
    """
    result = asyncio.run(_builder().build(_request()))

    assert result.snapshot.original_user_text == " 前天开始换新猫粮，大便偏软。 "
    assert result.snapshot.bounded_history_status == BoundedHistoryReadStatus.NO_PREVIOUS_TURN
    assert result.snapshot.verified_prior_fact_summary.status == (
        VerifiedPriorFactSummaryStatus.NO_VERIFIED_CLAIMS
    )
    assert result.snapshot.trusted_pet_context.profile.species == "cat"
    assert result.usage.total_context_chars > 0
    assert "前天开始换新猫粮" not in result.usage.to_metadata()
    result.snapshot.verify_digest(result.snapshot.context_digest)

    with pytest.raises(ValidationError):
        result.snapshot.turn_id = "changed"
    with pytest.raises(ValidationError):
        result.snapshot.trusted_pet_context.profile.species = "dog"


def test_snapshot_digest_is_canonical_and_key_order_independent() -> None:
    """验证 digest 使用 canonical JSON 且与字段书写顺序无关。

    :return: 无返回值。
    """
    first = {"turn_id": "turn-1", "original_user_text": "没有呕吐"}
    second = {"original_user_text": "没有呕吐", "turn_id": "turn-1"}

    assert compute_turn_snapshot_digest(first) == compute_turn_snapshot_digest(second)


def test_snapshot_rejects_digest_mismatch_from_task_envelope() -> None:
    """验证外部任务 envelope digest 不一致时显式失败。

    :return: 无返回值。
    """
    result = asyncio.run(_builder().build(_request()))

    with pytest.raises(TurnSnapshotDigestMismatchError):
        result.snapshot.verify_digest("0" * 64)


def test_budget_failure_is_explicit_and_does_not_truncate_text() -> None:
    """验证原文超预算时显式失败且不截断输入。

    :return: 无返回值。
    """
    request = _request()

    with pytest.raises(TurnSnapshotBudgetExceededError) as error:
        asyncio.run(_builder(budget=_budget(max_original=3)).build(request))

    assert error.value.failure_code == "context_budget_exceeded"
    assert error.value.budget_name == "original_user_text"
    assert error.value.used == len(request.original_user_text.text)
    assert error.value.limit == 3
    assert request.original_user_text.text == " 前天开始换新猫粮，大便偏软。 "


def test_source_failure_is_not_converted_to_empty_history() -> None:
    """验证来源失败不会被伪装为 no_previous_turn 或空集合。

    :return: 无返回值。
    """
    with pytest.raises(TurnSnapshotSourceUnavailableError, match="history source") as error:
        asyncio.run(
            _builder(history_reader=FailingHistoryReader()).build(_request()),
        )
    assert error.value.failure_code == "snapshot_source_unavailable"
    assert error.value.source_name == "bounded_conversation_history"


def test_attachment_input_is_rejected_until_supported() -> None:
    """验证 M02 文本契约不会静默忽略附件输入。

    :return: 无返回值。
    """
    request = _request().model_copy(update={"attachment_count": 1})

    with pytest.raises(UnsupportedTurnInputError, match="text-only"):
        asyncio.run(_builder().build(request))


def test_projector_only_returns_declared_snapshot_resources() -> None:
    """验证 SKILL 只能看到 SkillSpec 声明的 TurnSnapshot 资源。

    :return: 无返回值。
    """
    result = asyncio.run(_builder().build(_request()))
    projection = TurnSnapshotProjector().project(
        result.snapshot,
        TURN_INTENT_SPEC.context_contract,
    )

    assert projection.turn_snapshot_digest == result.snapshot.context_digest
    assert projection.original_user_text == result.snapshot.original_user_text
    assert projection.last_assistant_questions == ()
    assert projection.verified_prior_fact_summary == (
        result.snapshot.verified_prior_fact_summary
    )
    assert projection.trusted_pet_context is None
    assert "trusted_pet_context" not in projection.included_resources


def test_projector_enforces_skill_context_budget() -> None:
    """验证 SKILL 级投影超预算时显式失败。

    :return: 无返回值。
    """
    result = asyncio.run(_builder().build(_request()))
    context_contract = TURN_INTENT_SPEC.context_contract.model_copy(
        update={"max_context_chars": 1},
    )

    with pytest.raises(TurnSnapshotBudgetExceededError) as error:
        TurnSnapshotProjector().project(result.snapshot, context_contract)

    assert error.value.budget_name == "skill_context"
    assert error.value.limit == 1


def test_projector_leaves_scheduler_artifacts_outside_snapshot() -> None:
    """验证 verified artifact 依赖由调度器绑定而不进入 Snapshot 投影。

    :return: 无返回值。
    """
    result = asyncio.run(_builder().build(_request()))
    projection = TurnSnapshotProjector().project(
        result.snapshot,
        CLAIM_INVENTORY_SPEC.context_contract,
    )

    assert projection.trusted_pet_context == result.snapshot.trusted_pet_context
    assert SkillContextResource.VERIFIED_PEER_ARTIFACT not in (
        projection.included_resources
    )


def test_context_budget_failure_has_stable_skill_failure_code() -> None:
    """验证 M02 预算失败码已纳入 M01 稳定契约。

    :return: 无返回值。
    """

    assert SkillFailureCode.CONTEXT_BUDGET_EXCEEDED.value == "context_budget_exceeded"


def test_turn_snapshot_modules_do_not_import_forbidden_domains() -> None:
    """验证 M02 不直接依赖问诊、临床安全或记忆领域实现。

    :return: 无返回值。
    """
    package_root = (
        Path(__file__).parents[1] / "src/vet_agent/semantic_collaboration"
    )
    forbidden_prefixes = (
        "vet_agent.consultation_state",
        "vet_agent.clinical_safety",
        "vet_agent.memory",
        "vet_agent.input_preprocessing",
    )

    for python_file in sorted(package_root.glob("snapshot*.py")):
        tree = ast.parse(python_file.read_text(encoding="utf-8"))
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert all(
            not module.startswith(forbidden_prefixes)
            for module in imported_modules
        ), python_file.name
