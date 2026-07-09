##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/langgraph_settings.py
# 作用: 定义 LangGraph PostgresSaver 集成所需的配置读取能力，短期复用 DATABASE_URL 作为唯一数据库入口。
# 边界: 仅解析配置，不创建 PostgresSaver、不建立数据库连接、不调用 LangGraph setup 或 GraphRuntime。
##################################################################################################

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from veterinary_agent.checkpoint_store.migrations import DATABASE_URL_ENV_NAME

LANGGRAPH_POSTGRES_SETUP_ON_STARTUP_ENV_NAME: Final[str] = (
    "LANGGRAPH_POSTGRES_SETUP_ON_STARTUP"
)
LANGGRAPH_STRICT_MSGPACK_ENV_NAME: Final[str] = "LANGGRAPH_STRICT_MSGPACK"

_TRUE_ENV_VALUES: Final[frozenset[str]] = frozenset(
    {"1", "true", "t", "yes", "y", "on"}
)
_FALSE_ENV_VALUES: Final[frozenset[str]] = frozenset(
    {"0", "false", "f", "no", "n", "off"}
)


@dataclass(frozen=True, slots=True)
class LangGraphPostgresSaverSettings:
    """LangGraph PostgresSaver 配置。"""

    database_url: str
    setup_on_startup: bool
    strict_msgpack_enabled: bool


def _read_required_env(
    *,
    environ: Mapping[str, str],
    name: str,
) -> str:
    """读取必填环境变量并去除首尾空白。

    :param environ: 环境变量映射。
    :param name: 需要读取的环境变量名称。
    :return: 已去除首尾空白的环境变量值。
    :raises ValueError: 当环境变量不存在或为空字符串时抛出。
    """

    value = environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} 未配置，无法加载 LangGraph PostgresSaver 配置")
    return value


def _parse_bool_env_value(
    *,
    name: str,
    raw_value: str,
) -> bool:
    """解析布尔环境变量值。

    :param name: 正在解析的环境变量名称。
    :param raw_value: 原始环境变量值。
    :return: 解析后的布尔值。
    :raises ValueError: 当环境变量值不是受支持的布尔表示时抛出。
    """

    normalized_value = raw_value.strip().lower()
    if normalized_value in _TRUE_ENV_VALUES:
        return True
    if normalized_value in _FALSE_ENV_VALUES:
        return False
    raise ValueError(f"{name} 必须是布尔值，当前值为 {raw_value!r}")


def _read_bool_env(
    *,
    environ: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    """读取可选布尔环境变量。

    :param environ: 环境变量映射。
    :param name: 需要读取的环境变量名称。
    :param default: 环境变量未配置或为空字符串时使用的默认值。
    :return: 解析后的布尔值。
    :raises ValueError: 当环境变量值不是受支持的布尔表示时抛出。
    """

    raw_value = environ.get(name, "").strip()
    if not raw_value:
        return default
    return _parse_bool_env_value(name=name, raw_value=raw_value)


def load_langgraph_postgres_saver_settings(
    environ: Mapping[str, str] | None = None,
) -> LangGraphPostgresSaverSettings:
    """加载 LangGraph PostgresSaver 配置。

    :param environ: 可选环境变量映射；未传入时读取当前进程环境变量。
    :return: LangGraph PostgresSaver 配置。
    :raises ValueError: 当 DATABASE_URL 缺失或布尔配置值非法时抛出。
    """

    resolved_environ = os.environ if environ is None else environ
    return LangGraphPostgresSaverSettings(
        database_url=_read_required_env(
            environ=resolved_environ,
            name=DATABASE_URL_ENV_NAME,
        ),
        setup_on_startup=_read_bool_env(
            environ=resolved_environ,
            name=LANGGRAPH_POSTGRES_SETUP_ON_STARTUP_ENV_NAME,
            default=True,
        ),
        strict_msgpack_enabled=_read_bool_env(
            environ=resolved_environ,
            name=LANGGRAPH_STRICT_MSGPACK_ENV_NAME,
            default=True,
        ),
    )


__all__: tuple[str, ...] = (
    "LANGGRAPH_POSTGRES_SETUP_ON_STARTUP_ENV_NAME",
    "LANGGRAPH_STRICT_MSGPACK_ENV_NAME",
    "LangGraphPostgresSaverSettings",
    "load_langgraph_postgres_saver_settings",
)
