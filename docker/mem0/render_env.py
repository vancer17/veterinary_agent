# =============================================================================
# 文件: docker/mem0/render_env.py
# 作用: 加载并校验 Mem0 application.yml，将非敏感配置渲染为环境变量导出语句。
# 范围: 仅负责结构化配置、默认值、参数验证和 shell 安全转义；不读取或输出敏感参数。
# 说明: 该脚本由 Mem0 entrypoint 在容器内调用，Pydantic 模型与 application.yml 共同构成配置契约。
# =============================================================================

from __future__ import annotations

import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final
from urllib.parse import urlsplit

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
)

DEFAULT_SERVER_HOST: Final[str] = "0.0.0.0"
DEFAULT_SERVER_PORT: Final[int] = 8000
DEFAULT_SERVER_WORKERS: Final[int] = 1
DEFAULT_OPENAI_BASE_URL: Final[str] = "http://litellm:4000/v1"
DEFAULT_LLM_MODEL: Final[str] = "qwen-plus"
DEFAULT_EMBEDDER_MODEL: Final[str] = "text-embedding-v4"
DEFAULT_EMBEDDING_MODEL_DIMS: Final[int] = 1024
DEFAULT_POSTGRES_HOST: Final[str] = "postgres"
DEFAULT_POSTGRES_PORT: Final[int] = 5432
DEFAULT_VECTOR_DATABASE: Final[str] = "mem0_vector"
DEFAULT_POSTGRES_USER: Final[str] = "mem0"
DEFAULT_COLLECTION_NAME: Final[str] = "vet_agent_memories"
DEFAULT_APPLICATION_DATABASE: Final[str] = "mem0_app"
DEFAULT_DASHBOARD_URL: Final[str] = "http://localhost:3000"
DEFAULT_TELEMETRY_STATE_PATH: Final[str] = "/app/history/telemetry.json"
DEFAULT_HISTORY_DATABASE_PATH: Final[str] = "/app/history/history.db"

type ExportValue = str | int | bool | Path


def _validate_http_url(value: str) -> str:
    """验证 URL 字符串必须为 http 或 https 地址，并保持原始字符串形式。

    :param value: 待验证的 URL 字符串。
    :return: 通过验证的原始 URL 字符串。
    :raises ValueError: URL 为空、协议非法或缺少网络位置时抛出。
    """
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL 必须为包含主机的 http 或 https 地址。")
    return value


def _validate_absolute_path(value: Path) -> Path:
    """验证容器内持久化文件路径必须为绝对路径。

    :param value: 待验证的文件路径。
    :return: 通过验证的绝对路径。
    :raises ValueError: 路径不是绝对路径时抛出。
    """
    if not value.is_absolute():
        raise ValueError("路径必须使用绝对路径。")
    return value


type AbsolutePath = Annotated[Path, AfterValidator(_validate_absolute_path)]
type HttpUrlString = Annotated[StrictStr, AfterValidator(_validate_http_url)]


