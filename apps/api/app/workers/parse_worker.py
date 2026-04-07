import asyncio
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.models.parse import ParseJob
from app.models.config import ParserProfile
from app.services.task_service import TaskService
from parsing import get_parser
from storage import get_storage_client


async def run_parse(task_id: uuid.UUID, document_id: uuid.UUID, parser_profile_id: uuid.UUID, db: AsyncSession, redis=None):
    task_service = TaskService(db, redis)
    storage = get_storage_client(settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_secure)

    await task_service.update_status(task_id, "processing", progress=10)

    try:
        doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
        profile = (await db.execute(select(ParserProfile).where(ParserProfile.id == parser_profile_id))).scalar_one()

        parse_job = ParseJob(document_id=document_id, parser_profile_id=parser_profile_id, status="processing")
        db.add(parse_job)
        await db.flush()

        # Download PDF (sync IO → thread pool)
        await task_service.update_status(task_id, "processing", progress=20)
        pdf_data = await asyncio.to_thread(
            storage.download_file, settings.minio_bucket_documents, doc.minio_key,
        )

        # Parse (CPU/IO-bound → thread pool)
        await task_service.update_status(task_id, "processing", progress=40)
        parser_options = profile.parser_options or {}
        parser = get_parser(profile.parser_name, options=parser_options)
        result = await asyncio.to_thread(parser.parse, pdf_data)

        # Upload results (sync IO → thread pool)
        await task_service.update_status(task_id, "processing", progress=70)
        md_key = f"{doc.project_id}/{document_id}/parsed/raw.md"
        await asyncio.to_thread(
            storage.upload_file, settings.minio_bucket_outputs, md_key,
            result.raw_markdown.encode("utf-8"), "text/markdown",
        )

        json_key = f"{doc.project_id}/{document_id}/parsed/structured.json"
        await asyncio.to_thread(
            storage.upload_file, settings.minio_bucket_outputs, json_key,
            json.dumps(result.structured_json).encode("utf-8"), "application/json",
        )

        # Update parse job
        parse_job.status = "completed"
        parse_job.raw_markdown_key = md_key
        parse_job.structured_json_key = json_key
        parse_job.page_mapping = result.page_mapping

        # Update document
        doc.status = "parsed"
        if result.structured_json.get("page_count"):
            doc.page_count = result.structured_json["page_count"]

        await db.flush()
        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        result_job = await db.execute(
            select(ParseJob).where(ParseJob.document_id == document_id).order_by(ParseJob.created_at.desc())
        )
        job = result_job.scalars().first()
        if job:
            job.status = "failed"
            job.error_message = str(e)
        await task_service.update_status(task_id, "failed", error_message=str(e))
