import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.user import User
from app.schemas.section import (
    SectionAssignRequest,
    SectionCommentCreate,
    SectionCommentResponse,
    SectionLeaseResponse,
    SectionResponse,
    SectionReturnRequest,
    SectionReview,
    SectionRevisionResponse,
    SectionUpdate,
)
from app.services.section_service import SectionService
from domain.enums import UserRole

router = APIRouter(prefix="/api/sections", tags=["sections"])


def _get_redis(request: Request):
    return getattr(request.app.state, "redis", None)


@router.get("/{sid}", response_model=SectionResponse, operation_id="section_get")
async def get_section(
    sid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    section = await service.get_section(sid)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.patch("/{sid}", response_model=SectionResponse, operation_id="section_update")
async def update_section(
    sid: uuid.UUID,
    body: SectionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    section = await service.update_section(sid, body.cleaned_markdown, current_user.id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.post("/{sid}/submit", response_model=SectionResponse, operation_id="section_submit")
async def submit_section(
    sid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    section = await service.submit_for_review(sid)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.post("/{sid}/review", response_model=SectionResponse, operation_id="section_review")
async def review_section(
    sid: uuid.UUID,
    body: SectionReview,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.reviewer))],
):
    service = SectionService(db)
    try:
        section = await service.review_section(sid, body.action, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.post("/{sid}/lease/acquire", response_model=SectionLeaseResponse, operation_id="section_lease_acquire")
async def acquire_lease(
    sid: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db, _get_redis(request))
    try:
        return await service.acquire_lease(sid, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/{sid}/lease/heartbeat", response_model=SectionLeaseResponse, operation_id="section_lease_heartbeat")
async def heartbeat_lease(
    sid: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db, _get_redis(request))
    lease = await service.heartbeat_lease(sid, current_user.id)
    if lease is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租约不存在")
    return lease


@router.post("/{sid}/lease/release", status_code=status.HTTP_204_NO_CONTENT, operation_id="section_lease_release")
async def release_lease(
    sid: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db, _get_redis(request))
    if not await service.release_lease(sid, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租约不存在")


@router.post("/{sid}/comments", response_model=SectionCommentResponse, status_code=status.HTTP_201_CREATED, operation_id="section_add_comment")
async def add_comment(
    sid: uuid.UUID,
    body: SectionCommentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    return await service.add_comment(sid, current_user.id, body.comment_type, body.content)


@router.get("/{sid}/comments", response_model=list[SectionCommentResponse], operation_id="section_list_comments")
async def list_comments(
    sid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    return await service.list_comments(sid)


@router.get("/{sid}/revisions", response_model=list[SectionRevisionResponse], operation_id="section_list_revisions")
async def list_revisions(
    sid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    return await service.list_revisions(sid)


@router.post("/{sid}/assign", response_model=SectionResponse, operation_id="section_assign")
async def assign_section_endpoint(
    sid: uuid.UUID,
    body: SectionAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.reviewer))],
):
    service = SectionService(db)
    section = await service.assign_section(sid, body.assignee_id, current_user.id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.post("/{sid}/complete", response_model=SectionResponse, operation_id="section_complete")
async def complete_section_endpoint(
    sid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    is_admin = current_user.role in ("admin", "reviewer")
    try:
        section = await service.complete_section(sid, current_user.id, is_admin)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.post("/{sid}/return", response_model=SectionResponse, operation_id="section_return")
async def return_section_endpoint(
    sid: uuid.UUID,
    body: SectionReturnRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.reviewer))],
):
    service = SectionService(db)
    section = await service.return_section(sid, body.reason)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section
