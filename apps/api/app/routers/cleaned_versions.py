import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver, authorize_flat_resource
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.cleaned_version import CleanedDocumentVersionDetailResponse
from domain.enums import UserRole

router = APIRouter(prefix="/api/cleaned-versions", tags=["cleaned-versions"])


@router.get("/{vid}", response_model=CleanedDocumentVersionDetailResponse, operation_id="cleaned_version_get")
async def get_cleaned_version(
    vid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.cleaned_version_project_id(vid), UserRole.viewer
    )
    version = await resolver.cleaned_version(pid, vid)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="版本不存在")
    return version
