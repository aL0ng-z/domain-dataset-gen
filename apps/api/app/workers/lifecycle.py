"""Persisted handler/version lifecycle: shared by API, runner and lease recovery.

Every handler is reconstructed from Task payload; no live ExecutionContext is needed.
Callbacks execute in the Task transition transaction and must never commit or swallow errors.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task

TerminalHandler = Callable[[AsyncSession, Task, str, str | None], Awaitable[None]]


class LifecycleRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], TerminalHandler] = {}

    def register(self, handler: str, version: int, callback: TerminalHandler) -> None:
        self._handlers[(handler, version)] = callback

    def resolve(self, handler: str, version: int) -> TerminalHandler | None:
        return self._handlers.get((handler, version))

    async def terminal(self, db: AsyncSession, task: Task, status: str,
                       error: str | None = None) -> None:
        callback = self.resolve(task.handler, task.payload_version or 1)
        if callback is not None:
            await callback(db, task, status, error)


lifecycle_registry = LifecycleRegistry()


def _id(task: Task, name: str) -> uuid.UUID | None:
    value = (task.payload or {}).get(name)
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


async def _document_job_terminal(db: AsyncSession, task: Task, status: str,
                                 error: str | None) -> None:
    from app.models.document import Document
    from app.models.parse import ParseJob
    from app.models.section import CleaningJob

    model = ParseJob if task.handler == "parse_document" else CleaningJob
    job_id = _id(task, "parse_job_id" if model is ParseJob else "cleaning_job_id")
    if job_id is None:
        return
    result = await db.execute(
        update(model).where(
            model.id == job_id, model.task_id == task.id,
            model.status.in_(("queued", "processing")),
            model.document_id.in_(select(Document.id).where(Document.project_id == task.project_id)),
        ).values(status=status, error_code=task.error_code or (
            "TASK_CANCELLED" if status == "cancelled" else "BUSINESS_ERROR"),
            error_message=error or ("任务已取消" if status == "cancelled" else "任务失败"),
            completed_at=datetime.now(UTC))
    )
    if result.rowcount and model is ParseJob:
        # Only restore a parsing document when no other live parse can still publish.
        job = await db.get(ParseJob, job_id)
        others = await db.scalar(select(ParseJob.id).where(
            ParseJob.document_id == job.document_id,
            ParseJob.status.in_(("queued", "processing")), ParseJob.id != job_id,
        ).limit(1))
        if others is None:
            completed = await db.scalar(select(ParseJob.id).where(
                ParseJob.document_id == job.document_id, ParseJob.status == "completed",
            ).limit(1))
            await db.execute(update(Document).where(
                Document.id == job.document_id, Document.status == "parsing",
            ).values(status="parsed" if completed else "uploaded"))


async def _chunk_terminal(db: AsyncSession, task: Task, status: str, error: str | None) -> None:
    from app.models.chunk_set import ChunkSet
    from app.models.document import Document
    from app.workers.chunk_worker import _make_terminal_hook

    object_id = _id(task, "chunk_set_id")
    if object_id is not None and await db.scalar(select(ChunkSet.id).join(
            Document, Document.id == ChunkSet.document_id).where(
                ChunkSet.id == object_id, Document.project_id == task.project_id,
                ChunkSet.document_id == _id(task, "document_id"))):
        await _make_terminal_hook(object_id)(db, status, error)


async def _export_terminal(db: AsyncSession, task: Task, status: str, error: str | None) -> None:
    from app.models.export import Export
    from app.workers.export_worker import _make_export_terminal_hook

    object_id = _id(task, "export_id")
    if object_id is not None and await db.scalar(select(Export.id).where(
            Export.id == object_id, Export.project_id == task.project_id, Export.task_id == task.id)):
        hook = await _make_export_terminal_hook(object_id, task.id)
        await hook(db, status, error)


async def _generation_terminal(db: AsyncSession, task: Task, status: str,
                               error: str | None) -> None:
    from app.models.chunk import Chunk
    from app.models.document import Document
    from app.models.generation import GenerationRun
    from app.models.generation_batch import GenerationBatch
    from app.workers.generate_worker import _make_batch_terminal_hook, _make_run_terminal_hook

    if task.handler == "generate_single":
        run_id, chunk_id = _id(task, "generation_run_id"), _id(task, "chunk_id")
        if run_id is not None and chunk_id is not None and await db.scalar(
                select(GenerationRun.id).join(Chunk, Chunk.id == GenerationRun.chunk_id).join(
                    Document, Document.id == Chunk.document_id).where(
                        GenerationRun.id == run_id, GenerationRun.chunk_id == chunk_id,
                        GenerationRun.generation_batch_id == _id(task, "generation_batch_id"),
                        Document.project_id == task.project_id)):
            await _make_run_terminal_hook(run_id, chunk_id)(db, status, error)
            # A cancelled parent may have had running children. Serialize their final
            # summaries on the Batch row; the last terminal child sees all predecessors.
            batch = await db.scalar(select(GenerationBatch).where(
                GenerationBatch.id == _id(task, "generation_batch_id"),
                GenerationBatch.status == "cancelled",
            ).with_for_update().execution_options(populate_existing=True))
            if batch is not None:
                await _make_batch_terminal_hook(batch.id, batch.document_id)(db, "cancelled", None)
    else:
        batch_id, doc_id = _id(task, "generation_batch_id"), _id(task, "document_id")
        if batch_id is not None and doc_id is not None and await db.scalar(
                select(GenerationBatch.id).join(Document, Document.id == GenerationBatch.document_id).where(
                    GenerationBatch.id == batch_id, GenerationBatch.document_id == doc_id,
                    Document.project_id == task.project_id)):
            await _make_batch_terminal_hook(batch_id, doc_id)(db, status, error)


for _name in ("parse_document", "clean_document"):
    lifecycle_registry.register(_name, 1, _document_job_terminal)
lifecycle_registry.register("chunk_document", 2, _chunk_terminal)
for _name in ("export_dataset", "export_benchmark"):
    lifecycle_registry.register(_name, 1, _export_terminal)
for _name in ("generate_single", "generate_batch"):
    lifecycle_registry.register(_name, 1, _generation_terminal)


async def bind_document_job(db: AsyncSession, task: Task, *, source: Task | None = None) -> None:
    """Bind new and manually retried parse/clean jobs atomically, fencing predecessor tasks."""
    from app.models.document import Document
    from app.models.parse import ParseJob
    from app.models.section import CleaningJob

    if task.handler not in ("parse_document", "clean_document") or task.payload_version != 1:
        return
    model = ParseJob if task.handler == "parse_document" else CleaningJob
    job_id = _id(task, "parse_job_id" if model is ParseJob else "cleaning_job_id")
    if job_id is None:
        return
    conditions = [model.id == job_id, model.document_id.in_(
        select(Document.id).where(Document.project_id == task.project_id))]
    if source is None:
        conditions += [model.task_id.is_(None), model.status == "queued"]
    else:
        conditions += [model.task_id == source.id, model.status == "failed"]
    result = await db.execute(update(model).where(*conditions).values(
        task_id=task.id, status="queued", error_code=None, error_message=None, completed_at=None))
    if source is not None and result.rowcount != 1:
        from app.services.task_service import RetryPreparationError
        raise RetryPreparationError("JOB_RETRY_CONFLICT", "业务任务已变更，无法重试")
