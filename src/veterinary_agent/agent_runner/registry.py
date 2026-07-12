##################################################################################################
# 文件: src/veterinary_agent/agent_runner/registry.py
# 作用: 实现 AgentRunner 首版内存 AgentSpecRegistry，用于启动期或测试期注册版本化 Agent 规格。
# 边界: 不读取远程配置、不访问数据库、不实现 API 治理平台；后续真实规格源应通过 AgentSpecRegistry 端口替换。
##################################################################################################

from collections.abc import Iterable

from veterinary_agent.agent_runner.dto import (
    AgentSpecDto,
    AgentValidationErrorDto,
)
from veterinary_agent.agent_runner.enums import (
    AgentResponseFormat,
    AgentRunnerErrorCode,
    AgentRunnerOperation,
)
from veterinary_agent.agent_runner.errors import AgentRunnerError


class InMemoryAgentSpecRegistry:
    """内存版 Agent 规格注册表。"""

    def __init__(self, specs: Iterable[AgentSpecDto] | None = None) -> None:
        """初始化内存版 Agent 规格注册表。

        :param specs: 可选初始 Agent 规格集合。
        :return: None。
        :raises AgentRunnerError: 当初始规格存在重复键时抛出。
        """

        self._specs: dict[tuple[str, str], AgentSpecDto] = {}
        self._closed = False
        for spec in specs or ():
            self.register_spec(spec)

    def is_ready(self) -> bool:
        """判断注册表是否可用于解析规格。

        :return: 若注册表未关闭，则返回 True。
        """

        return not self._closed

    def register_spec(self, spec: AgentSpecDto) -> None:
        """注册一个 Agent 规格。

        :param spec: 需要注册的 Agent 规格。
        :return: None。
        :raises AgentRunnerError: 当相同 agent_id 与 agent_version 已存在时抛出。
        """

        key = (spec.agent_id, spec.agent_version)
        if key in self._specs:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.AGENT_SPEC_VERSION_UNAVAILABLE,
                operation=AgentRunnerOperation.VALIDATE_AGENT_SPEC,
                message="Agent 规格版本重复注册",
                agent_id=spec.agent_id,
                agent_version=spec.agent_version,
                model_profile_id=spec.model_profile,
                conflict_with={"reason": "duplicate_spec"},
            )
        self._specs[key] = spec

    def resolve_spec(
        self,
        *,
        agent_id: str,
        agent_version: str,
    ) -> AgentSpecDto:
        """解析指定版本的 Agent 规格。

        :param agent_id: Agent ID。
        :param agent_version: Agent 版本。
        :return: 已解析的 Agent 规格。
        :raises AgentRunnerError: 当注册表关闭、规格不存在或版本不可用时抛出。
        """

        if self._closed:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.AGENT_RUNNER_NOT_READY,
                operation=AgentRunnerOperation.RESOLVE_AGENT_SPEC,
                message="AgentSpecRegistry 已关闭",
                agent_id=agent_id,
                agent_version=agent_version,
            )
        key = (agent_id, agent_version)
        spec = self._specs.get(key)
        if spec is not None:
            return spec
        known_versions = sorted(
            version
            for current_agent_id, version in self._specs
            if current_agent_id == agent_id
        )
        if known_versions:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.AGENT_SPEC_VERSION_UNAVAILABLE,
                operation=AgentRunnerOperation.RESOLVE_AGENT_SPEC,
                message="Agent 规格版本不可用",
                agent_id=agent_id,
                agent_version=agent_version,
                conflict_with={"available_versions": known_versions},
            )
        raise AgentRunnerError(
            code=AgentRunnerErrorCode.AGENT_SPEC_NOT_FOUND,
            operation=AgentRunnerOperation.RESOLVE_AGENT_SPEC,
            message="Agent 规格不存在",
            agent_id=agent_id,
            agent_version=agent_version,
        )

    def validate_spec(self, spec: AgentSpecDto) -> list[AgentValidationErrorDto]:
        """校验 Agent 规格。

        :param spec: 待校验的 Agent 规格。
        :return: 结构化校验错误列表；空列表表示通过。
        """

        errors: list[AgentValidationErrorDto] = []
        if (
            spec.response_format is AgentResponseFormat.JSON_SCHEMA
            and spec.output_schema is None
        ):
            errors.append(
                AgentValidationErrorDto(
                    path="response_format",
                    message="json_schema 响应格式必须提供 output_schema",
                    error_type="schema_missing",
                )
            )
        if spec.output_schema is not None and not isinstance(spec.output_schema, dict):
            errors.append(
                AgentValidationErrorDto(
                    path="output_schema",
                    message="output_schema 必须是 JSON object",
                    error_type="schema_type_invalid",
                )
            )
        if not spec.prompt_template.strip():
            errors.append(
                AgentValidationErrorDto(
                    path="prompt_template",
                    message="prompt_template 不得为空",
                    error_type="prompt_template_empty",
                )
            )
        return errors

    def list_specs(self) -> list[AgentSpecDto]:
        """列出当前注册表中的 Agent 规格。

        :return: 按 agent_id 与 agent_version 排序后的 Agent 规格列表。
        """

        return [
            self._specs[key]
            for key in sorted(self._specs, key=lambda item: (item[0], item[1]))
        ]

    def close(self) -> None:
        """关闭内存注册表。

        :return: None。
        """

        self._closed = True


__all__: tuple[str, ...] = ("InMemoryAgentSpecRegistry",)
