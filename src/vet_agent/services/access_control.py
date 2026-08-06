"""
文件：src/vet_agent/services/access_control.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ingress import ForbiddenError, UnauthorizedError
from vet_agent import Settings
from vet_agent import TrustedIdentity
from vet_agent.db import PetProfileModel, PetSessionBindingModel, make_session_factory
from vet_agent.stores import JsonDocumentStore


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    api_key_id: str | None = None
    user_id: str | None = None
    authenticated: bool = False


class AccessControlService:
    def __init__(self, settings: Settings, store: "AccessControlStore") -> None:
        """初始化当前对象。

        :param settings: 应用配置对象。
        :param store: 参数 store。
        :return: 无返回值。
        """
        self.settings = settings
        self.store = store

    def authenticate(self, headers: Mapping[str, str]) -> AuthenticatedPrincipal:
        """解析并校验调用方认证信息。

        :param headers: HTTP 请求头。
        :return: 返回函数执行结果。
        """
        configured_keys = self.settings.api_keys
        auth_required = self.settings.require_api_auth or bool(configured_keys)
        if not auth_required:
            return AuthenticatedPrincipal(authenticated=False)

        token = self._bearer_token(headers) or self._header_value(headers, "x-api-key")
        if not token:
            raise UnauthorizedError("Missing API credential")
        if configured_keys and not any(hmac.compare_digest(token, item) for item in configured_keys):
            raise UnauthorizedError("Invalid API credential")
        return AuthenticatedPrincipal(
            api_key_id=self._fingerprint(token),
            user_id=self._header_value(headers, "x-user-id"),
            authenticated=True,
        )

    async def authorize(
        self,
        identity: TrustedIdentity,
        *,
        pet_info: dict[str, Any] | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> None:
        """执行用户与宠物访问授权。

        :param identity: 可信身份信息。
        :param pet_info: 宠物基础信息。
        :param principal: 认证主体。
        :return: 返回函数执行结果。
        """
        principal = principal or AuthenticatedPrincipal()
        if self.settings.require_auth_user_match and principal.user_id and principal.user_id != identity.user_id:
            raise ForbiddenError(
                "Authenticated user does not match vet_context.user_id",
                details={"authenticated_user_id": principal.user_id, "request_user_id": identity.user_id},
            )
        await self._authorize_pet(identity, pet_info or {})
        await self._enforce_session_policy(identity)

    async def _authorize_pet(self, identity: TrustedIdentity, pet_info: dict[str, Any]) -> None:
        """执行内部授权逻辑。

        :param identity: 可信身份信息。
        :param pet_info: 宠物基础信息。
        :return: 返回函数执行结果。
        """
        mode = self._mode(self.settings.pet_authorization_mode)
        if mode == "off":
            return

        owner = await self.store.pet_owner(identity.pet_id)
        if owner and owner != identity.user_id:
            raise ForbiddenError(
                "pet_id does not belong to vet_context.user_id",
                details={"pet_id": identity.pet_id},
            )
        if owner == identity.user_id:
            if pet_info:
                await self.store.upsert_pet(identity, pet_info, source="api_refresh")
            return
        if mode == "strict":
            raise ForbiddenError(
                "pet_id is not registered for this user",
                details={"pet_id": identity.pet_id, "user_id": identity.user_id},
            )
        await self.store.upsert_pet(identity, pet_info, source="first_seen")
        owner_after = await self.store.pet_owner(identity.pet_id)
        if owner_after != identity.user_id:
            raise ForbiddenError(
                "pet_id does not belong to vet_context.user_id",
                details={"pet_id": identity.pet_id},
            )

    async def _enforce_session_policy(self, identity: TrustedIdentity) -> None:
        """执行 _enforce_session_policy 内部辅助逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        mode = self._mode(self.settings.session_policy_mode)
        if mode == "off":
            return
        existing = await self.store.session_binding(identity.session_id)
        if existing is None:
            await self.store.bind_session(identity)
            existing = await self.store.session_binding(identity.session_id)
            if existing and (existing["user_id"] != identity.user_id or existing["pet_id"] != identity.pet_id):
                raise ForbiddenError(
                    "session_id is already bound to another user/pet",
                    details={
                        "session_id": identity.session_id,
                        "bound_user_id": existing["user_id"],
                        "bound_pet_id": existing["pet_id"],
                    },
                )
            return
        if existing["user_id"] != identity.user_id or existing["pet_id"] != identity.pet_id:
            raise ForbiddenError(
                "session_id is already bound to another user/pet",
                details={
                    "session_id": identity.session_id,
                    "bound_user_id": existing["user_id"],
                    "bound_pet_id": existing["pet_id"],
                },
            )
        await self.store.touch_session(identity)

    def _mode(self, value: str) -> str:
        """执行 _mode 内部辅助逻辑。

        :param value: 待处理值。
        :return: 返回函数执行结果。
        """
        normalized = (value or "permissive").strip().lower()
        return normalized if normalized in {"off", "permissive", "strict"} else "permissive"

    def _bearer_token(self, headers: Mapping[str, str]) -> str | None:
        """执行 _bearer_token 内部辅助逻辑。

        :param headers: HTTP 请求头。
        :return: 返回函数执行结果。
        """
        value = self._header_value(headers, "authorization")
        if not value:
            return None
        prefix = "bearer "
        if value.lower().startswith(prefix):
            token = value[len(prefix) :].strip()
            return token or None
        return None

    def _header_value(self, headers: Mapping[str, str], name: str) -> str | None:
        """执行 _header_value 内部辅助逻辑。

        :param headers: HTTP 请求头。
        :param name: 名称。
        :return: 返回函数执行结果。
        """
        value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _fingerprint(self, token: str) -> str:
        """执行 _fingerprint 内部辅助逻辑。

        :param token: 参数 token。
        :return: 返回函数执行结果。
        """
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}...{token[-4:]}"


