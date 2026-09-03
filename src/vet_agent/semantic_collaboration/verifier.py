"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/verifier.py
作用：实现受限语义协作 DAG M07 的最小生成结果结构验证器。
范围：覆盖 Turn Intent fixed-field 形态、自然语言 claims 数组、确定性
      claim 数量派生、重复 proposition、单行 proposition 与任务身份一致性校验。
说明：本文件不做 evidence 字面锚定、不做医学判断、不审查漏抽或语义忠实性，
      也不把模型 proposal 提升为 verified artifact。
=============================================================================
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import SkillFailureCode
from .gateway_contracts import SemanticModelProposal


class GenerationVerificationState(StrEnum):
    """表示 M07 对模型 proposal 的结构性验证结论。

    :return: 无返回值；该枚举不表示语义审查或 artifact 权威状态。
    """

    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class TurnIntentProposalShape(BaseModel):
    """表示 Turn Intent proposal 的 M07 权威结构形态。

    :return: 无返回值；该模型只做类型和 required 字段验证。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    answer_now: bool = Field(description="用户是否要求先回答。")
    wants_triage: bool = Field(description="用户是否询问是否就医。")
    correction: bool = Field(description="用户是否纠正此前信息。")
    clarification_request: bool = Field(description="用户是否请求澄清。")
    fact_statement_present: bool = Field(description="是否存在事实陈述。")
    question_present: bool = Field(description="是否存在问句或请求。")
    report_context_present: bool = Field(description="是否存在背景报告。")


class ClaimInventoryProposalShape(BaseModel):
    """表示自然语言 Claim Proposition Inventory 的 M07 权威结构形态。

    :return: 无返回值；该模型不判断 proposition 是否完整或忠实。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    claims: list[str] = Field(
        min_length=0,
        max_length=8,
        description="自包含自然语言 claim proposition 集合。",
    )


