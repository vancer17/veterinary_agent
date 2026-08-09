"""
文件：src/vet_agent/runtime/embeddings.py
作用：封装模型调用、向量生成与外部运行时能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Any, Protocol

import httpx

from vet_agent import Settings


class EmbeddingClient(Protocol):
    """定义运行时 embedding 客户端契约。"""

    @property
    def available(self) -> bool:
        """检查 embedding 客户端是否具备调用条件。

        :return: 可调用时返回 True，否则返回 False。
        """
        ...

    def embed(self, text: str) -> list[float]:
        """将输入文本转换为 embedding 向量。

        :param text: 待向量化文本。
        :return: 返回浮点向量列表。
        """
        ...


class QwenEmbeddingClient:
    """通过 LiteLLM 代理调用 Qwen embedding 模型。"""

    def __init__(self, settings: Settings) -> None:
        """初始化 Qwen embedding 客户端。

        :param settings: 包含 LiteLLM 地址、密钥和模型名的应用配置。
        :return: 无返回值。
        """
        self.settings = settings

    @property
    def available(self) -> bool:
        """检查 embedding 服务是否具备调用条件。

        :return: LiteLLM 配置完整时返回 True。
        """
        return self.settings.litellm_configured

    def embed(self, text: str) -> list[float]:
        """调用 embedding 接口生成文本向量。

        :param text: 待向量化文本。
        :return: 返回浮点 embedding 向量。
        """
        if not self.available:
            raise RuntimeError("LiteLLM proxy is not configured")
        payload: dict[str, Any] = {
            "model": self.settings.qwen_embedding_model,
            "input": text,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            response = client.post(
                f"{self.settings.litellm_base_url}/embeddings",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return [float(value) for value in data["data"][0]["embedding"]]
