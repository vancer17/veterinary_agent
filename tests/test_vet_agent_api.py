"""
文件：tests/test_vet_agent_api.py
作用：提供项目自动化测试用例与测试辅助函数。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ingress import create_app, set_orchestrator
from vet_agent import (
    AgentTurnRequest,
    AgentTurnResponse,
    Container,
    SafetySignal,
    Settings,
    TrustedIdentity,
    VetAgentIngressOrchestrator,
    set_container,
)
from vet_agent.agents import TaskSplitterAgent
from vet_agent.clinical_safety import (
    ClinicalSafetyAsset,
    ClinicalSafetyCandidate,
    ClinicalSafetyPolicyAction,
    ClinicalSafetyPolicyClient,
    ClinicalSafetyPolicyDecision,
    ClinicalSafetyPolicyInput,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
)
from vet_agent.input_safety import (
    InputSafetyService,
    LocalInputSafetyPolicyClient,
    StaticInputSafetyRepository,
)
from vet_agent.observability import AgentPathNode
from vet_agent.repositories import FileRuleRepository, ScopeRepository, SessionBinding, VerifiedPetProfile
from vet_agent.runtime import QwenClient
from vet_agent.services import TurnExecutionGateProtocol, TurnExecutor


app = create_app()


def _assert_policy_path_is_named(
    agent_path: Sequence[str],
    *,
    input_safety_expected: bool = True,
    clinical_safety_expected: bool = False,
) -> None:
    """验证 Agent 审计路径使用具备领域语义的策略节点。

    :param agent_path: API metadata.multi_agent_path 返回的审计路径。
    :param input_safety_expected: 是否期望输入安全策略节点已经参与链路。
    :param clinical_safety_expected: 是否期望临床安全策略节点已经参与链路。
    :return: 无返回值；断言通过表示审计链路未退化为裸 OPA 命名。
    """
    assert "OPA" not in agent_path
    if input_safety_expected:
        assert AgentPathNode.INPUT_SAFETY_POLICY_OPA.value in agent_path
    if clinical_safety_expected:
        assert AgentPathNode.CLINICAL_SAFETY_POLICY_OPA.value in agent_path


class StaticClinicalSafetyPolicyClient(ClinicalSafetyPolicyClient):
    """为 API 测试提供显式注入的临床安全策略客户端。

    说明：该替身只消费已召回候选和可信结构化语义，不扫描用户原始文本。
    """

    async def decide(self, policy_input: ClinicalSafetyPolicyInput) -> ClinicalSafetyPolicyDecision:
        """根据结构化临床安全候选返回 API 测试决策。

        :param policy_input: evaluator 已组装的临床安全策略输入。
        :return: 返回用于 API 主链路断言的测试策略决策。
        """
        signals = tuple(
            SafetySignal(
                code=candidate.asset.resolved_code(),
                severity=(
                    "urgent"
                    if candidate.asset.severity == "urgent"
                    or candidate.asset.action_class in {"emergency", "same_day_visit", "urgent_visit"}
                    or candidate.score >= policy_input.thresholds.urgent_min_score
                    else candidate.asset.severity
                ),
                message=candidate.asset.triage_message
                or candidate.asset.clinical_risk_summary
                or f"命中临床安全风险：{candidate.asset.canonical_name}",
                matched_terms=list(candidate.matched_terms()),
            )
            for candidate in policy_input.candidates
            if candidate.score >= policy_input.thresholds.signal_min_score
            and not self._context_mismatch(candidate, policy_input)
        )
        action = (
            ClinicalSafetyPolicyAction.ESCALATE
            if any(signal.severity in {"urgent", "blocked"} for signal in signals)
            else ClinicalSafetyPolicyAction.OBSERVE
            if signals
            else ClinicalSafetyPolicyAction.ALLOW
        )
        return ClinicalSafetyPolicyDecision(
            action=action,
            allow=True,
            message="API 测试临床安全策略完成结构化候选裁决。",
            reasons=tuple(signal.code for signal in signals),
            signals=signals,
            metadata={"policy_backend": "static_api_test"},
        )

    def is_ready(self) -> bool:
        """声明 API 测试策略客户端可用。

        :return: 始终返回 True。
        """
        return True

    def _context_mismatch(
        self,
        candidate: ClinicalSafetyCandidate,
        policy_input: ClinicalSafetyPolicyInput,
    ) -> bool:
        """判断候选资产与可信结构化宠物上下文是否不匹配。

        :param candidate: 已由测试向量仓储召回的临床安全候选。
        :param policy_input: evaluator 已组装的临床安全策略输入。
        :return: 可信物种、性别或年龄范围不适用时返回 True。
        """
        semantic = policy_input.semantic_result
        if semantic is None or not semantic.is_trusted():
            return False
        asset = candidate.asset
        if asset.species_scope and semantic.species != "unknown" and semantic.species not in asset.species_scope:
            return True
        if asset.sex_scope and semantic.sex != "unknown" and semantic.sex not in asset.sex_scope:
            return True
        return bool(
            "senior" in asset.age_scope
            and semantic.age_group not in {"senior", "unknown"}
        )


class InMemoryScopeRepository(ScopeRepository):
    """为 API 测试提供显式注入的范围仓储。

    :return: 无返回值。
    """

    def __init__(self) -> None:
        """初始化测试范围仓储。

        :return: 无返回值。
        """
        self.profiles: dict[tuple[str, str], VerifiedPetProfile] = {}
        self.bindings: dict[str, SessionBinding] = {}
        self.auto_register_profiles: bool = True
        self.upsert_profile_count: int = 0
        self.bind_session_count: int = 0
        self.touch_session_count: int = 0

    def get_pet_profile(self, identity: TrustedIdentity) -> VerifiedPetProfile | None:
        """读取测试范围内的已验证宠物画像。

        :param identity: 本轮可信身份范围。
        :return: 返回测试画像投影。
        """
        key = (identity.user_id, identity.pet_id)
        if key not in self.profiles:
            if not self.auto_register_profiles:
                for profile in self.profiles.values():
                    if profile.pet_id == identity.pet_id:
                        return profile
                return None
            self.profiles[key] = VerifiedPetProfile(
                user_id=identity.user_id,
                pet_id=identity.pet_id,
                profile={},
                source="test_verified_profile_stub",
                is_active=True,
            )
        return self.profiles[key]

    def upsert_pet_profile(
        self,
        identity: TrustedIdentity,
        *,
        profile: dict[str, Any],
        source: str,
        is_active: bool,
    ) -> VerifiedPetProfile:
        """写入或刷新测试范围内的上游已验证宠物画像投影。

        :param identity: 本轮可信身份范围。
        :param profile: 上游已验证宠物画像。
        :param source: 上游画像来源。
        :param is_active: 宠物画像是否启用。
        :return: 返回写入后的画像投影。
        """
        self.upsert_profile_count += 1
        item = VerifiedPetProfile(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            profile=profile,
            source=source,
            is_active=is_active,
        )
        self.profiles[(identity.user_id, identity.pet_id)] = item
        return item

    def get_session_binding(self, session_id: str) -> SessionBinding | None:
        """读取测试范围内的会话绑定。

        :param session_id: 会话标识。
        :return: 返回测试会话绑定投影。
        """
        return self.bindings.get(session_id)

    def bind_session(self, identity: TrustedIdentity) -> SessionBinding | None:
        """创建测试范围内的会话绑定。

        :param identity: 本轮可信身份范围。
        :return: 返回创建后或已存在的会话绑定投影。
        """
        self.bind_session_count += 1
        now = datetime.now(UTC)
        self.bindings.setdefault(
            identity.session_id,
            SessionBinding(
                session_id=identity.session_id,
                user_id=identity.user_id,
                pet_id=identity.pet_id,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            ),
        )
        return self.bindings.get(identity.session_id)

    def touch_session(self, identity: TrustedIdentity) -> None:
        """更新测试范围内的会话最近访问时间。

        :param identity: 本轮可信身份范围。
        :return: 无返回值。
        """
        self.touch_session_count += 1
        existing = self.bindings.get(identity.session_id)
        if existing is None:
            self.bind_session(identity)
            return
        now = datetime.now(UTC)
        self.bindings[identity.session_id] = SessionBinding(
            session_id=existing.session_id,
            user_id=existing.user_id,
            pet_id=existing.pet_id,
            created_at=existing.created_at,
            updated_at=now,
            last_seen_at=now,
        )

    def is_ready(self) -> bool:
        """检查测试范围仓储是否就绪。

        :return: 始终返回 True。
        """
        return True

    def register_profile(
        self,
        *,
        user_id: str,
        pet_id: str,
        profile: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> None:
        """显式登记测试范围内的已验证宠物画像。

        :param user_id: 宠物所属用户标识。
        :param pet_id: 宠物标识。
        :param profile: 已验证宠物画像内容。
        :param is_active: 宠物资料是否启用。
        :return: 无返回值。
        """
        self.profiles[(user_id, pet_id)] = VerifiedPetProfile(
            user_id=user_id,
            pet_id=pet_id,
            profile=profile or {},
            source="test_registered_profile",
            is_active=is_active,
        )


class InMemoryTurnExecutionGate(TurnExecutionGateProtocol):
    """为 API 测试提供显式注入的 turn execution 门禁替身。

    :return: 无返回值。
    """

    def __init__(self) -> None:
        """初始化测试 turn execution 门禁替身。

        :return: 无返回值。
        """
        self.responses: dict[tuple[str, str, str, str], AgentTurnResponse] = {}
        self.execution_count: int = 0

    async def run(self, request: AgentTurnRequest, execute: TurnExecutor) -> AgentTurnResponse:
        """执行测试范围内的 turn lock 与幂等重放逻辑。

        :param request: 当前 Agent 回合请求。
        :param execute: 测试主链路执行函数。
        :return: 返回新生成或已保存的测试响应。
        """
        idempotency_key = request.turn_options.idempotency_key
        if not idempotency_key:
            self.execution_count += 1
            return await execute()

        key = (
            request.trusted_identity.user_id,
            request.trusted_identity.pet_id,
            request.trusted_identity.session_id,
            idempotency_key,
        )
        existing = self.responses.get(key)
        if existing is not None:
            return existing

        self.execution_count += 1
        response = await execute()
        self.responses[key] = response
        return response

    def is_ready(self) -> bool:
        """检查测试 turn execution 门禁是否就绪。

        :return: 始终返回 True。
        """
        return True


class StaticEmbeddingClient:
    """为 API 测试提供固定 embedding 客户端。

    :return: 无返回值。
    """

    @property
    def available(self) -> bool:
        """声明测试 embedding 客户端始终可用。

        :return: 始终返回 True。
        """
        return True

    def embed(self, text: str) -> list[float]:
        """为测试查询返回固定 embedding。

        :param text: 待向量化文本。
        :return: 返回固定二维向量。
        """
        assert text
        return [0.2, 0.8]


class StaticClinicalSafetyRepository:
    """为 API 测试提供向量召回仓储替身。

    :return: 无返回值。
    """

    def __init__(self) -> None:
        """初始化测试临床安全向量仓储。

        :return: 无返回值。
        """
        self.assets_by_id = self._assets()
        self.chunks_by_id = {
            asset.asset_id: ClinicalSafetyChunk(
                chunk_id=f"{asset.asset_id}.recognition.v1",
                asset_id=asset.asset_id,
                chunk_type="recognition",
                title=f"{asset.canonical_name} 风险识别",
                embedding_text="；".join(
                    [
                        asset.canonical_name,
                        *asset.aliases,
                        *asset.symptoms,
                        *asset.recognition_phrases,
                    ]
                ),
                metadata={},
                review_status="approved",
            )
            for asset in self.assets_by_id.values()
        }

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取测试临床安全资产。

        :param published_only: 是否仅返回发布态资产。
        :return: 返回测试资产列表。
        """
        del published_only
        return list(self.assets_by_id.values())

    def chunks(
        self,
        *,
        chunk_type: ClinicalSafetyChunkType | None = None,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取测试临床安全 chunk。

        :param chunk_type: 限定读取的 chunk 类型。
        :param published_only: 是否仅返回发布态 chunk。
        :return: 返回测试 chunk 列表。
        """
        del published_only
        chunks = list(self.chunks_by_id.values())
        if chunk_type is None:
            return chunks
        return [chunk for chunk in chunks if chunk.chunk_type == chunk_type]

    def asset_by_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> ClinicalSafetyAsset | None:
        """按资产标识读取测试临床安全资产。

        :param asset_id: 资产标识。
        :param published_only: 是否仅返回发布态资产。
        :return: 返回匹配资产或 None。
        """
        del published_only
        return self.assets_by_id.get(asset_id)

    def chunks_by_asset_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取指定资产关联的测试 chunk。

        :param asset_id: 资产标识。
        :param published_only: 是否仅返回发布态 chunk。
        :return: 返回关联 chunk 列表。
        """
        del published_only
        chunk = self.chunks_by_id.get(asset_id)
        return [chunk] if chunk is not None else []

    def retrieve_vector_chunk_hits(
        self,
        query_embedding: Sequence[float],
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
        min_score: float,
    ) -> list[ClinicalSafetyChunkHit]:
        """根据本轮查询文本返回对应测试向量命中。

        :param query_embedding: 查询 embedding。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回匹配测试场景的向量命中。
        """
        del query_embedding, min_score
        assert limit > 0
        if "recognition" not in chunk_types:
            return []
        hits: list[ClinicalSafetyChunkHit] = []
        for asset_id in self._matched_asset_ids(_current_test_input_text):
            chunk = self.chunks_by_id[asset_id]
            hits.append(
                ClinicalSafetyChunkHit(
                    chunk=chunk,
                    score=0.91,
                    distance=0.09,
                    score_type="cosine_similarity",
                    retrieval_source="clinical_safety_pgvector",
                    embedding_model="test-embedding",
                )
            )
        return hits[:limit]

    def is_ready(self) -> bool:
        """检查测试临床安全仓储是否就绪。

        :return: 始终返回 True。
        """
        return True

    def _matched_asset_ids(self, text: str) -> list[str]:
        """根据测试输入选择临床安全资产。

        :param text: 当前测试请求文本。
        :return: 返回匹配的资产标识列表。
        """
        matches: list[str] = []
        if "xylitol" in text or "无糖口香糖" in text:
            matches.append("safety_toxin_xylitol")
        if "巧克力" in text:
            matches.append("safety_toxin_chocolate")
        if "呼吸困难" in text or "站不起来" in text:
            matches.append("safety_emergency_red_flag")
        if "尿少尿频" in text or "尿频" in text:
            matches.append("safety_urinary_obstruction")
        if "多饮多尿" in text and "消瘦" in text:
            matches.append("safety_senior_cat_polydipsia_weight_loss")
        if "肚子胀" in text and "干呕" in text:
            matches.append("safety_gdv")
        if "牙龈发紫" in text or "呼吸很快" in text:
            matches.append("safety_cyanosis")
        return matches

    def _assets(self) -> dict[str, ClinicalSafetyAsset]:
        """构造 API 测试用临床安全资产。

        :return: 返回按资产标识索引的资产字典。
        """
        return {
            "safety_toxin_xylitol": ClinicalSafetyAsset(
                asset_id="safety_toxin_xylitol",
                asset_type="toxin",
                canonical_name="木糖醇",
                category="毒物",
                species_scope=("dog",),
                sex_scope=(),
                age_scope=(),
                severity="urgent",
                action_class="emergency",
                code="TOXIC_XYLITOL",
                aliases=("xylitol", "无糖口香糖"),
                symptoms=(),
                recognition_phrases=("xylitol", "无糖口香糖"),
            ),
            "safety_toxin_chocolate": ClinicalSafetyAsset(
                asset_id="safety_toxin_chocolate",
                asset_type="toxin",
                canonical_name="巧克力",
                category="毒物",
                species_scope=("dog", "cat"),
                sex_scope=(),
                age_scope=(),
                severity="urgent",
                action_class="emergency",
                code="TOXIC_SUBSTANCE",
                aliases=("巧克力",),
                symptoms=(),
                recognition_phrases=("巧克力",),
            ),
            "safety_emergency_red_flag": ClinicalSafetyAsset(
                asset_id="safety_emergency_red_flag",
                asset_type="emergency_red_flag",
                canonical_name="呼吸困难或无法站立",
                category="急症红旗",
                species_scope=("cat", "dog"),
                sex_scope=(),
                age_scope=(),
                severity="urgent",
                action_class="emergency",
                code="EMERGENCY_RED_FLAG",
                symptoms=("呼吸困难", "站不起来"),
                recognition_phrases=("呼吸困难", "站不起来"),
            ),
            "safety_urinary_obstruction": ClinicalSafetyAsset(
                asset_id="safety_urinary_obstruction",
                asset_type="danger_pattern",
                canonical_name="尿频尿少尿道梗阻风险",
                category="泌尿",
                species_scope=("cat",),
                sex_scope=("male",),
                age_scope=(),
                severity="urgent",
                action_class="emergency",
                code="PARTIAL_URINARY_OBSTRUCTION_RISK",
                symptoms=("尿少尿频",),
                recognition_phrases=("尿少尿频", "还能尿一点"),
            ),
            "safety_senior_cat_polydipsia_weight_loss": ClinicalSafetyAsset(
                asset_id="safety_senior_cat_polydipsia_weight_loss",
                asset_type="danger_pattern",
                canonical_name="老年猫多饮多尿消瘦",
                category="内分泌肾脏",
                species_scope=("cat",),
                sex_scope=(),
                age_scope=("senior",),
                severity="urgent",
                action_class="same_day_visit",
                code="SENIOR_CAT_POLYDIPSIA_WEIGHT_LOSS_RISK",
                symptoms=("多饮多尿", "消瘦"),
                recognition_phrases=("多饮多尿", "消瘦"),
            ),
            "safety_gdv": ClinicalSafetyAsset(
                asset_id="safety_gdv",
                asset_type="danger_pattern",
                canonical_name="胃扩张扭转风险",
                category="消化急症",
                species_scope=("dog",),
                sex_scope=(),
                age_scope=(),
                severity="urgent",
                action_class="emergency",
                code="GDV_RISK_PATTERN",
                symptoms=("肚子胀", "干呕吐不出来"),
                recognition_phrases=("肚子胀", "干呕吐不出来"),
            ),
            "safety_cyanosis": ClinicalSafetyAsset(
                asset_id="safety_cyanosis",
                asset_type="emergency_red_flag",
                canonical_name="发绀发紫",
                category="呼吸循环",
                species_scope=("cat", "dog"),
                sex_scope=(),
                age_scope=(),
                severity="urgent",
                action_class="emergency",
                code="CYANOSIS_RISK_PATTERN",
                symptoms=("牙龈发紫", "呼吸很快"),
                recognition_phrases=("牙龈发紫", "呼吸很快"),
            ),
        }


_test_scope_repository: InMemoryScopeRepository | None = None
_current_test_input_text = ""


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """执行 _client 内部辅助逻辑。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 返回函数执行结果。
    """
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-litellm")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.test/v1")
    monkeypatch.setenv("ENABLE_MEM0", "false")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("VET_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(QwenClient, "_send_chat", _fake_litellm_send_chat)
    monkeypatch.setattr(QwenClient, "_send_structured_chat", _fake_litellm_send_structured_chat)
    global _test_scope_repository
    _test_scope_repository = InMemoryScopeRepository()
    container = Container(
        Settings.from_env(),
        scope_repository=_test_scope_repository,
        turn_execution_gate=InMemoryTurnExecutionGate(),
        clinical_safety_repository=StaticClinicalSafetyRepository(),
        clinical_safety_policy_client=StaticClinicalSafetyPolicyClient(),
        embedding_client=StaticEmbeddingClient(),
        input_safety_service=InputSafetyService(
            Settings.from_env(),
            repository=StaticInputSafetyRepository(),
            detectors=(),
            policy_client=LocalInputSafetyPolicyClient(),
        ),
    )
    set_container(container)
    set_orchestrator(VetAgentIngressOrchestrator(container))
    return TestClient(app)


def _clear_test_container() -> None:
    """清理测试期间注入的容器覆盖。

    :return: 无返回值。
    """
    set_container(None)


def _register_verified_profile(
    *,
    user_id: str = "u1",
    pet_id: str = "p1",
    profile: dict[str, Any] | None = None,
    is_active: bool = True,
) -> None:
    """在测试范围仓储中显式登记服务端已验证宠物画像。

    :param user_id: 宠物所属用户标识。
    :param pet_id: 宠物标识。
    :param profile: 已验证宠物画像内容。
    :param is_active: 宠物画像是否启用。
    :return: 无返回值。
    """
    assert _test_scope_repository is not None
    _test_scope_repository.register_profile(
        user_id=user_id,
        pet_id=pet_id,
        profile=profile,
        is_active=is_active,
    )


@pytest.fixture(autouse=True)
def _reset_container_after_test() -> Iterator[None]:
    """在每个测试结束后清理注入容器，避免范围仓储与记忆状态串用。

    :return: 返回 pytest fixture 迭代器。
    """
    yield
    _clear_test_container()


async def _fake_litellm_send_chat(
    self: object,
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float,
) -> str:
    """执行 _fake_litellm_send_chat 内部辅助逻辑。

    :param messages: 参数 messages。
    :param model: 模型名称。
    :param temperature: 参数 temperature。
    :return: 返回函数执行结果。
    """
    del self, model, temperature
    user_text = _message_text(messages)
    if "ConsultationSemanticExtractorAgent" in user_text and "没以前积极" in user_text:
        return """
        {
          "facts": [
            {
              "key": "appetite",
              "value": "仍会进食但主动性下降",
              "status": "confirmed",
              "confidence": 0.91,
              "source_text": "饭还是吃的，就是没以前积极",
              "category": "intake_output"
            },
            {
              "key": "mental_status",
              "value": "整体活跃度较平时轻度下降",
              "status": "confirmed",
              "confidence": 0.88,
              "source_text": "没以前积极",
              "category": "systemic_status"
            },
            {
              "key": "vomiting",
              "value": "没有把东西吐出来",
              "status": "negative",
              "confidence": 0.9,
              "source_text": "没有把东西吐出来，只是像反胃",
              "category": "intake_output"
            }
          ],
          "intent": {
            "answer_now": true,
            "wants_triage": true,
            "correction": false,
            "raw_intent": "用户希望根据现有材料先判断"
          }
        }
        """
    if "TaskRouterAgent" in user_text and "主餐都会清空" in user_text:
        return """
        {
          "tasks": [
            {"domain": "general", "title": "一般补充", "text": "主餐都会清空，前天第一次看到", "priority": 10, "reason": "补充问诊信息"},
            {"domain": "behavior", "title": "互动状态", "text": "叫名字会抬头，结束后会自己拿玩具过来", "priority": 20, "reason": "互动和行为信息"},
            {"domain": "gastrointestinal", "title": "腹部反应", "text": "轻碰腹部会把身体绷紧", "priority": 30, "reason": "腹部相关线索"}
          ]
        }
        """
    if "RagQuestionPlannerAgent" in user_text and "缩成一团" in user_text:
        return """
        {
          "questions": [
            {
              "slot": "mental_status",
              "question": "它缩成一团时，腹部有没有明显紧绷，或被抱起、轻碰肚子时躲开？",
              "reason": "知识库提示姿势改变要优先区分疼痛、腹部不适和普通休息状态。",
              "evidence_titles": ["消化道症状"],
              "priority": 10
            },
            {
              "slot": "onset",
              "question": "这种缩着趴通常是在饭后多久出现，每次会持续多长时间？",
              "reason": "发生时间和进食关系能帮助判断是否更偏向短暂胃肠不适。",
              "evidence_titles": ["消化道症状"],
              "priority": 20
            }
          ]
        }
        """
    if "image_url" in user_text:
        return """
        {
          "summary": "Parsed visible lab items from the OSS image.",
          "ocr_text": "ALT 126 U/L 10-100 H\\nWBC 18.5 10^9/L 6-17 H\\nHGB 145 g/L 120-180",
          "items": [
            {"item_name": "ALT", "value_text": "126", "numeric_value": 126, "unit": "U/L", "reference_range": "10-100", "abnormal_flag": "high", "confidence": 0.86},
            {"item_name": "WBC", "value_text": "18.5", "numeric_value": 18.5, "unit": "10^9/L", "reference_range": "6-17", "abnormal_flag": "high", "confidence": 0.86},
            {"item_name": "HGB", "value_text": "145", "numeric_value": 145, "unit": "g/L", "reference_range": "120-180", "abnormal_flag": null, "confidence": 0.82}
          ]
        }
        """
    if "结构化问诊状态已足够" in user_text:
        return (
            "分诊/紧急度: 目前根据已补充的信息，暂未看到必须立即急诊的红旗，但仍需要继续观察变化。\n"
            "可能方向与依据: 更偏向轻度、短时的消化道不适或饮食刺激。\n"
            "现在可以做什么: 先保证饮水，暂停零食和新食物，少量多餐，观察精神、食欲、呕吐、腹泻次数和是否出现血便。不要自行喂人药。\n"
            "线下兽医兜底: 如果症状加重、持续超过 24 小时、出现血便/频繁呕吐/精神明显变差，请尽快线下就诊。"
        )
    if "行为" in user_text or "乱叫" in user_text or "拆家" in user_text:
        return "这更像行为和环境管理问题，但仍要先排除突然疼痛、食欲下降或神经异常等医疗红旗。"
    if "喂" in user_text or "吃" in user_text or "粮" in user_text:
        return "饲养建议应结合物种、年龄、体重、体况和活动量，并避免突然换粮。"
    return "我会先做分诊:目前还需要确认症状开始时间、精神食欲、是否呕吐腹泻或咳喘。如果加重或出现红旗症状，请尽快就医。"


async def _fake_litellm_send_structured_chat(
    self: object,
    messages: list[dict[str, Any]],
    *,
    response_model: type[BaseModel],
    model: str,
    temperature: float,
) -> BaseModel:
    """执行 _fake_litellm_send_structured_chat 内部辅助逻辑。

    :param messages: 参数 messages。
    :param response_model: 结构化响应模型。
    :param model: 模型名称。
    :param temperature: 参数 temperature。
    :return: 返回函数执行结果。
    """
    del self, model, temperature
    prompt_payload = _structured_message_payload(messages)
    if prompt_payload.get("task") != "将用户输入归一为临床安全结构化语义。":
        return response_model.model_validate_json("{}")
    request_text = str(prompt_payload.get("user_text") or "")
    pet_context_text = str(prompt_payload.get("pet_context_summary") or "")
    payload = {
        "species": "unknown",
        "sex": "unknown",
        "age_group": "adult",
        "age_text": "",
        "exposure_state": "unknown",
        "symptom_state": "present",
        "temporal_state": "current",
        "temporal_scope": "ongoing",
        "resolution_state": "ongoing",
        "temporal_text": "现在",
        "intent_type": "symptom",
        "high_risk_terms": [],
        "negated_terms": [],
        "confidence": 0.92,
        "rationale": "测试替身返回可信临床安全语义。",
    }
    if "狗" in request_text or "canine" in pet_context_text:
        payload["species"] = "dog"
    if "猫" in request_text or "feline" in pet_context_text:
        payload["species"] = "cat"
    if "male" in pet_context_text or "公" in pet_context_text or "雄" in pet_context_text:
        payload["sex"] = "male"
    if "female" in pet_context_text or "母" in pet_context_text or "雌" in pet_context_text:
        payload["sex"] = "female"
    if "12 years" in pet_context_text or "12 岁" in request_text or "老猫" in request_text:
        payload["age_group"] = "senior"
        payload["age_text"] = "12 years"
    if "3 years" in pet_context_text or "3岁" in pet_context_text:
        payload["age_text"] = "3 years"
    if "巧克力" in request_text or "xylitol" in request_text or "无糖口香糖" in request_text:
        payload.update(
            {
                "exposure_state": "confirmed",
                "symptom_state": "unknown",
                "intent_type": "toxicity",
                "high_risk_terms": ["毒物暴露"],
            }
        )
    if "没有误食" in request_text or "没给" in request_text or "未给" in request_text:
        payload.update(
            {
                "exposure_state": "denied",
                "symptom_state": "unknown",
                "intent_type": "knowledge",
                "high_risk_terms": [],
                "negated_terms": ["暴露"],
                "rationale": "测试替身返回明确否认暴露语义。",
            }
        )
    return response_model.model_validate(payload)


def _message_text(messages: list[dict[str, Any]]) -> str:
    """执行 _message_text 内部辅助逻辑。

    :param messages: 参数 messages。
    :return: 返回函数执行结果。
    """
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item))
    return "\n".join(parts)


