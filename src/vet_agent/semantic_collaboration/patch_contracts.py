"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/patch_contracts.py
作用：定义受限语义协作 DAG M10 的 Repair SKILL 输出与 typed patch 契约。
范围：覆盖稀疏 claim delta、M11 base artifact 快照、系统 patch 信封、
      typed operations、patch set、验证结果、应用预览与 M11 提交端口。
说明：本文件不调用模型、不执行 patch、不写数据库、不生成权威 artifact，
      也不允许模型输出工程身份、base version 或完整 claims 数组。
=============================================================================
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .gateway_contracts import StructuredLLMCallMetadata
from .repair_contracts import (
    ReviewRepairDimension,
    SemanticRepairLane,
    SemanticRepairTask,
)


class ClaimPropositionRepairOutput(BaseModel):
    """表示单条 claim 修复 SKILL 的极薄模型输出。

    :return: 无返回值；模型只提出修复后的 proposition，不输出目标或维度。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    proposition: str = Field(
        min_length=1,
        max_length=240,
        description="修复后的自包含中文 claim proposition。",
    )

    @model_validator(mode="after")
    def validate_proposition(self) -> Self:
        """校验修复 proposition 是无空白的单行自然语言。

        :return: 返回可进入确定性 patch compiler 的 proposition。
        :raises ValueError: proposition 为空、含换行或带首尾空白时抛出。
        """
        if (
            not self.proposition.strip()
            or self.proposition != self.proposition.strip()
            or "\n" in self.proposition
            or "\r" in self.proposition
        ):
            raise ValueError("repaired proposition shape is invalid")
        return self


class ModifiedClaimUpdate(BaseModel):
    """表示 inventory repair 中的一个稀疏既有 claim 修改目标。

    :return: 无返回值； propositions 数量由系统解释，不由模型声明 operation。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    target: str = Field(
        pattern=r"^c[0-7]$",
        description="任务内局部 claim selector，例如 c0。",
    )
    propositions: tuple[str, ...] = Field(
        max_length=3,
        description="目标位置替换后的 proposition 集合；空集合表示删除。",
    )

    @field_validator("propositions", mode="before")
    @classmethod
    def coerce_propositions(
        cls,
        value: object,
    ) -> object:
        """将 JSON 数组规整为元组后再进入严格校验。

        :param value: 模型返回的 propositions JSON 值。
        :return: 返回可由严格 tuple 字段消费的序列值。
        """
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        """校验稀疏更新中的每条 proposition 形态合法。

        :return: 返回可由系统编译为 typed operation 的稀疏更新。
        :raises ValueError: proposition 为空、多行或带首尾空白时抛出。
        """
        if any(
            not proposition.strip()
            or proposition != proposition.strip()
            or "\n" in proposition
            or "\r" in proposition
            for proposition in self.propositions
        ):
            raise ValueError("modified claim proposition shape is invalid")
        return self


