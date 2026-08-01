import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import require_project_member
from app.models.chunk import Chunk
from app.models.config import ParserProfile
from app.models.document import Document
from app.models.parse import ParseJob
from app.models.section import CleaningJob, Section
from app.models.user import User
from app.schemas.chunk import ChunkResponse
from app.schemas.cleaned_version import (
    CleanedDocumentVersionResponse,
    CleanedFinalReviewRequest,
)
from app.schemas.document import (
    ChunkRequest,
    CleaningJobResponse,
    CleaningStartRequest,
    CleaningStartResponse,
    DocumentResponse,
    GenerateBatchRequest,
    ParseJobResponse,
    ParseRequest,
)
from app.schemas.section import BulkAssignRequest, SectionResponse
from app.services.clean_version_service import CleanVersionService
from app.services.document_service import DocumentService
from app.services.section_service import SectionService
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse
from storage import get_storage_client

router = APIRouter(prefix="/api/projects/{pid}/documents", tags=["documents"])


async def _create_bg_redis():
    """Create a dedicated Redis connection for background tasks."""
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _get_cleaning_job(db: AsyncSession, document_id: uuid.UUID, cleaning_job_id: uuid.UUID) -> CleaningJob:
    cleaning_job = (
        await db.execute(
            select(CleaningJob).where(
                CleaningJob.id == cleaning_job_id,
                CleaningJob.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    if cleaning_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="清洗任务不存在")
    return cleaning_job


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    pid: uuid.UUID,
    file: UploadFile,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    file_data = await file.read()
    if len(file_data) > 200 * 1024 * 1024:  # 200MB limit
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件大小超过 200MB 限制")

    service = DocumentService(db)
    try:
        return await service.upload(pid, file.filename or "unknown.pdf", file_data, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/", response_model=PaginatedResponse[DocumentResponse])
async def list_documents(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    doc_status: Annotated[str | None, Query(alias="status")] = None,
):
    service = DocumentService(db)
    docs, total = await service.list_documents(pid, page, page_size, doc_status)
    return PaginatedResponse(items=docs, total=total, page=page, page_size=page_size)


@router.get("/{did}", response_model=DocumentResponse)
async def get_document(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = DocumentService(db)
    doc = await service.get_document(did)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return doc


@router.get("/{did}/file")
async def get_document_file(
    pid: uuid.UUID,
    did: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    token: str | None = None,
):
    """Serve PDF file. Supports both Authorization header and ?token= query param (for iframe)."""
    from jose import JWTError
    from jose import jwt as jose_jwt

    # Extract token from Authorization header or query param
    raw_token = token
    if not raw_token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            raw_token = auth_header[7:]

    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败")

    try:
        payload = jose_jwt.decode(raw_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败")
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败") from e

    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    storage = get_storage_client(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_secure,
    )
    try:
        pdf_data = await asyncio.to_thread(
            storage.download_file,
            settings.minio_bucket_documents,
            doc.minio_key,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在") from e

    # RFC 5987: use filename* with UTF-8 encoding for non-ASCII filenames
    from urllib.parse import quote

    encoded_filename = quote(doc.filename)
    return Response(
        content=pdf_data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/{did}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = DocumentService(db)
    if not await service.delete_document(did):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")


@router.post("/{did}/parse", status_code=status.HTTP_202_ACCEPTED)
async def trigger_parse(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: ParseRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    # T03: 先校验 profile 与 registry，再创建 Task/ParseJob；409 前不得下载 PDF。
    from app.models.config import ParserProfile
    from app.services.parse_freeze_service import ParseFreezeError, freeze_parse_job

    profile = (
        await db.execute(select(ParserProfile).where(ParserProfile.id == body.parser_profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析器配置不存在")

    # 预冻结校验（不写入）：endpoint 存在、凭证就绪、无旧网络字段。
    try:
        from app.services.parse_freeze_service import freeze_profile_policy

        freeze_profile_policy(profile, require_credential_ready=True)
    except ParseFreezeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc

    # Reuse app-level Redis connection for publishing task.created event
    task_service = TaskService(db, request.app.state.redis)
    task = await task_service.create_task(pid, "parse", "document", did, current_user.id)

    # 在同一事务内原子冻结 profile/policy 快照并创建 ParseJob。
    parse_job = await freeze_parse_job(
        db, document_id=did, profile=profile, require_credential_ready=True
    )

    doc.status = "parsing"
    await db.commit()

    from app.database import async_session_factory
    from app.workers.parse_worker import run_parse

    async def _run():
        bg_redis = await _create_bg_redis()
        async with async_session_factory() as session:
            try:
                await run_parse(
                    task.id, did, body.parser_profile_id, session, redis=bg_redis, parse_job_id=parse_job.id
                )
                await session.commit()
            except Exception:
                await session.rollback()
            finally:
                await bg_redis.close()

    background_tasks.add_task(_run)
    return {"task_id": str(task.id), "message": "解析任务已创建"}


@router.get("/{did}/parse-jobs", response_model=list[ParseJobResponse])
async def list_parse_jobs(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = DocumentService(db)
    return await service.list_parse_jobs(did)


@router.get("/parse-jobs/{jid}", response_model=ParseJobResponse)
async def get_parse_job(
    pid: uuid.UUID,
    jid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = DocumentService(db)
    job = await service.get_parse_job(jid)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析任务不存在")
    return job


@router.delete("/{did}/parse-jobs/{jid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_parse_job(
    pid: uuid.UUID,
    did: uuid.UUID,
    jid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = DocumentService(db)
    if not await service.delete_parse_job(jid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析任务不存在")


@router.get("/{did}/cleaning-jobs", response_model=list[CleaningJobResponse])
async def list_cleaning_jobs(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    result = await db.execute(
        select(CleaningJob, ParseJob, ParserProfile)
        .join(ParseJob, ParseJob.id == CleaningJob.parse_job_id)
        .join(ParserProfile, ParserProfile.id == ParseJob.parser_profile_id)
        .where(CleaningJob.document_id == did)
        .order_by(CleaningJob.created_at.desc())
    )
    return [
        CleaningJobResponse(
            id=job.id,
            document_id=job.document_id,
            parse_job_id=job.parse_job_id,
            status=job.status,
            started_by=job.started_by,
            parser_profile_id=parse_job.parser_profile_id,
            parser_profile_name=profile.name,
            parse_completed_at=parse_job.completed_at,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
        for job, parse_job, profile in result.all()
    ]


@router.post("/{did}/cleaning/start", response_model=CleaningStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_cleaning(
    pid: uuid.UUID,
    did: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    body: CleaningStartRequest | None = None,
):
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    # Use specified parse job or fall back to latest completed
    parse_job_id = body.parse_job_id if body else None
    if parse_job_id:
        result = await db.execute(
            select(ParseJob).where(
                ParseJob.id == parse_job_id, ParseJob.document_id == did, ParseJob.status == "completed"
            )
        )
        parse_job = result.scalars().first()
        if parse_job is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="指定的解析任务不存在或未完成")
    else:
        result = await db.execute(
            select(ParseJob)
            .where(ParseJob.document_id == did, ParseJob.status == "completed")
            .order_by(ParseJob.created_at.desc())
        )
        parse_job = result.scalars().first()
        if parse_job is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有已完成的解析任务")

    existing_result = await db.execute(
        select(CleaningJob)
        .where(
            CleaningJob.document_id == did,
            CleaningJob.parse_job_id == parse_job.id,
            CleaningJob.status.in_(("queued", "processing", "completed")),
        )
        .order_by(CleaningJob.created_at.desc())
    )
    existing_job = existing_result.scalars().first()
    if existing_job is not None:
        return {
            "task_id": None,
            "cleaning_job_id": existing_job.id,
            "reused": True,
            "message": "已存在该解析结果的清洗工作台",
        }

    cleaning_job = CleaningJob(
        document_id=did,
        parse_job_id=parse_job.id,
        started_by=current_user.id,
        status="queued",
    )
    db.add(cleaning_job)
    await db.flush()

    task_service = TaskService(db, request.app.state.redis)
    task = await task_service.create_task(pid, "clean", "document", did, current_user.id)
    await db.commit()

    from app.database import async_session_factory
    from app.workers.clean_worker import run_clean

    async def _run():
        redis_client = await _create_bg_redis()
        async with async_session_factory() as session:
            try:
                await run_clean(
                    task.id,
                    did,
                    parse_job.id,
                    current_user.id,
                    session,
                    redis=redis_client,
                    cleaning_job_id=cleaning_job.id,
                )
                await session.commit()
            except Exception:
                await session.rollback()
            finally:
                await redis_client.close()

    background_tasks.add_task(_run)
    return {
        "task_id": task.id,
        "cleaning_job_id": cleaning_job.id,
        "reused": False,
        "message": "清洗任务已创建",
    }


@router.get("/{did}/sections", response_model=PaginatedResponse[SectionResponse])
async def list_sections(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    section_status: Annotated[str | None, Query(alias="status")] = None,
    cleaning_job_id: Annotated[uuid.UUID | None, Query()] = None,
):
    from sqlalchemy import func

    offset = (page - 1) * page_size
    base = select(Section).where(Section.document_id == did)
    if cleaning_job_id is not None:
        await _get_cleaning_job(db, did, cleaning_job_id)
        base = base.where(Section.cleaning_job_id == cleaning_job_id)
    if section_status:
        base = base.where(Section.status == section_status)
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(base.order_by(Section.ordinal).offset(offset).limit(page_size))
    items = list(result.scalars().all())
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{did}/chunk", status_code=status.HTTP_202_ACCEPTED)
async def trigger_chunk(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: ChunkRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    task_service = TaskService(db, request.app.state.redis)
    task = await task_service.create_task(pid, "chunk", "document", did, current_user.id)
    doc.status = "chunking"
    await db.commit()

    from app.database import async_session_factory
    from app.workers.chunk_worker import run_chunk

    async def _run():
        redis_client = await _create_bg_redis()
        async with async_session_factory() as session:
            try:
                await run_chunk(task.id, did, body.chunk_profile_id, session, redis=redis_client)
                await session.commit()
            except Exception:
                await session.rollback()
            finally:
                await redis_client.close()

    background_tasks.add_task(_run)
    return {"task_id": str(task.id), "message": "切分任务已创建"}


@router.get("/{did}/chunks", response_model=PaginatedResponse[ChunkResponse])
async def list_chunks(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    chunk_status: Annotated[str | None, Query(alias="status")] = None,
):
    from sqlalchemy import func

    offset = (page - 1) * page_size
    base = select(Chunk).where(Chunk.document_id == did)
    if chunk_status:
        base = base.where(Chunk.status == chunk_status)
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(base.order_by(Chunk.ordinal).offset(offset).limit(page_size))
    items = list(result.scalars().all())
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{did}/generate-batch", status_code=status.HTTP_202_ACCEPTED)
async def trigger_generate_batch(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: GenerateBatchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    task_service = TaskService(db, request.app.state.redis)
    task = await task_service.create_task(pid, "generate_batch", "document", did, current_user.id)
    doc.status = "generating"
    await db.commit()

    from app.database import async_session_factory
    from app.workers.generate_worker import run_generate_batch

    async def _run():
        redis_client = await _create_bg_redis()
        async with async_session_factory() as session:
            try:
                await run_generate_batch(
                    task.id,
                    did,
                    body.prompt_template_id,
                    body.model_config_id,
                    pid,
                    current_user.id,
                    session,
                    redis=redis_client,
                )
                await session.commit()
            except Exception:
                await session.rollback()
            finally:
                await redis_client.close()

    background_tasks.add_task(_run)
    return {"task_id": str(task.id), "message": "批量生成任务已创建"}


@router.post("/{did}/cleaning/assign", status_code=status.HTTP_200_OK)
async def bulk_assign_sections(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: BulkAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    await _get_cleaning_job(db, did, cleaning_job_id)

    service = SectionService(db)
    total_assigned = 0
    for assignment in body.assignments:
        n = await service.bulk_assign(
            assignment.section_ids,
            assignment.assignee_id,
            current_user.id,
            cleaning_job_id=cleaning_job_id,
        )
        if n != len(assignment.section_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="所选章节不属于当前清洗任务")
        total_assigned += n

    # Flip document.clean_status if still not_started
    if doc.clean_status == "not_started":
        doc.clean_status = "section_planned"

    await db.commit()
    return {"assigned": total_assigned}


@router.post(
    "/{did}/cleaning/merge", response_model=CleanedDocumentVersionResponse, status_code=status.HTTP_201_CREATED
)
async def merge_clean_version(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    service = CleanVersionService(db)
    try:
        version = await service.create_merged_version(did, cleaning_job_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    await db.commit()
    return version


@router.post("/{did}/cleaning/final-review", response_model=CleanedDocumentVersionResponse)
async def final_review_clean_version(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: CleanedFinalReviewRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    service = CleanVersionService(db)
    try:
        version = await service.final_review(
            body.version_id,
            current_user.id,
            body.action,
            body.reason,
            body.comment,
            document_id=did,
            cleaning_job_id=cleaning_job_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    await db.commit()
    return version


@router.get("/{did}/cleaning/versions", response_model=list[CleanedDocumentVersionResponse])
async def list_clean_versions(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    service = CleanVersionService(db)
    await _get_cleaning_job(db, did, cleaning_job_id)
    return await service.list_versions(did, cleaning_job_id)
