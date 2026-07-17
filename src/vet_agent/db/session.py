"""
文件：src/vet_agent/db/session.py
作用：提供数据库模型、连接与会话管理能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def sqlalchemy_url(database_url: str) -> str:
    """执行 sqlalchemy_url 业务逻辑。

    :param database_url: 数据库连接地址。
    :return: 返回函数执行结果。
    """
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def make_engine(database_url: str) -> Engine:
    """执行 make_engine 业务逻辑。

    :param database_url: 数据库连接地址。
    :return: 返回函数执行结果。
    """
    return create_engine(sqlalchemy_url(database_url), pool_pre_ping=True)


def make_session_factory(database_url: str) -> sessionmaker[Session]:
    """执行 make_session_factory 业务逻辑。

    :param database_url: 数据库连接地址。
    :return: 返回函数执行结果。
    """
    return sessionmaker(bind=make_engine(database_url), expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """执行 session_scope 业务逻辑。

    :param factory: 参数 factory。
    :return: 返回函数执行结果。
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