class Mem0ConfigModel(BaseModel):
    """定义 Mem0 配置模型的公共校验策略。

    所有未知配置字段均直接拒绝，避免 YAML 拼写错误静默回退到默认值。
    模型设置为不可变，防止渲染过程中意外修改已完成校验的配置对象。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class ServerConfig(Mem0ConfigModel):
    """定义 Mem0 REST Server 的监听与进程配置。"""

    host: StrictStr = Field(
        default=DEFAULT_SERVER_HOST,
        min_length=1,
        description="Mem0 REST Server 在容器内监听的地址。",
    )
    port: StrictInt = Field(
        default=DEFAULT_SERVER_PORT,
        ge=1,
        le=65535,
        description="Mem0 REST Server 在容器内监听的端口。",
    )
    workers: StrictInt = Field(
        default=DEFAULT_SERVER_WORKERS,
        ge=1,
        description="uvicorn worker 数量。",
    )


class LLMConfig(Mem0ConfigModel):
    """定义 LiteLLM OpenAI 兼容代理与默认模型配置。"""

    openai_base_url: HttpUrlString = Field(
        default=DEFAULT_OPENAI_BASE_URL,
        description="Mem0 访问 OpenAI 兼容 LiteLLM Proxy 的基础地址。",
    )
    openai_api_base: HttpUrlString | None = Field(
        default=None,
        description="兼容旧版 OpenAI SDK 的基础地址；为空时回退 openai_base_url。",
    )
    default_llm_model: StrictStr = Field(
        default=DEFAULT_LLM_MODEL,
        min_length=1,
        description="Mem0 默认对话模型名称。",
    )
    default_embedder_model: StrictStr = Field(
        default=DEFAULT_EMBEDDER_MODEL,
        min_length=1,
        description="Mem0 默认 embedding 模型名称。",
    )
    embedding_model_dims: StrictInt = Field(
        default=DEFAULT_EMBEDDING_MODEL_DIMS,
        gt=0,
        description="embedding 模型输出向量维度，必须与 pgvector 维度一致。",
    )


class VectorStoreConfig(Mem0ConfigModel):
    """定义 Mem0 pgvector 语义记忆库连接配置。"""

    host: StrictStr = Field(
        default=DEFAULT_POSTGRES_HOST,
        min_length=1,
        description="PostgreSQL 服务名或主机地址。",
    )
    port: StrictInt = Field(
        default=DEFAULT_POSTGRES_PORT,
        ge=1,
        le=65535,
        description="PostgreSQL 容器内端口。",
    )
    database: StrictStr = Field(
        default=DEFAULT_VECTOR_DATABASE,
        min_length=1,
        description="Mem0 语义记忆向量库名称。",
    )
    user: StrictStr = Field(
        default=DEFAULT_POSTGRES_USER,
        min_length=1,
        description="Mem0 访问向量库的数据库用户。",
    )
    collection_name: StrictStr = Field(
        default=DEFAULT_COLLECTION_NAME,
        min_length=1,
        description="Mem0 pgvector collection 名称。",
    )


class ApplicationDatabaseConfig(Mem0ConfigModel):
    """定义 Mem0 REST Server 应用数据逻辑库配置。"""

    database: StrictStr = Field(
        default=DEFAULT_APPLICATION_DATABASE,
        min_length=1,
        description="Mem0 用户、API key、请求日志和配置覆盖所在的逻辑库。",
    )


class DatabaseConfig(Mem0ConfigModel):
    """定义 Mem0 运行所需的 PostgreSQL 逻辑库集合。"""

    vector_store: VectorStoreConfig = Field(
        default_factory=VectorStoreConfig,
        description="Mem0 语义记忆向量库存储配置。",
    )
    application: ApplicationDatabaseConfig = Field(
        default_factory=ApplicationDatabaseConfig,
        description="Mem0 REST Server 应用数据配置。",
    )


class AuthConfig(Mem0ConfigModel):
    """定义 Mem0 REST Server 鉴权开关与 Dashboard 地址配置。"""

    disabled: StrictBool = Field(
        default=False,
        description="是否关闭 Mem0 鉴权；正式环境必须保持 false。",
    )
    dashboard_url: HttpUrlString = Field(
        default=DEFAULT_DASHBOARD_URL,
        description="Dashboard 地址，用于上游 CORS 与提示日志兼容。",
    )


class TelemetryConfig(Mem0ConfigModel):
    """定义 Mem0 上游匿名遥测开关与状态文件路径。"""

    enabled: StrictBool = Field(
        default=False,
        description="是否启用 Mem0 上游匿名遥测。",
    )
    state_path: AbsolutePath = Field(
        default=Path(DEFAULT_TELEMETRY_STATE_PATH),
        description="匿名遥测状态文件的容器内绝对路径。",
    )


class RuntimeConfig(Mem0ConfigModel):
    """定义 Mem0 本地历史数据库文件路径。"""

    history_db_path: AbsolutePath = Field(
        default=Path(DEFAULT_HISTORY_DATABASE_PATH),
        description="Mem0 history SQLite 文件的容器内绝对路径。",
    )


class Mem0ApplicationConfig(Mem0ConfigModel):
    """定义 Mem0 application.yml 的完整非敏感配置结构。"""

    server: ServerConfig = Field(
        default_factory=ServerConfig,
        description="Mem0 REST Server 监听配置。",
    )
    llm: LLMConfig = Field(
        default_factory=LLMConfig,
        description="LiteLLM 代理与默认模型配置。",
    )
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description="Mem0 PostgreSQL 逻辑库配置。",
    )
    auth: AuthConfig = Field(
        default_factory=AuthConfig,
        description="Mem0 鉴权与 Dashboard 配置。",
    )
    telemetry: TelemetryConfig = Field(
        default_factory=TelemetryConfig,
        description="Mem0 匿名遥测配置。",
    )
    runtime: RuntimeConfig = Field(
        default_factory=RuntimeConfig,
        description="Mem0 本地运行时文件配置。",
    )


def _stringify(value: ExportValue) -> str:
    """将已校验的配置值转换为 shell 环境变量字符串。

    :param value: Pydantic 校验后的配置值。
    :return: 可交给 shell 转义逻辑处理的字符串。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _export(name: str, value: ExportValue) -> str:
    """生成经过 shell 安全转义的 export 语句。

    :param name: 环境变量名称。
    :param value: 已校验的环境变量值。
    :return: 可被 POSIX shell eval 执行的 export 语句。
    """
    return f"export {name}={shlex.quote(_stringify(value))}"


