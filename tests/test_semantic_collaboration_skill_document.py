"""
=============================================================================
文件：tests/test_semantic_collaboration_skill_document.py
作用：验证受限语义协作 DAG M06 标准化 SKILL 文档与受限模板机制。
范围：覆盖包内资源加载、静态元数据与 SkillSpec 绑定、模型可见章节选择、
      metadata 不进入 prompt、受限 Jinja AST 与变量闭合门禁。
说明：本测试只读取包内 SKILL.md 和进程内对象，不访问模型网关、数据库、
      Temporal、OPA 或任何 input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import pytest

from tests.test_semantic_collaboration_generation import _execution, _snapshot
from vet_agent.semantic_collaboration import (
    CLAIM_INVENTORY_REPAIR_SPEC,
    CLAIM_INVENTORY_SPEC,
    CLAIM_PROPOSITION_REPAIR_SPEC,
    TURN_INTENT_SPEC,
    ClaimPropositionInventoryPromptRenderer,
    RestrictedSkillTemplate,
    SemanticPromptRenderError,
    SemanticSkillDocument,
    SemanticSkillDocumentError,
    SemanticSkillDocumentMetadata,
    SkillPromptRenderRequest,
    TurnIntentPromptRenderer,
    TurnSnapshotProjector,
    build_production_skill_catalog,
    load_semantic_skill_document,
)


def test_standard_skill_documents_match_authoritative_specs() -> None:
    """验证包内 SKILL.md 静态元数据与权威 SkillSpec 完全绑定。

    :return: 无返回值。
    """
    turn_document = load_semantic_skill_document("turn_intent")
    claim_document = load_semantic_skill_document("claim_inventory")
    inventory_repair_document = load_semantic_skill_document(
        "claim_inventory_repair",
    )
    proposition_repair_document = load_semantic_skill_document(
        "claim_proposition_repair",
    )

    turn_document.validate_against_spec(TURN_INTENT_SPEC)
    claim_document.validate_against_spec(CLAIM_INVENTORY_SPEC)
    inventory_repair_document.validate_against_spec(CLAIM_INVENTORY_REPAIR_SPEC)
    proposition_repair_document.validate_against_spec(CLAIM_PROPOSITION_REPAIR_SPEC)

    assert turn_document.metadata.prompt_version == "1.1.0"
    assert claim_document.metadata.prompt_version == "1.2.0"
    assert "trusted_pet_context" not in turn_document.metadata.prompt_variables
    assert "trusted_pet_context" in claim_document.metadata.prompt_variables
    assert "不得把“用户报告”“用户认为”作为 proposition 主语义" in (
        claim_document.section("Workflow").content
    )


def test_production_catalog_uses_standard_skill_documents() -> None:
    """验证生产 SkillCatalog 的投影来自标准化 SKILL.md。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()
    turn_spec = catalog.require("turn_intent", "2.0.0")
    claim_spec = catalog.require("claim_inventory", "2.0.0")
    turn_document = load_semantic_skill_document("turn_intent")
    claim_document = load_semantic_skill_document("claim_inventory")

    assert turn_spec.prompt_projection == turn_document.projection
    assert claim_spec.prompt_projection == claim_document.projection


def test_standard_skill_metadata_never_enters_model_prompt() -> None:
    """验证文件头部元数据只由确定性代码消费且不进入模型消息。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _execution(snapshot, skill_id="claim_inventory")
    projection = TurnSnapshotProjector().project(
        snapshot,
        CLAIM_INVENTORY_SPEC.context_contract,
    )
    prompt = ClaimPropositionInventoryPromptRenderer().render(
        SkillPromptRenderRequest(
            execution=execution,
            spec=CLAIM_INVENTORY_SPEC,
            projection=projection,
        ),
    )
    prompt_text = "\n\n".join(message.content for message in prompt.messages)

    assert prompt_text.startswith("你是 Claim Proposition Inventory 生成器")
    assert "<current_turn>" in prompt_text
    assert "<trusted_pet_context>" in prompt_text
    assert "skill_id:" not in prompt_text
    assert "skill_version:" not in prompt_text
    assert "prompt_version:" not in prompt_text
    assert "output_schema_id:" not in prompt_text
    assert "context_resources:" not in prompt_text
    assert "model_visible_sections:" not in prompt_text
    assert execution.task.task_id not in prompt_text


def test_turn_intent_standard_skill_excludes_unauthorized_pet_context() -> None:
    """验证 Turn Intent 标准化 SKILL 不请求或渲染宠物画像。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _execution(snapshot, skill_id="turn_intent")
    projection = TurnSnapshotProjector().project(
        snapshot,
        TURN_INTENT_SPEC.context_contract,
    )
    prompt = TurnIntentPromptRenderer().render(
        SkillPromptRenderRequest(
            execution=execution,
            spec=TURN_INTENT_SPEC,
            projection=projection,
        ),
    )
    prompt_text = "\n\n".join(message.content for message in prompt.messages)

    assert "<trusted_pet_context>" not in prompt_text
    assert prompt.skill_version == "2.0.0"
    assert prompt.prompt_version == "1.1.0"


def test_skill_document_contract_mismatch_is_blocked() -> None:
    """验证 SKILL.md 身份漂移无法绑定权威 SkillSpec。

    :return: 无返回值。
    """
    document = load_semantic_skill_document("turn_intent")
    tampered_markdown = document.projection.document.replace(
        "skill_id: turn_intent",
        "skill_id: broken_skill",
    )
    tampered = SemanticSkillDocument.from_markdown(tampered_markdown)

    assert isinstance(tampered.metadata, SemanticSkillDocumentMetadata)
    with pytest.raises(SemanticSkillDocumentError):
        tampered.validate_against_spec(TURN_INTENT_SPEC)


def test_restricted_template_only_allows_top_level_variables() -> None:
    """验证受限 Jinja 只允许白名单顶层字符串变量替换。

    :return: 无返回值。
    """
    template = RestrictedSkillTemplate(
        "<current_turn>\n{{ current_turn }}\n</current_turn>",
        allowed_variables=("current_turn",),
    )

    assert template.render({"current_turn": "英短没有呕吐"}) == (
        "<current_turn>\n英短没有呕吐\n</current_turn>"
    )


@pytest.mark.parametrize(
    ("source", "variables"),
    [
        ("{{ current_turn | upper }}", ("current_turn",)),
        ("{% if current_turn %}x{% endif %}", ("current_turn",)),
        ("{{ projection.original_user_text }}", ("projection",)),
        ("{{ unknown }}", ("current_turn",)),
    ],
)
def test_restricted_template_blocks_logic_and_unknown_variables(
    source: str,
    variables: tuple[str, ...],
) -> None:
    """验证模板逻辑、属性访问和未知变量在启动期阻断。

    :param source: 待校验的模板源码。
    :param variables: 声明的变量白名单。
    :return: 无返回值。
    """
    with pytest.raises(SemanticPromptRenderError, match="restricted skill template"):
        RestrictedSkillTemplate(source, allowed_variables=variables)


def test_restricted_template_requires_closed_render_variables() -> None:
    """验证渲染变量集合必须与模板白名单完全一致。

    :return: 无返回值。
    """
    template = RestrictedSkillTemplate(
        "{{ current_turn }}",
        allowed_variables=("current_turn",),
    )

    with pytest.raises(
        SemanticPromptRenderError,
        match="render variables are not closed",
    ):
        template.render({})
