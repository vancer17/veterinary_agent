##################################################################################################
# 文件: tests/checkpoint_store/test_langgraph_settings.py
# 作用: 验证 LangGraph PostgresSaver 配置读取能力，确保短期复用 DATABASE_URL 且布尔开关解析稳定。
# 边界: 仅测试 CheckpointStore 公共配置出口，不创建 PostgresSaver、不连接数据库、不调用 LangGraph。
##################################################################################################

import pytest

from veterinary_agent.checkpoint_store import (
    DATABASE_URL_ENV_NAME,
    LANGGRAPH_POSTGRES_SETUP_ON_STARTUP_ENV_NAME,
    LANGGRAPH_STRICT_MSGPACK_ENV_NAME,
    load_langgraph_postgres_saver_settings,
)


def test_load_langgraph_postgres_saver_settings_reuses_database_url() -> None:
    """验证 LangGraph PostgresSaver 配置复用 DATABASE_URL。

    :return: None。
    """

    settings = load_langgraph_postgres_saver_settings(
        {
            DATABASE_URL_ENV_NAME: "postgresql+psycopg://user:pass@db/app",
        }
    )

    assert settings.database_url == "postgresql+psycopg://user:pass@db/app"
    assert settings.setup_on_startup is True
    assert settings.strict_msgpack_enabled is True


def test_load_langgraph_postgres_saver_settings_reads_boolean_overrides() -> None:
    """验证 LangGraph PostgresSaver 布尔开关支持环境变量覆盖。

    :return: None。
    """

    settings = load_langgraph_postgres_saver_settings(
        {
            DATABASE_URL_ENV_NAME: "postgresql+psycopg://user:pass@db/app",
            LANGGRAPH_POSTGRES_SETUP_ON_STARTUP_ENV_NAME: "false",
            LANGGRAPH_STRICT_MSGPACK_ENV_NAME: "0",
        }
    )

    assert settings.setup_on_startup is False
    assert settings.strict_msgpack_enabled is False


def test_load_langgraph_postgres_saver_settings_rejects_missing_database_url() -> None:
    """验证缺失 DATABASE_URL 时配置读取会明确失败。

    :return: None。
    """

    with pytest.raises(ValueError, match="DATABASE_URL"):
        load_langgraph_postgres_saver_settings({})


def test_load_langgraph_postgres_saver_settings_rejects_invalid_bool() -> None:
    """验证非法布尔环境变量值会明确失败。

    :return: None。
    """

    with pytest.raises(ValueError, match=LANGGRAPH_STRICT_MSGPACK_ENV_NAME):
        load_langgraph_postgres_saver_settings(
            {
                DATABASE_URL_ENV_NAME: "postgresql+psycopg://user:pass@db/app",
                LANGGRAPH_STRICT_MSGPACK_ENV_NAME: "sometimes",
            }
        )