def _build_exports(config: Mem0ApplicationConfig) -> dict[str, ExportValue]:
    """将结构化 Mem0 配置映射为上游服务识别的环境变量。

    :param config: 已通过 Pydantic 校验的 Mem0 application 配置。
    :return: 非敏感环境变量名称和值的映射。
    """
    llm = config.llm
    vector_store = config.database.vector_store
    exports: dict[str, ExportValue] = {
        "MEM0_SERVER_HOST": config.server.host,
        "MEM0_SERVER_PORT": config.server.port,
        "MEM0_SERVER_WORKERS": config.server.workers,
        "OPENAI_BASE_URL": llm.openai_base_url,
        "OPENAI_API_BASE": llm.openai_api_base or llm.openai_base_url,
        "MEM0_DEFAULT_LLM_MODEL": llm.default_llm_model,
        "MEM0_DEFAULT_EMBEDDER_MODEL": llm.default_embedder_model,
        "MEM0_EMBEDDING_MODEL_DIMS": llm.embedding_model_dims,
        "POSTGRES_HOST": vector_store.host,
        "POSTGRES_PORT": vector_store.port,
        "POSTGRES_DB": vector_store.database,
        "POSTGRES_USER": vector_store.user,
        "POSTGRES_COLLECTION_NAME": vector_store.collection_name,
        "APP_DB_NAME": config.database.application.database,
        "AUTH_DISABLED": config.auth.disabled,
        "DASHBOARD_URL": config.auth.dashboard_url,
        "MEM0_TELEMETRY": config.telemetry.enabled,
        "MEM0_TELEMETRY_STATE_PATH": config.telemetry.state_path,
        "HISTORY_DB_PATH": config.runtime.history_db_path,
    }
    return exports


def _format_validation_error(error: ValidationError) -> str:
    """格式化 Pydantic 配置校验错误。

    :param error: Pydantic 配置校验异常。
    :return: 适合直接输出给运维人员的多行错误文本。
    """
    lines = ["Mem0 application.yml 配置校验失败:"]
    for item in error.errors():
        location_items = item.get("loc", ())
        location = ".".join(str(part) for part in location_items) if location_items else "<root>"
        lines.append(f"- {location}: {item['msg']}")
    return "\n".join(lines)


def _load_config(config_path: Path) -> Mem0ApplicationConfig:
    """读取 YAML 文件并构造已校验的 Mem0 配置模型。

    :param config_path: Mem0 application.yml 文件路径。
    :return: 已完成结构和字段验证的 Mem0 配置模型。
    :raises OSError: 配置文件无法读取时抛出。
    :raises TypeError: YAML 根节点不是 mapping 时抛出。
    :raises yaml.YAMLError: YAML 语法解析失败时抛出。
    :raises ValidationError: 配置字段类型或约束验证失败时抛出。
    """
    with config_path.open("r", encoding="utf-8") as file:
        raw_config: object = yaml.safe_load(file)

    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise TypeError("Mem0 application.yml 根节点必须是 YAML mapping。")

    return Mem0ApplicationConfig.model_validate(raw_config)


def main(argv: Sequence[str] | None = None) -> int:
    """执行 Mem0 application.yml 配置渲染命令。

    :param argv: 命令行参数；为空时读取 sys.argv[1:]。
    :return: 进程退出码；成功为 0，参数或配置错误为 2。
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: render_env.py <application.yml>", file=sys.stderr)
        return 2

    config_path = Path(arguments[0])
    try:
        config = _load_config(config_path)
    except ValidationError as error:
        print(_format_validation_error(error), file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"Mem0 application.yml 不存在: {config_path}", file=sys.stderr)
        return 2
    except yaml.YAMLError as error:
        print(f"Mem0 application.yml YAML 解析失败: {error}", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError) as error:
        print(f"Mem0 application.yml 加载失败: {error}", file=sys.stderr)
        return 2

    for name, value in _build_exports(config).items():
        print(_export(name, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
