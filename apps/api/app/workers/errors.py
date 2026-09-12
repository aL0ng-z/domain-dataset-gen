"""任务错误分类与稳定错误码（T07 §5 失败合同）。

可重试错误仅包括明确的网络/限流/临时基础设施失败；校验失败、资源不存在、
合同错误、未知 handler/版本为永久失败。永久错误只执行一次，可重试错误按
TaskPolicy 退避并在额度内重试；超限稳定 failed。

错误码是写入 Task.error_code / Attempt.error_code 的稳定标识，前端按 code
分支展示，不匹配中文消息。
"""

from __future__ import annotations

from enum import StrEnum


class TaskErrorCode(StrEnum):
    """稳定错误码：永久错误 / 可重试错误 / 特殊错误。"""

    # ---- 永久错误（不重试）----
    # 资源不存在：handler 加载资源时失败（document/chunk/profile 等）。
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    # 校验失败：项目链不一致、业务前置校验失败。
    VALIDATION_FAILED = "VALIDATION_FAILED"
    # 合同错误：handler 输出的业务字段非法、业务状态机不允许。
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    # 未知 handler 或 payload version：永久失败，不得无限重试。
    UNSUPPORTED_TASK_PAYLOAD = "UNSUPPORTED_TASK_PAYLOAD"
    # 解析/导出等业务内部逻辑错误（不可重试的业务异常）。
    BUSINESS_ERROR = "BUSINESS_ERROR"
    # T11：导出一致性快照缺失/冻结引用 hash 不符，禁止 fallback 到当前配置。
    PROVENANCE_SNAPSHOT_MISSING = "PROVENANCE_SNAPSHOT_MISSING"
    # T11：导出请求 source revision/hash 与当前 composition 不一致。
    EXPORT_REVISION_CONFLICT = "EXPORT_REVISION_CONFLICT"
    EXPORT_FORMAT_INCOMPATIBLE = "EXPORT_FORMAT_INCOMPATIBLE"
    EXPORT_CONTENT_INVALID = "EXPORT_CONTENT_INVALID"

    # ---- 可重试错误（按 Policy 退避）----
    # 网络/传输层临时失败（超时、连接重置、5xx）。
    NETWORK_ERROR = "NETWORK_ERROR"
    # 外部服务限流（429 / retry-after）。
    RATE_LIMITED = "RATE_LIMITED"
    # 临时基础设施失败（数据库抖动、依赖服务暂不可用）。
    TEMPORARY_INFRA_ERROR = "TEMPORARY_INFRA_ERROR"

    # ---- 特殊 ----
    # legacy 迁移中无法恢复 payload 的非终态旧任务。
    LEGACY_TASK_NOT_RESUMABLE = "LEGACY_TASK_NOT_RESUMABLE"
    # 任务被取消（cancelling -> cancelled）。
    TASK_CANCELLED = "TASK_CANCELLED"


class TaskError(Exception):
    """携带稳定错误码的 handler 异常（runner 原样写入 Task.error_code）。"""

    def __init__(self, code: TaskErrorCode, message: str, *, retriable: bool = False):
        super().__init__(message)
        self.code = code
        self.retriable = retriable


# 可重试错误码集合：runner 据此决定是否回 queued 退避重试。
RETRIABLE_ERROR_CODES: frozenset[TaskErrorCode] = frozenset(
    {
        TaskErrorCode.NETWORK_ERROR,
        TaskErrorCode.RATE_LIMITED,
        TaskErrorCode.TEMPORARY_INFRA_ERROR,
    }
)


def is_retriable(error_code: str | TaskErrorCode | None) -> bool:
    """错误码是否属于可重试集合。未知/None 一律视为永久失败。"""
    if error_code is None:
        return False
    try:
        return TaskErrorCode(error_code) in RETRIABLE_ERROR_CODES
    except ValueError:
        return False
