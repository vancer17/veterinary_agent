"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/review_verifier.py
作用：实现受限语义协作 DAG M08 的 Review 输出确定性验证器。
范围：覆盖 Coverage / Faithfulness 固定矩阵、任务身份、claim 身份、
      missing hint 形态、上下文 digest 与显式 blocked 终态校验。
说明：本文件不解释自然语言医学语义、不清洗 extra field、不输出 verdict、
      不修复模型 JSON，也不把 blocked Review 当作原 Claim Inventory 通过。
=============================================================================
"""

from __future__ import annotations

from pydantic import ValidationError

from .contracts import SkillFailureCode
from .gateway_contracts import SemanticModelProposal
from .review_contracts import (
    ClaimCoverageReviewOutput,
    ClaimCoverageReviewVerificationResult,
    ClaimFaithfulnessReviewOutput,
    ClaimFaithfulnessReviewVerificationResult,
    ReviewVerificationState,
    compute_claim_digest,
)
from .scheduler_contracts import SemanticTaskExecutionRequest


class SemanticReviewVerifier:
    """表示 M08 Review 模型 proposal 的确定性结构验证器。

    :return: 无返回值；验证器只判断契约与身份，不做医学语义判断。
    """

    def verify_coverage(
        self,
        proposal: SemanticModelProposal,
        expected_execution: SemanticTaskExecutionRequest,
    ) -> ClaimCoverageReviewVerificationResult:
        """验证 Coverage Review proposal 的任务身份和固定矩阵。

        :param proposal: M05 返回的 Coverage Review 模型 proposal。
        :param expected_execution: M08 构造的权威动态任务请求。
        :return: 返回 accepted 或 blocked 的显式验证结果。
        """
        if not self._identity_is_closed(proposal, expected_execution):
            return ClaimCoverageReviewVerificationResult(
                review_task_id=expected_execution.task.task_id,
                state=ReviewVerificationState.BLOCKED,
                failure_code=SkillFailureCode.CONTEXT_DIGEST_MISMATCH,
                failure_message="coverage review proposal identity mismatch",
            )
        try:
            output = ClaimCoverageReviewOutput.model_validate(proposal.payload)
        except ValidationError as error:
            return ClaimCoverageReviewVerificationResult(
                review_task_id=expected_execution.task.task_id,
                state=ReviewVerificationState.BLOCKED,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message=error.errors()[0].get("msg", "invalid review shape"),
            )
        return ClaimCoverageReviewVerificationResult(
            review_task_id=expected_execution.task.task_id,
            state=ReviewVerificationState.ACCEPTED,
            output=output,
        )

    def verify_faithfulness(
        self,
        proposal: SemanticModelProposal,
        expected_execution: SemanticTaskExecutionRequest,
        *,
        claim_index: int,
        claim_proposition: str,
    ) -> ClaimFaithfulnessReviewVerificationResult:
        """验证单条 Faithfulness Review proposal 的 claim 身份和矩阵。

        :param proposal: M05 返回的 Faithfulness Review 模型 proposal。
        :param expected_execution: M08 构造的权威动态任务请求。
        :param claim_index: 当前 claim 在 inventory 中的系统序号。
        :param claim_proposition: 当前唯一待审查 proposition。
        :return: 返回 accepted 或 blocked 的显式验证结果。
        """
        claim_digest = compute_claim_digest(claim_proposition)
        if not self._identity_is_closed(proposal, expected_execution):
            return ClaimFaithfulnessReviewVerificationResult(
                review_task_id=expected_execution.task.task_id,
                claim_index=claim_index,
                claim_digest=claim_digest,
                state=ReviewVerificationState.BLOCKED,
                failure_code=SkillFailureCode.CONTEXT_DIGEST_MISMATCH,
                failure_message="faithfulness review proposal identity mismatch",
            )
        try:
            output = ClaimFaithfulnessReviewOutput.model_validate(proposal.payload)
        except ValidationError as error:
            return ClaimFaithfulnessReviewVerificationResult(
                review_task_id=expected_execution.task.task_id,
                claim_index=claim_index,
                claim_digest=claim_digest,
                state=ReviewVerificationState.BLOCKED,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message=error.errors()[0].get("msg", "invalid review shape"),
            )
        return ClaimFaithfulnessReviewVerificationResult(
            review_task_id=expected_execution.task.task_id,
            claim_index=claim_index,
            claim_digest=claim_digest,
            state=ReviewVerificationState.ACCEPTED,
            output=output,
        )

    def _identity_is_closed(
        self,
        proposal: SemanticModelProposal,
        expected_execution: SemanticTaskExecutionRequest,
    ) -> bool:
        """校验 Review proposal 与动态任务身份完全一致。

        :param proposal: 当前 Review 模型 proposal。
        :param expected_execution: M08 构造的权威任务请求。
        :return: 任务、SKILL、attempt、上下文与 schema 均一致时返回 True。
        """
        return (
            proposal.execution == expected_execution
            and proposal.metadata.task_id == expected_execution.task.task_id
            and proposal.metadata.skill_id == expected_execution.task.skill_id
            and proposal.metadata.skill_version == expected_execution.task.skill_version
            and proposal.metadata.turn_snapshot_digest
            == expected_execution.turn_snapshot_digest
            and proposal.metadata.output_schema_digest
            == expected_execution.task.expected_output_schema.schema_digest
        )
