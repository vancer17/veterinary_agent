##################################################################################################
# 文件: tests/app/test_dependencies.py
# 作用: 验证 FastAPI 依赖层只负责读取统一应用状态容器。
# 边界: 不为各个组件复制透传 getter，不连接数据库，不执行 Agent 编排。
##################################################################################################

from typing import cast

import pytest
from fastapi import FastAPI, Request

from veterinary_agent.app import VeterinaryAgentAppState
from veterinary_agent.app.dependencies import APP_STATE_KEY, get_app_state


def _build_request(state: VeterinaryAgentAppState | None) -> Request:
    """构建带有可选应用状态的测试请求。

    :param state: 需要挂载到 FastAPI app.state 的状态对象。
    :return: 可传入应用依赖函数的请求对象。
    """

    app = FastAPI()
    if state is not None:
        setattr(app.state, APP_STATE_KEY, state)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "app": app,
        }
    )


def test_get_app_state_returns_mounted_state() -> None:
    """验证依赖函数返回组合根挂载的同一状态对象。

    :return: None。
    """

    state = cast(VeterinaryAgentAppState, object())

    assert get_app_state(_build_request(state)) is state


def test_get_app_state_rejects_missing_state() -> None:
    """验证应用状态缺失时依赖函数明确失败。

    :return: None。
    :raises RuntimeError: 由 get_app_state 抛出并被测试捕获。
    """

    with pytest.raises(RuntimeError, match="应用状态尚未初始化"):
        get_app_state(_build_request(None))
