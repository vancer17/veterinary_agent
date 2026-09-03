"""
=============================================================================
文件：tests/test_semantic_collaboration_skill_catalog.py
作用：验证受限语义协作 DAG M01 Skill 契约与目录的生产门禁。
范围：覆盖 SkillSpec 权威契约、SkillCatalog 冲突校验、修复映射闭合、
      SKILL.md 投影一致性以及领域隔离负例。
说明：本测试不依赖数据库、LiteLLM、OPA 或 V8 之后任何实验 runner。
=============================================================================
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from vet_agent.semantic_collaboration import (
    CLAIM_INVENTORY_SPEC,
    DOMAIN_ISOLATED_CONTEXT_RESOURCES,
    PRODUCTION_SEMANTIC_SKILL_SPECS,
    SEMANTIC_REVIEW_SPEC,
    SkillCatalog,
    SkillCatalogError,
    SkillContractError,
    SkillDocProjection,
    SkillProjectionError,
    SkillRegistry,
    SkillSpec,
    build_production_skill_catalog,
    projection_metadata_from_spec,
    render_skill_projection,
)


def _variant(spec: SkillSpec, updates: dict[str, object]) -> SkillSpec:
    """基于既有 SkillSpec 构造带新投影的测试变体。

    :param spec: 原始权威 SkillSpec。
    :param updates: 需要覆盖的字段字典。
    :return: 返回重新通过契约校验的 SkillSpec 变体。
    """
    payload = spec.model_dump(mode="json")
    payload.update(updates)
    metadata = replace(
        projection_metadata_from_spec(spec),
        skill_id=payload["skill_id"],
        skill_version=payload["skill_version"],
    )
    payload["prompt_projection"] = render_skill_projection(metadata).model_dump(
        mode="json"
    )
    return type(spec).model_validate(payload)


def test_production_catalog_is_closed_and_frozen() -> None:
    """验证生产 SkillCatalog 可闭合冻结并保留全部正交任务。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()

    assert catalog.frozen is True
    assert len(catalog.list_specs()) == len(PRODUCTION_SEMANTIC_SKILL_SPECS) == 5
    assert catalog.require("turn_intent", "2.0.0").skill_id == "turn_intent"
    assert len(catalog.ownership_matrix().records) > 0
    assert (
        catalog.contract_digest() == build_production_skill_catalog().contract_digest()
    )


def test_production_catalog_preserves_domain_isolation() -> None:
    """验证所有生产 SKILL 都显式禁止下游领域状态与未验证输出。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()

    for spec in catalog.list_specs():
        forbidden = set(spec.context_contract.forbidden_resources)
        assert DOMAIN_ISOLATED_CONTEXT_RESOURCES.issubset(forbidden)
        assert spec.context_contract.requires_snapshot_digest is True


def test_frozen_catalog_exposes_readonly_registry() -> None:
    """验证生产目录可暴露只读 SkillRegistry 与修复映射目录。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()
    registry = catalog.registry()

    assert isinstance(registry, SkillRegistry)
    assert registry.require("semantic_repair", "1.0.0").task_kind.value == "repair"
    assert registry.contract_digest() == catalog.contract_digest()
    assert any(
        record.mapping.repair_skill_id == "semantic_repair"
        for record in registry.repair_mappings()
    )

    unfrozen = SkillCatalog(initial_specs=(CLAIM_INVENTORY_SPEC,))
    with pytest.raises(SkillCatalogError, match="requires a frozen catalog"):
        SkillRegistry(unfrozen)


def test_duplicate_identity_and_frozen_registration_are_blocked() -> None:
    """验证重复身份与冻结后动态注册均显式失败。

    :return: 无返回值。
    """
    catalog = SkillCatalog(initial_specs=(CLAIM_INVENTORY_SPEC,))

    with pytest.raises(SkillCatalogError, match="duplicate skill identity"):
        catalog.register(CLAIM_INVENTORY_SPEC)

    frozen_catalog = SkillCatalog()
    frozen_catalog.freeze()
    with pytest.raises(SkillCatalogError, match="skill catalog is frozen"):
        frozen_catalog.register(
            _variant(CLAIM_INVENTORY_SPEC, {"skill_id": "late_skill"})
        )


def test_exact_and_parent_field_ownership_conflicts_are_blocked() -> None:
    """验证相同与父子覆盖字段所有权都会在注册时失败。

    :return: 无返回值。
    """
    exact_conflict = _variant(
        SEMANTIC_REVIEW_SPEC,
        {
            "skill_id": "exact_conflict",
            "owns": [{"path": "review.verdict"}],
        },
    )
    parent_conflict = _variant(
        SEMANTIC_REVIEW_SPEC,
        {
            "skill_id": "parent_conflict",
            "owns": [{"path": "review"}],
        },
    )

    exact_catalog = SkillCatalog(initial_specs=(SEMANTIC_REVIEW_SPEC,))
    with pytest.raises(SkillCatalogError, match="field ownership conflict"):
        exact_catalog.register(exact_conflict)

    parent_catalog = SkillCatalog(initial_specs=(SEMANTIC_REVIEW_SPEC,))
    with pytest.raises(SkillCatalogError, match="field ownership conflict"):
        parent_catalog.register(parent_conflict)


