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
from app.generation.snapshot import SnapshotUnsafeError
from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.config import ParserProfile
from app.models.document import Document
from app.models.parse import ParseJob
from app.models.section import CleaningJob, Section
from app.models.user import User
from app.schemas.chunk import ChunkResponse
from app.schemas.chunk_set import ChunkTriggerResponse
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
    ParseJobResponse,
    ParseRequest,
)
from app.schemas.generation import (
    GenerateAcceptedResponse,
    GenerateBatchRequest,
)
from app.schemas.section import BulkAssignRequest, SectionResponse
from app.services.chunk_set_service import (
    ChunkSetService,
    CleanVersionNotReadyError,
    CleanVersionStaleError,
    build_chunk_idempotency_key,
    chunk_client_key,
)
from app.services.clean_version_service import CleanVersionService
from app.services.document_service import DocumentInUseError, DocumentService
from app.services.generation_service import (
    GenerationConfigUnavailableError,
    GenerationIdempotencyConflictError,
    GenerationInProgressError,
    GenerationOrchestrationService,
    GenerationSourceNotReadyError,
    PromptTemplateVersionMaterializeError,
    build_generation_request_fingerprint,
)
from app.services.idempotency import idempotent_create_task
from app.services.section_service import SectionService
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import ErrorResponse, PaginatedResponse
from storage import get_storage_client

router = APIRouter(prefix="/api/projects/{pid}/documents", tags=["documents"])

MAX_UPLOAD_SIZE = 200 * 1024 * 1024
UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


def _gen_http_error(status_code: int, code: str, message: str, context: dict | None = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ErrorResponse(code=code, message=message, context=context).model_dump(),
    )


def _chunk_response_with_version(chunk, version_map: dict) -> ChunkResponse:
    """构建带 chunk_set_version 的 ChunkResponse（响应合同 §5）。"""
    resp = ChunkResponse.model_validate(chunk)
    resp.chunk_set_version = version_map.get(chunk.chunk_set_id)
    return resp


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
    # Starlette 在已知 multipart 长度时会提供 size；先快速拒绝，仍通过受限读取
    # 覆盖未知/不可信长度，绝不调用无参数 file.read()。
    if file.size is not None and file.size > MAX_UPLOAD_SIZE:
        await file.close()
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件大小超过 200MB 限制")

    filename = file.filename or "unknown.pdf"
    file_data = bytearray()
    try:
        while len(file_data) <= MAX_UPLOAD_SIZE:
            remaining = MAX_UPLOAD_SIZE + 1 - len(file_data)
            chunk = await file.read(min(UPLOAD_READ_CHUNK_SIZE, remaining))
            if not chunk:
                break
            file_data.extend(chunk)
        if len(file_data) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件大小超过 200MB 限制")
    finally:
        await file.close()

    service = DocumentService(db)
    try:
        return await service.upload(pid, filename, bytes(file_data), current_user.id)
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
    try:
        await service.delete_document(did)
    except DocumentInUseError as exc:
        raise _gen_http_error(
            status.HTTP_409_CONFLICT,
            "DOCUMENT_IN_USE",
            str(exc),
            {"document_id": str(did)},
        ) from exc


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
    try:
        await service.delete_parse_job(jid)
    except DocumentInUseError as exc:
        raise _gen_http_error(
            status.HTTP_409_CONFLICT,
            "DOCUMENT_IN_USE",
            str(exc),
            {"document_id": str(did), "parse_job_id": str(jid)},
        ) from exc


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
            task_id=job.task_id,
            error_code=job.error_code,
            error_message=job.error_message,
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

    # Serialize same-document creation so double clicks share one live workbench.
    await db.execute(select(Document).where(Document.id == did).with_for_update())
    from sqlalchemy import or_

    from app.models.task import Task

    existing_result = await db.execute(
        select(CleaningJob)
        .where(
            CleaningJob.document_id == did,
            CleaningJob.parse_job_id == parse_job.id,
            or_(CleaningJob.status == "completed", (
                CleaningJob.status.in_(("queued", "processing"))
                & CleaningJob.task_id.in_(select(Task.id).where(
                    Task.status.in_(("queued", "processing")),
                ))
            )),
        )
        .order_by(CleaningJob.created_at.desc())
    )
    existing_job = existing_result.scalars().first()
    if existing_job is not None:
        return {
            "task_id": existing_job.task_id if existing_job.status != "completed" else None,
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


@router.post("/{did}/chunk", response_model=ChunkTriggerResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="document_trigger_chunk")
async def trigger_chunk(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: ChunkRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """T06 版本化切分：ChunkSet + T07 Task 同一事务创建；幂等/并发 409 语义。

    - 请求头必须含 Idempotency-Key（缺失/超长 -> 422）。
    - cleaned_version_id 省略取 Document.active；显式必须等于 active，否则 409。
    - 相同 key/相同规范请求重放返回同一对象（reused:true）；摘要不同 409
      IDEMPOTENCY_KEY_REUSED；另一个不同 key 切分进行中 409 CHUNK_RUN_IN_PROGRESS。
    """
    if not idempotency_key or len(idempotency_key) > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key 头缺失或超过 100 字符",
        )

    resolver = ProjectResourceResolver(db)
    await _get_scoped_document(resolver, pid, did)
    from app.models.config import ChunkProfile

    await resolver.ensure_in_project(pid, [(ChunkProfile, body.chunk_profile_id)])
    profile = (
        await db.execute(select(ChunkProfile).where(ChunkProfile.id == body.chunk_profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="切分配置不存在")

    service = ChunkSetService(db)
    # 构造复合幂等键：client_key + 规范化请求摘要。
    request_for_digest = {
        "chunk_profile_id": str(body.chunk_profile_id),
        "cleaned_version_id": str(body.cleaned_version_id) if body.cleaned_version_id else None,
    }
    composite_key = build_chunk_idempotency_key(idempotency_key, request_for_digest)

    # 同一文档已有其它 key 的活跃切分：409 CHUNK_RUN_IN_PROGRESS（部分唯一索引兜底）。
    # 同 key 的活跃 set 直接复用（下放 create_chunk_set_task 锁内复核返回既有对象）。
    active = (
        await db.execute(
            select(ChunkSet)
            .where(
                ChunkSet.document_id == did,
                ChunkSet.status.in_(("pending", "processing")),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        active_client_key = chunk_client_key(active.idempotency_key or "")
        if active_client_key != idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CHUNK_RUN_IN_PROGRESS", "message": "该文档已有切分任务进行中"},
            )

    # 同 key 不同摘要：409 IDEMPOTENCY_KEY_REUSED（幂等键已被不同的请求复用）。
    if idempotency_key:
        existing_by_client = (
            await db.execute(
                select(ChunkSet).where(
                    ChunkSet.document_id == did,
                    ChunkSet.idempotency_key.like(f"{idempotency_key}:%"),
                )
            )
        ).scalar_one_or_none()
        if existing_by_client is not None and existing_by_client.idempotency_key != composite_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "幂等键已被不同的请求复用"},
            )

    try:
        clean_version = await service.resolve_clean_version(did, body.cleaned_version_id)
    except CleanVersionNotReadyError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CLEAN_VERSION_NOT_READY", "message": str(e)},
        ) from e
    except CleanVersionStaleError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CLEAN_VERSION_STALE",
                "message": str(e),
                "context": {"target_version": e.target_version, "active_version": e.active_version},
            },
        ) from e

    chunk_set, task, reused = await service.create_chunk_set_task(
        project_id=pid,
        document_id=did,
        profile=profile,
        clean_version=clean_version,
        created_by=current_user.id,
        idempotency_key=composite_key,
    )
    # 业务 ChunkSet + Task 同一事务提交后由独立 runner 领取。
    await db.commit()
    await db.refresh(chunk_set)

    return ChunkTriggerResponse(
        task_id=task.id,
        chunk_set_id=chunk_set.id,
        reused=reused,
        status=chunk_set.status,
        message="复用既有切分任务" if reused else "切分任务已创建",
    )


