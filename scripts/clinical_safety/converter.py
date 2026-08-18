"""
文件：scripts/clinical_safety/converter.py
作用：将原始长文本临床安全参考资料转换为标准资产与向量检索片段。
说明：转换逻辑用于离线数据治理；运行时安全裁决仍以已审核发布的结构化资产为准。
"""


from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vet_agent.clinical_safety import (
    ClinicalSafetyActionClass,
    ClinicalSafetyAsset,
    ClinicalSafetyAssetType,
    ClinicalSafetyChunk,
    SafetySeverity,
)


SOURCE_SECTIONS: tuple[str, ...] = ("toxinsAndDrugs", "emergencyRedFlags", "dangerPatterns")
SECTION_ASSET_TYPES: dict[str, ClinicalSafetyAssetType] = {
    "toxinsAndDrugs": "toxin",
    "emergencyRedFlags": "emergency_red_flag",
    "dangerPatterns": "danger_pattern",
}
CARRIER_MARKERS: tuple[str, ...] = (
    "口香糖",
    "糖果",
    "薄荷糖",
    "巧克力",
    "蛋糕",
    "饼干",
    "药膏",
    "软膏",
    "片",
    "胶囊",
    "丸",
    "颗粒",
    "滴剂",
    "牙膏",
    "保健品",
    "花",
    "叶",
    "鼠药",
    "农药",
    "杀虫剂",
    "香薰",
    "精油",
    "面团",
    "剩菜",
    "汤",
    "骨头",
    "玩具",
    "包装",
)
SYMPTOM_MARKERS: tuple[str, ...] = (
    "多饮多尿",
    "尿频尿少",
    "尿不出",
    "排尿困难",
    "频繁排尿",
    "血尿",
    "干呕",
    "呕吐",
    "流口水",
    "腹部膨大",
    "腹胀",
    "肚子胀",
    "腹泻",
    "血便",
    "发绀",
    "发紫",
    "发青",
    "牙龈",
    "舌头",
    "呼吸困难",
    "呼吸急促",
    "呼吸很快",
    "喘气",
    "抽搐",
    "癫痫",
    "瘫倒",
    "虚弱",
    "精神萎靡",
    "不吃",
    "精神差",
    "消瘦",
    "多饮",
    "多尿",
    "食欲下降",
    "食欲亢进",
    "无效干呕",
    "张口呼吸",
    "呼吸暂停",
    "腹部膨隆",
    "腹围增大",
    "腹痛",
    "烦躁不安",
    "厌食",
    "拒食",
    "食欲不振",
    "意识丧失",
    "咳嗽",
    "口臭",
    "脱毛",
    "皮肤变薄",
)
TITLE_SPLIT_PATTERN = re.compile(r"\s*(?:\+|＋|/|／|、|,|，|;|；|:|：|→|->|\(|\)|（|）)\s*")
NON_RECOGNITION_TERMS: frozenset[str] = frozenset(
    {
        "猫",
        "犬",
        "狗",
        "老年猫",
        "老年犬",
        "中老年犬",
        "幼猫",
        "幼犬",
        "公猫",
        "母猫",
        "公犬",
        "母犬",
        "dog",
        "cat",
        "senior",
        "juvenile",
        "早期",
        "中期",
        "晚期",
        "注意",
        "提示",
        "表现",
        "临床",
        "牙龈",
        "舌",
        "舌头",
    }
)
SUPPLEMENTAL_ALIASES: dict[str, tuple[str, ...]] = {
    "木糖醇": ("xylitol",),
}