def test_parent_owned_and_forbidden_path_conflict_is_blocked() -> None:
    """验证同一 SkillSpec 内父子路径的 owns 与 forbidden 冲突会失败。

    :return: 无返回值。
    """
    payload = SEMANTIC_REVIEW_SPEC.model_dump(mode="json")
    payload["skill_id"] = "invalid_forbidden_scope"
    payload["forbidden_output"] = [{"path": "review"}]
    metadata = replace(
        projection_metadata_from_spec(SEMANTIC_REVIEW_SPEC),
        skill_id=payload["skill_id"],
    )
    payload["prompt_projection"] = render_skill_projection(metadata).model_dump(
        mode="json"
    )

    with pytest.raises(SkillContractError, match="owned and forbidden"):
        type(SEMANTIC_REVIEW_SPEC).model_validate(payload)


def test_unregistered_repair_target_fails_catalog_validation() -> None:
    """验证修复映射必须在目录内闭合到 Repair SKILL。

    :return: 无返回值。
    """
    payload = CLAIM_INVENTORY_SPEC.model_dump(mode="json")
    payload["skill_id"] = "broken_repair_source"
    payload["repair_mappings"][0]["repair_skill_version"] = "9.9.9"
    metadata = replace(
        projection_metadata_from_spec(CLAIM_INVENTORY_SPEC),
        skill_id=payload["skill_id"],
    )
    payload["prompt_projection"] = render_skill_projection(metadata).model_dump(
        mode="json"
    )
    broken = type(CLAIM_INVENTORY_SPEC).model_validate(payload)
    catalog = SkillCatalog(initial_specs=(broken,))

    with pytest.raises(SkillCatalogError, match="repair target is not registered"):
        catalog.validate()


def test_missing_verifier_and_unknown_contract_values_are_blocked() -> None:
    """验证缺失 verifier、未知上下文和未知失败码均无法构造 SkillSpec。

    :return: 无返回值。
    """
    verifier_payload = CLAIM_INVENTORY_SPEC.model_dump(mode="json")
    verifier_payload.pop("verifier_binding")
    with pytest.raises(ValidationError):
        type(CLAIM_INVENTORY_SPEC).model_validate(verifier_payload)

    context_payload = CLAIM_INVENTORY_SPEC.model_dump(mode="json")
    context_payload["context_contract"]["required_resources"] = ["unknown_context"]
    with pytest.raises(ValidationError):
        type(CLAIM_INVENTORY_SPEC).model_validate(context_payload)

    failure_payload = CLAIM_INVENTORY_SPEC.model_dump(mode="json")
    failure_payload["repair_mappings"][0]["failure_code"] = "unknown_failure"
    with pytest.raises(ValidationError):
        type(CLAIM_INVENTORY_SPEC).model_validate(failure_payload)


def test_skill_projection_missing_section_is_blocked() -> None:
    """验证 SKILL.md 缺少必需段时无法通过启动期投影校验。

    :return: 无返回值。
    """
    projection = projection_metadata_from_spec(CLAIM_INVENTORY_SPEC)
    document = render_skill_projection(projection).document.replace(
        "## Safety Boundary\n",
        "",
        1,
    )

    with pytest.raises(SkillProjectionError, match="missing sections"):
        SkillDocProjection(
            document=document,
            content_sha256=CLAIM_INVENTORY_SPEC.prompt_projection.content_sha256,
        )


def test_semantic_collaboration_functions_have_rest_docs_and_no_lambdas() -> None:
    """验证 M01 公共函数均具备中文 ReST 说明且未使用无文档 lambda。

    :return: 无返回值。
    """
    package_root = Path(__file__).parents[1] / "src/vet_agent/semantic_collaboration"
    python_files = sorted(package_root.glob("*.py"))

    assert len(python_files) >= 5
    for python_file in python_files:
        tree = ast.parse(python_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.Lambda)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert ast.get_docstring(node) is not None, (
                    f"{python_file.name}:{node.name} lacks a docstring"
                )


def test_semantic_collaboration_does_not_import_experiment_modules() -> None:
    """验证生产包不依赖 input preprocessing 历史 experiment runner。

    :return: 无返回值。
    """
    package_root = Path(__file__).parents[1] / "src/vet_agent/semantic_collaboration"

    for python_file in sorted(package_root.glob("*.py")):
        tree = ast.parse(python_file.read_text(encoding="utf-8"))
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert all(
            not name.startswith("vet_agent.input_preprocessing")
            for name in imported_names
        )
        assert all(
            not module.startswith("vet_agent.input_preprocessing")
            for module in imported_modules
        )