@router.get("/{did}/chunks", response_model=PaginatedResponse[ChunkResponse], operation_id="document_list_chunks")
async def list_chunks(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    chunk_status: Annotated[str | None, Query(alias="status")] = None,
    chunk_set_id: Annotated[uuid.UUID | None, Query()] = None,
    section_id: Annotated[uuid.UUID | None, Query()] = None,
):
    """Chunk 列表：默认限定 active_chunk_set_id；可显式传 chunk_set_id 查看历史。"""
    from sqlalchemy import func

    from app.models.chunk_set import ChunkSet

    resolver = ProjectResourceResolver(db)
    doc = await _get_scoped_document(resolver, pid, did)

    # 解析目标集合：默认 active；显式 chunk_set_id 必须属于该文档（否则 404）。
    target_set_id = chunk_set_id
    if target_set_id is None:
        target_set_id = doc.active_chunk_set_id
    else:
        cs = await resolver.chunk_set(pid, chunk_set_id)
        if cs is None or cs.document_id != did:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="切分集合不存在")

    base = select(Chunk).where(Chunk.document_id == did)
    if target_set_id is not None:
        base = base.where(Chunk.chunk_set_id == target_set_id)
    if chunk_status:
        base = base.where(Chunk.status == chunk_status)
    if section_id:
        base = base.where(Chunk.section_id == section_id)

    offset = (page - 1) * page_size
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(base.order_by(Chunk.ordinal).offset(offset).limit(page_size))
    items = list(result.scalars().all())

    # 补充 chunk_set_version（响应合同：GET /chunks 默认只返回 active set）。
    chunk_sets = (
        await db.execute(
            select(ChunkSet).where(ChunkSet.document_id == did)
        )
    ).scalars().all()
    version_map = {cs.id: cs.version for cs in chunk_sets}
    resp_items = []
    for c in items:
        resp_items.append(_chunk_response_with_version(c, version_map))
    return PaginatedResponse(items=resp_items, total=total, page=page, page_size=page_size)


