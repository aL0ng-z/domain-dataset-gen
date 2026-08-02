import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
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
    AsyncTaskAcceptedResponse,
    BulkAssignResponse,
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
from app.services.idempotency import idempotent_create_task
from app.services.section_service import SectionService
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse
from storage import get_storage_client

router = APIRouter(prefix="/api/projects/{pid}/documents", tags=["documents"])


async def _get_cleaning_job(
    resolver: ProjectResourceResolver,
    pid: uuid.UUID,
    document_id: uuid.UUID,
    cleaning_job_id: uuid.UUID,
) -> CleaningJob:
    """按项目归属加载清洗任务；文档或任务不属于 pid -> 404。

    父子 ID 组合不成立也统一 404，不泄露哪一个 ID 存在（任务卡 §5.1）。
    """
    if await resolver.document(pid, document_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    cleaning_job = await resolver.cleaning_job(pid, cleaning_job_id)
    if cleaning_job is None or cleaning_job.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="清洗任务不存在")
    return cleaning_job


async def _get_scoped_document(
    resolver: ProjectResourceResolver,
    pid: uuid.UUID,
    did: uuid.UUID,
) -> Document:
    """按项目归属加载文档；不存在或不属于 pid -> 404。"""
    doc = await resolver.document(pid, did)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return doc


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED, operation_id="document_upload")
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


@router.get("/", response_model=PaginatedResponse[DocumentResponse], operation_id="document_list")
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