def build_standard_safety_documents(
    payload: dict[str, Any],
    *,
    source_file: str,
    version: str = "v1",
    review_status: str = "pending",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构建标准临床安全资产文档和向量片段文档。

    :param payload: 原始临床安全参考 JSON 数据。
    :param source_file: 原始参考文件路径。
    :param version: 生成资产版本。
    :param review_status: 生成资产的默认审核状态。
    :return: 返回资产文档与向量片段文档。
    """
    generated_at = datetime.now(UTC)
    published_at = generated_at if review_status == "approved" else None
    assets = convert_safety_reference_payload(
        payload,
        source_file=source_file,
        version=version,
        review_status=review_status,
        published_at=published_at,
    )
    chunks = build_safety_chunks(assets)
    generated_at_text = generated_at.isoformat()
    source_meta = dict(payload.get("_meta") or {})
    asset_document = {
        "_meta": {
            "schema": "clinical_safety_assets",
            "schema_version": "1.0.0",
            "version": version,
            "source_file": source_file,
            "source_meta": source_meta,
            "asset_count": len(assets),
            "generated_at": generated_at_text,
        },
        "assets": [asset.to_dict() for asset in assets],
    }
    chunk_document = {
        "_meta": {
            "schema": "clinical_safety_chunks",
            "schema_version": "1.0.0",
            "version": version,
            "source_file": source_file,
            "asset_count": len(assets),
            "chunk_count": len(chunks),
            "generated_at": generated_at_text,
        },
        "chunks": [chunk.to_dict() for chunk in chunks],
    }
    return asset_document, chunk_document


def convert_safety_reference_payload(
    payload: dict[str, Any],
    *,
    source_file: str,
    version: str = "v1",
    review_status: str = "pending",
    published_at: datetime | None = None,
) -> list[ClinicalSafetyAsset]:
    """将原始安全参考 JSON 转换为标准临床安全资产列表。

    :param payload: 原始临床安全参考 JSON 数据。
    :param source_file: 原始参考文件路径。
    :param version: 生成资产版本。
    :param review_status: 生成资产的默认审核状态。
    :param published_at: 发布态资产的发布时间；草稿资产保持为空。
    :return: 返回标准临床安全资产列表。
    """
    assets: list[ClinicalSafetyAsset] = []
    for section in SOURCE_SECTIONS:
        raw_items = payload.get(section) or []
        if not isinstance(raw_items, list):
            continue
        for index, raw_item in enumerate(raw_items, start=1):
            if not isinstance(raw_item, dict):
                continue
            assets.append(
                _convert_item(
                    raw_item,
                    section=section,
                    index=index,
                    source_file=source_file,
                    version=version,
                    review_status=review_status,
                    published_at=published_at,
                )
            )
    return assets


def build_safety_chunks(assets: list[ClinicalSafetyAsset]) -> list[ClinicalSafetyChunk]:
    """从标准临床安全资产派生向量检索片段。

    :param assets: 标准临床安全资产列表。
    :return: 返回向量检索片段列表。
    """
    chunks: list[ClinicalSafetyChunk] = []
    for asset in assets:
        chunks.extend(_chunks_for_asset(asset))
    return chunks


def _convert_item(
    raw_item: dict[str, Any],
    *,
    section: str,
    index: int,
    source_file: str,
    version: str,
    review_status: str,
    published_at: datetime | None,
) -> ClinicalSafetyAsset:
    """转换单条原始临床安全条目。

    :param raw_item: 原始临床安全条目。
    :param section: 原始条目所属顶层分组。
    :param index: 条目在分组中的序号。
    :param source_file: 原始参考文件路径。
    :param version: 生成资产版本。
    :param review_status: 生成资产的默认审核状态。
    :param published_at: 发布态资产的发布时间。
    :return: 返回标准临床安全资产。
    """
    item_text = _clean_text(raw_item.get("item"))
    aliases_text = _clean_text(raw_item.get("aliases"))
    species_text = _clean_text(raw_item.get("species"))
    danger_text = _clean_text(raw_item.get("danger"))
    action_text = _clean_text(raw_item.get("action"))
    category = _clean_text(raw_item.get("category"))
    source = _clean_text(raw_item.get("source"))
    asset_type = _asset_type(section, category, item_text)
    action_class = _action_class(action_text)
    severity = _severity(section, action_class)
    aliases = _aliases(item_text, aliases_text)
    user_expressions = _user_expressions(f"{item_text}。{aliases_text}")
    carriers = _carriers(aliases)
    symptoms = _symptoms(f"{item_text}。{aliases_text}。{danger_text}。{action_text}")
    recognition_phrases = _recognition_phrases(
        item_text=item_text,
        aliases=aliases,
        user_expressions=user_expressions,
        symptoms=symptoms,
        danger_text=danger_text,
        action_text=action_text,
    )
    species_scope = _species_scope(f"{item_text}。{species_text}")
    sex_scope = _sex_scope(f"{item_text}。{aliases_text}。{species_text}")
    age_scope = _age_scope(f"{item_text}。{aliases_text}。{species_text}")
    required_context = _required_context(species_scope, sex_scope, age_scope, symptoms)
    decision_hints = _decision_hints(asset_type, action_class)
    canonical_name = _canonical_name(item_text, index)
    asset_id = _asset_id(asset_type, item_text, section, index)
    code = _asset_code(
        raw_item,
        canonical_name=canonical_name,
        source_path=f"{section}[{index}]",
    )
    return ClinicalSafetyAsset(
        asset_id=asset_id,
        asset_type=asset_type,
        canonical_name=canonical_name,
        category=category,
        species_scope=species_scope,
        sex_scope=sex_scope,
        age_scope=age_scope,
        severity=severity,
        action_class=action_class,
        code=code,
        aliases=aliases,
        carriers=carriers,
        user_expressions=user_expressions,
        symptoms=symptoms,
        recognition_phrases=recognition_phrases,
        required_context=required_context,
        decision_hints=decision_hints,
        clinical_risk_summary=danger_text,
        triage_message=action_text,
        source={
            "source_file": source_file,
            "source_path": f"{section}[{index}]",
            "source_text": source,
        },
        review_status=review_status,
        version=version,
        enabled=review_status == "approved",
        published_at=published_at if review_status == "approved" else None,
        raw_text={
            "item": item_text,
            "aliases": aliases_text,
            "species": species_text,
            "danger": danger_text,
            "action": action_text,
            "source": source,
        },
        metadata={
            "source_section": section,
            "source_index": index,
            "conversion_strategy": "deterministic_text_normalization_v1",
        },
    )


def _chunks_for_asset(asset: ClinicalSafetyAsset) -> list[ClinicalSafetyChunk]:
    """为单个标准安全资产生成字段级向量片段。

    :param asset: 标准临床安全资产。
    :return: 返回该资产对应的向量检索片段列表。
    """
    base_metadata = {
        "asset_id": asset.asset_id,
        "code": asset.code,
        "asset_type": asset.asset_type,
        "canonical_name": asset.canonical_name,
        "category": asset.category,
        "severity": asset.severity,
        "action_class": asset.action_class,
        "species_scope": list(asset.species_scope),
        "sex_scope": list(asset.sex_scope),
        "age_scope": list(asset.age_scope),
        "recognition_phrase_count": len(asset.recognition_phrases),
        "source_path": asset.source.get("source_path"),
    }
    recognition_text = _join_embedding_text(
        [
            asset.canonical_name,
            *asset.recognition_phrases,
            asset.category,
            *asset.aliases,
            *asset.carriers,
            *asset.user_expressions,
            *asset.symptoms,
            *asset.species_scope,
            *asset.sex_scope,
            *asset.age_scope,
        ]
    )
    clinical_text = _join_embedding_text([asset.canonical_name, asset.clinical_risk_summary])
    triage_text = _join_embedding_text([asset.canonical_name, asset.action_class, asset.triage_message])
    return [
        ClinicalSafetyChunk(
            chunk_id=f"{asset.asset_id}.recognition.{asset.version}",
            asset_id=asset.asset_id,
            chunk_type="recognition",
            title=f"{asset.canonical_name} 风险识别",
            embedding_text=recognition_text,
            metadata={**base_metadata, "chunk_role": "候选召回"},
            review_status=asset.review_status,
            version=asset.version,
            enabled=asset.enabled,
            content_hash=_content_hash(recognition_text),
        ),
        ClinicalSafetyChunk(
            chunk_id=f"{asset.asset_id}.clinical_risk.{asset.version}",
            asset_id=asset.asset_id,
            chunk_type="clinical_risk",
            title=f"{asset.canonical_name} 临床风险",
            embedding_text=clinical_text,
            metadata={**base_metadata, "chunk_role": "风险解释"},
            review_status=asset.review_status,
            version=asset.version,
            enabled=asset.enabled,
            content_hash=_content_hash(clinical_text),
        ),
        ClinicalSafetyChunk(
            chunk_id=f"{asset.asset_id}.triage_action.{asset.version}",
            asset_id=asset.asset_id,
            chunk_type="triage_action",
            title=f"{asset.canonical_name} 分诊处置",
            embedding_text=triage_text,
            metadata={**base_metadata, "chunk_role": "处置口径"},
            review_status=asset.review_status,
            version=asset.version,
            enabled=asset.enabled,
            content_hash=_content_hash(triage_text),
        ),
    ]


def _asset_code(
    raw_item: dict[str, Any],
    *,
    canonical_name: str,
    source_path: str,
) -> str:
    """读取原始资产显式声明的稳定安全信号编码。

    :param raw_item: 原始临床安全条目。
    :param canonical_name: 资产规范名称。
    :param source_path: 原始条目的来源路径。
    :return: 返回经规范化的显式安全信号编码。
    :raises ValueError: 原始条目缺少 code 时抛出，避免离线转换器兜底生成临时编码。
    """
    raw_code = _clean_text(raw_item.get("code"))
    if not raw_code:
        raise ValueError(f"clinical safety asset code is required: {source_path}:{canonical_name}")
    return raw_code.strip().upper()


def _asset_type(section: str, category: str, item_text: str) -> ClinicalSafetyAssetType:
    """推导标准安全资产类型。

    :param section: 原始条目所属顶层分组。
    :param category: 原始条目分类。
    :param item_text: 原始条目名称。
    :return: 返回标准安全资产类型。
    """
    if section == "toxinsAndDrugs":
        combined = f"{category}。{item_text}"
        if "人用药" in combined or "药物" in combined:
            return "human_drug"
        if "植物" in combined or any(marker in combined for marker in ("百合", "绿萝", "芦荟", "富贵竹")):
            return "plant_toxin"
        if any(marker in combined for marker in ("防冻液", "鼠药", "杀虫剂", "农药", "精油", "樟脑")):
            return "chemical_toxin"
    return SECTION_ASSET_TYPES.get(section, "toxin")


def _action_class(action_text: str) -> ClinicalSafetyActionClass:
    """根据处置长文本推导分诊动作分类。

    :param action_text: 原始处置建议长文本。
    :return: 返回标准分诊动作分类。
    """
    primary_sentence = re.split(r"[。；;]", action_text, maxsplit=1)[0]
    if "当天" in primary_sentence or "勿拖过夜" in primary_sentence or "不要过夜" in primary_sentence:
        return "same_day_visit"
    if "尽快就诊" in primary_sentence or "数天内" in primary_sentence:
        return "urgent_visit"
    if any(marker in primary_sentence for marker in ("即刻", "立即", "急诊", "最高优先级", "24h")):
        return "emergency"
    if any(marker in action_text for marker in ("当天", "勿拖过夜", "不要过夜")):
        return "same_day_visit"
    if any(marker in action_text for marker in ("尽快就诊", "尽快", "数天内")):
        return "urgent_visit"
    return "safety_warning"


def _severity(section: str, action_class: ClinicalSafetyActionClass) -> SafetySeverity:
    """根据原始分组与动作分类推导安全严重级别。

    :param section: 原始条目所属顶层分组。
    :param action_class: 标准分诊动作分类。
    :return: 返回安全严重级别。
    """
    if action_class in {"emergency", "same_day_visit"}:
        return "urgent"
    if section == "emergencyRedFlags":
        return "urgent"
    if action_class == "urgent_visit":
        return "caution"
    return "caution"


def _aliases(item_text: str, aliases_text: str) -> tuple[str, ...]:
    """抽取规范名称、别名、英文名、商品名与俗称。

    :param item_text: 原始条目名称。
    :param aliases_text: 原始别名长文本。
    :return: 返回去重后的别名列表。
    """
    canonical_name = _canonical_name(item_text, 0)
    terms = [
        canonical_name,
        *SUPPLEMENTAL_ALIASES.get(canonical_name, ()),
        *_terms_from_text(item_text),
        *_terms_from_text(aliases_text),
    ]
    return _unique_terms(terms, max_items=80)


def _user_expressions(aliases_text: str) -> tuple[str, ...]:
    """抽取用户常见表达和引号内描述样例。

    :param aliases_text: 原始别名长文本。
    :return: 返回用户表达列表。
    """
    expressions = _quoted_terms(aliases_text)
    marker_match = re.search(r"(用户(?:常见)?描述样例|用户常见表达|用户说|关键早期用户描述样例)[:：]?(.*)", aliases_text)
    if marker_match:
        expressions.extend(_terms_from_text(marker_match.group(2)))
    return _unique_terms(expressions, max_items=40)


def _carriers(terms: Iterable[str]) -> tuple[str, ...]:
    """从候选词中抽取风险载体。

    :param terms: 候选别名和表达列表。
    :return: 返回风险载体列表。
    """
    carriers: list[str] = []
    for term in terms:
        short = _short_term(term)
        if not short:
            continue
        if any(marker in term or marker in short for marker in CARRIER_MARKERS) and len(short) <= 32:
            carriers.append(short)
    return _unique_terms(carriers)


def _symptoms(text: str) -> tuple[str, ...]:
    """从长文本中抽取症状和风险线索短语。

    :param text: 原始风险描述、用户表达与处置文本。
    :return: 返回症状和风险线索列表。
    """
    quoted = _quoted_terms(text)
    terms = [
        term
        for term in [*quoted, *_terms_from_text(text)]
        if any(marker in term for marker in SYMPTOM_MARKERS) and len(term) <= 32
    ]
    terms.extend(marker for marker in SYMPTOM_MARKERS if marker in text)
    return _unique_terms(terms, max_items=60)


def _recognition_phrases(
    *,
    item_text: str,
    aliases: tuple[str, ...],
    user_expressions: tuple[str, ...],
    symptoms: tuple[str, ...],
    danger_text: str,
    action_text: str,
) -> tuple[str, ...]:
    """生成资产级组合症状、别名和原子症状召回短语。

    :param item_text: 原始条目主标题。
    :param aliases: 已抽取的规范名称、别名和俗称。
    :param user_expressions: 已抽取的用户自然语言表达。
    :param symptoms: 已抽取的原子症状和风险线索。
    :param danger_text: 原始临床风险描述。
    :param action_text: 原始分诊处置描述。
    :return: 返回去重后的召回短语列表。
    """
    title_phrases = _title_phrases(item_text)
    combination_phrases = _combination_phrases(f"{item_text}。{danger_text}。{action_text}")
    candidates = [
        *title_phrases,
        *aliases,
        *user_expressions,
        *symptoms,
        *combination_phrases,
    ]
    return _unique_terms(
        (
            term
            for term in candidates
            if _useful_term(term) and _clean_text(term) not in NON_RECOGNITION_TERMS
        ),
        max_items=100,
    )


def _title_phrases(item_text: str) -> tuple[str, ...]:
    """保留主标题整体并拆出标题中的组合症状短语。

    :param item_text: 原始条目主标题。
    :return: 返回主标题整体、标题分项和可召回的标题短语。
    """
    title = re.split(r"[（(]", _clean_text(item_text), maxsplit=1)[0].strip(" :-：")
    if not title:
        return ()
    terms: list[str] = [title]
    for fragment in TITLE_SPLIT_PATTERN.split(_clean_text(item_text)):
        terms.extend(_term_candidates(fragment))
    return _unique_terms(
        term
        for term in terms
        if _useful_term(term) and _clean_text(term) not in NON_RECOGNITION_TERMS
    )


def _combination_phrases(text: str) -> tuple[str, ...]:
    """从同一临床语句中生成二联组合症状短语。

    :param text: 原始主标题、风险描述和处置文本。
    :return: 返回由两个以上原子症状组成的组合短语。
    """
    phrases: list[str] = []
    for clause in re.split(r"[。；;！？!?。\n]+", _clean_text(text)):
        matched = _markers_in_text(clause)
        if len(matched) < 2:
            continue
        phrases.append("+".join(matched[:4]))
    return _unique_terms(phrases, max_items=20)


def _markers_in_text(text: str) -> list[str]:
    """按长词优先从文本中提取原子症状标记。

    :param text: 待分析文本。
    :return: 返回按出现顺序去重后的症状标记。
    """
    matches: list[tuple[int, int, str]] = []
    for marker in SYMPTOM_MARKERS:
        matches.extend(
            (match.start(), match.end(), marker)
            for match in re.finditer(re.escape(marker), text)
        )
    selected: list[tuple[int, int, str]] = []
    for start, end, marker in sorted(matches, key=_marker_sort_key):
        if any(start < selected_end and end > selected_start for selected_start, selected_end, _ in selected):
            continue
        selected.append((start, end, marker))
    return [marker for _, _, marker in selected][:8]


def _marker_sort_key(value: tuple[int, int, str]) -> tuple[int, int]:
    """返回症状标记按起点和长度排序的键。

    :param value: 症状标记的起点、终点和文本元组。
    :return: 返回用于排序的起点和负长度元组。
    """
    start, end, _ = value
    return start, -(end - start)


def _species_scope(species_text: str) -> tuple[str, ...]:
    """从原始物种说明中抽取标准物种范围。

    :param species_text: 原始物种说明文本。
    :return: 返回标准物种范围。
    """
    text = species_text.lower()
    if any(marker in text for marker in ("老年猫", "公猫", "母猫", "猫特异", "feline")) and not any(
        marker in text for marker in ("母犬", "公犬", "犬特异")
    ):
        return ("cat",)
    species: list[str] = []
    if any(marker in text for marker in ("both", "犬猫", "猫犬", "狗猫")):
        species.extend(["dog", "cat"])
    else:
        if any(marker in text for marker in ("dog", "犬", "狗")):
            species.append("dog")
        if any(marker in text for marker in ("cat", "猫")):
            species.append("cat")
    return _unique_terms(species, max_items=4)


def _sex_scope(text: str) -> tuple[str, ...]:
    """从条目说明中抽取标准性别范围。

    :param text: 原始条目名称、别名和物种说明。
    :return: 返回标准性别范围。
    """
    sexes: list[str] = []
    if any(marker in text.lower() for marker in ("公", "雄", "male")):
        sexes.append("male")
    if any(marker in text.lower() for marker in ("母", "雌", "female")):
        sexes.append("female")
    return _unique_terms(sexes, max_items=2)


def _age_scope(text: str) -> tuple[str, ...]:
    """从条目说明中抽取标准年龄范围。

    :param text: 原始条目名称、别名和物种说明。
    :return: 返回标准年龄范围。
    """
    age_scopes: list[str] = []
    if any(marker in text.lower() for marker in ("老年", "高龄", "中老年", "senior", "年纪大")):
        age_scopes.append("senior")
    if any(marker in text for marker in ("幼犬", "幼猫", "小狗", "小猫")):
        age_scopes.append("juvenile")
    return _unique_terms(age_scopes, max_items=2)


def _required_context(
    species_scope: tuple[str, ...],
    sex_scope: tuple[str, ...],
    age_scope: tuple[str, ...],
    symptoms: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """构造用于后续裁决的结构化上下文提示。

    :param species_scope: 标准物种范围。
    :param sex_scope: 标准性别范围。
    :param age_scope: 标准年龄范围。
    :param symptoms: 症状和风险线索。
    :return: 返回结构化上下文提示字典。
    """
    context: dict[str, tuple[str, ...]] = {}
    if species_scope:
        context["species"] = species_scope
    if sex_scope:
        context["sex"] = sex_scope
    if age_scope:
        context["age"] = age_scope
    if symptoms:
        context["symptoms"] = symptoms[:20]
    return context


def _decision_hints(
    asset_type: ClinicalSafetyAssetType,
    action_class: ClinicalSafetyActionClass,
) -> dict[str, str]:
    """生成不同用户意图下的安全动作提示。

    :param asset_type: 标准安全资产类型。
    :param action_class: 标准分诊动作分类。
    :return: 返回意图到动作的提示字典。
    """
    urgent_action = "safety_escalated" if action_class in {"emergency", "same_day_visit"} else "clinical_caution"
    if asset_type in {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}:
        return {
            "actual_exposure": "safety_escalated",
            "possible_exposure": "safety_escalated",
            "knowledge_question": "completed_with_safety_warning",
            "prevention_question": "completed_with_safety_warning",
        }
    return {
        "active_symptom": urgent_action,
        "possible_symptom": urgent_action,
        "historical_context": "record_as_history",
        "knowledge_question": "completed_with_safety_warning",
    }


def _canonical_name(item_text: str, index: int) -> str:
    """从原始条目名称中提取规范名称。

    :param item_text: 原始条目名称。
    :param index: 条目序号。
    :return: 返回规范名称。
    """
    cleaned = _clean_text(item_text)
    if not cleaned:
        return f"未命名安全资产 {index}"
    return re.split(r"[（(]", cleaned, maxsplit=1)[0].strip(" :-：")


def _asset_id(asset_type: ClinicalSafetyAssetType, item_text: str, section: str, index: int) -> str:
    """生成稳定临床安全资产标识。

    :param asset_type: 标准安全资产类型。
    :param item_text: 原始条目名称。
    :param section: 原始条目所属顶层分组。
    :param index: 条目在分组中的序号。
    :return: 返回稳定资产标识。
    """
    digest = hashlib.sha256(f"{section}:{index}:{item_text}".encode("utf-8")).hexdigest()[:10]
    return f"safety_{asset_type}_{index:03d}_{digest}"


def _terms_from_text(text: str) -> list[str]:
    """从长文本中按常见分隔符抽取短语。

    :param text: 原始长文本。
    :return: 返回候选短语列表。
    """
    cleaned = _clean_text(text)
    cleaned = re.sub(r"用户(?:常见)?描述样例[:：]?", " ", cleaned)
    cleaned = re.sub(r"关键早期用户描述样例[:：]?", " ", cleaned)
    cleaned = re.sub(r"用户常见表达[:：]?", " ", cleaned)
    cleaned = re.sub(r"用户说[:：]?", " ", cleaned)
    tokens = re.split(r"[、，,;；。|/+＋→:：()（）=＝]+", _without_quoted_segments(cleaned))
    terms: list[str] = []
    for token in tokens:
        for term in _term_candidates(token):
            if _useful_term(term):
                terms.append(term)
    return terms


def _quoted_terms(text: str) -> list[str]:
    """抽取中英文引号包裹的用户表达。

    :param text: 原始长文本。
    :return: 返回引号内表达列表。
    """
    terms: list[str] = []
    patterns = (r"「([^」]+)」", r"\"([^\"]+)\"", r"'([^']+)'", r"“([^”]+)”")
    for pattern in patterns:
        for match in re.findall(pattern, text):
            terms.extend(term for term in _term_candidates(match) if _useful_term(term))
    return terms


def _term_candidates(value: str) -> list[str]:
    """从单个候选片段中派生更干净的短语候选。

    :param value: 原始候选片段。
    :return: 返回原始片段及其清洗后的短语候选。
    """
    stripped = value.strip(" \"'“”‘’[]【】")
    if not stripped:
        return []
    for label in ("俗称", "别名"):
        if label in stripped:
            stripped = stripped.split(label, 1)[1].strip()
            break
    candidates = [stripped]
    if ":" in stripped or "：" in stripped:
        candidates.append(re.split(r"[:：]", stripped, maxsplit=1)[1].strip())
    short = _short_term(stripped)
    if short and short not in candidates:
        candidates.append(short)
    return candidates


def _short_term(value: str) -> str:
    """将带说明前缀或括号注释的候选短语缩短为核心词。

    :param value: 原始候选短语。
    :return: 返回缩短后的核心短语。
    """
    stripped = value.strip(" \"'“”‘’[]【】")
    if ":" in stripped or "：" in stripped:
        stripped = re.split(r"[:：]", stripped, maxsplit=1)[1].strip()
    return re.split(r"[（(]", stripped, maxsplit=1)[0].strip(" \"'“”‘’[]【】")


def _useful_term(value: str) -> bool:
    """判断候选短语是否适合进入标准检索字段。

    :param value: 候选短语。
    :return: 适合保留时返回 True，否则返回 False。
    """
    stripped = value.strip(" \"'“”‘’()（）[]【】")
    if not stripped or len(stripped) > 48:
        return False
    if stripped in {
        "等",
        "如",
        "见",
        "部分",
        "注意",
        "别名",
        "医学词",
        "临床",
        "机制",
        "剂量阈值",
        "早期",
        "中期",
        "晚期",
        "提示",
        "表现",
    }:
        return False
    return True


def _without_quoted_segments(text: str) -> str:
    """移除引号包裹的完整表达，避免普通切分生成半截短语。

    :param text: 待清理的原始文本。
    :return: 返回移除引号表达后的文本。
    """
    patterns = (r"「[^」]*」", r"“[^”]*”", r'"[^"]*"', r"'[^']*'")
    result = text
    for pattern in patterns:
        result = re.sub(pattern, " ", result)
    return result


def _unique_terms(values: Iterable[str], *, max_items: int | None = None) -> tuple[str, ...]:
    """对短语列表进行清洗、去重和截断。

    :param values: 原始短语列表。
    :param max_items: 最大保留条数。
    :return: 返回去重后的短语元组。
    """
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        term = _clean_text(value).strip(" \"'“”‘’()（）[]【】")
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if max_items is not None and len(terms) >= max_items:
            break
    return tuple(terms)


def _join_embedding_text(values: Iterable[str]) -> str:
    """构造用于向量化的短文本。

    :param values: 候选字段值。
    :return: 返回去重拼接后的向量化文本。
    """
    return "；".join(_unique_terms(values))


def _content_hash(text: str) -> str:
    """生成临床安全 chunk 向量化文本内容哈希。

    :param text: 待写入 chunk 的向量化文本。
    :return: 返回 SHA-256 十六进制摘要。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_text(value: Any) -> str:
    """清理原始 JSON 字段中的空白字符。

    :param value: 原始字段值。
    :return: 返回清理后的文本。
    """
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_safety_reference(path: Path) -> dict[str, Any]:
    """读取原始临床安全参考 JSON。

    :param path: 原始参考文件路径。
    :return: 返回原始 JSON 字典。
    """
    import json

    return json.loads(path.read_text(encoding="utf-8"))
