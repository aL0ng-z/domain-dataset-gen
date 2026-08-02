import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jwt import TokenType, resolve_user_id
from app.database import get_db
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
    """返回一个依赖：校验当前用户是 URL 中 pid 的成员且角色不低于 min_role。

    委托集中式 :func:`app.authz.check_project_member`，保持既有 403 语义。
    """
    from app.authz import check_project_member

    async def dependency(
        pid: uuid.UUID,
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> User:
        await check_project_member(db, pid, current_user, min_role)
        return current_user

    return dependency
