"""版本化切分服务（T06 §5、§7）。

职责：
- ``create_chunk_set_task``：在一次数据库事务中创建 ChunkSet（version 在 Document
  行锁下分配）与 T07 queued Task；幂等 key/并发活跃 set 由 DB 唯一/部分唯一约束兜底。
- ``_freeze_config``：冻结 strategy/max_tokens/overlap_tokens/options/
  tokenizer_name/tokenizer_version/splitter_version 到 config_json，后续 Profile
  修改不改变历史集合。
- cleaned_version 校验：省略取 Document.active；显式提供时必须等于 active id；
  没有 active/非 accepted/不属于该文档 -> 409 CLEAN_VERSION_NOT_READY；
  旧于/不同于 active -> 409 CLEAN_VERSION_STALE。
- idempotency_key 格式 ``<client_key>:<request_digest>``：同 key 同摘要重放返回
  既有对象；同 key 不同摘要 -> 409 IDEMPOTENCY_KEY_REUSED（由路由映射）。
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.config import ChunkProfile
from app.models.document import Document
from app.models.task import Task
from splitters import splitter_version, tokenizer_version

# payload 版本固定：T07 handler 按 (handler, payload_version) 分派。
CHUNK_PAYLOAD_VERSION = 2


class CleanVersionNotReadyError(Exception):
    """没有 active cleaned version，或版本非 accepted/不属于该文档。"""


class CleanVersionStaleError(Exception):
    """显式提供的版本旧于/不同于 active。"""

    def __init__(self, target_version: int | None, active_version: int | None):
        super().__init__("目标清洗版本不是当前 active 版本")
        self.target_version = target_version
        self.active_version = active_version


class IdempotencyKeyReusedError(Exception):
    """同 idempotency key 但请求摘要不同。"""


def build_chunk_idempotency_key(client_key: str, request: dict) -> str:
    """构造数据库幂等键：client key + 规范化请求摘要。

    摘要保证同 key 但不同请求（不同 profile/cleaned_version）不会误复用同一 ChunkSet。
    """
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return f"{client_key}:{digest}"


def chunk_client_key(composite_key: str) -> str:
    """从复合幂等键还原客户端 key（路由用前缀匹配活跃 set 归属）。"""
    return composite_key.split(":", 1)[0]


def _freeze_config(profile: ChunkProfile) -> dict:
    """冻结切分配置快照（后续 Profile 修改不改变历史集合）。"""
    return {
        "strategy": profile.strategy,
        "max_tokens": profile.max_tokens,
        "overlap_tokens": profile.overlap_tokens,
        "options": profile.options or {},
        "tokenizer_name": "cl100k_base",
        "tokenizer_version": tokenizer_version(),
        "splitter_version": splitter_version(),
    }


def _chunk_payload(chunk_set_id: uuid.UUID, document_id: uuid.UUID) -> dict:
    """切分 Task payload：明确携带 chunk_set_id 与冻结输入引用。"""
    return {
        "document_id": str(document_id),
        "chunk_set_id": str(chunk_set_id),
    }


class ChunkSetService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve_clean_version(
        self,
        document_id: uuid.UUID,
        requested: uuid.UUID | None,
    ) -> CleanedDocumentVersion:
        """解析切分来源清洗版本：省略取 active；显式必须等于 active。

        失败抛 CleanVersionNotReadyError / CleanVersionStaleError（路由映射 409）。
        """
        doc = (
            await self.db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            raise CleanVersionNotReadyError("文档不存在")

        active_id = doc.active_clean_version_id
        version_id = requested or active_id
        if version_id is None:
            raise CleanVersionNotReadyError("没有已接受的清洗版本，请先完成终审")

        version = (
            await self.db.execute(
                select(CleanedDocumentVersion).where(
                    CleanedDocumentVersion.id == version_id,
                    CleanedDocumentVersion.document_id == document_id,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise CleanVersionNotReadyError("清洗版本不存在或不属于该文档")
        if version.status != "accepted":
            raise CleanVersionNotReadyError("清洗版本尚未 accepted")

        # 显式提供时：必须等于 active id（旧版本不得成为 active 链路来源）。
        if requested is not None and active_id != requested:
            active_version = None
            if active_id is not None:
                active_version = (
                    await self.db.execute(
                        select(CleanedDocumentVersion.version).where(
                            CleanedDocumentVersion.id == active_id
                        )
                    )
                ).scalar_one_or_none()
            raise CleanVersionStaleError(version.version, active_version)
        return version

    async def create_chunk_set_task(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        profile: ChunkProfile,
        clean_version: CleanedDocumentVersion,
        created_by: uuid.UUID,
        idempotency_key: str | None,
    ) -> tuple[ChunkSet, Task, bool]:
        """在同一事务中创建 ChunkSet + T07 queued Task（调用方负责 commit）。

        版本号在锁定 documents 行后分配（不允许无锁 MAX(version)+1）。
        返回 ``(chunk_set, task, reused)``：reused=True 表示同 key 幂等重放（未新建）。
        """
        # 锁定 Document 行：版本分配与 active 校验的串行化点。
        doc = (
            await self.db.execute(
                select(Document).where(Document.id == document_id).with_for_update()
            )
        ).scalar_one()

        # 幂等复核（锁内）：同 key 同摘要命中直接返回既有 ChunkSet + Task；
        # 同 key 不同摘要 -> IDEMPOTENCY_KEY_REUSED（路由映射 409）。
        if idempotency_key:
            existing = (
                await self.db.execute(
                    select(ChunkSet).where(
                        ChunkSet.document_id == document_id,
                        ChunkSet.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                task = (
                    await self.db.execute(select(Task).where(Task.id == existing.task_id))
                ).scalar_one_or_none()
                if task is None:
                    raise RuntimeError(f"ChunkSet {existing.id} 的 task 不存在")
                return existing, task, True

        # 版本号在行锁内分配。
        from sqlalchemy import func

        max_version = (
            await self.db.execute(
                select(func.coalesce(func.max(ChunkSet.version), 0)).where(
                    ChunkSet.document_id == document_id
                )
            )
        ).scalar_one()
        version = int(max_version) + 1

        # 预生成 ChunkSet UUID：Task payload 需要引用它，而 ChunkSet 又需要
        # task_id 满足非 legacy 必填 CHECK——先建 Task 再建 ChunkSet（同一事务）。
        chunk_set_id = uuid.uuid4()
        from app.services.task_service import TaskService

        task_service = TaskService(self.db)
        task = await task_service.create_task(
            project_id=project_id,
            task_type="chunk",
            entity_type="chunk_set",
            entity_id=chunk_set_id,
            created_by=created_by,
            payload=_chunk_payload(chunk_set_id, document_id),
            handler="chunk_document",
            idempotency_key=idempotency_key,
            timeout_seconds=600,
        )

        frozen = _freeze_config(profile)
        chunk_set = ChunkSet(
            id=chunk_set_id,
            document_id=document_id,
            cleaned_document_version_id=clean_version.id,
            chunk_profile_id=profile.id,
            strategy=profile.strategy,
            config_json=frozen,
            splitter_version=frozen["splitter_version"],
            status="pending",
            total_chunks=0,
            total_tokens=0,
            version=version,
            is_legacy=False,
            idempotency_key=idempotency_key,
            source_sha256=clean_version.content_sha256,
            task_id=task.id,
            created_by=created_by,
        )
        self.db.add(chunk_set)
        await self.db.flush()
        await self.db.refresh(chunk_set)

        doc.status = "chunking"
        await self.db.flush()
        await self.db.refresh(chunk_set)
        return chunk_set, task, False
