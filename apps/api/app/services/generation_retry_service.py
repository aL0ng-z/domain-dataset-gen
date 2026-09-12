"""生成 retry planner（T08 §5.4）。

对 failed/cancelled 的 generation Batch 人工 retry：原子创建新 Task + 新
GenerationBatch + 新 GenerationRun；新 Batch 的 ``retry_of_generation_batch_id``
指向旧 Batch，复制旧 Batch 的同一 verified 模板/模型快照、hash 和 renderer，
只把旧 Batch 中没有 completed Run + Candidate 的 Chunk 写入新 selected_chunk_ids
并创建 verified 新 Run。旧 Batch/Run/Task 永不改写或复活。

- 源 provenance 非 verified -> GENERATION_PROVENANCE_INVALID。
- 没有未成功项 -> GENERATION_NOT_RETRYABLE。
- 同一源 Batch 已有直接 retry 后继 -> GENERATION_RETRY_EXISTS（同一
  Idempotency-Key 并发请求只创建一个新 Task/Batch，不产生分叉链）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.generation.renderer import rebuild_input_prompt_and_hash
from app.models.chunk import Chunk
from app.models.generation import GenerationRun
from app.models.generation_batch import GenerationBatch
from app.models.task import Task
from app.services.task_service import TaskService


class GenerationRetryNotRetryableError(Exception):
    """Task/Batch 非可重试状态或没有未成功项。"""


class GenerationRetryExistsError(Exception):
    """源 Batch 已有直接 retry 后继。"""


class GenerationProvenanceInvalidError(Exception):
    """Batch provenance 非 verified，拒绝作为可信 retry 来源。"""


class GenerationRetryPlanner:
    """生成批次 retry：创建新 Task/Batch/Run（旧终态保持不变）。"""

    def __init__(self, db: AsyncSession, task_service: TaskService):
        self.db = db
        self.task_service = task_service

    async def plan_retry(
        self,
        *,
        source_batch: GenerationBatch,
        project_id: uuid.UUID,
        created_by: uuid.UUID,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationBatch, Task]:
        """创建 retry 派生 Batch + parent task。

        返回 (new_batch, new_parent_task)。旧 Batch/Run/Task 永不改写或复活。
        调用方在同一事务提交后由 runner 派发。
        """
        source_batch = await self.db.scalar(select(GenerationBatch).where(
            GenerationBatch.id == source_batch.id,
        ).with_for_update().execution_options(populate_existing=True))
        if source_batch.is_legacy or source_batch.provenance_status != "verified":
            raise GenerationProvenanceInvalidError("源 Batch provenance 非 verified")
        active = await self.db.scalar(select(Task.id).where(
            Task.payload["generation_batch_id"].astext == str(source_batch.id),
            Task.status.in_(("queued", "processing", "cancelling")),
        ).limit(1))
        if active is not None:
            raise GenerationRetryNotRetryableError("当前批次仍有执行中的任务，请等待任务结束")

        # 同一源 Batch 已有直接 retry 后继 -> 409（不产生分叉链）。
        existing_successor = (
            await self.db.execute(
                select(GenerationBatch).where(
                    GenerationBatch.retry_of_generation_batch_id == source_batch.id
                )
            )
        ).scalars().all()
        if existing_successor:
            successor = existing_successor[0]
            successor_task = await self._find_source_parent_task(successor)
            if (idempotency_key is not None and successor_task is not None
                    and successor_task.idempotency_key == idempotency_key):
                return successor, successor_task
            raise GenerationRetryExistsError("源 Batch 已有 retry 后继")

        # 复制旧 Batch 快照（verified 模板/模型快照、hash、renderer）。
        if (
            source_batch.prompt_template_snapshot is None
            or source_batch.model_config_snapshot is None
            or source_batch.prompt_template_sha256 is None
            or source_batch.model_config_sha256 is None
            or source_batch.prompt_template_version_id is None
            or source_batch.renderer_version is None
        ):
            raise GenerationProvenanceInvalidError("源 Batch 缺少冻结快照")

        # 找出源批次未成功的 Chunk（没有 completed Run + Candidate 的 Chunk）。
        retry_chunks = await self._select_uncompleted_chunks(source_batch)
        if not retry_chunks:
            raise GenerationRetryNotRetryableError("源批次没有未成功项")

        new_batch = GenerationBatch(
            document_id=source_batch.document_id,
            chunk_set_id=source_batch.chunk_set_id,
            model_config_id=source_batch.model_config_id,
            prompt_template_id=source_batch.prompt_template_id,
            selected_chunk_ids=[str(c.id) for c in retry_chunks],
            status="pending",
            total_chunks=len(retry_chunks),
            completed_chunks=0,
            summary_json=None,
            created_by=created_by,
            retry_of_generation_batch_id=source_batch.id,
            prompt_template_version_id=source_batch.prompt_template_version_id,
            prompt_template_snapshot=source_batch.prompt_template_snapshot,
            prompt_template_sha256=source_batch.prompt_template_sha256,
            model_config_snapshot=source_batch.model_config_snapshot,
            model_config_sha256=source_batch.model_config_sha256,
            renderer_version=source_batch.renderer_version,
            is_legacy=False,
            provenance_status="verified",
            provenance_error_code=None,
        )
        self.db.add(new_batch)
        await self.db.flush()
        await self.db.refresh(new_batch)

        # 新 parent task（generate_batch handler，entity -> 新 Batch）。
        # 通过 T07 的 retry_of_task_id 指向旧 parent task（任务卡 §5.4）。
        source_parent_task = await self._find_source_parent_task(source_batch)
        parent_payload = {
            "document_id": str(source_batch.document_id),
            "generation_batch_id": str(new_batch.id),
        }
        parent_task = await self.task_service.create_task(
            project_id=project_id,
            task_type="generate_batch",
            entity_type="generation_batch",
            entity_id=new_batch.id,
            created_by=created_by,
            payload=parent_payload,
            handler="generate_batch",
            idempotency_key=idempotency_key,
            **self._frozen_policy(source_parent_task),
        )
        if source_parent_task is not None:
            parent_task.retry_of_task_id = source_parent_task.id
            await self.db.flush()

        # 每个未成功 Chunk 创建 verified 新 Run + child task。
        prompt_snapshot = source_batch.prompt_template_snapshot
        for chunk in retry_chunks:
            input_prompt, rendered_hash = rebuild_input_prompt_and_hash(
                prompt_snapshot, chunk.content, chunk.heading_path
            )
            run = GenerationRun(
                chunk_id=chunk.id,
                prompt_template_id=source_batch.prompt_template_id,
                model_config_id=source_batch.model_config_id,
                context_mode="single_chunk",
                input_prompt=input_prompt,
                rendered_prompt_sha256=rendered_hash,
                status="queued",
                generation_batch_id=new_batch.id,
                is_legacy=False,
                provenance_status="verified",
                provenance_error_code=None,
            )
            self.db.add(run)
            await self.db.flush()
            await self.db.refresh(run)

            child_payload = {
                "chunk_id": str(chunk.id),
                "generation_batch_id": str(new_batch.id),
                "generation_run_id": str(run.id),
            }
            source_child = await self.db.scalar(select(Task).where(
                Task.handler == "generate_single",
                Task.payload["generation_batch_id"].astext == str(source_batch.id),
                Task.entity_id == chunk.id,
            ).order_by(Task.created_at.desc()).limit(1))
            await self.task_service.create_task(
                project_id=project_id,
                task_type="generate",
                entity_type="chunk",
                entity_id=chunk.id,
                created_by=created_by,
                payload=child_payload,
                handler="generate_single",
                parent_task_id=parent_task.id,
                **self._frozen_policy(source_child),
            )

        await self.db.flush()
        return new_batch, parent_task

    @staticmethod
    def _frozen_policy(task: Task | None) -> dict:
        if task is None:
            return {}
        return {"max_attempts": task.max_attempts, "timeout_seconds": task.timeout_seconds,
                "policy_snapshot": task.policy_snapshot, "resolve_policy": False}

    async def _select_uncompleted_chunks(self, batch: GenerationBatch) -> list[Chunk]:
        """旧 Batch 中没有 completed Run + Candidate 的 Chunk（按 ordinal 排序）。

        - completed run（有 Candidate）的 chunk 不重跑。
        - 其他 chunk（failed/cancelled/queued/processing 残留）全部进入 retry。
        """
        selected_ids = batch.selected_chunk_ids or []
        result = await self.db.execute(
            select(Chunk)
            .where(Chunk.chunk_set_id == batch.chunk_set_id, Chunk.id.in_([uuid.UUID(s) for s in selected_ids]))
            .order_by(Chunk.ordinal)
        )
        chunks = list(result.scalars().all())

        # completed run 的 chunk 集合（一个 batch/chunk 唯一约束，至多一个 run）。
        completed_chunk_ids = set(
            (
                await self.db.execute(
                    select(GenerationRun.chunk_id).where(
                        GenerationRun.generation_batch_id == batch.id,
                        GenerationRun.status == "completed",
                    )
                )
            ).scalars().all()
        )
        return [c for c in chunks if c.id not in completed_chunk_ids]

    async def _find_source_parent_task(self, batch: GenerationBatch) -> Task | None:
        """查找源 Batch 的 parent task（generate_batch handler，entity -> 该 Batch）。

        用于新 parent task 的 retry_of_task_id 指向（T07 派生链）。
        """
        result = await self.db.execute(
            select(Task).where(
                Task.task_type == "generate_batch",
                Task.entity_type == "generation_batch",
                Task.entity_id == batch.id,
            )
        )
        return result.scalars().first()
