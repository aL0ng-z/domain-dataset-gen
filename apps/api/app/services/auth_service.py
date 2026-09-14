import logging

from jose import JWTError
from passlib.context import CryptContext
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jwt import (
    INVALID_TOKEN_MESSAGE,
    TokenType,
    create_access_token,
    create_refresh_token,
    resolve_user_id,
)
from app.models.user import User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, username: str, email: str, password: str, role: str = "editor") -> User:
        # 这里只关心是否存在。不能用 scalar_one_or_none：用户名和邮箱分别命中
        # 两个用户时会返回两行，进而泄漏为 500。
        already_exists = await self.db.scalar(
            select(exists().where((User.username == username) | (User.email == email)))
        )
        if already_exists:
            raise ValueError("用户名或邮箱已存在")

        user = User(
            username=username,
            email=email,
            password_hash=pwd_context.hash(password),
            role=role,
        )
        try:
            # 唯一约束是并发注册的最终裁决。使用 savepoint 保留当前请求会话，
            # 让冲突仍以普通业务错误返回。
            async with self.db.begin_nested():
                self.db.add(user)
                await self.db.flush()
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23505":
                raise ValueError("用户名或邮箱已存在") from None
            raise
        await self.db.refresh(user)
        return user

    async def authenticate(self, username: str, password: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None or not pwd_context.verify(password, user.password_hash):
            return None
        if not user.is_active:
            return None
        return user

    def create_access_token(self, user: User) -> str:
        return create_access_token(user.id, role=user.role)

    def create_refresh_token(self, user: User) -> str:
        return create_refresh_token(user.id)

    async def refresh_tokens(self, refresh_token: str) -> tuple[str, str]:
        """仅接受 refresh token：解码校验 type=refresh、sub 为 UUID，再确认用户存在且启用。"""
        try:
            user_id = resolve_user_id(refresh_token, TokenType.REFRESH)
        except JWTError:
            logger.warning("refresh 令牌校验失败")
            raise ValueError(INVALID_TOKEN_MESSAGE) from None

        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            logger.warning("refresh 令牌对应的用户不存在或已停用")
            raise ValueError(INVALID_TOKEN_MESSAGE)

        return self.create_access_token(user), self.create_refresh_token(user)