class ClaimInventoryRepairOutput(BaseModel):
    """表示 Claim Inventory 修复 SKILL 的稀疏 delta 输出。

    :return: 无返回值；未列出的 claim 由系统从 M11 base artifact 原样保留。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    modified_claims: tuple[ModifiedClaimUpdate, ...] = Field(
        default=(),
        max_length=2,
        description="需要替换、拆分或删除的既有 claim 稀疏集合。",
    )
    added_claims: tuple[str, ...] = Field(
        default=(),
        max_length=2,
        description="需要追加到 inventory 末尾的新 proposition 集合。",
    )

    @field_validator("modified_claims", mode="before")
    @classmethod
    def coerce_modified_claims(
        cls,
        value: object,
    ) -> object:
        """将 JSON 修改数组规整为元组后再进入严格校验。

        :param value: 模型返回的 modified_claims JSON 值。
        :return: 返回可由严格 tuple 字段消费的序列值。
        """
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("added_claims", mode="before")
    @classmethod
    def coerce_added_claims(
        cls,
        value: object,
    ) -> object:
        """将 JSON 新增数组规整为元组后再进入严格校验。

        :param value: 模型返回的 added_claims JSON 值。
        :return: 返回可由严格 tuple 字段消费的序列值。
        """
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        """校验稀疏 delta 非空、目标唯一且总预算合法。

        :return: 返回可进入确定性 compiler 的 inventory delta。
        :raises ValueError: 无更新、目标重复、预算超限或 proposition 非法时抛出。
        """
        if not self.modified_claims and not self.added_claims:
            raise ValueError("claim inventory repair delta is empty")
        if len(self.modified_claims) + len(self.added_claims) > 2:
            raise ValueError("claim inventory repair delta exceeds budget")
        targets = [update.target for update in self.modified_claims]
        if len(targets) != len(set(targets)):
            raise ValueError("modified claim target is duplicate")
        if any(
            not proposition.strip()
            or proposition != proposition.strip()
            or "\n" in proposition
            or "\r" in proposition
            for proposition in self.added_claims
        ):
            raise ValueError("added claim proposition shape is invalid")
        return self


class RepairTargetArtifactSnapshot(BaseModel):
    """表示 M11 提供给 M10 的权威 base artifact 视图。

    :return: 无返回值；该快照不是新 artifact，也不是模型可修改对象。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_reference: str = Field(min_length=1, max_length=512)
    base_version: int = Field(ge=1)
    repair_depth: int = Field(default=0, ge=0, le=0)
    claims: tuple[str, ...] = Field(
        max_length=8,
        description="base artifact 中的权威 Claim Inventory proposition 集合。",
    )

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """校验 base claims 和修复深度满足 M10 前置条件。

        :return: 返回可被 M10 消费的 base artifact 快照。
        :raises ValueError: claims 形态非法或已处于修复输出时抛出。
        """
        if any(
            not claim.strip()
            or claim != claim.strip()
            or "\n" in claim
            or "\r" in claim
            for claim in self.claims
        ):
            raise ValueError("repair target artifact claims are invalid")
        return self


