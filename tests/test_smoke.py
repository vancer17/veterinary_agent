##################################################################################################
# 文件: tests/test_smoke.py
# 作用: 验证 veterinary_agent 项目根包能够完成最小导入。
# 边界: 只执行包级导入冒烟检查，不创建 ASGI 应用、不加载配置或连接外部依赖。
##################################################################################################

import veterinary_agent


def test_package_importable() -> None:
    """验证项目根包可以被 Python 正常导入。

    :return: None。
    """

    assert veterinary_agent is not None
