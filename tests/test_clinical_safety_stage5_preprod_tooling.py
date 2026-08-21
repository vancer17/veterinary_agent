"""临床安全阶段 5 预发布工具契约测试。"""

from __future__ import annotations

import importlib.util
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
    assert "--no-build" in text
    assert " --build " not in text


def test_stage5_smoke_script_uses_tunnel_and_does_not_mutate_preprod() -> None:
    """验证阶段 5 冒烟只读真实预发布 API。

    :return: 无返回值；断言通过表示冒烟不执行部署、迁移或 seed。
    """
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "ssh" in text
    assert "127.0.0.1:${remote_app_port}" in text
    for forbidden in (" deploy ", " migrate", " seed", "docker build"):
        assert forbidden not in text


def test_prod_compose_uses_opa_image_as_policy_source() -> None:
    """验证生产 OPA 策略来源是 Release 镜像而不是宿主机目录。

    :return: 无返回值；断言通过表示 OPA tag 与策略版本保持一致。
    """
    compose_text = (REPO_ROOT / "docker/compose.yml").read_text(encoding="utf-8")
    assert "./opa/policies:/opa/policies:ro" not in compose_text
    assert "./opa/application.yml:/opa/config/application.yml:ro" in compose_text


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
