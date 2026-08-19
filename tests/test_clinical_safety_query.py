"""
=============================================================================
文件：tests/test_clinical_safety_query.py
作用：验证临床安全召回请求将向量正文、宠物画像范围和证据门槛严格分离。
范围：覆盖阶段 2 收紧召回输入后的结构化查询契约，不验证向量数据库排序或 OPA 医学动作裁决。
说明：测试只通过 vet_agent.clinical_safety 包顶层导出对象构造查询，避免依赖内部模块实现。
=============================================================================
"""

from __future__ import annotations

import pytest

from vet_agent.clinical_safety import (
    ClinicalSafetyAgeGroup,
    ClinicalSafetyAsset,
    ClinicalSafetyRetrievalRequest,
    ClinicalSafetyRetrievalScope,
    ClinicalSafetyRiskEvidenceState,
    ClinicalSafetySemanticResult,
    ClinicalSafetySex,
    ClinicalSafetySpecies,
)


def _scope_asset(
    *,
    species_scope: tuple[str, ...] = (),
    sex_scope: tuple[str, ...] = (),
    age_scope: tuple[str, ...] = (),
) -> ClinicalSafetyAsset:
    """构造范围语义矩阵测试使用的临床安全资产。

    :param species_scope: 资产适用物种范围。
    :param sex_scope: 资产适用性别范围。
    :param age_scope: 资产适用年龄阶段范围。
    :return: 返回仅范围字段不同的测试资产。
    """
    return ClinicalSafetyAsset(
        asset_id="safety_scope_matrix",
        asset_type="danger_pattern",
        canonical_name="范围矩阵测试资产",
        category="测试",
        species_scope=species_scope,
        sex_scope=sex_scope,
        age_scope=age_scope,
        severity="caution",
        action_class="safety_warning",
        code="SCOPE_MATRIX_RISK",
    )


def _trusted_semantic(
    *,
    risk_evidence_state: ClinicalSafetyRiskEvidenceState,
    species: ClinicalSafetySpecies = "dog",
    sex: ClinicalSafetySex = "female",
    age_group: ClinicalSafetyAgeGroup = "adult",
) -> ClinicalSafetySemanticResult:
    """构造阶段 2 查询契约测试使用的结构化语义结果。

    :param risk_evidence_state: 当前回合风险证据充分性状态。
    :param species: 可信结构化物种。
    :param sex: 可信结构化性别。
    :param age_group: 可信结构化年龄阶段。
    :return: 返回用于验证召回输入边界的语义结果。
    """
    return ClinicalSafetySemanticResult(
        species=species,
        sex=sex,
        age_group=age_group,
        exposure_state="confirmed",
        symptom_state="present",
        intent_type="symptom",
        risk_evidence_state=risk_evidence_state,
        confidence=0.95,
        strategy="litellm_response_format",
        source_text="用户原始文本只允许作为查询正文来源。",
    )


def test_sufficient_request_keeps_only_user_text_in_query_body() -> None:
    """验证证据充分时查询正文只保留用户本轮事实文本。

    :return: 无返回值；断言通过表示画像、意图和结构化状态未被拼入 embedding 正文。
    """
    semantic = _trusted_semantic(risk_evidence_state="sufficient")

    request = ClinicalSafetyRetrievalRequest.from_semantic_result(
        "我家成年雌性犬今天误食药物后开始呕吐。",
        semantic,
    )

    assert request.is_searchable()
    assert request.query_text == "我家成年雌性犬今天误食药物后开始呕吐。"
    assert "species=" not in request.query_text
    assert "intent_type" not in request.query_text
    assert request.scope == ClinicalSafetyRetrievalScope(
        species="dog",
        sex="female",
        age_group="adult",
    )


def test_insufficient_request_skips_strong_retrieval_without_profile_fallback() -> None:
    """验证证据不足时不会因结构化宠物画像进入强召回。

    :return: 无返回值；断言通过表示画像只保留为范围信息，不能替代本轮风险事实。
    """
    semantic = _trusted_semantic(risk_evidence_state="insufficient")

    request = ClinicalSafetyRetrievalRequest.from_semantic_result(
        "成年雌性犬什么情况下需要去医院？",
        semantic,
    )

    assert not request.is_searchable()
    assert request.query_text == ""
    assert request.scope.species == "dog"
    assert request.skip_reason() == "risk_evidence_not_sufficient"


