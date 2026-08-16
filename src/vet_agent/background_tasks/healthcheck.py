"""
=============================================================================
文件：src/vet_agent/background_tasks/healthcheck.py
作用：提供后台 Worker 容器健康检查入口。
范围：仅校验后台任务 Worker 运行所需的最小基础设施状态，即 DATABASE_URL
      配置与 background_tasks 仓储可访问性；不领取任务、不调用模型、不执行
      长期事实写入或 Mem0 投影。
说明：该模块用于 Docker Compose healthcheck 和运维手动排障，保证 Worker
      容器的健康语义与可持久化任务表这一唯一任务可信源对齐。
=============================================================================
"""

from __future__ import annotations

import sys

from vet_agent import Settings
from vet_agent.repositories import BackgroundTaskRepository, PostgresBackgroundTaskRepository


def check_background_task_worker_health(
    settings: Settings,
    *,
    repository: BackgroundTaskRepository | None = None,
) -> None:
    """校验后台 Worker 容器的最小运行依赖。

    说明：本函数位于可持久化后台任务数据链的运维探测边界，只验证任务仓储
    是否可访问，不把 LiteLLM、OPA、Mem0 等业务依赖纳入容器存活判断。
    业务依赖异常应在具体任务执行中进入 retry 或 dead letter 审计。

    :param settings: 当前运行环境配置。
    :param repository: 可选的后台任务仓储，测试场景可注入替身。
    :return: 无返回值；校验失败时抛出 RuntimeError。
    :raises RuntimeError: 当数据库连接串缺失或后台任务仓储不可访问时抛出。
    """
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for background task worker")
    task_repository = repository or PostgresBackgroundTaskRepository(settings.database_url)
    if not task_repository.is_ready():
        raise RuntimeError("background task repository is not ready")


def main_healthcheck() -> None:
    """执行后台 Worker 容器健康检查命令行入口。

    :return: 无返回值；检查失败时以非零状态码退出。
    """
    try:
        check_background_task_worker_health(Settings.from_env())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main_healthcheck()