class RepairTargetSnapshotResolver(Protocol):
    """表示 M11 向 M10 提供权威 base artifact 快照的端口。

    :return: 无返回值；实现不得返回内存猜测、旧版本或摘要替代品。
    """

    async def load(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> RepairTargetArtifactSnapshot:
        """读取当前修复目标绑定的 M11 base artifact 快照。

        :param source_proposal_digest: 被修复 Claim Inventory proposal 摘要。
        :param review_bundle_digest: M08 Review Bundle 摘要。
        :return: 返回权威 claims、artifact 引用与 base version。
        """


class TODORepairTargetSnapshotResolver:
    """表示 M11 base artifact 快照读取尚未接入前的显式空壳。

    :return: 无返回值；该占位始终 Fail Fast，不伪造 base claims。
    """

    async def load(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> RepairTargetArtifactSnapshot:
        """阻断尚未实现的 M11 base artifact 快照读取。

        :param source_proposal_digest: 被修复 Claim Inventory proposal 摘要。
        :param review_bundle_digest: M08 Review Bundle 摘要。
        :raises NotImplementedError: M11 未实现时始终抛出。
        :return: 无返回值。
        """
        raise NotImplementedError("M11 repair target snapshot is not implemented")


class SemanticPatchOperationType(StrEnum):
    """表示系统从稀疏 delta 确定性编译出的 typed operation 类型。

    :return: 无返回值；该枚举不由 Repair LLM 输出。
    """

    REMOVE_CLAIM = "remove_claim"
    REPLACE_CLAIM = "replace_claim"
    REPLACE_CLAIM_WITH_CLAIMS = "replace_claim_with_claims"
    ADD_CLAIM = "add_claim"


class SemanticPatchOperation(BaseModel):
    """表示一个由系统编译并附加权威身份的 typed patch operation。

    :return: 无返回值；该对象不是自由 JSON Patch，也不能直接应用。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    operation: SemanticPatchOperationType
    target_claim_index: int | None = Field(default=None, ge=0)
    base_claim_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    proposition: str | None = Field(default=None, min_length=1, max_length=240)
    propositions: tuple[str, ...] | None = Field(default=None, max_length=3)
    after_claim_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_operation_payload(self) -> Self:
        """校验 operation 类型与目标、值和插入位置负载闭合。

        :return: 返回通过 typed operation 契约校验的 patch 操作。
        :raises ValueError: operation 与负载组合非法时抛出。
        """
        if self.operation is SemanticPatchOperationType.REMOVE_CLAIM:
            if (
                self.target_claim_index is None
                or self.base_claim_digest is None
                or self.proposition is not None
                or self.propositions is not None
                or self.after_claim_index is not None
            ):
                raise ValueError("remove claim operation payload is invalid")
            return self
        if self.operation is SemanticPatchOperationType.REPLACE_CLAIM:
            if (
                self.target_claim_index is None
                or self.base_claim_digest is None
                or self.proposition is None
                or self.propositions is not None
                or self.after_claim_index is not None
            ):
                raise ValueError("replace claim operation payload is invalid")
            return self
        if self.operation is SemanticPatchOperationType.REPLACE_CLAIM_WITH_CLAIMS:
            if (
                self.target_claim_index is None
                or self.base_claim_digest is None
                or self.proposition is not None
                or self.propositions is None
                or len(self.propositions) < 2
                or self.after_claim_index is not None
            ):
                raise ValueError("replace claim with claims payload is invalid")
            return self
        if (
            self.target_claim_index is not None
            or self.base_claim_digest is not None
            or self.proposition is None
            or self.propositions is not None
        ):
            raise ValueError("add claim operation payload is invalid")
        return self


class SemanticPatchPolicy(BaseModel):
    """表示 M10 typed patch 编译与应用的固定预算策略。

    :return: 无返回值；策略不引入维度级医学规则或隐藏重写路径。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    max_operations_per_patch: int = Field(default=2, ge=1, le=2)
    max_split_propositions: int = Field(default=3, ge=2, le=3)


class SemanticPatchProposal(BaseModel):
    """表示 M10 系统信封包装后的 typed patch proposal。

    :return: 无返回值；该 proposal 不是 verified artifact，也不是应用结果。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    patch_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    repair_plan_id: str = Field(min_length=64, max_length=64)
    repair_task: SemanticRepairTask
    repair_lane: SemanticRepairLane
    repair_skill_id: str = Field(min_length=1, max_length=120)
    repair_skill_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    run_id: str = Field(min_length=1, max_length=64)
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_reference: str = Field(min_length=1, max_length=512)
    base_version: int = Field(ge=1)
    repair_dimensions: tuple[ReviewRepairDimension, ...] = Field(min_length=1)
    operations: tuple[SemanticPatchOperation, ...] = Field(
        min_length=1,
        max_length=2,
    )
    model_output_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_metadata: StructuredLLMCallMetadata

    @model_validator(mode="after")
    def validate_proposal_identity(self) -> Self:
        """校验 patch 信封与 M09 修复任务身份完全一致。

        :return: 返回身份闭合的 typed patch proposal。
        :raises ValueError:任务、lane、版本、上下文或维度不一致时抛出。
        """
        task = self.repair_task
        if (
            self.repair_lane,
            self.repair_skill_id,
            self.repair_skill_version,
            self.run_id,
            self.turn_snapshot_digest,
            self.source_proposal_digest,
            self.review_bundle_digest,
            self.repair_dimensions,
        ) != (
            task.repair_lane,
            task.repair_skill_id,
            task.repair_skill_version,
            task.run_id,
            task.turn_snapshot_digest,
            task.source_proposal_digest,
            task.review_bundle_digest,
            task.review_dimensions,
        ):
            raise ValueError("semantic patch proposal identity mismatch")
        if (
            self.model_metadata.turn_snapshot_digest != self.turn_snapshot_digest
            or self.model_metadata.skill_id != self.repair_skill_id
            or self.model_metadata.skill_version != self.repair_skill_version
        ):
            raise ValueError("semantic patch model metadata identity mismatch")
        return self


class SemanticPatchVerificationState(StrEnum):
    """表示 M10 typed patch 的确定性验证结论。

    :return: 无返回值；blocked patch 不得提交给 M11。
    """

    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class SemanticPatchFailureCode(StrEnum):
    """表示 M10 patch 编译或验证失败的稳定错误编码。

    :return: 无返回值；未知失败不得进入 patch 应用。
    """

    IDENTITY_MISMATCH = "identity_mismatch"
    BASE_VERSION_CONFLICT = "base_version_conflict"
    REPAIR_DEPTH_EXCEEDED = "repair_depth_exceeded"
    TARGET_MISMATCH = "target_mismatch"
    DIMENSION_ENVELOPE_MISMATCH = "dimension_envelope_mismatch"
    BUDGET_EXCEEDED = "budget_exceeded"
    RESULT_INVALID = "result_invalid"
    PATCH_CONFLICT = "patch_conflict"


class SemanticPatchVerificationResult(BaseModel):
    """表示单个 typed patch proposal 的确定性验证结果。

    :return: 无返回值；accepted 不表示语义修复成功。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    state: SemanticPatchVerificationState
    failure_code: SemanticPatchFailureCode | None = None
    failure_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_result_payload(self) -> Self:
        """校验 patch 验证结果与失败负载闭合。

        :return: 返回通过显式终态契约校验的验证结果。
        :raises ValueError: accepted 带失败或 blocked 缺失败时抛出。
        """
        if self.state is SemanticPatchVerificationState.ACCEPTED:
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("accepted semantic patch cannot carry failure")
            return self
        if self.failure_code is None or not self.failure_message:
            raise ValueError("blocked semantic patch requires failure")
        return self


class SemanticPatchSet(BaseModel):
    """表示同一 M09 plan 下多个 verified patch 的原子应用集合。

    :return: 无返回值；patch set 只能共享同一 base artifact version。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    repair_plan_id: str = Field(min_length=64, max_length=64)
    patches: tuple[SemanticPatchProposal, ...] = Field(min_length=1, max_length=2)
    artifact_reference: str = Field(min_length=1, max_length=512)
    base_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_patch_set(self) -> Self:
        """校验 patch set 身份、版本和任务数量闭合。

        :return: 返回可交给 set verifier 的 patch 集合。
        :raises ValueError: plan、artifact、版本或任务重复不一致时抛出。
        """
        if len({patch.repair_plan_id for patch in self.patches}) != 1:
            raise ValueError("semantic patch set plan identity mismatch")
        if any(
            patch.artifact_reference != self.artifact_reference
            or patch.base_version != self.base_version
            for patch in self.patches
        ):
            raise ValueError("semantic patch set base binding mismatch")
        if len({patch.repair_task.repair_task_id for patch in self.patches}) != len(
            self.patches,
        ):
            raise ValueError("semantic patch set task is duplicate")
        return self


class PatchApplicationState(StrEnum):
    """表示确定性 patch 应用预览的业务状态。

    :return: 无返回值；该状态不是 artifact verified 状态。
    """

    PREVIEW_READY = "preview_ready"
    BLOCKED = "blocked"


class PatchApplicationPreview(BaseModel):
    """表示 typed patch set 的确定性应用预览。

    :return: 无返回值；预览不是权威 artifact，必须经 M11 append-only 提交。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    state: PatchApplicationState
    repair_plan_id: str
    base_artifact_reference: str
    base_version: int = Field(ge=1)
    next_version: int = Field(ge=1)
    claims: tuple[str, ...] = Field(max_length=8)
    failure_code: SemanticPatchFailureCode | None = None
    failure_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_preview_payload(self) -> Self:
        """校验应用预览状态与失败负载闭合。

        :return: 返回通过状态机校验的 patch 应用预览。
        :raises ValueError: ready 带失败或 blocked 缺失败时抛出。
        """
        if self.state is PatchApplicationState.PREVIEW_READY:
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("ready patch preview cannot carry failure")
            return self
        if self.failure_code is None or not self.failure_message:
            raise ValueError("blocked patch preview requires failure")
        return self


class SemanticRepairExecutionState(StrEnum):
    """表示一次 M10 repair plan 执行后的工程门禁状态。

    :return: 无返回值；该状态不是 M11 artifact verified 状态。
    """

    PATCH_READY = "patch_ready"
    BLOCKED = "blocked"


class SemanticRepairExecutionResult(BaseModel):
    """表示 M10 Runner 对一个 M09 plan 的完整执行结果。

    :return: 无返回值；结果只包含 verified patch set 与应用预览。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    repair_plan_id: str = Field(min_length=64, max_length=64)
    state: SemanticRepairExecutionState
    patch_set: SemanticPatchSet | None = None
    preview: PatchApplicationPreview | None = None
    failure_code: SemanticPatchFailureCode | None = None
    failure_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_execution_payload(self) -> Self:
        """校验 M10 执行结果与 patch / preview 负载闭合。

        :return: 返回通过状态机校验的执行结果。
        :raises ValueError: ready 缺 patch、blocked 带 patch 或失败负载非法时抛出。
        """
        if self.state is SemanticRepairExecutionState.PATCH_READY:
            if (
                self.patch_set is None
                or self.preview is None
                or self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError("ready repair execution payload is invalid")
            return self
        if self.patch_set is not None or self.preview is not None:
            raise ValueError("blocked repair execution cannot carry patch payload")
        if self.failure_code is None or not self.failure_message:
            raise ValueError("blocked repair execution requires failure")
        return self


class RepairPatchStore(Protocol):
    """表示 M11 接收 verified patch 应用预览的 append-only 端口。

    :return: 无返回值；实现必须保证版本、lineage、stale 与幂等提交。
    """

    async def commit(
        self,
        patch_set: SemanticPatchSet,
        preview: PatchApplicationPreview,
    ) -> str:
        """以 append-only 方式提交 patch 应用结果。

        :param patch_set: 已通过 set verifier 的 typed patch 集合。
        :param preview: 确定性应用预览。
        :return: 返回 M11 新版本 artifact 引用。
        """


class TODORepairPatchStore:
    """表示 M11 patch 权威提交尚未接入前的显式空壳。

    :return: 无返回值；该占位始终 Fail Fast，不生成伪 artifact 引用。
    """

    async def commit(
        self,
        patch_set: SemanticPatchSet,
        preview: PatchApplicationPreview,
    ) -> str:
        """阻断尚未实现的 M11 patch append-only 提交。

        :param patch_set: 已验证的 typed patch 集合。
        :param preview: 确定性应用预览。
        :raises NotImplementedError: M11 未实现时始终抛出。
        :return: 无返回值。
        """
        raise NotImplementedError("M11 repair patch store is not implemented")


def build_blocked_repair_execution(
    *,
    repair_plan_id: str,
    failure_code: SemanticPatchFailureCode,
    failure_message: str,
) -> SemanticRepairExecutionResult:
    """构造 M10 显式 blocked 执行结果。

    :param repair_plan_id: 当前 M09 repair plan 稳定身份。
    :param failure_code: 稳定 patch 失败码。
    :param failure_message: 面向工程排障的失败说明。
    :return: 返回不携带 patch 权威负载的 blocked 结果。
    """
    return SemanticRepairExecutionResult(
        repair_plan_id=repair_plan_id,
        state=SemanticRepairExecutionState.BLOCKED,
        failure_code=failure_code,
        failure_message=failure_message,
    )


def compute_model_output_digest(
    output: ClaimPropositionRepairOutput | ClaimInventoryRepairOutput,
) -> str:
    """计算 Repair 模型语义输出的 canonical SHA-256 摘要。

    :param output: 通过权威 schema 的极薄模型输出。
    :return: 返回写入 patch 信封的稳定模型输出摘要。
    """
    return sha256(output.model_dump_json().encode("utf-8")).hexdigest()


def compute_patch_id(proposal: SemanticPatchProposal) -> str:
    """计算 typed patch proposal 的 canonical SHA-256 身份。

    :param proposal: 尚未写入 patch_id 的身份闭合 proposal 构造输入。
    :return: 返回基于任务、版本和 canonical operations 的稳定 patch id。
    """
    material = {
        "repair_task_id": proposal.repair_task.repair_task_id,
        "repair_plan_id": proposal.repair_plan_id,
        "artifact_reference": proposal.artifact_reference,
        "base_version": proposal.base_version,
        "operations": [
            operation.model_dump(mode="json") for operation in proposal.operations
        ],
        "model_output_digest": proposal.model_output_digest,
    }
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "ClaimInventoryRepairOutput",
    "ClaimPropositionRepairOutput",
    "ModifiedClaimUpdate",
    "PatchApplicationPreview",
    "PatchApplicationState",
    "RepairPatchStore",
    "RepairTargetArtifactSnapshot",
    "RepairTargetSnapshotResolver",
    "SemanticPatchFailureCode",
    "SemanticPatchOperation",
    "SemanticPatchOperationType",
    "SemanticPatchPolicy",
    "SemanticPatchProposal",
    "SemanticPatchSet",
    "SemanticPatchVerificationResult",
    "SemanticPatchVerificationState",
    "SemanticRepairExecutionResult",
    "SemanticRepairExecutionState",
    "TODORepairPatchStore",
    "TODORepairTargetSnapshotResolver",
    "build_blocked_repair_execution",
    "compute_model_output_digest",
    "compute_patch_id",
]