def test_unknown_semantic_result_fails_fast_without_query_or_scope_inference() -> None:
    """验证缺失或不可信语义时不从原文推断召回范围和查询资格。

    :return: 无返回值；断言通过表示语义失败不会回退到画像字符串或关键词路径。
    """
    request = ClinicalSafetyRetrievalRequest.from_semantic_result(
        "猫可能误食了某种东西。",
        None,
    )

    assert not request.is_searchable()
    assert request.query_text == ""
    assert request.scope == ClinicalSafetyRetrievalScope()
    assert request.skip_reason() == "risk_evidence_unknown"


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (ClinicalSafetyRetrievalScope(species="unknown"), True),
        (ClinicalSafetyRetrievalScope(species="dog"), True),
        (ClinicalSafetyRetrievalScope(species="cat"), False),
    ],
)
def test_species_scope_matrix_keeps_recall_and_decision_semantics_aligned(
    scope: ClinicalSafetyRetrievalScope,
    expected: bool,
) -> None:
    """验证物种范围判断与 OPA context_mismatch 使用同一语义矩阵。

    :param scope: 当前结构化召回范围。
    :param expected: 资产限定 dog 时的匹配期望。
    :return: 无返回值；断言通过表示空范围不限制、未知值不推断、失配才排除。
    """
    assert scope.matches_asset(_scope_asset(species_scope=("dog",))) is expected


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (ClinicalSafetyRetrievalScope(sex="unknown"), True),
        (ClinicalSafetyRetrievalScope(sex="female"), True),
        (ClinicalSafetyRetrievalScope(sex="male"), False),
    ],
)
def test_sex_scope_matrix_keeps_recall_and_decision_semantics_aligned(
    scope: ClinicalSafetyRetrievalScope,
    expected: bool,
) -> None:
    """验证性别范围判断与 OPA context_mismatch 使用同一语义矩阵。

    :param scope: 当前结构化召回范围。
    :param expected: 资产限定 female 时的匹配期望。
    :return: 无返回值；断言通过表示空范围不限制、未知值不推断、失配才排除。
    """
    assert scope.matches_asset(_scope_asset(sex_scope=("female",))) is expected


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (ClinicalSafetyRetrievalScope(age_group="unknown"), True),
        (ClinicalSafetyRetrievalScope(age_group="adult"), True),
        (ClinicalSafetyRetrievalScope(age_group="juvenile"), False),
    ],
)
def test_age_scope_matrix_keeps_recall_and_decision_semantics_aligned(
    scope: ClinicalSafetyRetrievalScope,
    expected: bool,
) -> None:
    """验证年龄范围判断与 OPA context_mismatch 使用同一语义矩阵。

    :param scope: 当前结构化召回范围。
    :param expected: 资产限定 adult 时的匹配期望。
    :return: 无返回值；断言通过表示空范围不限制、未知值不推断、失配才排除。
    """
    assert scope.matches_asset(_scope_asset(age_scope=("adult",))) is expected


def test_empty_asset_scope_matches_all_structured_values() -> None:
    """验证资产空范围表示该维度不限制。

    :return: 无返回值；断言通过表示通用资产不会因结构化画像值被错误排除。
    """
    scope = ClinicalSafetyRetrievalScope(species="cat", sex="male", age_group="senior")

    assert scope.matches_asset(_scope_asset()) is True


def test_sufficient_request_truncates_overlong_query_text() -> None:
    """验证超长查询正文只保留头部主诉片段。

    :return: 无返回值；断言通过表示召回入口具备确定性长度上限，且不因超长跳过召回。
    """
    semantic = _trusted_semantic(risk_evidence_state="sufficient")
    long_text = "犬持续呕吐" + "补充描述" * 800

    request = ClinicalSafetyRetrievalRequest.from_semantic_result(long_text, semantic)

    assert request.is_searchable()
    assert len(request.normalized_query_text()) == 2000
    assert request.normalized_query_text().startswith("犬持续呕吐补充描述")
