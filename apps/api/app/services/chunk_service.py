import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.generation import GenerationRun
from app.models.task import Task


class ChunkService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None:
        result = await self.db.execute(select(Chunk).where(Chunk.id == chunk_id))
        return result.scalar_one_or_none()

    async def update_chunk(self, chunk_id: uuid.UUID, **kwargs) -> Chunk | None:
        chunk = await self.get_chunk(chunk_id)
        if chunk is None:
            return None
        for key, value in kwargs.items():
            if value is not None:
                setattr(chunk, key, value)
        await self.db.flush()
        await self.db.refresh(chunk)
        return chunk

    async def list_by_document(
        self, document_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Chunk], int]:
        offset = (page - 1) * page_size

        count_result = await self.db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        )
        total = count_result.scalar() or 0

        result = await self.db.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.ordinal)
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_by_section(
        self, section_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Chunk], int]:
        offset = (page - 1) * page_size

        count_result = await self.db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.section_id == section_id)
        )
        total = count_result.scalar() or 0

        result = await self.db.execute(
            select(Chunk)
            .where(Chunk.section_id == section_id)
            .order_by(Chunk.ordinal)
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def create_generate_task(
        self,
        chunk_id: uuid.UUID,
        prompt_template_id: uuid.UUID,
        model_config_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> tuple[Task, GenerationRun]:
        """Create a generation task and run for a chunk. Returns (task, generation_run)."""
        chunk = await self.get_chunk(chunk_id)
        if chunk is None:
            raise ValueError("Chunk不存在")

        # Resolve project_id from chunk -> document
        doc_result = await self.db.execute(
            select(Document.project_id).where(Document.id == chunk.document_id)
        )
        project_id = doc_result.scalar_one_or_none()
        if project_id is None:
            raise ValueError("关联文档不存在")

        # Create generation run
        gen_run = GenerationRun(
            chunk_id=chunk_id,
            prompt_template_id=prompt_template_id,
            model_config_id=model_config_id,
            context_mode="single_chunk",
            status="queued",
        )
        self.db.add(gen_run)
        await self.db.flush()

        # 创建持久化 Task（handler=generate_single，payload 只存资源 id）。
        from app.services.task_service import TaskService

        task_service = TaskService(self.db)
        task = await task_service.create_task(
            project_id=project_id,
            task_type="generate",
            entity_type="chunk",
            entity_id=chunk_id,
            created_by=created_by,
            payload={
                "chunk_id": str(chunk_id),
                "prompt_template_id": str(prompt_template_id),
                "model_config_id": str(model_config_id),
            },
            handler="generate_single",
        )

        # Update chunk status
        chunk.status = "generating"
        await self.db.flush()

        await self.db.refresh(gen_run)
        await self.db.refresh(task)
        return task, gen_run