@router.get("/{did}", response_model=DocumentResponse, operation_id="document_get")
async def get_document(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    doc = await resolver.document(pid, did)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return doc


@router.get("/{did}/file", operation_id="document_get_file")
async def get_document_file(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    """Serve PDF file. Requires Authorization: Bearer access token and project viewer.

    - 仅接受 Authorization 头；query token 一律拒绝（任务卡 §5.2）。
    - did 必须属于 pid，调用者至少为 viewer；校验完成前不访问 MinIO。
    - 响应 private, no-store；任何日志不得打印 Authorization 或文件内容。
    """
    resolver = ProjectResourceResolver(db)
    doc = await resolver.document(pid, did)
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
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/{did}", status_code=status.HTTP_204_NO_CONTENT, operation_id="document_delete")
async def delete_document(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    doc = await resolver.document(pid, did)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    service = DocumentService(db)
    await service.delete_document(did)


@router.post("/{did}/parse", response_model=AsyncTaskAcceptedResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="document_trigger_parse")
async def trigger_parse(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: ParseRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)
    # 请求体引用的 parser profile 必须属于同一项目（任务卡 §5.1）。
    await resolver.ensure_in_project(pid, [(ParserProfile, body.parser_profile_id)])

    # T03: 先校验 profile 与 registry，再创建 Task/ParseJob；409 前不得下载 PDF。
    from app.services.parse_freeze_service import ParseFreezeError

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

    # Idempotency-Key：同 key/同请求返回同 Task；不同请求 409。
    request_payload = {"document_id": str(did), "parser_profile_id": str(body.parser_profile_id)}
    task_service = TaskService(db, request.app.state.redis)

    if idempotency_key:
        task = await idempotent_create_task(
            db,
            task_service=task_service,
            client_key=idempotency_key,
            project_id=pid,
            task_type="parse",
            payload_for_digest=request_payload,
            create=lambda key: _create_parse_task_and_job(
                db, task_service, pid, did, profile, current_user, body, key
            ),
        )
    else:
        task = await _create_parse_task_and_job(
            db, task_service, pid, did, profile, current_user, body, None
        )

    return AsyncTaskAcceptedResponse(task_id=task.id, message="解析任务已创建")


async def _create_parse_task_and_job(
    db, task_service, pid, did, profile, current_user, body, idempotency_key
):
    """冻结 ParseJob 并创建持久 parse Task（同一事务）。"""
    from app.services.parse_freeze_service import freeze_parse_job

    # 在同一事务内原子冻结 profile/policy 快照并创建 ParseJob。
    parse_job = await freeze_parse_job(
        db, document_id=did, profile=profile, require_credential_ready=True
    )

    task = await task_service.create_task(
        pid,
        "parse",
        "document",
        did,
        current_user.id,
        payload={"document_id": str(did), "parse_job_id": str(parse_job.id)},
        handler="parse_document",
        idempotency_key=idempotency_key,
    )
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one()
    doc.status = "parsing"
    # 业务 job + Task 在同一事务提交后由独立 runner 领取（不再调用 background_tasks）。
    await db.commit()
    return task


@router.get("/{did}/parse-jobs", response_model=list[ParseJobResponse], operation_id="document_list_parse_jobs")
async def list_parse_jobs(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)
    service = DocumentService(db)
    return await service.list_parse_jobs(did)


@router.get("/parse-jobs/{jid}", response_model=ParseJobResponse, operation_id="document_get_parse_job")
async def get_parse_job(
    pid: uuid.UUID,
    jid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    job = await resolver.parse_job(pid, jid)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析任务不存在")
    return job


@router.delete("/{did}/parse-jobs/{jid}", status_code=status.HTTP_204_NO_CONTENT, operation_id="document_delete_parse_job")
async def delete_parse_job(
    pid: uuid.UUID,
    did: uuid.UUID,
    jid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)
    # 解析任务必须真实属于该文档（父子组合不成立 -> 404）。
    job = await resolver.parse_job(pid, jid)
    if job is None or job.document_id != did:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析任务不存在")
    service = DocumentService(db)
    await service.delete_parse_job(jid)


@router.get("/{did}/cleaning-jobs", response_model=list[CleaningJobResponse], operation_id="document_list_cleaning_jobs")
async def list_cleaning_jobs(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)
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


@router.post("/{did}/cleaning/start", response_model=CleaningStartResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="document_start_cleaning")
async def start_cleaning(
    pid: uuid.UUID,
    did: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    body: CleaningStartRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)

    # Use specified parse job or fall back to latest completed
    parse_job_id = body.parse_job_id if body else None
    if parse_job_id:
        # 父子组合校验：解析任务必须真实属于该文档（否则 404，不泄露 ID 存在性）。
        scoped_job = await resolver.parse_job(pid, parse_job_id)
        if scoped_job is None or scoped_job.document_id != did:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指定的解析任务不存在")
        if scoped_job.status != "completed":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="指定的解析任务未完成")
        parse_job = scoped_job
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
    clean_payload = {
        "document_id": str(did),
        "parse_job_id": str(parse_job.id),
        "cleaning_job_id": str(cleaning_job.id),
    }
    if idempotency_key:
        task = await idempotent_create_task(
            db,
            task_service=task_service,
            client_key=idempotency_key,
            project_id=pid,
            task_type="clean",
            payload_for_digest=clean_payload,
            create=lambda key: _create_clean_task(
                task_service, pid, did, current_user, clean_payload, key
            ),
        )
    else:
        task = await _create_clean_task(
            task_service, pid, did, current_user, clean_payload, None
        )
    # 业务 job + Task 同一事务提交后由独立 runner 领取。
    await db.commit()

    return {
        "task_id": task.id,
        "cleaning_job_id": cleaning_job.id,
        "reused": False,
        "message": "清洗任务已创建",
    }


async def _create_clean_task(task_service, pid, did, current_user, payload, idempotency_key):
    """创建持久 clean Task（同一事务内，不自行 commit）。"""
    return await task_service.create_task(
        pid,
        "clean",
        "document",
        did,
        current_user.id,
        payload=payload,
        handler="clean_document",
        idempotency_key=idempotency_key,
    )


@router.get("/{did}/sections", response_model=PaginatedResponse[SectionResponse], operation_id="document_list_sections")
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

    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)
    offset = (page - 1) * page_size
    base = select(Section).where(Section.document_id == did)
    if cleaning_job_id is not None:
        await _get_cleaning_job(resolver, pid, did, cleaning_job_id)
        base = base.where(Section.cleaning_job_id == cleaning_job_id)
    if section_status:
        base = base.where(Section.status == section_status)
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(base.order_by(Section.ordinal).offset(offset).limit(page_size))
    items = list(result.scalars().all())
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{did}/chunk", response_model=AsyncTaskAcceptedResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="document_trigger_chunk")
async def trigger_chunk(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: ChunkRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    resolver = ProjectResourceResolver(db)
    doc = await _get_scoped_document(resolver, pid, did)
    from app.models.config import ChunkProfile

    await resolver.ensure_in_project(pid, [(ChunkProfile, body.chunk_profile_id)])

    task_service = TaskService(db, request.app.state.redis)
    chunk_payload = {
        "document_id": str(did),
        "chunk_profile_id": str(body.chunk_profile_id),
    }
    if idempotency_key:
        task = await idempotent_create_task(
            db,
            task_service=task_service,
            client_key=idempotency_key,
            project_id=pid,
            task_type="chunk",
            payload_for_digest=chunk_payload,
            create=lambda key: task_service.create_task(
                pid, "chunk", "document", did, current_user.id,
                payload=chunk_payload, handler="chunk_document", idempotency_key=key,
            ),
        )
    else:
        task = await task_service.create_task(
            pid, "chunk", "document", did, current_user.id,
            payload=chunk_payload, handler="chunk_document",
        )
    doc.status = "chunking"
    # 业务 job + Task 同一事务提交后由独立 runner 领取。
    await db.commit()

    return AsyncTaskAcceptedResponse(task_id=task.id, message="切分任务已创建")


@router.get("/{did}/chunks", response_model=PaginatedResponse[ChunkResponse], operation_id="document_list_chunks")
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

    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)
    offset = (page - 1) * page_size
    base = select(Chunk).where(Chunk.document_id == did)
    if chunk_status:
        base = base.where(Chunk.status == chunk_status)
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(base.order_by(Chunk.ordinal).offset(offset).limit(page_size))
    items = list(result.scalars().all())
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{did}/generate-batch", response_model=AsyncTaskAcceptedResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="document_trigger_generate_batch")
async def trigger_generate_batch(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: GenerateBatchRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    resolver = ProjectResourceResolver(db)
    doc = await _get_scoped_document(resolver, pid, did)
    from app.models.config import ModelConfig
    from app.models.prompt_template import PromptTemplate

    await resolver.ensure_in_project(
        pid,
        [
            (PromptTemplate, body.prompt_template_id),
            (ModelConfig, body.model_config_id),
        ],
    )

    task_service = TaskService(db, request.app.state.redis)
    batch_payload = {
        "document_id": str(did),
        "prompt_template_id": str(body.prompt_template_id),
        "model_config_id": str(body.model_config_id),
        "created_by": str(current_user.id),
    }
    if idempotency_key:
        task = await idempotent_create_task(
            db,
            task_service=task_service,
            client_key=idempotency_key,
            project_id=pid,
            task_type="generate_batch",
            payload_for_digest=batch_payload,
            create=lambda key: task_service.create_task(
                pid, "generate_batch", "document", did, current_user.id,
                payload=batch_payload, handler="generate_batch", idempotency_key=key,
            ),
        )
    else:
        task = await task_service.create_task(
            pid, "generate_batch", "document", did, current_user.id,
            payload=batch_payload, handler="generate_batch",
        )
    doc.status = "generating"
    # 业务 job + Task 同一事务提交后由独立 runner 领取。
    await db.commit()

    return AsyncTaskAcceptedResponse(task_id=task.id, message="批量生成任务已创建")


@router.post("/{did}/cleaning/assign", response_model=BulkAssignResponse, status_code=status.HTTP_200_OK, operation_id="document_bulk_assign_sections")
async def bulk_assign_sections(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: BulkAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    resolver = ProjectResourceResolver(db)
    doc = await _get_scoped_document(resolver, pid, did)
    await _get_cleaning_job(resolver, pid, did, cleaning_job_id)

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
    return BulkAssignResponse(assigned=total_assigned)


@router.post(
    "/{did}/cleaning/merge", response_model=CleanedDocumentVersionResponse, status_code=status.HTTP_201_CREATED, operation_id="document_merge_clean_version"
)
async def merge_clean_version(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    resolver = ProjectResourceResolver(db)
    await _get_cleaning_job(resolver, pid, did, cleaning_job_id)
    service = CleanVersionService(db)
    try:
        version = await service.create_merged_version(did, cleaning_job_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    await db.commit()
    return version


@router.post("/{did}/cleaning/final-review", response_model=CleanedDocumentVersionResponse, operation_id="document_final_review_clean_version")
async def final_review_clean_version(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: CleanedFinalReviewRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    resolver = ProjectResourceResolver(db)
    await _get_cleaning_job(resolver, pid, did, cleaning_job_id)
    # 请求体 version_id 必须真实属于该文档与清洗任务（父子组合不成立 -> 404）。
    version = await resolver.cleaned_version(pid, body.version_id)
    if version is None or version.document_id != did or version.source_cleaning_job_id != cleaning_job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="合并版本不存在")

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


@router.get("/{did}/cleaning/versions", response_model=list[CleanedDocumentVersionResponse], operation_id="document_list_clean_versions")
async def list_clean_versions(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    cleaning_job_id: Annotated[uuid.UUID, Query()],
):
    resolver = ProjectResourceResolver(db)
    await _get_cleaning_job(resolver, pid, did, cleaning_job_id)
    service = CleanVersionService(db)
    return await service.list_versions(did, cleaning_job_id)
