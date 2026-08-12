"""
文件：src/vet_agent/services/scope.py
作用：组装身份、宠物资料与会话范围上下文，并执行范围策略裁决。
范围：位于 Agent 主链路之前，负责将可信身份、认证主体、宠物画像和会话绑定收束为结构化事实。
说明：本文件不承担医学推理、问诊状态迁移或自然语言理解；后续接入 OPA 时应替换策略裁决器实现而不改变调用方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from ingress import ForbiddenError, InvalidRequestError
from vet_agent import AuthorizedScopeContext, ScopeAssertion, TrustedIdentity
from vet_agent.repositories import ScopeRepository, SessionBinding, VerifiedPetProfile


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """表示上游认证主体在范围数据链中的只读投影。

    :param api_key_id: API Key 脱敏标识。
    :param user_id: 认证主体对应的用户标识。
    :param authenticated: 当前请求是否已经通过认证。
    :return: 无返回值。
    """

    api_key_id: str | None = None
    user_id: str | None = None
    authenticated: bool = False


@dataclass(frozen=True)
class ScopeContext:
    """表示本轮请求进入 Agent 主链路前的访问范围事实。

    :param identity: 本轮可信身份范围。
    :param principal: 上游认证主体。
    :param scope_assertion: BFF 对本轮 Agent 调用范围的服务端声明。
    :param pet_profile: 上游已验证宠物画像在 Agent 侧的本地投影。
    :param session_binding: 已存在的会话绑定。
    :param reported_pet_info: 请求侧未验证宠物资料审计副本。
    :return: 无返回值。
    """

    identity: TrustedIdentity
    scope_assertion: ScopeAssertion | None
    principal: AuthenticatedPrincipal
    pet_profile: VerifiedPetProfile | None
    session_binding: SessionBinding | None
    reported_pet_info: dict[str, Any] = field(default_factory=dict)

    @property
    def verified_profile(self) -> dict[str, Any]:
        """返回可进入临床链路的已验证宠物画像。

        :return: 存在启用画像时返回画像字典，否则返回空字典。
        """
        if self.pet_profile is None or not self.pet_profile.is_active:
            return {}
        return dict(self.pet_profile.profile)

    def to_authorized_snapshot(self) -> AuthorizedScopeContext:
        """转换为主链路可复用的入口授权快照。

        :return: 返回已授权范围上下文快照。
        """
        return AuthorizedScopeContext(
            identity=self.identity,
            verified_profile=self.verified_profile,
            reported_pet_info=dict(self.reported_pet_info),
        )


class ScopeDecisionAction(StrEnum):
    """表示范围策略裁决后的有限动作集合。

    :return: 无返回值。
    """

    ALLOW_TURN = "allow_turn"
    BIND_SESSION_THEN_ALLOW = "bind_session_then_allow"
    DENY_PET_NOT_FOUND = "deny_pet_not_found"
    DENY_INACTIVE_PET = "deny_inactive_pet"
    DENY_PET_OWNER_MISMATCH = "deny_pet_owner_mismatch"
    DENY_PRINCIPAL_MISMATCH = "deny_principal_mismatch"
    DENY_SESSION_SCOPE_MISMATCH = "deny_session_scope_mismatch"
    DENY_SCOPE_ASSERTION_INVALID = "deny_scope_assertion_invalid"


@dataclass(frozen=True)
class ScopeDecision:
    """表示身份、宠物资料与会话范围策略裁决结果。

    :param allow: 是否允许请求继续进入 Agent 主链路。
    :param action: 策略裁决动作。
    :param reasons: 策略裁决原因。
    :param obligations: 放行前后必须执行的确定性义务。
    :return: 无返回值。
    """

    allow: bool
    action: ScopeDecisionAction
    reasons: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        """转换为可审计 metadata。

        :return: 返回策略裁决 metadata 字典。
        """
        return {
            "allow": self.allow,
            "action": self.action.value,
            "reasons": list(self.reasons),
            "obligations": list(self.obligations),
        }


class ScopePolicyEvaluator(Protocol):
    """定义范围策略裁决协议。

    :return: 无返回值。
    """

    def evaluate(self, context: ScopeContext) -> ScopeDecision:
        """根据结构化范围事实裁决本轮动作。

        :param context: 本轮请求的范围上下文。
        :return: 返回范围策略裁决结果。
        """
        ...


class DeterministicScopePolicyEvaluator(ScopePolicyEvaluator):
    """使用确定性事实裁决范围动作的默认策略实现。

    :return: 无返回值。
    """

    def evaluate(self, context: ScopeContext) -> ScopeDecision:
        """根据结构化范围事实裁决本轮动作。

        :param context: 本轮请求的范围上下文。
        :return: 返回范围策略裁决结果。
        """
        principal = context.principal
        identity = context.identity
        if principal.user_id and principal.user_id != identity.user_id:
            return ScopeDecision(
                allow=False,
                action=ScopeDecisionAction.DENY_PRINCIPAL_MISMATCH,
                reasons=("认证主体与 scope_assertion.user_id 不一致。",),
            )
        if context.pet_profile is None:
            return ScopeDecision(
                allow=False,
                action=ScopeDecisionAction.DENY_PET_NOT_FOUND,
                reasons=("当前 user_id 与 pet_id 未找到已验证宠物画像。",),
            )
        if context.pet_profile.user_id != identity.user_id:
            return ScopeDecision(
                allow=False,
                action=ScopeDecisionAction.DENY_PET_OWNER_MISMATCH,
                reasons=("当前 pet_id 不属于 scope_assertion.user_id。",),
            )
        if not context.pet_profile.is_active:
            return ScopeDecision(
                allow=False,
                action=ScopeDecisionAction.DENY_INACTIVE_PET,
                reasons=("当前宠物资料已停用。",),
            )
        if context.session_binding is None:
            return ScopeDecision(
                allow=True,
                action=ScopeDecisionAction.BIND_SESSION_THEN_ALLOW,
                obligations=("bind_session",),
            )
        if (
            context.session_binding.user_id != identity.user_id
            or context.session_binding.pet_id != identity.pet_id
        ):
            return ScopeDecision(
                allow=False,
                action=ScopeDecisionAction.DENY_SESSION_SCOPE_MISMATCH,
                reasons=("当前 session_id 已绑定到其他用户或宠物。",),
            )
        return ScopeDecision(
            allow=True,
            action=ScopeDecisionAction.ALLOW_TURN,
            obligations=("touch_session",),
        )


class ScopeContextService:
    """组装范围上下文并执行策略裁决。

    :return: 无返回值。
    """

    def __init__(
        self,
        repository: ScopeRepository,
        policy_evaluator: ScopePolicyEvaluator | None = None,
    ) -> None:
        """初始化范围上下文服务。

        :param repository: 身份、宠物资料与会话范围仓储。
        :param policy_evaluator: 范围策略裁决器；为空时使用确定性默认实现。
        :return: 无返回值。
        """
        self.repository = repository
        self.policy_evaluator = policy_evaluator or DeterministicScopePolicyEvaluator()

    def load(
        self,
        scope_assertion: ScopeAssertion,
        *,
        pet_info: dict[str, Any] | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> ScopeContext:
        """加载本轮请求的范围事实。

        :param scope_assertion: BFF 对本轮 Agent 调用范围的服务端声明。
        :param pet_info: 请求侧未验证宠物资料。
        :param principal: 上游认证主体。
        :return: 返回范围上下文。
        """
        identity = scope_assertion.trusted_identity()
        return ScopeContext(
            identity=identity,
            scope_assertion=scope_assertion,
            principal=principal or AuthenticatedPrincipal(),
            pet_profile=self.repository.get_pet_profile(identity),
            session_binding=self.repository.get_session_binding(identity.session_id),
            reported_pet_info=self._reported_pet_info(pet_info or {}),
        )

    async def authorize(
        self,
        scope_assertion: ScopeAssertion,
        *,
        pet_info: dict[str, Any] | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> ScopeContext:
        """执行范围上下文裁决，并在放行时完成必要会话绑定义务。

        :param scope_assertion: BFF 对本轮 Agent 调用范围的服务端声明。
        :param pet_info: 请求侧未验证宠物资料。
        :param principal: 上游认证主体。
        :return: 返回已通过裁决的范围上下文。
        """
        self._validate_scope_assertion(scope_assertion)
        identity = scope_assertion.trusted_identity()
        self.repository.upsert_pet_profile(
            identity,
            profile=scope_assertion.profile_projection(),
            source=self._assertion_source(scope_assertion),
            is_active=scope_assertion.authorization.pet_active and not scope_assertion.authorization.pet_deleted,
        )
        context = self.load(scope_assertion, pet_info=pet_info, principal=principal)
        decision = self.policy_evaluator.evaluate(context)
        if not decision.allow:
            self._raise_for_decision(decision, identity)
        context = self._apply_obligations(context, decision)
        return context

    async def authorize_identity(
        self,
        identity: TrustedIdentity,
        *,
        pet_info: dict[str, Any] | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> ScopeContext:
        """基于既有本地画像投影执行管理接口范围授权。

        :param identity: 本轮可信身份范围。
        :param pet_info: 请求侧未验证宠物资料。
        :param principal: 上游认证主体。
        :return: 返回已通过裁决的范围上下文。
        """
        context = ScopeContext(
            identity=identity,
            scope_assertion=None,
            principal=principal or AuthenticatedPrincipal(),
            pet_profile=self.repository.get_pet_profile(identity),
            session_binding=self.repository.get_session_binding(identity.session_id),
            reported_pet_info=self._reported_pet_info(pet_info or {}),
        )
        decision = self.policy_evaluator.evaluate(context)
        if not decision.allow:
            self._raise_for_decision(decision, identity)
        return self._apply_obligations(context, decision)

    def is_ready(self) -> bool:
        """检查范围上下文服务是否可访问底层仓储。

        :return: 仓储就绪时返回 True。
        """
        return self.repository.is_ready()

    def _apply_obligations(self, context: ScopeContext, decision: ScopeDecision) -> ScopeContext:
        """执行范围策略裁决返回的确定性义务。

        :param context: 本轮请求的范围上下文。
        :param decision: 范围策略裁决结果。
        :return: 返回执行义务后的范围上下文。
        """
        if "bind_session" in decision.obligations:
            binding = self.repository.bind_session(context.identity)
            if binding is None:
                raise InvalidRequestError(
                    "failed to bind session scope",
                    details={"session_id": context.identity.session_id},
                )
            refreshed = ScopeContext(
                identity=context.identity,
                scope_assertion=context.scope_assertion,
                principal=context.principal,
                pet_profile=context.pet_profile,
                session_binding=binding,
                reported_pet_info=context.reported_pet_info,
            )
            post_decision = self.policy_evaluator.evaluate(refreshed)
            if not post_decision.allow:
                self._raise_for_decision(post_decision, context.identity)
            return refreshed
        if "touch_session" in decision.obligations:
            self.repository.touch_session(context.identity)
        return context

    def _raise_for_decision(self, decision: ScopeDecision, identity: TrustedIdentity) -> None:
        """将范围策略拒绝结果转换为入口层错误。

        :param decision: 范围策略裁决结果。
        :param identity: 本轮可信身份范围。
        :return: 无返回值；拒绝时抛出入口层异常。
        """
        details = {
            "scope_decision": decision.to_metadata(),
            "user_id": identity.user_id,
            "pet_id": identity.pet_id,
            "session_id": identity.session_id,
        }
        if decision.action == ScopeDecisionAction.DENY_PRINCIPAL_MISMATCH:
            raise ForbiddenError("authenticated user does not match scope_assertion.user_id", details=details)
        if decision.action == ScopeDecisionAction.DENY_PET_NOT_FOUND:
            raise ForbiddenError("pet_id is not registered for this user", details=details)
        if decision.action == ScopeDecisionAction.DENY_PET_OWNER_MISMATCH:
            raise ForbiddenError("pet_id does not belong to scope_assertion.user_id", details=details)
        if decision.action == ScopeDecisionAction.DENY_INACTIVE_PET:
            raise ForbiddenError("pet profile is inactive", details=details)
        if decision.action == ScopeDecisionAction.DENY_SESSION_SCOPE_MISMATCH:
            raise ForbiddenError("session_id is already bound to another user/pet", details=details)
        if decision.action == ScopeDecisionAction.DENY_SCOPE_ASSERTION_INVALID:
            raise ForbiddenError("scope assertion rejected this request", details=details)
        raise ForbiddenError("scope policy rejected this request", details=details)

    def _validate_scope_assertion(self, scope_assertion: ScopeAssertion) -> None:
        """校验 BFF 范围声明是否允许进入 Agent 身份范围链路。

        :param scope_assertion: BFF 对本轮 Agent 调用范围的服务端声明。
        :return: 无返回值；非法声明会抛出拒绝异常。
        """
        reasons: list[str] = []
        now = datetime.now(UTC)
        if scope_assertion.expires_at is not None and scope_assertion.expires_at < now:
            reasons.append("范围声明已过期。")
        if not scope_assertion.authorization.ownership_verified:
            reasons.append("BFF 未声明已完成宠物归属校验。")
        if scope_assertion.authorization.pet_deleted:
            reasons.append("BFF 声明当前宠物档案已删除。")
        if not scope_assertion.authorization.pet_active:
            reasons.append("BFF 声明当前宠物档案不可用。")
        if scope_assertion.session_policy.binding_mode != "single_user_pet_per_session":
            reasons.append("会话绑定策略不受支持。")
        if reasons:
            decision = ScopeDecision(
                allow=False,
                action=(
                    ScopeDecisionAction.DENY_INACTIVE_PET
                    if scope_assertion.authorization.pet_deleted or not scope_assertion.authorization.pet_active
                    else ScopeDecisionAction.DENY_SCOPE_ASSERTION_INVALID
                ),
                reasons=tuple(reasons),
            )
            self._raise_for_decision(decision, scope_assertion.trusted_identity())

    def _assertion_source(self, scope_assertion: ScopeAssertion) -> str:
        """生成 Agent 本地画像投影的来源标识。

        :param scope_assertion: BFF 对本轮 Agent 调用范围的服务端声明。
        :return: 返回可审计的来源字符串。
        """
        source = scope_assertion.source
        return f"{scope_assertion.issuer}:{source.system}:{source.table}:{source.record_id}"

    def _reported_pet_info(self, pet_info: dict[str, Any]) -> dict[str, Any]:
        """整理请求侧未验证宠物资料的审计副本。

        :param pet_info: 请求侧宠物资料。
        :return: 返回经过基础筛选的未验证宠物资料。
        """
        reported: dict[str, Any] = {}
        for key, value in pet_info.items():
            if value not in (None, ""):
                reported[str(key)] = value
        return reported
