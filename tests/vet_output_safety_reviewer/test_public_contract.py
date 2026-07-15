##################################################################################################
# 文件: tests/vet_output_safety_reviewer/test_public_contract.py
# 作用: 验证 VetOutputSafetyReviewer 一级包的公共出口是否完整可导入。
# 边界: 只做包面契约断言，不执行业务审查或调用真实依赖。
##################################################################################################

import veterinary_agent.vet_output_safety_reviewer as output_review_pkg


def test_package_exports_core_output_review_contracts() -> None:
    """验证一级包会导出组件核心公共契约。

    :return: None。
    """

    assert "create_default_vet_output_safety_reviewer" in output_review_pkg.__all__
    assert (
        "create_vet_output_safety_reviewer_guardrail_handler"
        in output_review_pkg.__all__
    )
    assert "LogicTraceVetOutputSafetyReviewerTraceSink" in output_review_pkg.__all__
    assert output_review_pkg.VetOutputSafetyReviewerGuardrailHandler is not None
