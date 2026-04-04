import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.user import UserResponse, UserUpdate
from app.services.user_service import UserService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/", response_model=PaginatedResponse[UserResponse])
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    service = UserService(db)
    users, total = await service.list_users(page, page_size)
    return PaginatedResponse(items=users, total=total, page=page, page_size=page_size)


@router.get("/{uid}", response_model=UserResponse)
async def get_user(
    uid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = UserService(db)
    user = await service.get_user(uid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


@router.patch("/{uid}", response_model=UserResponse)
async def update_user(
    uid: uuid.UUID,
    body: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = UserService(db)
    user = await service.update_user(uid, **body.model_dump(exclude_unset=True))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    uid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = UserService(db)
    if not await service.delete_user(uid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