@router.post("/{did}/generate-batch", response_model=GenerateAcceptedResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="document_trigger_generate_batch")
async def trigger_generate_batch(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: GenerateBatchRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=100)
    ] = None,
):
    """批量生成（T08 §5.1）：202 接收；selected_chunk_ids 省略 = active set 全部 ready。"""
    resolver = ProjectResourceResolver(db)
    doc = await resolver.document(pid, did)
    if doc is None:
        raise _gen_http_error(404, "GENERATION_SOURCE_NOT_FOUND", "文档不存在", {"source_type": "Document"})

    task_service = TaskService(db, request.app.state.redis)
    service = GenerationOrchestrationService(db, task_service)
    request_fingerprint = build_generation_request_fingerprint(
        kind="batch",
        source_id=did,
        prompt_template_id=body.prompt_template_id,
        model_config_id=body.model_config_id,
        selected_chunk_ids=body.selected_chunk_ids,
        created_by=current_user.id,
    )
    if idempotency_key is not None:
        try:
            existing = await service.resolve_idempotent_request(
                project_id=pid,
                document_id=did,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
        except GenerationIdempotencyConflictError as exc:
            await db.rollback()
            raise _gen_http_error(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                str(exc),
                {"idempotency_key": idempotency_key},
            ) from exc
        if existing is not None:
            batch, parent_task = existing
            await db.commit()
            return GenerateAcceptedResponse(
                task_id=parent_task.id,
                generation_batch_id=batch.id,
                status=parent_task.status,
            )

    template = await resolver.prompt_template(pid, body.prompt_template_id)
    if template is None:
        raise _gen_http_error(
            404, "GENERATION_CONFIG_NOT_FOUND", "PromptTemplate 不存在", {"config_type": "prompt_template"}
        )
    model_config = await resolver.model_config(pid, body.model_config_id)
    if model_config is None:
        raise _gen_http_error(
            404, "GENERATION_CONFIG_NOT_FOUND", "ModelConfig 不存在", {"config_type": "model_config"}
        )

    try:
        batch, parent_task, _runs = await service.create_batch(
            project_id=pid,
            document_id=did,
            prompt_template_id=body.prompt_template_id,
            model_config_id=body.model_config_id,
            selected_chunk_ids=body.selected_chunk_ids,
            created_by=current_user.id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
    except GenerationSourceNotReadyError as e:
        raise _gen_http_error(
            409, "GENERATION_SOURCE_NOT_READY", str(e),
            {"document_id": str(did)},
        ) from e
    except GenerationConfigUnavailableError as e:
        raise _gen_http_error(
            409, "GENERATION_CONFIG_UNAVAILABLE", str(e),
            {"config_type": "model_config", "config_id": str(body.model_config_id)},
        ) from e
    except (PromptTemplateVersionMaterializeError, ValueError) as e:
        raise _gen_http_error(
            409, "GENERATION_CONFIG_UNAVAILABLE", str(e), {"config_type": "prompt_template"}
        ) from e
    except SnapshotUnsafeError as e:
        raise _gen_http_error(
            409, "GENERATION_SNAPSHOT_UNSAFE", str(e),
            {"config_type": "model_config", "field_path": str(body.model_config_id)},
        ) from e
    except GenerationInProgressError as e:
        raise _gen_http_error(
            409, "GENERATION_IN_PROGRESS", str(e),
            {"generation_batch_id": str(e.existing_batch_id) if e.existing_batch_id else None},
        ) from e

    # 业务 Batch + Task 同一事务提交后由独立 runner 领取。
    doc.status = "generating"
    await db.commit()

    return GenerateAcceptedResponse(
        task_id=parent_task.id,
        generation_batch_id=batch.id,
        status="queued",
    )


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
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    resolver = ProjectResourceResolver(db)
    await _get_cleaning_job(resolver, pid, did, cleaning_job_id)
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key 头缺失或超过 128 字符",
        )
    service = CleanVersionService(db)
    from app.services.clean_version_service import CleanSourceChangedError

    try:
        version = await service.create_merged_version(
            did, cleaning_job_id, current_user.id, idempotency_key=idempotency_key
        )
    except CleanSourceChangedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CLEAN_SOURCE_CHANGED", "message": "来源 Section 在合并期间已变化"},
        ) from None
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
    from app.services.clean_version_service import (
        CleanVersionReviewConflictError,
        CleanVersionStaleError,
    )

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
    except CleanVersionReviewConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CLEAN_VERSION_REVIEW_CONFLICT", "message": "该版本已被其他评审者处理"},
        ) from None
    except CleanVersionStaleError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CLEAN_VERSION_STALE",
                "message": "目标版本旧于当前 active 版本",
                "context": {"target_version": e.target_version, "active_version": e.active_version},
            },
        ) from e
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
