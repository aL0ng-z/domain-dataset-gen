import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.user import User
from app.schemas.curated import (
    CuratedItemResponse,
    CuratedItemReview,
    CuratedItemUpdate,
    CuratedRevisionResponse,
    EvidenceLinkResponse,
)
from app.services.curated_item_service import (
    CuratedApprovalGateFailedError,
    CuratedItemService,
    CuratedReviewStateConflictError,
    CuratedRevisionConflictError,
)
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/curated-items", tags=["curated-items"])


@router.get("/", response_model=PaginatedResponse[CuratedItemResponse], operation_id="curated_item_list")
async def list_curated_items(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    item_status: str | None = Query(None, alias="status"),
    item_type: str | None = None,
):
    service = CuratedItemService(db)
    items, total = await service.list_items(pid, page, page_size, item_status, item_type)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{iid}", response_model=CuratedItemResponse, operation_id="curated_item_get")
async def get_curated_item(
    pid: uuid.UUID,
    iid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    item = await resolver.curated_item(pid, iid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    return item


@router.patch("/{iid}", response_model=CuratedItemResponse, operation_id="curated_item_update")
async def update_curated_item(
    pid: uuid.UUID,
    iid: uuid.UUID,
    body: CuratedItemUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    service = CuratedItemService(db)
    try:
        updated = await service.update(
            item_id=iid,
            revised_by=current_user.id,
            content=body.content,
            revision_note=body.revision_note,
            expected_revision=body.expected_revision,
        )
    except CuratedRevisionConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CURATED_REVISION_CONFLICT",
                "message": str(e),
                "context": {"current_revision": e.current_revision},
            },
        ) from e
    except CuratedReviewStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CURATED_REVIEW_STATE_CONFLICT", "message": str(e)},
        ) from e
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    return updated


@router.post("/{iid}/review", response_model=CuratedItemResponse, operation_id="curated_item_review")
async def review_curated_item(
    pid: uuid.UUID,
    iid: uuid.UUID,
    body: CuratedItemReview,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    service = CuratedItemService(db)
    try:
        item = await service.review(
            item_id=iid,
            reviewer_id=current_user.id,
            action=body.action,
            reason=body.reason,
            expected_revision=body.expected_revision,
        )
    except CuratedRevisionConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CURATED_REVISION_CONFLICT",
                "message": str(e),
                "context": {"current_revision": e.current_revision},
            },
        ) from e
    except CuratedApprovalGateFailedError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CURATED_APPROVAL_GATE_FAILED", "message": str(e)},
        ) from e
    except CuratedReviewStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CURATED_REVIEW_STATE_CONFLICT", "message": str(e)},
        ) from e
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    return item


@router.get("/{iid}/revisions", response_model=PaginatedResponse[CuratedRevisionResponse], operation_id="curated_item_list_revisions")
async def list_revisions(
    pid: uuid.UUID,
    iid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    service = CuratedItemService(db)
    items, total = await service.list_revisions(iid, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{iid}/evidence", response_model=PaginatedResponse[EvidenceLinkResponse], operation_id="curated_item_list_evidence")
async def list_evidence(
    pid: uuid.UUID,
    iid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    service = CuratedItemService(db)
    items, total = await service.list_evidence_links(iid, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)
