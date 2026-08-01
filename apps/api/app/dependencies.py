import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jwt import TokenType, resolve_user_id
from app.database import get_db
from app.models.project import ProjectMember
from app.models.user import User
from domain.enums import ROLE_HIERARCHY, UserRole

security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """通用认证依赖：只接受 access token，并从数据库确认用户存在且启用。

    签名/声明/UUID 任何校验失败统一返回 401（不得产生 500，任务卡 §4）。
    """
    token = credentials.credentials
    try:
        user_id = resolve_user_id(token, TokenType.ACCESS)
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已停用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(min_role: UserRole):
    def dependency(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        user_level = ROLE_HIERARCHY.get(UserRole(current_user.role), -1)
        required_level = ROLE_HIERARCHY.get(min_role, 999)
        if user_level < required_level:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return current_user

    return dependency


def require_project_member(min_role: UserRole):
    async def dependency(
        pid: uuid.UUID,
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> User:
        # Global admin bypasses project membership check
        if UserRole(current_user.role) == UserRole.admin:
            return current_user

        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == pid,
                ProjectMember.user_id == current_user.id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="非项目成员")

        member_level = ROLE_HIERARCHY.get(UserRole(member.role), -1)
        required_level = ROLE_HIERARCHY.get(min_role, 999)
        if member_level < required_level:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="项目权限不足")
        return current_user

    return dependency