def _structured_message_payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """从结构化抽取测试消息中读取 JSON 负载。

    :param messages: 结构化抽取请求消息。
    :return: 解析成功时返回 JSON 对象，否则返回空字典。
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _scope_assertion(
    *,
    user_id: str = "u1",
    pet_id: str = "p1",
    session_id: str = "s1",
    profile: dict[str, Any] | None = None,
    ownership_verified: bool = True,
    pet_active: bool = True,
    pet_deleted: bool = False,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """构造测试用 BFF 范围声明。

    :param user_id: BFF 已认证用户标识。
    :param pet_id: BFF 已校验归属的宠物标识。
    :param session_id: BFF 发放或复用的会话标识。
    :param profile: 服务端已验证宠物基础画像。
    :param ownership_verified: 是否声明已完成宠物归属校验。
    :param pet_active: 是否声明宠物可用。
    :param pet_deleted: 是否声明宠物已删除。
    :param expires_at: 声明过期时间。
    :return: 返回测试请求可直接使用的范围声明。
    """
    now = datetime.now(UTC).isoformat()
    verified_profile = profile or {
        "species": "犬",
        "breed": "柯基",
        "age": "3岁",
        "weight_kg": 12,
    }
    assertion: dict[str, Any] = {
        "schema_version": "v1",
        "issuer": "test-bff",
        "issued_at": now,
        "user_id": user_id,
        "pet_id": pet_id,
        "session_id": session_id,
        "authorization": {
            "ownership_verified": ownership_verified,
            "pet_active": pet_active,
            "pet_status": "active" if pet_active else "inactive",
            "pet_deleted": pet_deleted,
        },
        "profile": verified_profile,
        "source": {
            "system": "test-main-service",
            "database": "app_dev",
            "table": "master_pet_info",
            "record_id": pet_id,
            "record_updated_at": now,
            "data_source": "test",
        },
        "session_policy": {"binding_mode": "single_user_pet_per_session"},
    }
    if expires_at is not None:
        assertion["expires_at"] = expires_at
    return assertion


def _payload(text: str, **extra: Any) -> dict[str, Any]:
    """执行 _payload 内部辅助逻辑。

    :param text: 待处理文本。
    :param extra: 参数 extra。
    :return: 返回函数执行结果。
    """
    global _current_test_input_text
    _current_test_input_text = text
    vet_context = dict(
        extra.pop(
            "vet_context",
            {
                "user_id": "u1",
                "session_id": "s1",
                "pet_id": "p1",
                "pet_info": {
                    "species": "犬",
                    "breed": "柯基",
                    "age": "3岁",
                    "weight_kg": 12,
                },
            },
        )
    )
    user_id = str(vet_context.pop("user_id", "u1"))
    session_id = str(vet_context.pop("session_id", "s1"))
    pet_id = str(vet_context.pop("pet_id", "p1"))
    pet_info = dict(vet_context.get("pet_info") or {})
    scope_assertion = extra.pop(
        "scope_assertion",
        _scope_assertion(user_id=user_id, pet_id=pet_id, session_id=session_id, profile=pet_info or None),
    )
    payload = {
        "input": text,
        "stream": False,
        "scope_assertion": scope_assertion,
        "vet_context": {"pet_info": pet_info},
    }
    payload.update(extra)
    return payload


def _payload_without_pet_info(text: str, session_id: str = "s_ctx") -> dict[str, Any]:
    """执行 _payload_without_pet_info 内部辅助逻辑。

    :param text: 待处理文本。
    :param session_id: 参数 session_id。
    :return: 返回函数执行结果。
    """
    return {
        "input": text,
        "stream": False,
        "scope_assertion": _scope_assertion(
            user_id="u_ctx",
            session_id=session_id,
            pet_id="p_ctx",
            profile={"species": "dog"},
        ),
        "vet_context": {"pet_info": {}},
    }


def test_health_and_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["orchestrator"] is True


def test_sync_turn_uses_litellm_gateway_and_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("我家狗今天有点拉稀，应该怎么办？"))

    assert response.status_code == 200
    data = response.json()
    assert data["request_id"]
    assert data["trace_id"]
    assert "线下兽医" in data["output_text"]
    assert data["evidence"]
    assert "InputSafetyService" in data["metadata"]["multi_agent_path"]
    _assert_policy_path_is_named(
        data["metadata"]["multi_agent_path"],
        clinical_safety_expected=True,
    )


def test_toxic_substance_is_escalated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("狗误食了巧克力，还能观察一下吗？"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert "请尽快联系线下兽医医院" in data["output_text"]
    assert any(signal["code"] == "TOXIC_SUBSTANCE" for signal in data["safety_signals"])
    assert data["metadata"]["input_safety_decision"]["allow"] is True


def test_emergency_red_flag_skips_followup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("猫现在呼吸困难，站不起来"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert "请尽快联系线下兽医医院" in data["output_text"]
    assert any(signal["code"] == "EMERGENCY_RED_FLAG" for signal in data["safety_signals"])
    assert data["metadata"]["input_safety_decision"]["allow"] is True


def test_radiology_attachment_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json=_payload(
            "帮我看看这张 X 光片",
            attachments=[
                {
                    "attachment_id": "a1",
                    "mime_type": "image/jpeg",
                    "purpose": "radiology",
                    "storage_ref": "s3://bucket/xray.jpg",
                }
            ],
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked"
    assert "未开放影像判读能力" in data["output_text"]
    assert any(signal["code"] == "RADIOLOGY_GATE" for signal in data["safety_signals"])
    assert data["metadata"]["memory_extraction"]["skipped_reason"] == "input_safety_policy_stopped_main_chain"
    _assert_policy_path_is_named(
        data["metadata"]["multi_agent_path"],
        clinical_safety_expected=False,
    )


@pytest.mark.parametrize(
    ("text", "pet_info", "expected_code"),
    [
        (
            "它刚才把桌上写着 xylitol 的无糖口香糖咽下去了，目前还能自行走动。我现在最先应该做什么？",
            {"species": "canine", "sex": "male", "age": "2 years", "weight_kg": 9.5},
            "TOXIC_XYLITOL",
        ),
        (
            "它尿少尿频但还能尿一点。",
            {"species": "cat", "sex": "male", "age": "3 years", "weight_kg": 5.2},
            "PARTIAL_URINARY_OBSTRUCTION_RISK",
        ),
        (
            "我家 12 岁老猫最近多饮多尿，而且慢慢消瘦。",
            {},
            "SENIOR_CAT_POLYDIPSIA_WEIGHT_LOSS_RISK",
        ),
        (
            "狗饭后肚子胀，干呕吐不出来，流口水很烦躁。",
            {"species": "canine", "sex": "male", "age": "4 years", "weight_kg": 28},
            "GDV_RISK_PATTERN",
        ),
        (
            "猫牙龈发紫，呼吸很快。",
            {"species": "feline", "sex": "female", "age": "5 years", "weight_kg": 4.1},
            "CYANOSIS_RISK_PATTERN",
        ),
    ],
)
def test_contextual_clinical_safety_escalates_hidden_risk_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    pet_info: dict[str, object],
    expected_code: str,
) -> None:
    """验证结构化临床安全层能够升级隐匿高风险组合。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :param text: 用户本轮输入文本。
    :param pet_info: 请求侧自报宠物资料。
    :param expected_code: 期望命中的结构化临床安全信号编码。
    :return: 无返回值；断言通过表示隐匿高风险场景已进入安全升级路径。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload(text)
    payload["vet_context"]["pet_info"] = pet_info
    if pet_info:
        payload["scope_assertion"] = _scope_assertion(profile=dict(pet_info))
    else:
        payload["scope_assertion"] = _scope_assertion(profile={"species": "cat", "age": "12 years"})

    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert "ClinicalSafetyEvaluator" in data["metadata"]["multi_agent_path"]
    _assert_policy_path_is_named(
        data["metadata"]["multi_agent_path"],
        clinical_safety_expected=True,
    )
    assert data["metadata"]["input_safety_decision"]["allow"] is True
    assert any(signal["code"] == expected_code for signal in data["safety_signals"])


