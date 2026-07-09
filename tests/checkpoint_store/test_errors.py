##################################################################################################
# 文件: tests/checkpoint_store/test_errors.py
# 作用: 验证 CheckpointStore 错误码默认重试策略、错误 DTO 构建和领域异常转换。
# 边界: 仅测试 CheckpointStore 公共契约，不访问内部实现模块或领域外依赖。
##################################################################################################

from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
    build_checkpoint_store_error_dto,
    is_checkpoint_error_retryable_by_default,
)


def test_checkpoint_error_retryable_defaults() -> None:
    """验证 CheckpointStore 错误码默认重试策略。

    :return: None。
    """

    assert is_checkpoint_error_retryable_by_default(
        CheckpointErrorCode.CHECKPOINT_LOCKED
    )
    assert is_checkpoint_error_retryable_by_default(
        CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT
    )
    assert not is_checkpoint_error_retryable_by_default(
        CheckpointErrorCode.CHECKPOINT_PET_CONFLICT
    )
    assert not is_checkpoint_error_retryable_by_default(
        CheckpointErrorCode.CHECKPOINT_STATE_TOO_LARGE
    )


def test_build_checkpoint_store_error_dto_uses_default_retryable() -> None:
    """验证错误 DTO 构建时会按错误码补齐默认重试策略。

    :return: None。
    """

    error = build_checkpoint_store_error_dto(
        code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
        operation=CheckpointOperation.LOAD_LATEST_CHECKPOINT,
        message="checkpoint read timeout",
        request_id="req_1",
        trace_id="trace_1",
    )

    assert error.retryable is True
    assert error.request_id == "req_1"
    assert error.trace_id == "trace_1"


def test_checkpoint_store_error_exposes_stable_dto() -> None:
    """验证领域异常可稳定转换为错误 DTO。

    :return: None。
    """

    error = CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT,
        operation=CheckpointOperation.SAVE_CHECKPOINT,
        message="expected version conflict",
        request_id="req_1",
        trace_id="trace_1",
        conflict_with={"latest_version": 2},
    )

    assert error.code is CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT
    assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
    assert error.retryable is True
    assert error.to_dto().conflict_with == {"latest_version": 2}
    assert "CHECKPOINT_VERSION_CONFLICT" in str(error)
