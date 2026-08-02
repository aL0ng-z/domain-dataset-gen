import uuid

from sqlalchemy import select

from app.models.chunk import Chunk
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.config import ChunkProfile
from app.models.document import Document
from app.models.section import Section
from app.workers.execution import ExecutionContext
from splitters import ChunkConfig, get_chunker


async def run_chunk_handler(ctx: ExecutionContext) -> None:
    """chunk_document:v1 handler。

    payload: {"document_id": UUID, "chunk_profile_id": UUID}
    项目链以 task.project_id 为锚点复核；批次循环内调用 checkpoint 支持协作取消。
    """
    payload = ctx.payload
    document_id = uuid.UUID(str(payload["document_id"]))
    chunk_profile_id = uuid.UUID(str(payload["chunk_profile_id"]))
    db = ctx.db

    # 项目链复核：以 task.project_id 为锚点，doc/chunk profile 同项目。
    from app.authz import ProjectChainError, verify_project_chain

    doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
    if doc is None:
        raise ProjectChainError("文档不存在")
    profile = (
        await db.execute(select(ChunkProfile).where(ChunkProfile.id == chunk_profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise ProjectChainError("切分配置不存在")
    await verify_project_chain(
        db,
        ctx.project_id,
        [(Document, document_id), (ChunkProfile, chunk_profile_id)],
        detail="切分任务项目链不一致",
    )
    await ctx.checkpoint()

    source_cleaning_job_id: uuid.UUID | None = None
    if doc.active_clean_version_id is not None:
        active_version = (
            await db.execute(
                select(CleanedDocumentVersion).where(
                    CleanedDocumentVersion.id == doc.active_clean_version_id,
                    CleanedDocumentVersion.document_id == document_id,
                )
            )
        ).scalar_one_or_none()
        if active_version is not None:
            source_cleaning_job_id = active_version.source_cleaning_job_id

    if source_cleaning_job_id is None:
        source_result = await db.execute(
            select(Section.cleaning_job_id)
            .where(
                Section.document_id == document_id,
                Section.status == "accepted",
            )
            .distinct()
        )
        available_sources = list(source_result.scalars().all())
        if len(available_sources) > 1:
            raise ValueError("存在多个已通过的清洗来源，请先终审选定一个清洗版本后再切分")
        if available_sources:
            source_cleaning_job_id = available_sources[0]

    # Get accepted sections from one explicit cleaning source.
    section_query = select(Section).where(
        Section.document_id == document_id,
        Section.status == "accepted",
    )
    if source_cleaning_job_id is not None:
        section_query = section_query.where(Section.cleaning_job_id == source_cleaning_job_id)
    result = await db.execute(section_query.order_by(Section.ordinal))
    sections = list(result.scalars().all())

    if not sections:
        raise ValueError("没有已通过审核的 Section 可供切分")

    chunker = get_chunker(profile.strategy)
    config = ChunkConfig(max_tokens=profile.max_tokens, overlap_tokens=profile.overlap_tokens)

    total_sections = len(sections)
    global_ordinal = 0

    for i, section in enumerate(sections):
        # 批次循环 checkpoint：支持协作取消与 run token 校验。
        if i % 5 == 0 or i == total_sections - 1:
            await ctx.checkpoint()
            await ctx.heartbeat()

        chunk_data_list = chunker.chunk(
            section.cleaned_markdown or section.raw_markdown, section.heading_path, config
        )

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