def test_unverified_pet_info_does_not_independently_trigger_clinical_risk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证请求侧自报宠物资料不会单独触发临床高风险信号。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示自报资料已与临床硬判断隔离。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload("最近多饮多尿，而且慢慢消瘦。")
    payload["vet_context"]["pet_info"] = {
        "species": "cat",
        "sex": "female",
        "age": "12 years",
        "weight_kg": 3.8,
    }

    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert not any(
        signal["code"] == "SENIOR_CAT_POLYDIPSIA_WEIGHT_LOSS_RISK"
        for signal in data["safety_signals"]
    )


def test_scope_policy_requires_scope_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证缺少 BFF 范围声明时入口请求会被拒绝。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示旧版 vet_context 身份路径已移除。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload("它今天有点拉稀。")
    payload.pop("scope_assertion")

    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "MISSING_REQUIRED_CONTEXT"


def test_scope_policy_rejects_legacy_vet_context_identity_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证旧版 vet_context 身份字段不会被入口模型继续接收。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示旧版身份字段已从请求契约中移除。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload("它今天有点拉稀。")
    payload["vet_context"]["user_id"] = "legacy_user"
    payload["vet_context"]["session_id"] = "legacy_session"
    payload["vet_context"]["pet_id"] = "legacy_pet"

    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"


