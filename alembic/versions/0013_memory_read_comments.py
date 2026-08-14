"""
文件：alembic/versions/0013_memory_read_comments.py
作用：为记忆读取相关数据表补充 PostgreSQL 表和字段说明。
范围：仅更新 conversation_turns、consultation_states、pet_memory_facts 与 pet_memory_episodes 的数据库注释，不改变表结构和业务数据。
说明：字段说明用于运维、审计和后续数据治理理解结构化记忆读取数据链边界。
"""

from __future__ import annotations

from alembic import op


revision = "0013_memory_read_comments"
down_revision = "0012_clinical_safety_publish_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移，补充记忆读取数据表说明。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE conversation_turns IS 'Agent 对话回合表，用于当前 session 滑动窗口、回合审计、幂等响应快照关联与记忆投影来源。'")
    _comment("COMMENT ON COLUMN conversation_turns.id IS '对话回合内部主键。'")
    _comment("COMMENT ON COLUMN conversation_turns.turn_id IS 'Agent 生成的稳定回合标识。'")
    _comment("COMMENT ON COLUMN conversation_turns.request_id IS '入口请求标识，用于幂等与 trace 关联。'")
    _comment("COMMENT ON COLUMN conversation_turns.trace_id IS '链路追踪标识。'")
    _comment("COMMENT ON COLUMN conversation_turns.user_id IS '可信用户标识。'")
    _comment("COMMENT ON COLUMN conversation_turns.session_id IS '可信会话标识。'")
    _comment("COMMENT ON COLUMN conversation_turns.pet_id IS '可信宠物标识。'")
    _comment("COMMENT ON COLUMN conversation_turns.input_text IS '本轮用户输入文本快照。'")
    _comment("COMMENT ON COLUMN conversation_turns.summary IS '本轮 Agent 响应摘要或完整输出快照。'")
    _comment("COMMENT ON COLUMN conversation_turns.status IS '本轮 Agent 响应状态。'")
    _comment("COMMENT ON COLUMN conversation_turns.medical IS '本轮是否属于医疗咨询主链路。'")
    _comment("COMMENT ON COLUMN conversation_turns.metadata IS '本轮回合附加审计元数据。'")
    _comment("COMMENT ON COLUMN conversation_turns.response_snapshot IS '本轮响应结构化快照。'")
    _comment("COMMENT ON COLUMN conversation_turns.created_at IS '本轮回合创建时间。'")

    _comment("COMMENT ON TABLE consultation_states IS '活跃问诊状态表，用于当前 session 默认问诊状态与多任务问诊状态持久化。'")
    _comment("COMMENT ON COLUMN consultation_states.id IS '问诊状态内部主键。'")
    _comment("COMMENT ON COLUMN consultation_states.user_id IS '可信用户标识。'")
    _comment("COMMENT ON COLUMN consultation_states.pet_id IS '可信宠物标识。'")
    _comment("COMMENT ON COLUMN consultation_states.session_id IS '可信会话标识。'")
    _comment("COMMENT ON COLUMN consultation_states.task_key IS '问诊任务键；__default__ 表示默认单任务状态。'")
    _comment("COMMENT ON COLUMN consultation_states.state IS '结构化活跃问诊状态 JSON。'")
    _comment("COMMENT ON COLUMN consultation_states.version IS '问诊状态更新版本号。'")
    _comment("COMMENT ON COLUMN consultation_states.created_at IS '问诊状态创建时间。'")
    _comment("COMMENT ON COLUMN consultation_states.updated_at IS '问诊状态最近更新时间。'")

    _comment("COMMENT ON TABLE pet_memory_facts IS '宠物权威长期事实表，用于保存经抽取、确认或人工纠正后的可信记忆事实。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.id IS '长期事实内部主键。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.user_id IS '可信用户标识。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.pet_id IS '可信宠物标识。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.fact_type IS '事实类型，例如 medical 或 owner_preference。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.fact_key IS '事实键名。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.fact_value IS '事实内容。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.confidence IS '事实置信度，范围应由写入策略控制。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.source_turn_id IS '事实来源回合标识。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.source_text IS '事实来源文本片段。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.valid_from IS '事实生效时间。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.valid_until IS '事实失效时间。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.is_active IS '事实是否处于可读状态。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.metadata IS '长期事实附加审计元数据。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.created_at IS '长期事实创建时间。'")
    _comment("COMMENT ON COLUMN pet_memory_facts.updated_at IS '长期事实最近更新时间。'")

    _comment("COMMENT ON TABLE pet_memory_episodes IS '宠物中期历史 episode 表，用于保存跨 session 的历史事件摘要和语义投影来源审计。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.id IS 'episode 内部主键。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.user_id IS '可信用户标识。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.pet_id IS '可信宠物标识。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.session_id IS 'episode 来源会话标识。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.turn_id IS 'episode 来源回合标识。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.title IS 'episode 标题。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.summary IS 'episode 摘要。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.memory_scope IS 'episode 记忆范围，例如 medium。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.metadata IS 'episode 附加审计元数据。'")
    _comment("COMMENT ON COLUMN pet_memory_episodes.created_at IS 'episode 创建时间。'")


def downgrade() -> None:
    """执行 Alembic 回滚迁移，移除记忆读取数据表说明。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE conversation_turns IS NULL")
    for column in (
        "id",
        "turn_id",
        "request_id",
        "trace_id",
        "user_id",
        "session_id",
        "pet_id",
        "input_text",
        "summary",
        "status",
        "medical",
        "metadata",
        "response_snapshot",
        "created_at",
    ):
        _comment(f"COMMENT ON COLUMN conversation_turns.{column} IS NULL")

    _comment("COMMENT ON TABLE consultation_states IS NULL")
    for column in ("id", "user_id", "pet_id", "session_id", "task_key", "state", "version", "created_at", "updated_at"):
        _comment(f"COMMENT ON COLUMN consultation_states.{column} IS NULL")

    _comment("COMMENT ON TABLE pet_memory_facts IS NULL")
    for column in (
        "id",
        "user_id",
        "pet_id",
        "fact_type",
        "fact_key",
        "fact_value",
        "confidence",
        "source_turn_id",
        "source_text",
        "valid_from",
        "valid_until",
        "is_active",
        "metadata",
        "created_at",
        "updated_at",
    ):
        _comment(f"COMMENT ON COLUMN pet_memory_facts.{column} IS NULL")

    _comment("COMMENT ON TABLE pet_memory_episodes IS NULL")
    for column in (
        "id",
        "user_id",
        "pet_id",
        "session_id",
        "turn_id",
        "title",
        "summary",
        "memory_scope",
        "metadata",
        "created_at",
    ):
        _comment(f"COMMENT ON COLUMN pet_memory_episodes.{column} IS NULL")


def _comment(statement: str) -> None:
    """执行数据库注释 SQL。

    :param statement: PostgreSQL COMMENT 语句。
    :return: 无返回值。
    """
    op.execute(statement)
