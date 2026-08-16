"""
文件：src/vet_agent/background_tasks/__init__.py
作用：作为 background_tasks 包入口，统一暴露持久化后台任务的领域模型、入队
      服务、处理器与 worker 运行入口。
说明：跨包引用应通过本文件暴露的稳定公共能力，不应直接引用内部实现模块；
      运行时服务使用延迟导入，避免仓储、服务与 worker 在包初始化阶段形成循环依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    BackgroundTaskExecutionOutcome,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
    BackgroundTaskType,
    MemoryCandidateExtractionTaskPayload,
)

if TYPE_CHECKING:
    from .handlers import BackgroundTaskHandler, BackgroundTaskHandlerRegistry, MemoryCandidateExtractionTaskHandler
    from .healthcheck import check_background_task_worker_health, main_healthcheck
    from .service import (
        BackgroundTaskService,
        BackgroundTaskServiceProtocol,
        DisabledBackgroundTaskService,
    )
    from .worker import BackgroundTaskWorker

__all__ = [
    "BackgroundTaskExecutionOutcome",
    "BackgroundTaskHandler",
    "BackgroundTaskHandlerRegistry",
    "BackgroundTaskRecord",
    "BackgroundTaskService",
    "BackgroundTaskServiceProtocol",
    "BackgroundTaskStatus",
    "BackgroundTaskType",
    "BackgroundTaskWorker",
    "DisabledBackgroundTaskService",
    "MemoryCandidateExtractionTaskHandler",
    "MemoryCandidateExtractionTaskPayload",
    "build_background_task_worker",
    "check_background_task_worker_health",
    "main",
    "main_healthcheck",
    "make_memory_extraction_task_metadata",
    "run_worker",
]


def __getattr__(name: str) -> object:
    """按名称延迟解析后台任务包公共对象。

    说明：领域模型在包初始化时直接暴露；handler、service 与 worker 会牵涉
    到服务层、仓储层和容器依赖，因此通过本函数按需加载。

    :param name: 需要解析的公共对象名称。
    :return: 返回对应的公共对象。
    """
    if name in {"BackgroundTaskHandler", "BackgroundTaskHandlerRegistry", "MemoryCandidateExtractionTaskHandler"}:
        from .handlers import BackgroundTaskHandler, BackgroundTaskHandlerRegistry, MemoryCandidateExtractionTaskHandler

        handler_values: dict[str, object] = {
            "BackgroundTaskHandler": BackgroundTaskHandler,
            "BackgroundTaskHandlerRegistry": BackgroundTaskHandlerRegistry,
            "MemoryCandidateExtractionTaskHandler": MemoryCandidateExtractionTaskHandler,
        }
        return handler_values[name]
    if name in {"BackgroundTaskService", "BackgroundTaskServiceProtocol", "DisabledBackgroundTaskService"}:
        from .service import BackgroundTaskService, BackgroundTaskServiceProtocol, DisabledBackgroundTaskService

        service_values: dict[str, object] = {
            "BackgroundTaskService": BackgroundTaskService,
            "BackgroundTaskServiceProtocol": BackgroundTaskServiceProtocol,
            "DisabledBackgroundTaskService": DisabledBackgroundTaskService,
        }
        return service_values[name]
    if name == "make_memory_extraction_task_metadata":
        from .service import make_memory_extraction_task_metadata

        return make_memory_extraction_task_metadata
    if name in {"check_background_task_worker_health", "main_healthcheck"}:
        from .healthcheck import check_background_task_worker_health, main_healthcheck

        healthcheck_values: dict[str, object] = {
            "check_background_task_worker_health": check_background_task_worker_health,
            "main_healthcheck": main_healthcheck,
        }
        return healthcheck_values[name]
    if name in {"BackgroundTaskWorker", "build_background_task_worker", "main", "run_worker"}:
        from .worker import BackgroundTaskWorker, build_background_task_worker, main, run_worker

        worker_values: dict[str, object] = {
            "BackgroundTaskWorker": BackgroundTaskWorker,
            "build_background_task_worker": build_background_task_worker,
            "main": main,
            "run_worker": run_worker,
        }
        return worker_values[name]
    raise AttributeError(f"module 'vet_agent.background_tasks' has no attribute {name!r}")