class AccessControlStore:
    async def pet_owner(self, pet_id: str) -> str | None:
        """执行 pet_owner 业务逻辑。

        :param pet_id: 参数 pet_id。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def upsert_pet(self, identity: TrustedIdentity, profile: dict[str, Any], *, source: str) -> None:
        """执行 upsert_pet 业务逻辑。

        :param identity: 可信身份信息。
        :param profile: 参数 profile。
        :param source: 参数 source。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def session_binding(self, session_id: str) -> dict[str, str] | None:
        """执行 session_binding 业务逻辑。

        :param session_id: 参数 session_id。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def bind_session(self, identity: TrustedIdentity) -> None:
        """执行 bind_session 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def touch_session(self, identity: TrustedIdentity) -> None:
        """执行 touch_session 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError


class JsonAccessControlStore(AccessControlStore):
    def __init__(self, store: JsonDocumentStore) -> None:
        """初始化当前对象。

        :param store: 参数 store。
        :return: 无返回值。
        """
        self.store = store

    async def pet_owner(self, pet_id: str) -> str | None:
        """执行 pet_owner 业务逻辑。

        :param pet_id: 参数 pet_id。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        owner = data.get("pet_owners", {}).get(pet_id)
        return str(owner) if owner else None

    async def upsert_pet(self, identity: TrustedIdentity, profile: dict[str, Any], *, source: str) -> None:
        """执行 upsert_pet 业务逻辑。

        :param identity: 可信身份信息。
        :param profile: 参数 profile。
        :param source: 参数 source。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        now = datetime.now(UTC).isoformat()
        owners = data.setdefault("pet_owners", {})
        existing_owner = owners.get(identity.pet_id)
        if existing_owner and existing_owner != identity.user_id:
            return
        owners[identity.pet_id] = identity.user_id
        profiles = data.setdefault("pet_profiles", {})
        current = dict(profiles.get(identity.pet_id) or {})
        current.update(
            {
                "user_id": identity.user_id,
                "pet_id": identity.pet_id,
                "profile": profile or current.get("profile") or {},
                "source": source,
                "is_active": True,
                "updated_at": now,
            }
        )
        current.setdefault("created_at", now)
        profiles[identity.pet_id] = current
        self.store.save(data)

    async def session_binding(self, session_id: str) -> dict[str, str] | None:
        """执行 session_binding 业务逻辑。

        :param session_id: 参数 session_id。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        binding = data.get("session_bindings", {}).get(session_id)
        return dict(binding) if isinstance(binding, dict) else None

    async def bind_session(self, identity: TrustedIdentity) -> None:
        """执行 bind_session 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        now = datetime.now(UTC).isoformat()
        data.setdefault("session_bindings", {})[identity.session_id] = {
            "user_id": identity.user_id,
            "pet_id": identity.pet_id,
            "created_at": now,
            "updated_at": now,
            "last_seen_at": now,
        }
        self.store.save(data)

    async def touch_session(self, identity: TrustedIdentity) -> None:
        """执行 touch_session 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        binding = data.setdefault("session_bindings", {}).setdefault(
            identity.session_id,
            {"user_id": identity.user_id, "pet_id": identity.pet_id},
        )
        binding["last_seen_at"] = datetime.now(UTC).isoformat()
        self.store.save(data)


