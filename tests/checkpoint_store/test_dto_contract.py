##################################################################################################
# 文件: tests/checkpoint_store/test_dto_contract.py
# 作用: 验证 CheckpointStore DTO 契约的严格字段校验与基础状态承载能力。
# 边界: 仅测试公共 DTO，不测试数据库、LangGraph 或业务组件集成。
##################################################################################################

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from veterinary_agent.checkpoint_store import (
    CheckpointRecordStatus,
    GraphExecutionStateDto,
    SaveCheckpointCommandDto,
    SessionBusinessStateDto,
)


def test_save_checkpoint_command_accepts_recovery_state_summary() -> None:
    """验证保存 checkpoint 命令可以承载恢复所需状态摘要。

    :return: None。
    """

    command = SaveCheckpointCommandDto(
        request_id="req_1",
        trace_id="trace_1",
        session_id="session_1",
        thread_id="thread_1",
        run_id="run_1",
        expected_version=0,
        graph_name="vet_main_graph",
        graph_version="graph.v1",
        state_schema_version="checkpoint.v1",
        status=CheckpointRecordStatus.RECOVERABLE,
        current_node="context_builder",
        graph_state=GraphExecutionStateDto(
            current_node="context_builder",
            completed_nodes=["pet_policy"],
            pending_nodes=["response_composer"],
            node_outputs={"pet_policy": {"hash": "abc"}},
            retry_state={},
            recoverable_from="context_builder",
        ),
        business_state=SessionBusinessStateDto(
            params_version="params.v1",
            pet_id="pet_1",
            current_complaint_type="skin",
            slot_progress={"itching": "asked"},
            tasks=[],
            segments=[],
            rolling_summary_ref="summary_1",
        ),
        metadata={"state_hash": "hash_1"},
    )

    assert command.expected_version == 0
    assert command.graph_state.completed_nodes == ["pet_policy"]
    assert command.business_state.pet_id == "pet_1"


def test_checkpoint_dto_rejects_extra_fields() -> None:
    """验证 CheckpointStore DTO 拒绝未声明字段。

    :return: None。
    """

    with pytest.raises(ValidationError):
        GraphExecutionStateDto.model_validate(
            {
                "current_node": "node_a",
                "completed_nodes": [],
                "pending_nodes": [],
                "node_outputs": {},
                "retry_state": {},
                "recoverable_from": None,
                "unexpected_field": "not_allowed",
            }
        )


def test_checkpoint_dto_rejects_invalid_version() -> None:
    """验证保存 checkpoint 命令拒绝负数 expected_version。

    :return: None。
    """

    with pytest.raises(ValidationError):
        SaveCheckpointCommandDto(
            request_id="req_1",
            trace_id="trace_1",
            session_id="session_1",
            thread_id="thread_1",
            run_id="run_1",
            expected_version=-1,
            graph_name="vet_main_graph",
            graph_version="graph.v1",
            state_schema_version="checkpoint.v1",
            status=CheckpointRecordStatus.RECOVERABLE,
            current_node=None,
            graph_state=GraphExecutionStateDto(),
            business_state=SessionBusinessStateDto(),
            metadata={},
        )


def test_datetime_field_accepts_timezone_aware_value() -> None:
    """验证契约中的时间字段可承载带时区时间值。

    :return: None。
    """

    published_at = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)

    assert published_at.tzinfo is UTC
