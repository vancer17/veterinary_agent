"""临床安全阶段 5 预发布黑盒冒烟测试。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_clinical_safety_stage5_preprod_black_box_smoke() -> None:
    """执行真实预发布 API 的阶段 5 回归冒烟。

    :return: 无返回值；断言通过表示四类阶段 5 行为在预发布环境同时成立。
    """
    if os.getenv("RUN_CLINICAL_SAFETY_STAGE5_PREPROD_TEST", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        pytest.skip(
            "Set RUN_CLINICAL_SAFETY_STAGE5_PREPROD_TEST=true to run preprod smoke."
        )

    subprocess.run(
        [
            "bash",
            "scripts/integration/run-clinical-safety-stage5-preprod-smoke.sh",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