class SemanticGenerationVerificationResult(BaseModel):
    """表示 M07 生成 proposal 的确定性验证结果。

    :return: 无返回值；结果只描述结构准入，不表示语义正确。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    task_id: str = Field(description="验证结果对应的权威 PlanTask 标识。")
    skill_id: str = Field(description="验证结果对应的生产 SKILL 标识。")
    state: GenerationVerificationState = Field(
        description="结构验证是否通过。",
    )
    failure_code: SkillFailureCode | None = Field(
        default=None,
        description="结构验证失败时的稳定失败码。",
    )
    failure_message: str | None = Field(
        default=None,
        max_length=1000,
        description="面向工程排障的失败说明。",
    )
    claim_count: int | None = Field(
        default=None,
        ge=0,
        description="Claim Inventory proposal 的 claim 数量。",
    )

    @classmethod
    def accepted(
        cls,
        *,
        task_id: str,
        skill_id: str,
        claim_count: int | None = None,
    ) -> SemanticGenerationVerificationResult:
        """构造通过 M07 结构验证的结果。

        :param task_id: 权威 PlanTask 标识。
        :param skill_id: 生产 SKILL 标识。
        :param claim_count: Claim Inventory 的 claim 数量。
        :return: 返回 accepted 结构验证结果。
        """
        return cls(
            task_id=task_id,
            skill_id=skill_id,
            state=GenerationVerificationState.ACCEPTED,
            claim_count=claim_count,
        )

    @classmethod
    def blocked(
        cls,
        *,
        task_id: str,
        skill_id: str,
        failure_code: SkillFailureCode,
        failure_message: str,
        claim_count: int | None = None,
    ) -> SemanticGenerationVerificationResult:
        """构造被 M07 阻断的结构验证结果。

        :param task_id: 权威 PlanTask 标识。
        :param skill_id: 生产 SKILL 标识。
        :param failure_code: 稳定失败码。
        :param failure_message: 工程排障说明。
        :param claim_count: Claim Inventory 的 claim 数量。
        :return: 返回 blocked 结构验证结果。
        """
        return cls(
            task_id=task_id,
            skill_id=skill_id,
            state=GenerationVerificationState.BLOCKED,
            failure_code=failure_code,
            failure_message=failure_message,
            claim_count=claim_count,
        )


class SemanticGenerationVerifier:
    """表示 M06 自然语言 proposal 的 M07 结构验证器。

    :return: 无返回值；验证器不消费原始用户文本来做医学或关键词判断。
    """

    def verify(
        self,
        proposal: SemanticModelProposal,
    ) -> SemanticGenerationVerificationResult:
        """验证单个 M06 模型 proposal 的结构和任务身份。

        :param proposal: M05 返回且已通过权威 JSON Schema 的模型 proposal。
        :return: 返回显式结构验证结果。
        """
        task = proposal.execution.task
        if (
            proposal.metadata.task_id != task.task_id
            or proposal.metadata.skill_id != task.skill_id
            or proposal.metadata.turn_snapshot_digest
            != proposal.execution.turn_snapshot_digest
        ):
            return SemanticGenerationVerificationResult.blocked(
                task_id=task.task_id,
                skill_id=task.skill_id,
                failure_code=SkillFailureCode.CONTEXT_DIGEST_MISMATCH,
                failure_message="semantic proposal identity mismatch",
            )
        try:
            if task.skill_id == "turn_intent":
                TurnIntentProposalShape.model_validate(proposal.payload)
                return SemanticGenerationVerificationResult.accepted(
                    task_id=task.task_id,
                    skill_id=task.skill_id,
                )
            if task.skill_id == "claim_inventory":
                return self._verify_claim_inventory(proposal)
        except ValidationError as exc:
            return SemanticGenerationVerificationResult.blocked(
                task_id=task.task_id,
                skill_id=task.skill_id,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message=exc.errors()[0].get("msg", "invalid proposal shape"),
            )
        return SemanticGenerationVerificationResult.blocked(
            task_id=task.task_id,
            skill_id=task.skill_id,
            failure_code=SkillFailureCode.OWNERSHIP_VIOLATION,
            failure_message="semantic generation skill is not accepted by M07",
        )

    def _verify_claim_inventory(
        self,
        proposal: SemanticModelProposal,
    ) -> SemanticGenerationVerificationResult:
        """验证 Claim Proposition Inventory 的数组形态并派生数量。

        :param proposal: M05 返回的 Claim Inventory 模型 proposal。
        :return: 返回显式结构验证结果。
        """
        task = proposal.execution.task
        try:
            shape = ClaimInventoryProposalShape.model_validate(proposal.payload)
        except ValidationError as exc:
            return SemanticGenerationVerificationResult.blocked(
                task_id=task.task_id,
                skill_id=task.skill_id,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message=exc.errors()[0].get("msg", "invalid claims shape"),
                claim_count=None,
            )
        normalized_claims = tuple(
            claim.strip() for claim in shape.claims
        )
        if normalized_claims != tuple(shape.claims):
            return SemanticGenerationVerificationResult.blocked(
                task_id=task.task_id,
                skill_id=task.skill_id,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message="claim proposition has surrounding whitespace",
                claim_count=len(shape.claims),
            )
        if any(not claim for claim in normalized_claims):
            return SemanticGenerationVerificationResult.blocked(
                task_id=task.task_id,
                skill_id=task.skill_id,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message="claim proposition cannot be blank",
                claim_count=len(shape.claims),
            )
        if any("\n" in claim or "\r" in claim for claim in shape.claims):
            return SemanticGenerationVerificationResult.blocked(
                task_id=task.task_id,
                skill_id=task.skill_id,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message="claim proposition must be a single line",
                claim_count=len(shape.claims),
            )
        if len(set(normalized_claims)) != len(normalized_claims):
            return SemanticGenerationVerificationResult.blocked(
                task_id=task.task_id,
                skill_id=task.skill_id,
                failure_code=SkillFailureCode.SCHEMA_INVALID,
                failure_message="duplicate claim proposition",
                claim_count=len(shape.claims),
            )
        return SemanticGenerationVerificationResult.accepted(
            task_id=task.task_id,
            skill_id=task.skill_id,
            claim_count=len(shape.claims),
        )
