import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.section import Section
from app.models.chunk import Chunk
from app.models.config import ChunkProfile
from app.services.task_service import TaskService
from splitters import get_chunker, ChunkConfig


async def run_chunk(task_id: uuid.UUID, document_id: uuid.UUID, chunk_profile_id: uuid.UUID, db: AsyncSession, redis=None):
    task_service = TaskService(db, redis)

    await task_service.update_status(task_id, "processing", progress=10)

    try:
        doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
        profile = (await db.execute(select(ChunkProfile).where(ChunkProfile.id == chunk_profile_id))).scalar_one()

        # Get accepted sections
        result = await db.execute(
            select(Section).where(
                Section.document_id == document_id,
                Section.status == "accepted",
            ).order_by(Section.ordinal)
        )
        sections = list(result.scalars().all())

        if not sections:
            raise ValueError("没有已通过审核的 Section 可供切分")

        chunker = get_chunker(profile.strategy)
        config = ChunkConfig(max_tokens=profile.max_tokens, overlap_tokens=profile.overlap_tokens)

        total_sections = len(sections)
        global_ordinal = 0

        for i, section in enumerate(sections):
            await task_service.update_status(task_id, "processing", progress=10 + int(80 * i / total_sections))

            chunk_data_list = chunker.chunk(section.cleaned_markdown or section.raw_markdown, section.heading_path, config)

            for cdata in chunk_data_list:
                chunk = Chunk(
                    section_id=section.id,
                    document_id=document_id,
                    ordinal=global_ordinal,
                    heading_path=cdata.heading_path,
                    content=cdata.content,
                    source_pages=cdata.source_pages,
                    token_count=cdata.token_count,
                    status="ready",
                )
                db.add(chunk)
                global_ordinal += 1

        doc.status = "chunked"
        await db.flush()
        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        await task_service.update_status(task_id, "failed", error_message=str(e))
