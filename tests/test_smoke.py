#########################################################################
# 模块：tests.test_smoke
# 用途：验证当前 Python 包可被正常导入。
# 层级：测试层；pytest 冒烟测试。
# 契约：仅导入项目公开包入口。
#########################################################################

from __future__ import annotations

import veterinary_agent


def test_package_importable() -> None:
    """校验项目根包可导入。

    :return: 无返回值。
    :rtype: None
    """

    assert veterinary_agent is not None
