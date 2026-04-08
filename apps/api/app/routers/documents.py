import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_project_member
from app.models.document import Document
from app.models.parse import ParseJob
from app.models.section import Section
from app.models.chunk import Chunk
from app.models.user import User
from app.schemas.document import (
    ChunkRequest, DocumentResponse, GenerateBatchRequest, ParseJobResponse, ParseRequest,
)
from app.schemas.section import SectionResponse
from app.schemas.chunk import ChunkResponse
from app.config import settings
from app.services.document_service import DocumentService
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse
from storage import get_storage_client

router = APIRouter(prefix="/api/projects/{pid}/documents", tags=["documents"])


async def _create_bg_redis():
    """Create a dedicated Redis connection for background tasks."""
    import redis.asyncio as aioredis
    return aioredis.from_url(settings.redis_url, decode_responses=True)


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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/", response_model=PaginatedResponse[DocumentResponse])
async def list_documents(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doc_status: str | None = Query(None, alias="status"),
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
    token: str | None = Query(None),
):
    """Serve PDF file. Supports both Authorization header and ?token= query param (for iframe)."""
    from jose import JWTError, jwt as jose_jwt

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
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败")

    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )
    try:
        pdf_data = await asyncio.to_thread(
            storage.download_file, settings.minio_bucket_documents, doc.minio_key,
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")

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

    # Reuse app-level Redis connection for publishing task.created event
    task_service = TaskService(db, request.app.state.redis)
    task = await task_service.create_task(pid, "parse", "document", did, current_user.id)

    # Create ParseJob immediately so frontend can see it right away
    parse_job = ParseJob(
        document_id=did, parser_profile_id=body.parser_profile_id,
        status="queued",
    )
    db.add(parse_job)

    doc.status = "parsing"
    await db.commit()

    from app.workers.parse_worker import run_parse
    from app.database import async_session_factory

    async def _run():
        bg_redis = await _create_bg_redis()
        async with async_session_factory() as session:
            try:
                await run_parse(task.id, did, body.parser_profile_id, session, redis=bg_redis, parse_job_id=parse_job.id)
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


@router.post("/{did}/cleaning/start", status_code=status.HTTP_202_ACCEPTED)
async def start_cleaning(
    pid: uuid.UUID,
    did: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    body: dict | None = None,
):
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    # Use specified parse job or fall back to latest completed
    parse_job_id = (body or {}).get("parse_job_id")
    if parse_job_id:
        result = await db.execute(
            select(ParseJob).where(ParseJob.id == uuid.UUID(parse_job_id), ParseJob.document_id == did, ParseJob.status == "completed")
        )
        parse_job = result.scalars().first()
        if parse_job is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="指定的解析任务不存在或未完成")
    else:
        result = await db.execute(
            select(ParseJob).where(ParseJob.document_id == did, ParseJob.status == "completed").order_by(ParseJob.created_at.desc())
        )
        parse_job = result.scalars().first()
        if parse_job is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有已完成的解析任务")

    task_service = TaskService(db, request.app.state.redis)
    task = await task_service.create_task(pid, "clean", "document", did, current_user.id)
    await db.commit()

    from app.workers.clean_worker import run_clean
    from app.database import async_session_factory

    async def _run():
        redis_client = await _create_bg_redis()
        async with async_session_factory() as session:
            try:
                await run_clean(task.id, did, parse_job.id, current_user.id, session, redis=redis_client)
                await session.commit()
            except Exception:
                await session.rollback()
            finally:
                await redis_client.close()

    background_tasks.add_task(_run)
    return {"task_id": str(task.id), "message": "清洗任务已创建"}


@router.get("/{did}/sections", response_model=PaginatedResponse[SectionResponse])
async def list_sections(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    section_status: str | None = Query(None, alias="status"),
):
    from sqlalchemy import func

    offset = (page - 1) * page_size
    base = select(Section).where(Section.document_id == did)
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

    from app.workers.chunk_worker import run_chunk
    from app.database import async_session_factory

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
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    chunk_status: str | None = Query(None, alias="status"),
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

    from app.workers.generate_worker import run_generate_batch
    from app.database import async_session_factory

    async def _run():
        redis_client = await _create_bg_redis()
        async with async_session_factory() as session:
            try:
                await run_generate_batch(
                    task.id, did, body.prompt_template_id, body.model_config_id,
                    pid, current_user.id, session, redis=redis_client,
                )
                await session.commit()
            except Exception:
                await session.rollback()
            finally:
                await redis_client.close()

    background_tasks.add_task(_run)
    return {"task_id": str(task.id), "message": "批量生成任务已创建"}
