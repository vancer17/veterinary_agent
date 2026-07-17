"""
文件：src/vet_agent/runtime/embeddings.py
作用：封装模型调用、向量生成与外部运行时能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Any

import httpx

from vet_agent import Settings


class QwenEmbeddingClient:
    """OpenAI-compatible LiteLLM proxy embedding client."""

    def __init__(self, settings: Settings) -> None:
        """初始化当前对象。

        :param settings: 应用配置对象。
        :return: 无返回值。
        """
        self.settings = settings

    @property
    def available(self) -> bool:
        """执行 available 业务逻辑。

        :return: 返回函数执行结果。
        """
        return self.settings.litellm_configured

    def embed(self, text: str) -> list[float]:
        """执行 embed 业务逻辑。

        :param text: 待处理文本。
        :return: 返回函数执行结果。
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
