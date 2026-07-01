import asyncio
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.config import ParserProfile
from app.models.document import Document
from app.models.parse import ParseJob
from app.services.mineru_local_service_manager import ensure_mineru_local_service
from app.services.paddleocr_local_service_manager import ensure_paddleocr_local_service
from app.services.task_service import TaskService
from parsing import get_parser
from storage import get_storage_client


def _parser_options_with_credentials(parser_name: str, stored_options: dict | None) -> dict:
    options = dict(stored_options or {})
    token_setting = {
        "mineru": settings.mineru_api_token,
        "paddleocr": settings.paddleocr_api_token,
    }.get(parser_name)
    if token_setting is not None and token_setting.get_secret_value().strip():
        options["api_key"] = token_setting.get_secret_value().strip()
    return options


async def run_parse(
    task_id: uuid.UUID,
    document_id: uuid.UUID,
    parser_profile_id: uuid.UUID,
    db: AsyncSession,
    redis=None,
    parse_job_id: uuid.UUID | None = None,
):
    task_service = TaskService(db, redis)
    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_secure
    )
    parse_job: ParseJob | None = None

    await task_service.update_status(task_id, "processing", progress=10)

    try:
        doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
        profile = (await db.execute(select(ParserProfile).where(ParserProfile.id == parser_profile_id))).scalar_one()

        # Use existing ParseJob if provided, otherwise create new one
        if parse_job_id:
            parse_job = (await db.execute(select(ParseJob).where(ParseJob.id == parse_job_id))).scalar_one()
        else:
            parse_job = ParseJob(
                document_id=document_id,
                parser_profile_id=parser_profile_id,
            )
            db.add(parse_job)
            await db.flush()

        parse_job.status = "processing"
        parse_job.started_at = datetime.now(UTC)
        await db.flush()

        # Download PDF (sync IO → thread pool)
        await task_service.update_status(task_id, "processing", progress=20)
        pdf_data = await asyncio.to_thread(
            storage.download_file,
            settings.minio_bucket_documents,
            doc.minio_key,
        )

        # Parse (CPU/IO-bound → thread pool)
        await task_service.update_status(task_id, "processing", progress=40)
        parser_options = _parser_options_with_credentials(profile.parser_name, profile.parser_options)
        if profile.parser_name == "mineru_local_service":
            await asyncio.to_thread(ensure_mineru_local_service, parser_options)
        if profile.parser_name == "paddleocr_local_service":
            await asyncio.to_thread(ensure_paddleocr_local_service, parser_options)
        parser = get_parser(profile.parser_name, options=parser_options)
        result = await asyncio.to_thread(parser.parse, pdf_data)

        # Upload results (sync IO → thread pool)
        await task_service.update_status(task_id, "processing", progress=70)
        md_key = f"{doc.project_id}/{document_id}/parsed/{parse_job.id}/raw.md"
        await asyncio.to_thread(
            storage.upload_file,
            settings.minio_bucket_outputs,
            md_key,
            result.raw_markdown.encode("utf-8"),
            "text/markdown",
        )

        json_key = f"{doc.project_id}/{document_id}/parsed/{parse_job.id}/structured.json"
        await asyncio.to_thread(
            storage.upload_file,
            settings.minio_bucket_outputs,
            json_key,
            json.dumps(result.structured_json, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json",
        )

        # Update parse job
        parse_job.status = "completed"
        parse_job.completed_at = datetime.now(UTC)
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
        job = parse_job
        if job is None and parse_job_id is not None:
            job = (await db.execute(select(ParseJob).where(ParseJob.id == parse_job_id))).scalar_one_or_none()
        if job is None:
            result_job = await db.execute(
                select(ParseJob).where(ParseJob.document_id == document_id).order_by(ParseJob.created_at.desc())
            )
            job = result_job.scalars().first()
        if job:
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now(UTC)
        await task_service.update_status(task_id, "failed", error_message=str(e))
