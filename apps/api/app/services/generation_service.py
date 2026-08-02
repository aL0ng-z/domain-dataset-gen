"""生成编排服务（T08 §5）。

单一入口把单 Chunk 与批量生成合并：原子冻结模板/模型快照、写 GenerationBatch、
创建 parent/child Task 与 GenerationRun、派发到 T07 dispatcher。返回 202 前完成
版本物化和非秘密快照/hash；任何不可安全分类的配置/渲染失败都以异常上抛，由
路由映射为 409，不创建 Batch/Task。

worker 只读 Batch 冻结快照重建 input_prompt 并核验 rendered_prompt_sha256，
绝不重新读取 PromptTemplate/ModelConfig 的可变参数。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.generation.renderer import (
    RENDERER_VERSION,
    rebuild_input_prompt_and_hash,
)
from app.generation.snapshot import (
    build_model_config_snapshot,
    build_prompt_template_snapshot,
    model_config_snapshot_sha256,
    prompt_template_snapshot_sha256,
)
from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.config import ModelConfig
from app.models.document import Document
from app.models.generation import GenerationRun
from app.models.generation_batch import GenerationBatch
from app.models.prompt_template import PromptTemplate, PromptTemplateVersion
from app.services.task_service import TaskService


class GenerationSourceNotReadyError(Exception):
    """无 active ChunkSet、Chunk 不属 active set、状态不允许或选择集为空。"""


class GenerationConfigUnavailableError(Exception):
    """配置禁用、凭证引用不可用或模板版本不能唯一物化。"""


class GenerationInProgressError(Exception):
    """相同 source/Chunk 已有不允许并行的生成。

    携带已存在 batch 的 id，供路由映射 409 context。
    """

    def __init__(self, message: str, *, existing_batch_id: uuid.UUID | None = None):
        super().__init__(message)
        self.existing_batch_id = existing_batch_id


class PromptTemplateVersionMaterializeError(Exception):
    """模板当前版本无法唯一物化。"""


def _sort_unique_chunk_ids(chunk_ids: list[uuid.UUID]) -> list[str]:
    """按 ordinal 排序、去重后的 UUID 字符串数组（任务卡 §4）。

    排序依据是 Chunk 在文档中的 ordinal；由调用方传入已按 ordinal 排序的
    chunk 列表，这里只做去重并转字符串。
    """
    seen: set[str] = set()
    out: list[str] = []
    for cid in chunk_ids:
        s = str(cid)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


class GenerationOrchestrationService:
    """生成批次编排：路由调用后在同一事务提交，再由 runner 派发。"""

    def __init__(self, db: AsyncSession, task_service: TaskService):
        self.db = db
        self.task_service = task_service

    # ------------------------------------------------------------------
    # 资源加载与校验（项目归属由路由先行 scoped load）
    # ------------------------------------------------------------------

    async def _load_active_chunk_set(self, document_id: uuid.UUID) -> ChunkSet | None:
        """读取 Document.active_chunk_set_id 对应的 ChunkSet（必须是 completed）。"""
        doc = (
            await self.db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None or doc.active_chunk_set_id is None:
            return None
        cs = (
            await self.db.execute(select(ChunkSet).where(ChunkSet.id == doc.active_chunk_set_id))
        ).scalar_one_or_none()
        return cs

    async def _resolve_ready_chunks(
        self, document_id: uuid.UUID, chunk_set_id: uuid.UUID
    ) -> list[Chunk]:
        """active ChunkSet 中全部 ready chunks（按 ordinal 排序）。"""
        result = await self.db.execute(
            select(Chunk)
            .where(Chunk.chunk_set_id == chunk_set_id, Chunk.status == "ready")
            .order_by(Chunk.ordinal)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # PromptTemplateVersion 物化
    # ------------------------------------------------------------------

    async def _materialize_template_version(
        self, template: PromptTemplate
    ) -> PromptTemplateVersion:
        """物化模板当前版本为不可变 PromptTemplateVersion 行。

        - 若已存在 (template_id, version) 行且内容与当前一致 -> 复用。
        - 若已存在但内容矛盾 -> 抛 PromptTemplateVersionMaterializeError
          （模板版本表存在多份不一致数据，无法唯一物化）。
        - 否则创建该版本行（同一事务，与 Batch 原子）。
        """
        existing = (
            await self.db.execute(
                select(PromptTemplateVersion).where(
                    PromptTemplateVersion.template_id == template.id,
                    PromptTemplateVersion.version == template.version,
                )
            )
        ).scalars().all()
        if len(existing) > 1:
            raise PromptTemplateVersionMaterializeError(
                f"模板 {template.id} 版本 {template.version} 存在多份且内容不一致"
            )
        if existing:
            ver = existing[0]
            if (
                ver.system_prompt != template.system_prompt
                or ver.user_prompt_template != template.user_prompt_template
                or ver.input_schema != template.input_schema
                or ver.output_schema != template.output_schema
            ):
                raise PromptTemplateVersionMaterializeError(
                    f"模板 {template.id} 版本 {template.version} 内容与既有版本行矛盾"
                )
            return ver
        ver = PromptTemplateVersion(
            template_id=template.id,
            version=template.version,
            system_prompt=template.system_prompt,
            user_prompt_template=template.user_prompt_template,
            input_schema=template.input_schema,
            output_schema=template.output_schema,
        )
        self.db.add(ver)
        await self.db.flush()
        await self.db.refresh(ver)
        return ver

    # ------------------------------------------------------------------
    # 快照冻结
    # ------------------------------------------------------------------

    async def _freeze_snapshots(
        self, template: PromptTemplate, model_config: ModelConfig
    ) -> dict[str, Any]:
        """冻结模板/模型快照与 hash；发现秘密抛 SnapshotUnsafeError（fail closed）。"""
        tpl_snapshot = build_prompt_template_snapshot(template)
        model_snapshot = build_model_config_snapshot(model_config)
        return {
            "prompt_template_version": await self._materialize_template_version(template),
            "prompt_template_snapshot": tpl_snapshot,
            "prompt_template_sha256": prompt_template_snapshot_sha256(tpl_snapshot),
            "model_config_snapshot": model_snapshot,
            "model_config_sha256": model_config_snapshot_sha256(model_snapshot),
            "renderer_version": RENDERER_VERSION,
        }

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------

    async def create_single(
        self,
        *,
        project_id: uuid.UUID,
        chunk_id: uuid.UUID,
        document_id: uuid.UUID,
        prompt_template_id: uuid.UUID,
        model_config_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> tuple[GenerationBatch, Any, list[GenerationRun]]:
        """单 Chunk 生成：1 batch + 1 parent task + 1 child task + 1 run。

        返回 (batch, parent_task, runs)。路由在返回 202 前 commit。
        """
        chunk = (
            await self.db.execute(select(Chunk).where(Chunk.id == chunk_id))
        ).scalar_one_or_none()
        if chunk is None or chunk.document_id != document_id:
            raise GenerationSourceNotReadyError("Chunk 不存在")
        active_set = await self._load_active_chunk_set(document_id)
        if active_set is None:
            raise GenerationSourceNotReadyError("文档没有 active ChunkSet")
        if chunk.chunk_set_id != active_set.id:
            raise GenerationSourceNotReadyError("Chunk 不属于 active ChunkSet")
        if chunk.status != "ready":
            raise GenerationSourceNotReadyError(f"Chunk 状态不允许生成: {chunk.status}")

        template = (
            await self.db.execute(select(PromptTemplate).where(PromptTemplate.id == prompt_template_id))
        ).scalar_one_or_none()
        model_config = (
            await self.db.execute(select(ModelConfig).where(ModelConfig.id == model_config_id))
        ).scalar_one_or_none()
        if template is None or model_config is None:
            raise GenerationConfigUnavailableError("生成配置不存在")

        return await self._create_batch(
            project_id=project_id,
            document_id=document_id,
            chunk_set=active_set,
            template=template,
            model_config=model_config,
            selected_chunks=[chunk],
            created_by=created_by,
        )

    async def create_batch(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        prompt_template_id: uuid.UUID,
        model_config_id: uuid.UUID,
        selected_chunk_ids: list[uuid.UUID] | None,
        created_by: uuid.UUID,
    ) -> tuple[GenerationBatch, Any, list[GenerationRun]]:
        """批量生成：selected_chunk_ids 省略表示 active ChunkSet 全部 ready chunks。

        返回 (batch, parent_task, runs)。
        """
        active_set = await self._load_active_chunk_set(document_id)
        if active_set is None:
            raise GenerationSourceNotReadyError("文档没有 active ChunkSet")

        template = (
            await self.db.execute(select(PromptTemplate).where(PromptTemplate.id == prompt_template_id))
        ).scalar_one_or_none()
        model_config = (
            await self.db.execute(select(ModelConfig).where(ModelConfig.id == model_config_id))
        ).scalar_one_or_none()
        if template is None or model_config is None:
            raise GenerationConfigUnavailableError("生成配置不存在")

        if selected_chunk_ids is None:
            chunks = await self._resolve_ready_chunks(document_id, active_set.id)
        else:
            chunks = []
            result = await self.db.execute(
                select(Chunk)
                .where(
                    Chunk.chunk_set_id == active_set.id,
                    Chunk.id.in_(selected_chunk_ids),
                    Chunk.status == "ready",
                )
                .order_by(Chunk.ordinal)
            )
            chunks = list(result.scalars().all())
            # 显式选择集必须全部属于 active set 且非空；缺失/越界 -> 409。
            selected_set = {str(c) for c in selected_chunk_ids}
            found_set = {str(c.id) for c in chunks}
            if not selected_chunk_ids or selected_set != found_set:
                raise GenerationSourceNotReadyError("所选 Chunk 不在 active ChunkSet 或不可用")

        if not chunks:
            raise GenerationSourceNotReadyError("没有可生成的 Chunk")

        return await self._create_batch(
            project_id=project_id,
            document_id=document_id,
            chunk_set=active_set,
            template=template,
            model_config=model_config,
            selected_chunks=chunks,
            created_by=created_by,
        )

    # ------------------------------------------------------------------
    # 核心创建（单/批量共用）
    # ------------------------------------------------------------------

    async def _create_batch(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        chunk_set: ChunkSet,
        template: PromptTemplate,
        model_config: ModelConfig,
        selected_chunks: list[Chunk],
        created_by: uuid.UUID,
    ) -> tuple[GenerationBatch, Any, list[GenerationRun]]:
        """原子创建 GenerationBatch + parent task + child tasks + runs。

        相同 source/Chunk 已有进行中（pending/processing）Batch 时抛
        GenerationInProgressError（路由映射 409），避免并行生成冲突。
        """
        active = (
            await self.db.execute(
                select(GenerationBatch.id).where(
                    GenerationBatch.document_id == document_id,
                    GenerationBatch.chunk_set_id == chunk_set.id,
                    GenerationBatch.status.in_(("pending", "processing")),
                )
            )
        ).scalars().first()
        if active is not None:
            raise GenerationInProgressError(
                "该文档已有进行中的生成批次",
                existing_batch_id=active,
            )

        frozen = await self._freeze_snapshots(template, model_config)
        selected_ids = _sort_unique_chunk_ids([c.id for c in selected_chunks])

        batch = GenerationBatch(
            document_id=document_id,
            chunk_set_id=chunk_set.id,
            model_config_id=model_config.id,
            prompt_template_id=template.id,
            selected_chunk_ids=selected_ids,
            status="pending",
            total_chunks=len(selected_ids),
            completed_chunks=0,
            summary_json=None,
            created_by=created_by,
            prompt_template_version_id=frozen["prompt_template_version"].id,
            prompt_template_snapshot=frozen["prompt_template_snapshot"],
            prompt_template_sha256=frozen["prompt_template_sha256"],
            model_config_snapshot=frozen["model_config_snapshot"],
            model_config_sha256=frozen["model_config_sha256"],
            renderer_version=frozen["renderer_version"],
            is_legacy=False,
            provenance_status="verified",
            provenance_error_code=None,
        )
        self.db.add(batch)
        await self.db.flush()
        await self.db.refresh(batch)

        # 预渲染 input_prompt 与 hash（同一事务内建立，供 worker 核验）。
        prompt_snapshot = frozen["prompt_template_snapshot"]
        pre_rendered = [
            (
                chunk,
                *rebuild_input_prompt_and_hash(
                    prompt_snapshot, chunk.content, chunk.heading_path
                ),
            )
            for chunk in selected_chunks
        ]

        # parent task（generate_batch handler，entity -> GenerationBatch）。
        parent_payload = {
            "document_id": str(document_id),
            "generation_batch_id": str(batch.id),
        }
        parent_task = await self.task_service.create_task(
            project_id=project_id,
            task_type="generate_batch",
            entity_type="generation_batch",
            entity_id=batch.id,
            created_by=created_by,
            payload=parent_payload,
            handler="generate_batch",
        )

        # 每个 Chunk 一个 child task + 一个 GenerationRun（verified）。
        runs: list[GenerationRun] = []
        for chunk, input_prompt, rendered_hash in pre_rendered:
            run = GenerationRun(
                chunk_id=chunk.id,
                prompt_template_id=template.id,
                model_config_id=model_config.id,
                context_mode="single_chunk",
                input_prompt=input_prompt,
                rendered_prompt_sha256=rendered_hash,
                status="queued",
                generation_batch_id=batch.id,
                is_legacy=False,
                provenance_status="verified",
                provenance_error_code=None,
            )
            self.db.add(run)
            await self.db.flush()
            await self.db.refresh(run)

            child_payload = {
                "chunk_id": str(chunk.id),
                "generation_batch_id": str(batch.id),
                "generation_run_id": str(run.id),
            }
            await self.task_service.create_task(
                project_id=project_id,
                task_type="generate",
                entity_type="chunk",
                entity_id=chunk.id,
                created_by=created_by,
                payload=child_payload,
                handler="generate_single",
                parent_task_id=parent_task.id,
            )
            runs.append(run)

        await self.db.flush()
        return batch, parent_task, runs
