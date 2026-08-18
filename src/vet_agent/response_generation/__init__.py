"""
=============================================================================
文件：src/vet_agent/response_generation/__init__.py
作用：作为回复生成上下文编译包入口，集中暴露模型调用、上下文编译与
      结果封装能力。
范围：供 container、orchestrator 与测试代码通过包级导出访问稳定契约；
      调用方不直接引用包内实现文件，避免跨包穿透实现细节。
说明：本包替代旧版 ResponseComposer 的内联拼接与硬编码回退路径。
=============================================================================
"""

from .context_builder import ResponseGenerationContextBuilder
from .errors import (
    ResponseGenerationContractError,
    ResponseGenerationDependencyError,
    ResponseGenerationError,
)
from .models import (
    ResponseGenerationContext,
    ResponseGenerationRequest,
    ResponseGenerationResult,
    ResponseGenerationStrategy,
)
from .ports import ResponseGenerationServiceProtocol
from .projections import (
    AnswerEvidenceContext,
    AnswerabilityGenerationContext,
    ClinicalSafetyGenerationContext,
    ConsultationGenerationContext,
    MemoryGenerationContext,
)
from .service import ResponseGenerationService

__all__ = [
    "AnswerEvidenceContext",
    "AnswerabilityGenerationContext",
    "ClinicalSafetyGenerationContext",
    "ConsultationGenerationContext",
    "MemoryGenerationContext",
    "ResponseGenerationContext",
    "ResponseGenerationContextBuilder",
    "ResponseGenerationContractError",
    "ResponseGenerationDependencyError",
    "ResponseGenerationError",
    "ResponseGenerationRequest",
    "ResponseGenerationResult",
    "ResponseGenerationService",
    "ResponseGenerationServiceProtocol",
    "ResponseGenerationStrategy",
]
