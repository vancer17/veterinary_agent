##################################################################################################
# 文件: src/veterinary_agent/app/__init__.py
# 作用: 作为 ASGI 应用包的统一出口，集中暴露 FastAPI 应用工厂与框架级状态模型。
# 边界: 外部包应从本文件导入 ASGI 应用能力，避免跨包直接引用实现模块。
##################################################################################################

from veterinary_agent.app.factory import create_app
from veterinary_agent.app.state import VeterinaryAgentAppState

__all__: tuple[str, ...] = (
    "VeterinaryAgentAppState",
    "create_app",
)