def test_scope_assertion_rejects_source_record_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证范围声明来源记录与宠物标识不一致时会被入口模型拒绝。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示来源审计字段具备一致性校验。
    """
    client = _client(tmp_path, monkeypatch)
    assertion = _scope_assertion(pet_id="p_scope")
    assertion["source"]["record_id"] = "p_other"
    payload = _payload("它今天有点拉稀。", scope_assertion=assertion)

    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"


def test_scope_policy_rejects_inactive_pet_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证停用的服务端宠物画像会被范围策略拒绝。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示停用资料不会进入 Agent 主链路。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload(
        "它今天有点拉稀。",
        scope_assertion=_scope_assertion(pet_active=False),
    )
    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "FORBIDDEN"
    assert data["details"]["scope_decision"]["action"] == "deny_inactive_pet"


def test_scope_policy_rejects_unverified_ownership_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 BFF 未声明已完成宠物归属校验时范围策略会拒绝请求。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示 Agent 不接受未验证归属声明。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload(
        "它今天有点拉稀。",
        scope_assertion=_scope_assertion(ownership_verified=False),
    )
    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "FORBIDDEN"
    assert data["details"]["scope_decision"]["action"] == "deny_scope_assertion_invalid"


def test_scope_assertion_bootstraps_verified_pet_profile_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证冷启动时 BFF 范围声明会写入 Agent 本地画像投影。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示冷启动不再依赖 pet_info 自动注册。
    """
    client = _client(tmp_path, monkeypatch)
    assert _test_scope_repository is not None
    _test_scope_repository.auto_register_profiles = False
    payload = _payload(
        "它今天有点拉稀。",
        vet_context={
            "user_id": "u_bootstrap",
            "session_id": "s_bootstrap",
            "pet_id": "p_bootstrap",
            "pet_info": {},
        },
        scope_assertion=_scope_assertion(
            user_id="u_bootstrap",
            session_id="s_bootstrap",
            pet_id="p_bootstrap",
            profile={"species": "dog", "breed": "corgi", "age": "3 years", "weight_kg": 12},
        ),
    )

    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 200
    profile = _test_scope_repository.profiles[("u_bootstrap", "p_bootstrap")]
    assert profile.profile["species"] == "dog"
    assert profile.profile["breed"] == "corgi"
    assert profile.source.startswith("test-bff:test-main-service:master_pet_info:p_bootstrap")


