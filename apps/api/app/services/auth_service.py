import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, username: str, email: str, password: str, role: str = "editor") -> User:
        # Check for existing user
        result = await self.db.execute(
            select(User).where((User.username == username) | (User.email == email))
        )
        if result.scalar_one_or_none():
            raise ValueError("用户名或邮箱已存在")

        user = User(
            username=username,
            email=email,
            password_hash=pwd_context.hash(password),
            role=role,
        )
        self.db.add(user)
        await self.db.flush()
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
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
        payload = {"sub": str(user.id), "role": user.role, "exp": expire, "iat": datetime.now(timezone.utc)}
        return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    def create_refresh_token(self, user: User) -> str:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
        payload = {"sub": str(user.id), "type": "refresh", "exp": expire, "iat": datetime.now(timezone.utc)}
        return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    async def refresh_tokens(self, refresh_token: str) -> tuple[str, str]:
        from jose import JWTError

        try:
            payload = jwt.decode(refresh_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            if payload.get("type") != "refresh":
                raise ValueError("无效的刷新令牌")
            user_id = payload.get("sub")
        except JWTError:
            raise ValueError("无效的刷新令牌")

        result = await self.db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise ValueError("用户不存在或已停用")

        return self.create_access_token(user), self.create_refresh_token(user)
