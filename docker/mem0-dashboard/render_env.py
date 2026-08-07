"""
文件：docker/mem0-dashboard/render_env.py
作用：将 Mem0 Dashboard application.yml 渲染为 Next.js standalone server 识别的环境变量导出语句。
范围：仅处理非敏感服务配置；如未来新增敏感参数，应由 env 文件注入而非写入 application.yml。
说明：该脚本在容器 entrypoint 与 healthcheck 中执行，输出供 POSIX shell eval 使用的 export 语句。
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

import yaml


def _stringify(value: Any) -> str:
    """将 YAML 标量转换为上游服务可读取的字符串。

    :param value: YAML 配置项值。
    :return: 可写入环境变量的字符串值。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _read_mapping(data: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    """读取嵌套 YAML mapping。

    :param data: 已解析的 YAML 根对象。
    :param path: 需要读取的嵌套路径。
    :return: 路径对应的 mapping；缺失时返回空 mapping。
    """
    current: Any = data
    for item in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(item, {})
    return current if isinstance(current, dict) else {}


def _validate_port(name: str, value: Any) -> int:
    """校验端口配置并转换为整数。

    :param name: 配置项名称。
    :param value: 配置项值。
    :return: 合法端口号。
    """
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{name} 必须为整数端口。") from exc
    if port < 1 or port > 65535:
        raise SystemExit(f"{name} 必须位于 1 到 65535 之间。")
    return port


def _validate_api_url(name: str, value: Any, *, allow_relative: bool) -> str:
    """校验 Dashboard API 地址。

    :param name: 配置项名称。
    :param value: 配置项值。
    :param allow_relative: 是否允许同源相对路径。
    :return: 合法 API 地址。
    """
    text = str(value or "").strip()
    if not text:
        raise SystemExit(f"{name} 不得为空。")
    if text.startswith(("http://", "https://")):
        return text.rstrip("/")
    if allow_relative and text.startswith("/"):
        return text.rstrip("/")
    raise SystemExit(f"{name} 必须为 http(s) URL{ ' 或同源相对路径' if allow_relative else '' }。")


def _export(name: str, value: Any) -> str:
    """生成 shell export 语句。

    :param name: 环境变量名称。
    :param value: 环境变量值。
    :return: 可被 shell eval 的 export 语句。
    """
    return f"export {name}={shlex.quote(_stringify(value))}"


def _build_exports(config: dict[str, Any]) -> dict[str, Any]:
    """构建 Mem0 Dashboard 运行所需的非敏感环境变量。

    :param config: 已解析的 application.yml 配置。
    :return: 环境变量名称和值的映射。
    """
    server = _read_mapping(config, ("server",))
    dashboard = _read_mapping(config, ("dashboard",))
    health = _read_mapping(config, ("health",))

    host = str(server.get("host", "0.0.0.0")).strip() or "0.0.0.0"
    port = _validate_port("server.port", server.get("port", 3000))
    public_api_url = _validate_api_url(
        "dashboard.public_api_url",
        dashboard.get("public_api_url", "/api/mem0"),
        allow_relative=True,
    )
    internal_api_url = _validate_api_url(
        "dashboard.internal_api_url",
        dashboard.get("internal_api_url", "http://mem0:8000"),
        allow_relative=False,
    )
    public_url = _validate_api_url(
        "dashboard.public_url",
        dashboard.get("public_url", "http://127.0.0.1:3001"),
        allow_relative=False,
    )
    instance_name = str(dashboard.get("instance_name", "Mem0 Operations")).strip()
    if not instance_name:
        raise SystemExit("dashboard.instance_name 不得为空。")

    health_path = str(health.get("path", "/api/health")).strip()
    if not health_path.startswith("/"):
        raise SystemExit("health.path 必须为以 / 开头的路径。")

    return {
        "HOSTNAME": host,
        "PORT": port,
        "NEXT_PUBLIC_API_URL": public_api_url,
        "NEXT_PUBLIC_INSTANCE_NAME": instance_name,
        "API_INTERNAL_URL": internal_api_url,
        "DASHBOARD_URL": public_url,
        "MEM0_DASHBOARD_HEALTH_PATH": health_path,
    }


def main() -> None:
    """执行命令行入口逻辑。

    :return: 无返回值。
    """
    if len(sys.argv) != 2:
        raise SystemExit("usage: render_env.py <application.yml>")

    config_path = Path(sys.argv[1])
    with config_path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file) or {}

    if not isinstance(raw_config, dict):
        raise SystemExit("Mem0 Dashboard application.yml 根节点必须是 YAML mapping。")

    for name, value in _build_exports(raw_config).items():
        print(_export(name, value))


if __name__ == "__main__":
    main()