def test_scope_authorization_side_effect_runs_once_per_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证单次主接口请求不会重复执行范围授权副作用。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示入口授权快照已被主链路复用。
    """
    client = _client(tmp_path, monkeypatch)
    assert _test_scope_repository is not None

    response = client.post("/agent/turns", json=_payload("它今天有点拉稀。"))

    assert response.status_code == 200
    assert _test_scope_repository.upsert_profile_count == 1
    assert _test_scope_repository.bind_session_count == 1
    assert _test_scope_repository.touch_session_count == 0


def test_scope_policy_rejects_expired_scope_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证过期 BFF 范围声明会被范围策略拒绝。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示 Agent 不接受过期范围声明。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload(
        "它今天有点拉稀。",
        scope_assertion=_scope_assertion(expires_at="2000-01-01T00:00:00+00:00"),
    )

    response = client.post("/agent/turns", json=payload)

    assert response.status_code == 403
    assert response.json()["details"]["scope_decision"]["action"] == "deny_scope_assertion_invalid"


def test_memory_read_correct_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    correction = {
        "user_id": "u1",
        "session_id": "s1",
        "pet_id": "p1",
        "summary": "主人偏好先给简短结论，再看依据。",
    }
    assert client.put("/memories", json=correction).status_code == 200

    memory = client.get("/memories?user_id=u1&session_id=s1&pet_id=p1").json()
    assert memory["pet"]["last_summary"] == correction["summary"]

    assert client.delete("/memories/pets/p1?user_id=u1&session_id=s1").status_code == 200
    memory_after_delete = client.get("/memories?user_id=u1&session_id=s1&pet_id=p1").json()
    assert memory_after_delete["pet"] == {}


def test_pet_fact_memory_can_be_persisted_and_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    fact = {
        "user_id": "u_fact",
        "session_id": "s_fact",
        "pet_id": "p_fact",
        "fact_type": "medical",
        "fact_key": "allergy",
        "fact_value": "疑似鸡肉过敏",
        "confidence": 0.9,
    }
    assert client.put("/memories/facts", json=fact).status_code == 200

    memory = client.get("/memories?user_id=u_fact&session_id=s_fact&pet_id=p_fact").json()
    facts = memory["pet"]["facts"]
    assert facts[0]["fact_value"] == "疑似鸡肉过敏"


def test_idempotency_key_reuses_first_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload(
        "我家狗今天有点拉稀，应该怎么办？",
        turn_options={"idempotency_key": "idem_same_turn"},
    )

    first = client.post("/agent/turns", json=payload)
    second = client.post("/agent/turns", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_openai_compatible_response_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/openai/v1/responses", json=_payload("我家狗最近乱叫"))

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "response"
    assert data["output"][0]["role"] == "assistant"
    assert data["output"][0]["content"][0]["type"] == "output_text"
    assert data["reasoning_display"]["text"]
    assert data["segments"][0]["reasoning_display"]["text"]
    assert data["vet_result"]["route"]
    assert data["metadata"]["request_id"]


def test_agent_turn_external_contract_includes_reasoning_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload_without_pet_info("它有点拉稀，怎么办？", session_id="s_reasoning"))

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "agent.turn"
    assert data["created_at"]
    assert data["output"][0]["content"][0]["type"] == "output_text"
    assert data["reasoning_display"]["title"] == "本轮思考过程"
    assert data["reasoning_display"]["metadata"]["kind"] == "user_visible_diagnostic_evidence"
    assert data["reasoning_display"]["text"]
    assert data["segments"][0]["reasoning_display"]["projection_id"] == data["reasoning_display"]["projection_id"]
    assert data["segments"][0]["output_text"] == data["output_text"]
    assert data["vet_result"]["route"]


def test_stream_turn_emits_reasoning_display_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json={**_payload_without_pet_info("它有点拉稀，怎么办？", session_id="s_stream_reasoning"), "stream": True},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: reasoning_display.started" in body
    assert "event: reasoning_display.delta" in body
    assert "event: reasoning_display.completed" in body
    assert "event: segment.delta" in body


def test_multi_task_turn_splits_into_independent_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json=_payload(
            "我家狗今天拉稀，精神正常，食欲正常，没有呕吐。还有最近夜里乱叫。顺便问下能不能换粮？",
            vet_context={
                "user_id": "u_multi",
                "session_id": "s_multi",
                "pet_id": "p_multi",
                "pet_info": {
                    "species": "犬",
                    "breed": "柯基",
                    "age": "3岁",
                    "weight_kg": 12,
                },
            },
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["vet_result"]["route"] == "multi_task_consultation"
    assert data["metadata"]["task_count"] == 3
    assert len(data["segments"]) == 3
    assert data["reasoning_display"]["metadata"]["kind"] == "user_visible_multi_task_routing"
    titles = [segment["title"] for segment in data["segments"]]
    assert any("消化道问题" in title for title in titles)
    assert any("行为问题" in title for title in titles)
    assert any("喂养问题" in title for title in titles)
    assert all(segment["reasoning_display"]["text"] for segment in data["segments"])


def test_llm_task_router_can_drive_task_splitting() -> None:
    """验证对应业务场景是否符合预期。

    :return: 无返回值；断言通过表示场景符合预期。
    """
    class FakeQwen:
        available = True

        async def chat(
            self,
            messages: list[dict[str, Any]],
            *,
            model: str | None = None,
            temperature: float = 0.2,
        ) -> str:
            """执行 chat 业务逻辑。

            :param messages: 参数 messages。
            :param model: 模型名称。
            :param temperature: 参数 temperature。
            :return: 返回异步执行结果。
            """
            return """
            {
              "tasks": [
                {"domain": "behavior", "title": "夜里乱叫", "text": "最近夜里乱叫", "priority": 20, "reason": "行为场景"},
                {"domain": "gastrointestinal", "title": "拉稀", "text": "今天拉稀，精神正常", "priority": 10, "reason": "消化道症状"}
              ]
            }
            """

    splitter = TaskSplitterAgent(
        FileRuleRepository(Settings().seed_dir),
        FakeQwen(),
        Settings(enable_llm_task_splitter=True, litellm_api_key="test"),
    )

    import asyncio

    decision = asyncio.run(splitter.split("我家狗今天拉稀，精神正常。还有最近夜里乱叫。"))

    assert decision.strategy == "llm_task_router"
    assert [task.domain for task in decision.tasks] == ["gastrointestinal", "behavior"]
    assert decision.tasks[0].reason == "消化道症状"


def test_header_body_id_conflict_returns_invalid_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload_without_pet_info("它有点拉稀，怎么办？", session_id="s_header_conflict")
    payload["request_id"] = "req_body"

    response = client.post("/agent/turns", json=payload, headers={"X-Request-ID": "req_header"})

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert data["request_id"] == "req_body"


def test_consultation_first_turn_collects_slots_without_final_advice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload_without_pet_info("它有点拉稀，怎么办？"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "requires_followup"
    assert data["metadata"]["consultation_phase"] == "collecting_info"
    assert "我先不武断下结论" in data["output_text"]
    assert "物种: dog" in data["output_text"]
    assert "它是猫还是狗" not in data["output_text"]
    assert "请先回答" in data["output_text"]
    assert "QwenResponseAgent" not in data["metadata"]["multi_agent_path"]


def test_rag_guided_followup_uses_knowledge_to_plan_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证知识库命中结果可反推动态追问。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json=_payload(
            "我家 3 岁、12 公斤的柯基犬饭后总是缩成一团趴着，看起来不太舒服。",
            vet_context={
                "user_id": "u_rag_followup",
                "session_id": "s_rag_followup",
                "pet_id": "p_rag_followup",
                "pet_info": {
                    "species": "犬",
                    "breed": "柯基",
                    "age": "3岁",
                    "weight_kg": 12,
                },
            },
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "requires_followup"
    assert data["vet_result"]["route"] == "rag_guided_followup"
    assert "RagQuestionPlannerAgent" in data["metadata"]["multi_agent_path"]
    assert "QwenResponseAgent" not in data["metadata"]["multi_agent_path"]
    assert "腹部有没有明显紧绷" in data["output_text"]
    assert "饭后多久出现" in data["output_text"]
    assert "为什么先问这些" in data["output_text"]
    plan = data["metadata"]["followup_question_plan"]
    assert plan["strategy"] == "rag_llm_question_planner"
    assert plan["questions"][0]["evidence_titles"] == ["消化道症状"]
    assert data["evidence"]


def test_unfinished_consultation_state_skips_task_splitting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证未完成问诊状态会优先吸收下一轮回答。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    session_id = "s_skip_task_router"
    vet_context = {
        "user_id": "u_skip_task_router",
        "session_id": session_id,
        "pet_id": "p_skip_task_router",
        "pet_info": {
            "species": "犬",
            "breed": "柯基",
            "age": "3岁",
            "weight_kg": 12,
        },
    }

    first = client.post(
        "/agent/turns",
        json=_payload(
            "我家 3 岁、12 公斤的柯基犬饭后总是缩成一团趴着，看起来不太舒服。",
            vet_context=vet_context,
        ),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "requires_followup"

    second = client.post(
        "/agent/turns",
        json=_payload(
            "主餐都会清空，平时喜欢的小块奖励也主动来拿，叫名字会抬头并且结束后会自己拿玩具过来，前天第一次看到，轻碰腹部会把身体绷紧但不会躲开。",
            vet_context=vet_context,
        ),
    )

    assert second.status_code == 200
    data = second.json()
    assert data["status"] == "completed"
    assert data["vet_result"]["route"] == "standard_consultation"
    assert data["metadata"]["task_router_skipped"] is True
    assert data["metadata"]["task_router_strategy"] == "skipped_unfinished_consultation_state"
    assert "TaskRouterAgent" not in data["metadata"]["multi_agent_path"]
    assert "AnswerabilityEvaluator" in data["metadata"]["multi_agent_path"]
    assert data["metadata"]["answerability"]["mode"] in {"slot_complete", "sufficient_semantic_evidence"}
    assert "任务 1" not in data["output_text"]


def test_answer_now_intent_stops_followup_funnel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证用户明确要求先答时，系统会进入带边界的阶段性回答。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    vet_context = {
        "user_id": "u_answer_now",
        "session_id": "s_answer_now",
        "pet_id": "p_answer_now",
        "pet_info": {
            "species": "犬",
            "breed": "柯基",
            "age": "3岁",
            "weight_kg": 12,
        },
    }

    first = client.post(
        "/agent/turns",
        json=_payload(
            "我家 3 岁、12 公斤的柯基犬饭后总是缩成一团趴着，看起来不太舒服。",
            vet_context=vet_context,
        ),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "requires_followup"

    second = client.post(
        "/agent/turns",
        json=_payload(
            "别再追问了，直接说目前怎么看。它前天开始这样，饭量和平常一样，精神也还行，没吐。",
            vet_context=vet_context,
        ),
    )

    assert second.status_code == 200
    data = second.json()
    assert data["status"] == "completed"
    assert data["metadata"]["consultation_phase"] == "ready_to_answer"
    assert data["metadata"]["missing_slots"] == []
    assert data["metadata"]["answerability"]["mode"] == "user_requested_answer_now"
    assert "QwenResponseAgent" in data["metadata"]["multi_agent_path"]
    assert "请先回答" not in data["output_text"]


def test_semantic_answers_reduce_missing_slots_without_exact_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证宽泛语义表达可以补全上下文，避免固定槽位追问漏斗。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    vet_context = {
        "user_id": "u_semantic_slots",
        "session_id": "s_semantic_slots",
        "pet_id": "p_semantic_slots",
        "pet_info": {
            "species": "猫",
            "breed": "中华田园猫",
            "age": "4岁",
            "weight_kg": 4.6,
        },
    }

    first = client.post(
        "/agent/turns",
        json=_payload(
            "我家 4 岁猫这两天偶尔会躲到角落，晚上节奏和平常不太一样。",
            vet_context=vet_context,
        ),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "requires_followup"

    second = client.post(
        "/agent/turns",
        json=_payload("饭量没减少，叫它有反应，也会照常喝水；没有吐，也没有拉肚子。", vet_context=vet_context),
    )

    assert second.status_code == 200
    data = second.json()
    assert data["status"] == "completed"
    slots = data["metadata"]["consultation_state"]["slots"]
    assert slots["mental_status"] == "精神基本正常"
    assert slots["appetite"] == "食欲/饮水基本正常"
    assert slots["vomiting"] == "无呕吐"
    assert data["metadata"]["answerability"]["mode"] in {"slot_complete", "sufficient_semantic_evidence"}


def test_llm_semantic_extractor_is_primary_fact_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 LLM 语义抽取结果会作为主路径合并到问诊状态。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    vet_context = {
        "user_id": "u_llm_semantic",
        "session_id": "s_llm_semantic",
        "pet_id": "p_llm_semantic",
        "pet_info": {
            "species": "犬",
            "breed": "柯基",
            "age": "3岁",
            "weight_kg": 12,
        },
    }

    response = client.post(
        "/agent/turns",
        json=_payload(
            "我家 3 岁犬饭还是吃的，就是没以前积极；没有把东西吐出来，只是像反胃。先根据这些告诉我需不需要检查。",
            vet_context=vet_context,
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "ConsultationSemanticExtractorAgent" in data["metadata"]["multi_agent_path"]
    semantic = data["metadata"]["semantic_extraction"]
    assert semantic["strategy"] == "llm_semantic_extractor"
    assert semantic["used_as_primary_semantic_path"] is True
    assert {"appetite", "mental_status", "vomiting"}.issubset(set(semantic["applied_fact_keys"]))
    slots = data["metadata"]["consultation_state"]["slots"]
    assert slots["appetite"] == "仍会进食但主动性下降"
    assert slots["mental_status"] == "整体活跃度较平时轻度下降"
    assert slots["vomiting"] == "没有把东西吐出来"
    assert data["metadata"]["answerability"]["mode"] == "user_requested_answer_now"


def test_consultation_second_turn_completes_after_context_is_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    session_id = "s_ctx_2"

    first = client.post("/agent/turns", json=_payload_without_pet_info("它有点拉稀，怎么办？", session_id=session_id))
    assert first.json()["status"] == "requires_followup"

    second = client.post(
        "/agent/turns",
        json=_payload_without_pet_info(
            "是狗，3岁，12公斤，今天早上开始，精神食欲正常，没有呕吐，大便拉稀但没有血。",
            session_id=session_id,
        ),
    )

    assert second.status_code == 200
    data = second.json()
    assert data["status"] == "completed"
    assert data["metadata"]["consultation_phase"] == "ready_to_answer"
    assert data["metadata"]["missing_slots"] == []
    assert "QwenResponseAgent" in data["metadata"]["multi_agent_path"]
    assert "阶段性最终建议" not in data["output_text"]
    assert "请先回答" not in data["output_text"]
    assert "线下兽医" in data["output_text"]


def test_completed_consultation_does_not_pollute_next_chief_complaint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证已完成问诊不会污染同一 session 的下一次独立主诉。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量与依赖替换工具。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    session_id = "s_state_layering"
    vet_context = {
        "user_id": "u_state_layering",
        "session_id": session_id,
        "pet_id": "p_state_layering",
        "pet_info": {
            "species": "犬",
            "breed": "柯基",
            "age": "3岁",
            "weight_kg": 12,
        },
    }

    first = client.post(
        "/agent/turns",
        json=_payload(
            "我家狗今天早上拉稀，精神食欲正常，没有呕吐，大便没有血。",
            vet_context=vet_context,
        ),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "completed"

    second = client.post(
        "/agent/turns",
        json=_payload(
            "它现在又开始咳嗽了，像卡住一样。",
            vet_context=vet_context,
        ),
    )

    assert second.status_code == 200
    data = second.json()
    assert data["status"] == "requires_followup"
    state = data["metadata"]["consultation_state"]
    assert state["chief_complaint"] == "它现在又开始咳嗽了，像卡住一样。"
    assert state["domain"] == "respiratory"
    assert "stool" not in state["slots"]
    assert "appetite" not in state["slots"]
    assert "onset" not in state["slots"]


def test_api_key_auth_can_be_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    monkeypatch.setenv("REQUIRE_API_AUTH", "true")
    monkeypatch.setenv("VET_AGENT_API_KEYS", "secret-token")
    client = _client(tmp_path, monkeypatch)
    payload = _payload_without_pet_info("My dog has mild diarrhea.", session_id="s_auth")

    missing = client.post("/agent/turns", json=payload)
    wrong = client.post("/agent/turns", json=payload, headers={"Authorization": "Bearer wrong"})
    ok = client.post("/agent/turns", json=payload, headers={"Authorization": "Bearer secret-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert ok.status_code == 200


def test_session_policy_blocks_switching_pet_in_same_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    first = _payload_without_pet_info("My dog has mild diarrhea.", session_id="s_one_pet")
    second = _payload_without_pet_info("My cat has mild diarrhea.", session_id="s_one_pet")
    second["scope_assertion"] = _scope_assertion(
        user_id="u_ctx",
        session_id="s_one_pet",
        pet_id="another_pet",
        profile={"species": "cat"},
    )

    assert client.post("/agent/turns", json=first).status_code == 200
    response = client.post("/agent/turns", json=second)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_memory_extraction_does_not_persist_pet_info_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证请求侧自报宠物资料不会写入长期记忆。

    :param tmp_path: 临时数据目录。
    :param monkeypatch: pytest 环境变量和方法替换工具。
    :return: 无返回值；断言通过表示自报资料不会污染长期记忆。
    """
    client = _client(tmp_path, monkeypatch)
    payload = {
        "input": "Please remember this profile.",
        "stream": False,
        "scope_assertion": _scope_assertion(
            user_id="u_extract",
            session_id="s_extract",
            pet_id="p_extract",
            profile={"species": "dog"},
        ),
        "vet_context": {
            "pet_info": {
                "species": "dog",
                "breed": "corgi",
                "age": "3 years",
                "weight_kg": 12,
            },
        },
    }

    assert client.post("/agent/turns", json=payload).status_code == 200
    memory = client.get("/memories?user_id=u_extract&session_id=s_extract&pet_id=p_extract").json()
    fact_keys = {item["fact_key"] for item in memory["pet"].get("facts", [])}
    assert _test_scope_repository is not None
    profile = _test_scope_repository.profiles[("u_extract", "p_extract")]

    assert not {"species", "breed", "age", "weight_kg"}.intersection(fact_keys)
    assert profile.profile == {"species": "dog"}
    assert profile.source.startswith("test-bff:test-main-service:master_pet_info:p_extract")