class PostgresAccessControlStore(AccessControlStore):
    def __init__(self, database_url: str) -> None:
        """初始化当前对象。

        :param database_url: 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    async def pet_owner(self, pet_id: str) -> str | None:
        """执行 pet_owner 业务逻辑。

        :param pet_id: 参数 pet_id。
        :return: 返回函数执行结果。
        """
        with self.session_factory() as session:
            row = session.scalar(
                select(PetProfileModel).where(
                    PetProfileModel.pet_id == pet_id,
                    PetProfileModel.is_active.is_(True),
                )
            )
        return row.user_id if row else None

    async def upsert_pet(self, identity: TrustedIdentity, profile: dict[str, Any], *, source: str) -> None:
        """执行 upsert_pet 业务逻辑。

        :param identity: 可信身份信息。
        :param profile: 参数 profile。
        :param source: 参数 source。
        :return: 返回函数执行结果。
        """
        now = datetime.now(UTC)
        statement = pg_insert(PetProfileModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            profile=profile,
            source=source,
            is_active=True,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_pet_profiles_pet_id",
            set_={
                "profile": profile,
                "source": source,
                "is_active": True,
                "updated_at": now,
            },
            where=(PetProfileModel.user_id == identity.user_id),
        )
        with self.session_factory.begin() as session:
            session.execute(statement)

    async def session_binding(self, session_id: str) -> dict[str, str] | None:
        """执行 session_binding 业务逻辑。

        :param session_id: 参数 session_id。
        :return: 返回函数执行结果。
        """
        with self.session_factory() as session:
            row = session.scalar(
                select(PetSessionBindingModel).where(PetSessionBindingModel.session_id == session_id)
            )
        if not row:
            return None
        return {"user_id": row.user_id, "pet_id": row.pet_id, "session_id": row.session_id}

    async def bind_session(self, identity: TrustedIdentity) -> None:
        """执行 bind_session 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        now = datetime.now(UTC)
        statement = pg_insert(PetSessionBindingModel).values(
            session_id=identity.session_id,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            updated_at=now,
            last_seen_at=now,
        )
        statement = statement.on_conflict_do_nothing(constraint="uq_pet_session_bindings_session_id")
        with self.session_factory.begin() as session:
            session.execute(statement)

    async def touch_session(self, identity: TrustedIdentity) -> None:
        """执行 touch_session 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        now = datetime.now(UTC)
        statement = pg_insert(PetSessionBindingModel).values(
            session_id=identity.session_id,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            updated_at=now,
            last_seen_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_pet_session_bindings_session_id",
            set_={"updated_at": now, "last_seen_at": now},
        )
        with self.session_factory.begin() as session:
            session.execute(statement)
