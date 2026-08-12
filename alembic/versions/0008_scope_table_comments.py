"""
文件：alembic/versions/0008_scope_table_comments.py
作用：为身份、宠物资料与会话范围数据表补充 PostgreSQL 表和字段说明。
范围：仅更新 pet_profiles 与 pet_session_bindings 的数据库注释，不改变数据结构和业务数据。
说明：字段说明用于运维、审计和后续数据治理理解范围数据链边界。
"""

from __future__ import annotations

from alembic import op


revision = "0008_scope_table_comments"
down_revision = "0007_clinical_safety_recognition_phrases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移，补充范围数据表说明。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE pet_profiles IS '上游已验证宠物画像在 Agent 侧的本地投影表，用于身份、宠物资料与会话范围数据链。'")
    _comment("COMMENT ON COLUMN pet_profiles.id IS '宠物画像内部主键。'")
    _comment("COMMENT ON COLUMN pet_profiles.user_id IS '宠物所属用户标识，来自可信上游或宠物资料领域。'")
    _comment("COMMENT ON COLUMN pet_profiles.pet_id IS '宠物标识，作为业务侧宠物范围主键。'")
    _comment("COMMENT ON COLUMN pet_profiles.profile IS '上游已验证宠物画像 JSON，由可信 BFF 范围声明或受控同步流程写入。'")
    _comment("COMMENT ON COLUMN pet_profiles.source IS '画像投影来源。'")
    _comment("COMMENT ON COLUMN pet_profiles.is_active IS '宠物画像是否处于启用状态；停用后范围策略应拒绝进入 Agent 主链路。'")
    _comment("COMMENT ON COLUMN pet_profiles.created_at IS '宠物画像创建时间。'")
    _comment("COMMENT ON COLUMN pet_profiles.updated_at IS '宠物画像最近更新时间。'")
    _comment("COMMENT ON TABLE pet_session_bindings IS '会话与用户、宠物范围绑定表，用于保证一 session 一宠且避免跨宠串话。'")
    _comment("COMMENT ON COLUMN pet_session_bindings.id IS '会话绑定内部主键。'")
    _comment("COMMENT ON COLUMN pet_session_bindings.session_id IS '会话标识，一个 session 只能绑定到同一用户与宠物。'")
    _comment("COMMENT ON COLUMN pet_session_bindings.user_id IS '会话绑定的用户标识。'")
    _comment("COMMENT ON COLUMN pet_session_bindings.pet_id IS '会话绑定的宠物标识。'")
    _comment("COMMENT ON COLUMN pet_session_bindings.created_at IS '会话绑定创建时间。'")
    _comment("COMMENT ON COLUMN pet_session_bindings.updated_at IS '会话绑定更新时间。'")
    _comment("COMMENT ON COLUMN pet_session_bindings.last_seen_at IS '会话绑定最近一次通过范围授权的时间。'")


def downgrade() -> None:
    """执行 Alembic 回滚迁移，移除范围数据表说明。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE pet_profiles IS NULL")
    for column in ("id", "user_id", "pet_id", "profile", "source", "is_active", "created_at", "updated_at"):
        _comment(f"COMMENT ON COLUMN pet_profiles.{column} IS NULL")
    _comment("COMMENT ON TABLE pet_session_bindings IS NULL")
    for column in ("id", "session_id", "user_id", "pet_id", "created_at", "updated_at", "last_seen_at"):
        _comment(f"COMMENT ON COLUMN pet_session_bindings.{column} IS NULL")


def _comment(statement: str) -> None:
    """执行数据库注释 SQL。

    :param statement: PostgreSQL COMMENT 语句。
    :return: 无返回值。
    """
    op.execute(statement)
