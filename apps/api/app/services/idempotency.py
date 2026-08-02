"""Idempotency-Key 支持（T07 §6 API 合同）。

触发 API 支持 Idempotency-Key：同 key/同请求返回同 Task；不同请求摘要返回
409 IDEMPOTENCY_KEY_REUSED。请求摘要基于 payload 的规范化 JSON 计算，
避免把完整请求体存入数据库。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task


def build_idempotency_key(client_key: str, request_digest: str) -> str:
    """构造数据库幂等键：客户端 key + 请求摘要。

    摘要确保同 key 但不同请求不会误复用同一 Task。
    """
    return f"{client_key}:{request_digest}"


def request_digest(payload: dict[str, Any] | None) -> str:
    """对请求 payload 计算稳定摘要（规范化 JSON，忽略键序）。"""
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


async def find_task_by_client_key(
    db: AsyncSession, project_id, task_type: str, client_key: str
) -> Task | None:
    """按客户端 key 前缀查找既有 Task（可能为同 key 同请求或同 key 不同请求）。"""
    prefix = f"{client_key}:"
    result = await db.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.task_type == task_type,
            Task.idempotency_key.like(f"{prefix}%"),
        )
    )
    return result.scalar_one_or_none()


def ensure_idempotency_compatible(
    existing: Task | None,
    client_key: str,
    payload: dict[str, Any] | None,
    *,
    task_type: str,
) -> bool:
    """校验既有 Task 与当前请求是否同一摘要。

    返回 True 表示可复用（同 key 同请求）；False 表示 409 IDEMPOTENCY_KEY_REUSED。
    无既有 Task 时返回 True（继续创建）。
    """
    if existing is None:
        return True
    if existing.task_type != task_type:
        return False
    digest = request_digest(payload)
    if existing.idempotency_key is None:
        return False
    # idempotency_key 格式 client_key:digest；比对 digest 部分。
    if ":" in existing.idempotency_key:
        stored_digest = existing.idempotency_key.split(":", 1)[1]
        return stored_digest == digest
    return existing.idempotency_key == client_key


def idempotency_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "CONFLICT", "message": "幂等键已被不同的请求复用"},
    )


async def idempotent_create_task(
    db: AsyncSession,
    *,
    task_service,
    client_key: str,
    project_id,
    task_type: str,
    payload_for_digest: dict[str, Any] | None,
    create,
):
    """通用幂等创建：同 key/同摘要返回既有 Task；不同摘要 409。

    - ``payload_for_digest``：用于计算请求摘要的规范化载荷（不含运行时生成 id）。
    - ``create(key)``：实际创建 Task 的协程（返回 Task），key 为合成幂等键。
    """
    existing = await find_task_by_client_key(db, project_id, task_type, client_key)
    if existing is not None:
        if not ensure_idempotency_compatible(
            existing, client_key, payload_for_digest, task_type=task_type
        ):
            raise idempotency_conflict()
        return existing
    key = build_idempotency_key(client_key, request_digest(payload_for_digest))
    return await create(key)
