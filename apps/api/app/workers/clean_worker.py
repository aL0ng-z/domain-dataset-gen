import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.parse import ParseJob
from app.models.section import CleaningJob, Section
from app.services.task_service import TaskService
from app.config import settings
from cleaning import split_into_sections
from storage import get_storage_client


async def run_clean(task_id: uuid.UUID, document_id: uuid.UUID, parse_job_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession, redis=None):
    task_service = TaskService(db, redis)
    storage = get_storage_client(settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_secure)

    await task_service.update_status(task_id, "processing", progress=10)

    try:
        doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
        parse_job = (await db.execute(select(ParseJob).where(ParseJob.id == parse_job_id))).scalar_one()

        # Download raw markdown
        await task_service.update_status(task_id, "processing", progress=30)
        md_bytes = storage.download_file(settings.minio_bucket_outputs, parse_job.raw_markdown_key)
        raw_markdown = md_bytes.decode("utf-8")

        # Split into sections
        await task_service.update_status(task_id, "processing", progress=50)
        section_data_list = split_into_sections(raw_markdown)

        # Create cleaning job
        cleaning_job = CleaningJob(document_id=document_id, parse_job_id=parse_job_id, started_by=user_id, status="completed")
        db.add(cleaning_job)
        await db.flush()

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

        doc.status = "cleaning"
        await db.flush()
        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        await task_service.update_status(task_id, "failed", error_message=str(e))
