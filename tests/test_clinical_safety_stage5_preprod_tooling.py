"""临床安全阶段 5 预发布工具契约测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts/preprod/deploy-clinical-safety-stage5.sh"
SMOKE_SCRIPT = (
    REPO_ROOT / "scripts/integration/run-clinical-safety-stage5-preprod-smoke.sh"
)


def _load_smoke_module():
    path = REPO_ROOT / "scripts/integration/clinical_safety_stage5_preprod_smoke.py"
    spec = importlib.util.spec_from_file_location(
        "clinical_safety_stage5_preprod_smoke",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage5_deploy_script_keeps_required_order_and_no_runtime_build() -> None:
    """验证阶段 5 部署顺序与预编译镜像边界。

    :return: 无返回值；断言通过表示 seed 先于 migrate，且不会现场构建镜像。
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "create_backup_remote" in text
    assert text.index("sync-production-bundle.sh") < text.index("pull app worker opa")
    assert text.index("run --rm --pull never seed") < text.index(
        "run --rm --pull never migrate"
    )
    assert text.index("stop app worker") < text.index("run --rm --pull never seed")
    assert '[[ "$app_variant" != "core" ]]' in text
    assert "VET_AGENT_IMAGE_VARIANT" in text
    assert "ENABLE_OUTPUT_SAFETY_GUARDRAILS" in text
    assert "rollback-runtime" in text
    assert "ssh_args=(" in text
    assert "    ssh\n" in text
    assert "stop app worker mem0-dashboard" in text
    assert "mem0-dashboard \\\n    opa" in text
    assert "--no-build" in text
    assert " --build " not in text


def test_stage5_smoke_script_uses_tunnel_and_does_not_mutate_preprod() -> None:
    """验证阶段 5 冒烟只读真实预发布 API。

    :return: 无返回值；断言通过表示冒烟不执行部署、迁移或 seed。
    """
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "ssh" in text
    assert "127.0.0.1:${remote_app_port}" in text
    assert "uuidgen" not in text
    for forbidden in (" deploy ", " migrate", " seed", "docker build"):
        assert forbidden not in text


def test_prod_compose_uses_opa_image_as_policy_source() -> None:
    """验证生产 OPA 策略来源是 Release 镜像而不是宿主机目录。

    :return: 无返回值；断言通过表示 OPA tag 与策略版本保持一致。
    """
    compose_text = (REPO_ROOT / "docker/compose.yml").read_text(encoding="utf-8")
    assert "./opa/policies:/opa/policies:ro" not in compose_text
    assert "./opa/application.yml:/opa/config/application.yml:ro" in compose_text


def test_prod_compose_pins_backend_subnet_outside_aliyun_vpc() -> None:
    """验证生产 Compose 不落入 Docker 默认的阿里云 VPC 冲突网段。

    :return: 无返回值；断言通过表示 backend 网段由环境配置显式约束。
    """
    compose_text = (REPO_ROOT / "docker/compose.yml").read_text(encoding="utf-8")
    assert "COMPOSE_BACKEND_SUBNET:-192.168.254.0/24" in compose_text
    assert "COMPOSE_BACKEND_GATEWAY:-192.168.254.1" in compose_text
    assert "172.16.0.0/12" not in compose_text


def test_postgres_ops_scripts_wait_for_compose_dns() -> None:
    """验证 PostgreSQL 一次性任务会等待 Compose DNS 就绪。

    :return: 无返回值；断言通过表明网络重建后的短暂 DNS 窗口不会误判扩展失败。
    """
    for relative_path in (
        "docker/postgres/ops/ensure-extensions.sh",
        "docker/postgres/ops/vector-smoke-check.sh",
    ):
        script_text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "wait_for_postgres_host" in script_text
        assert "getent hosts" in script_text


def test_stage5_seed_contains_approved_followup_vector_asset() -> None:
    """验证阶段 5 模糊分诊冒烟具备追问 RAG 资产前提。

    :return: 无返回值；断言通过表示模糊分诊不会因缺少 followup 向量而退化为 503。
    """
    document = json.loads(
        (REPO_ROOT / "assets/seeds/knowledge_chunks.json").read_text(encoding="utf-8")
    )
    followup_chunks = [
        item
        for item in document
        if item.get("metadata", {}).get("chunk_type") == "followup_questions"
        and item.get("metadata", {}).get("review_status") == "approved"
        and item.get("metadata", {}).get("enabled") is True
    ]
    assert followup_chunks


def test_stage5_preprod_smoke_has_bounded_transient_retries() -> None:
    """验证阶段 5 冒烟对真实模型波动使用有界重试。

    :return: 无返回值；断言通过表示同一场景最多尝试 3 次且失败会被记录。
    """
    module = _load_smoke_module()
    smoke_text = (
        REPO_ROOT / "scripts/integration/clinical_safety_stage5_preprod_smoke.py"
    ).read_text(encoding="utf-8")
    assert module.MAX_SCENARIO_ATTEMPTS == 3
    assert 'scenario == "precondition_unknown_followup"' in smoke_text
    assert "failed_attempts" in smoke_text
    assert "狗狗突然拉大量血水" in smoke_text


def test_stage5_payload_uses_isolated_scope_and_idempotency() -> None:
    """验证黑盒冒烟请求具有回合隔离身份。

    :return: 无返回值；断言通过表示测试数据不会复用既有用户或会话。
    """
    module = _load_smoke_module()
    payload = module._payload(
        text="猫现在牙龈发紫，呼吸很快。",
        run_id="stage5_unit",
        scenario="single_primary_signal",
        profile={"species": "猫"},
    )
    scope = payload["scope_assertion"]
    assert payload["turn_options"]["idempotency_key"] == (
        "idem_stage5_unit_single_primary_signal_session"
    )
    assert scope["user_id"] == "stage5_unit_single_primary_signal_user"
    assert scope["pet_id"] == "stage5_unit_single_primary_signal_pet"
    assert scope["session_id"] == "stage5_unit_single_primary_signal_session"
    assert scope["authorization"]["ownership_verified"] is True


def test_stage5_tool_reads_cat_only_codes_from_release_manifest() -> None:
    """验证范围不匹配断言来自资产治理 manifest。

    :return: 无返回值；断言通过表示测试不硬编码医学候选 code。
    """
    module = _load_smoke_module()
    codes = module._cat_only_emergency_codes(
        REPO_ROOT / "assets/clinical_safety/vet_safety_assets.v1.json"
    )
    assert codes
    assert all(code.startswith("EMERGENCY_MODE_") for code in codes)
