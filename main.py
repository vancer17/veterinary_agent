##################################################################################################
# 文件: main.py
# 作用: 提供本地开发运行入口，通过 Uvicorn 托管 FastAPI ASGI 应用工厂。
# 边界: 仅负责启动 HTTP Server，不承载 ApiIngress 路由、编排调用或兽医业务逻辑。
##################################################################################################

from typing import Final

import uvicorn

APP_FACTORY_IMPORT: Final[str] = "veterinary_agent.app:create_app"


def main() -> None:
    """启动本地 Uvicorn ASGI 服务。

    :return: 无返回值。
    """

    uvicorn.run(
        APP_FACTORY_IMPORT,
        factory=True,
        host="127.0.0.1",
        port=8000,
    )


if __name__ == "__main__":
    main()
