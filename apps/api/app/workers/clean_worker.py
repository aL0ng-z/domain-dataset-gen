import asyncio
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.models.document import Document
from app.models.parse import ParseJob
from app.models.section import CleaningJob, Section
from app.workers.execution import ExecutionContext
from cleaning import split_into_sections
from storage import get_storage_client


async def run_clean_handler(ctx: ExecutionContext) -> None:
    """clean_document:v1 handler。

    payload: {"document_id": UUID, "parse_job_id": UUID, "cleaning_job_id": UUID,
              "started_by": UUID}
    项目链以 task.project_id 为锚点复核；下载/分节/建 Section 前调用 checkpoint。
    """
    payload = ctx.payload
    document_id = uuid.UUID(str(payload["document_id"]))
    parse_job_id = uuid.UUID(str(payload["parse_job_id"]))
    cleaning_job_id = uuid.UUID(str(payload["cleaning_job_id"]))
    db = ctx.db

    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_secure
    )

    # 项目链复核：以 task.project_id 为锚点，doc/parse_job 同项目。
    from app.authz import ProjectChainError, verify_project_chain

    doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
    if doc is None:
        raise ProjectChainError("文档不存在")
    parse_job = (await db.execute(select(ParseJob).where(ParseJob.id == parse_job_id))).scalar_one_or_none()
    if parse_job is None:
        raise ProjectChainError("解析任务不存在")
    await verify_project_chain(
        db,
        ctx.project_id,
        [(Document, document_id), (ParseJob, parse_job_id)],
        detail="清洗任务项目链不一致",
    )
    await ctx.checkpoint()

    cleaning_job = (
        await db.execute(
            select(CleaningJob).where(
                CleaningJob.id == cleaning_job_id,
                CleaningJob.document_id == document_id,
                CleaningJob.parse_job_id == parse_job_id,
            )
        )
    ).scalar_one_or_none()
    if cleaning_job is None:
        # 项目链已保证 doc/parse_job 同项目；cleaning_job 必须匹配该 doc+parse_job。
        raise ProjectChainError("清洗任务不存在")
    if cleaning_job.task_id is not None and cleaning_job.task_id != ctx.task_id:
        from app.workers.execution import TaskProtocolError
        raise TaskProtocolError("清洗任务已由其他 Task 接管")
    cleaning_job.status = "processing"
    await db.flush()
    await ctx.checkpoint()

    # Download parser artifacts
    md_bytes = await asyncio.to_thread(storage.download_file, settings.minio_bucket_outputs, parse_job.raw_markdown_key)
    raw_markdown = md_bytes.decode("utf-8")
    structured_json: dict = {}
    if parse_job.structured_json_key:
        json_bytes = await asyncio.to_thread(storage.download_file, settings.minio_bucket_outputs, parse_job.structured_json_key)
        structured_json = json.loads(json_bytes.decode("utf-8"))
    await ctx.checkpoint()

    # Split into sections
    section_data_list = await asyncio.to_thread(
        split_into_sections, raw_markdown,
        page_mapping=parse_job.page_mapping if isinstance(parse_job.page_mapping, list) else None,
        structured_json=structured_json if isinstance(structured_json, dict) else None,
    )
    await ctx.checkpoint()

    # Create sections
    for sdata in section_data_list:
        section = Section(
            cleaning_job_id=cleaning_job.id,
            document_id=document_id,
            ordinal=sdata.ordinal,
            heading_path=sdata.heading_path,
            source_pages=sdata.source_pages,
            raw_markdown=sdata.raw_markdown,
            cleaned_markdown=sdata.raw_markdown,  # Initialize with raw
            status="draft",
        )
        db.add(section)

    cleaning_job.status = "completed"
    cleaning_job.completed_at = datetime.now(UTC)
    doc.status = "cleaning"
    await db.flush()
