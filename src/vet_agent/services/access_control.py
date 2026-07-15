from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.ingress.errors import ForbiddenError, UnauthorizedError
from src.vet_agent.config import Settings
from src.vet_agent.contracts import TrustedIdentity
from src.vet_agent.db.models import PetProfileModel, PetSessionBindingModel
from src.vet_agent.db.session import make_session_factory
from src.vet_agent.stores.json_store import JsonDocumentStore


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    api_key_id: str | None = None
    user_id: str | None = None
    authenticated: bool = False


class AccessControlService:
    def __init__(self, settings: Settings, store: "AccessControlStore") -> None:
        self.settings = settings
        self.store = store

    def authenticate(self, headers: Mapping[str, str]) -> AuthenticatedPrincipal:
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
        principal = principal or AuthenticatedPrincipal()
        if self.settings.require_auth_user_match and principal.user_id and principal.user_id != identity.user_id:
            raise ForbiddenError(
                "Authenticated user does not match vet_context.user_id",
                details={"authenticated_user_id": principal.user_id, "request_user_id": identity.user_id},
            )
        await self._authorize_pet(identity, pet_info or {})
        await self._enforce_session_policy(identity)

    async def _authorize_pet(self, identity: TrustedIdentity, pet_info: dict[str, Any]) -> None:
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
        normalized = (value or "permissive").strip().lower()
        return normalized if normalized in {"off", "permissive", "strict"} else "permissive"

    def _bearer_token(self, headers: Mapping[str, str]) -> str | None:
        value = self._header_value(headers, "authorization")
        if not value:
            return None
        prefix = "bearer "
        if value.lower().startswith(prefix):
            token = value[len(prefix) :].strip()
            return token or None
        return None

    def _header_value(self, headers: Mapping[str, str], name: str) -> str | None:
        value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _fingerprint(self, token: str) -> str:
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}...{token[-4:]}"


class AccessControlStore:
    async def pet_owner(self, pet_id: str) -> str | None:
        raise NotImplementedError

    async def upsert_pet(self, identity: TrustedIdentity, profile: dict[str, Any], *, source: str) -> None:
        raise NotImplementedError

    async def session_binding(self, session_id: str) -> dict[str, str] | None:
        raise NotImplementedError

    async def bind_session(self, identity: TrustedIdentity) -> None:
        raise NotImplementedError

    async def touch_session(self, identity: TrustedIdentity) -> None:
        raise NotImplementedError


class JsonAccessControlStore(AccessControlStore):
    def __init__(self, store: JsonDocumentStore) -> None:
        self.store = store

    async def pet_owner(self, pet_id: str) -> str | None:
        data = self.store.load()
        owner = data.get("pet_owners", {}).get(pet_id)
        return str(owner) if owner else None

    async def upsert_pet(self, identity: TrustedIdentity, profile: dict[str, Any], *, source: str) -> None:
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
        data = self.store.load()
        binding = data.get("session_bindings", {}).get(session_id)
        return dict(binding) if isinstance(binding, dict) else None

    async def bind_session(self, identity: TrustedIdentity) -> None:
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
        data = self.store.load()
        binding = data.setdefault("session_bindings", {}).setdefault(
            identity.session_id,
            {"user_id": identity.user_id, "pet_id": identity.pet_id},
        )
        binding["last_seen_at"] = datetime.now(UTC).isoformat()
        self.store.save(data)


class PostgresAccessControlStore(AccessControlStore):
    def __init__(self, database_url: str) -> None:
        self.session_factory = make_session_factory(database_url)

    async def pet_owner(self, pet_id: str) -> str | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(PetProfileModel).where(
                    PetProfileModel.pet_id == pet_id,
                    PetProfileModel.is_active.is_(True),
                )
            )
        return row.user_id if row else None

    async def upsert_pet(self, identity: TrustedIdentity, profile: dict[str, Any], *, source: str) -> None:
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
        with self.session_factory() as session:
            row = session.scalar(
                select(PetSessionBindingModel).where(PetSessionBindingModel.session_id == session_id)
            )
        if not row:
            return None
        return {"user_id": row.user_id, "pet_id": row.pet_id, "session_id": row.session_id}

    async def bind_session(self, identity: TrustedIdentity) -> None:
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
