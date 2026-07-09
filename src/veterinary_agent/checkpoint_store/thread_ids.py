##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/thread_ids.py
# 作用: 定义 CheckpointStore thread_id 的稳定派生规则，供控制面存储与后续 GraphRuntime 集成复用。
# 边界: 仅处理 session_id 到 thread_id 的确定性转换，不访问数据库、不调用 LangGraph、不解释业务语义。
##################################################################################################

from hashlib import sha256
from typing import Final

CHECKPOINT_THREAD_ID_PREFIX: Final[str] = "checkpoint_thread_v1_"


def build_checkpoint_thread_id(*, session_id: str) -> str:
    """根据 session_id 派生稳定 checkpoint thread_id。

    :param session_id: 上游可信传入的会话 ID。
    :return: 可作为 CheckpointStore 与 LangGraph thread 标识使用的稳定 ID。
    :raises ValueError: 当 session_id 为空或去除首尾空白后为空时抛出。
    """

    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise ValueError("session_id 不得为空")
    session_digest = sha256(normalized_session_id.encode("utf-8")).hexdigest()
    return f"{CHECKPOINT_THREAD_ID_PREFIX}{session_digest}"


__all__: tuple[str, ...] = (
    "CHECKPOINT_THREAD_ID_PREFIX",
    "build_checkpoint_thread_id",
)
