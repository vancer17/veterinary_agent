"""
文件：src/vet_agent/main.py
作用：提供兽医 Agent 项目的业务实现。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from ingress import create_app


app = create_app()


def main() -> None:
    """执行命令行入口逻辑。

    :return: 返回函数执行结果。
    """
    import uvicorn

    uvicorn.run("vet_agent.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
