import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.cleaned_version import CleanedDocumentVersionDetailResponse
from app.services.clean_version_service import CleanVersionService

router = APIRouter(prefix="/api/cleaned-versions", tags=["cleaned-versions"])


@router.get("/{vid}", response_model=CleanedDocumentVersionDetailResponse)
async def get_cleaned_version(
    vid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = CleanVersionService(db)
    version = await service.get_version(vid)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="版本不存在")
    return version
