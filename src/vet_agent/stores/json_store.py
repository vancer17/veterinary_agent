"""
文件：src/vet_agent/stores/json_store.py
作用：提供轻量文件存储封装。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class JsonDocumentStore:
    def __init__(self, path: Path) -> None:
        """初始化当前对象。

        :param path: 文件或接口路径。
        :return: 无返回值。
        """
        self.path = path
        self.lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        """加载结构化数据。

        :return: 返回函数执行结果。
        """
        with self.lock:
            if not self.path.exists():
                return {}
            return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any]) -> None:
        """保存结构化数据。

        :param data: 结构化数据。
        :return: 返回函数执行结果。
        """
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def append_jsonl(self, item: dict[str, Any]) -> None:
        """执行 append_jsonl 业务逻辑。

        :param item: 单条数据项。
        :return: 返回函数执行结果。
        """
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