def test_safety_review_removes_dosage_expression() -> None:
    """验证对应业务场景是否符合预期。

    :return: 无返回值；断言通过表示场景符合预期。
    """
    from vet_agent.agents import SafetyAgent, SafetyReviewAgent

    reviewer = SafetyReviewAgent(SafetyAgent())
    result = reviewer.review_text("You can give 5 mg/kg twice daily.")

    assert "5 mg/kg" not in result.text
    assert any(signal.code == "DOSAGE_REMOVED" for signal in result.signals)


def test_report_parse_extracts_structured_lab_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/reports/parse",
        json={
            "user_id": "u_report",
            "session_id": "s_report",
            "pet_id": "p_report",
            "report_type": "bloodwork",
            "oss_image_url": "https://infra-dev-file-storage.oss-cn-hangzhou-internal.aliyuncs.com/uploads/reports/lab.jpg",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "parsed"
    assert data["source_type"] == "oss_image_url"
    assert data["report_id"].startswith("rpt_")
    assert len(data["items"]) >= 3
    assert data["attachments"][0]["storage_ref"] == "oss://infra-dev-file-storage/uploads/reports/lab.jpg"
    assert any(item["item_name"] == "ALT" and item["abnormal_flag"] == "high" for item in data["items"])


def test_radiology_report_is_blocked_from_online_interpretation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/reports/parse",
        json={
            "user_id": "u_xray_report",
            "session_id": "s_xray_report",
            "pet_id": "p_xray_report",
            "report_type": "xray",
            "oss_image_url": "oss://infra-dev-file-storage/uploads/reports/xray.jpg",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked"
    assert data["items"] == []
    assert data["safety_flags"][0]["code"] == "RADIOLOGY_REPORT_GATE"


def test_report_parse_rejects_non_oss_image_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/reports/parse",
        json={
            "user_id": "u_bad_report",
            "session_id": "s_bad_report",
            "pet_id": "p_bad_report",
            "report_type": "bloodwork",
            "oss_image_url": "https://example.com/lab.jpg",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


def test_rag_governance_admin_can_list_and_update_seed_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    stats = client.get("/admin/rag/stats")
    chunks = client.get("/admin/rag/chunks?limit=1")
    update = client.patch(
        "/admin/rag/chunks/1",
        json={"review_status": "rejected", "enabled": False, "reason": "test quarantine"},
    )

    assert stats.status_code == 200
    assert stats.json()["total"] >= 1
    assert chunks.status_code == 200
    assert chunks.json()["items"]
    assert update.status_code == 200
    assert update.json()["review_status"] == "rejected"
    assert update.json()["enabled"] is False


def test_admin_can_preview_import_publish_clinical_conditions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证结构化临床病症卡可通过 Admin API 预览、导入、发布与查询。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    payload = {
        "_meta": {
            "file_name": "vet_conditions.json",
            "source_document": "common_conditions_handbook.md",
            "clinical_review_required": True,
        },
        "source": "common_conditions_handbook",
        "version": "v-test",
        "conditions": [
            {
                "system": "耳部(外耳)",
                "condition": "外耳炎 / 外耳道炎",
                "presentation": "频繁甩头、抓耳、耳道发红、有异味。",
                "differentials": "过敏、耳螨、细菌或酵母感染、异物。",
                "followupQuestions": "1) 单耳还是双耳? 2) 分泌物是什么颜色和气味? 3) 有没有歪头或走路不稳?",
                "triage": "明显异味、分泌物或疼痛建议尽快就诊。",
                "redFlagsEscalate": "歪头、转圈、眼球震颤、剧痛或耳道大量出血需立即就诊。",
                "medicationDirection": "滴耳药和洗耳液需兽医检查鼓膜后选择，不给剂量。",
                "homeAdvice": "避免棉签深捅，保持耳道干燥，按兽医方案复查。",
                "source": "Merck/MSD Veterinary Manual, Otitis Externa in Animals",
            }
        ],
    }

    preview = client.post("/admin/clinical-knowledge/conditions/preview", json=payload)
    assert preview.status_code == 200
    preview_data = preview.json()
    assert preview_data["valid"] is True
    assert preview_data["items"][0]["chunk_count"] == 6
    assert any(chunk["chunk_type"] == "followup_questions" for chunk in preview_data["items"][0]["chunks"])

    imported = client.post("/admin/clinical-knowledge/conditions/import", json={**payload, "publish": False})
    assert imported.status_code == 200
    batch = imported.json()
    assert batch["status"] == "imported"
    assert batch["review_status"] == "pending"
    assert batch["total_conditions"] == 1
    assert batch["total_chunks"] == 6

    published = client.post(
        f"/admin/clinical-knowledge/batches/{batch['batch_id']}/publish",
        json={"reason": "vet reviewed test batch"},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["review_status"] == "approved"

    batches = client.get("/admin/clinical-knowledge/batches")
    conditions = client.get("/admin/clinical-knowledge/conditions?review_status=approved")

    assert batches.status_code == 200
    assert batches.json()["total"] >= 1
    assert conditions.status_code == 200
    assert conditions.json()["items"][0]["condition_name"] == "外耳炎 / 外耳道炎"
