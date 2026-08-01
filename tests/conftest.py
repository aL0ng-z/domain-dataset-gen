"""共享测试 fixture：隔离测试环境、ASGI 客户端、认证 helper、双项目/角色/资源工厂。

集成测试依赖测试基础设施（PostgreSQL/Redis/MinIO）：
  docker compose -f infra/docker/docker-compose.test.yml --env-file infra/docker/.env.test up -d
仅当 POSTGRES_DB 等环境变量指向测试库时才会连接真实服务；否则所有 destructive
fixture 都会抛出 RuntimeError（见 tests/safety.py）。

本仓库路由器会直接调用 ``db.commit()``，因此集成测试不能依赖
“begin/rollback” 事务包裹。改为：每个测试结束对测试库所有表 TRUNCATE CASCADE，
并对测试 Redis 前缀 key 做清理，保证无残留业务数据。

运行方式（仓库根目录，无需手工 PYTHONPATH）：
  conda run -n DatasetGen python -m pytest -q
"""

import os
import sys
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# 保证在未安装 editable 包时也能导入本地库（CI 全新 checkout 场景）。
REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in ("apps/api", "libs/domain", "libs/storage", "libs/parsing", "libs/cleaning", "libs/splitters", "libs/llm"):
    _path = str(REPO_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# 测试环境变量必须在导入 app.config.settings 之前设置。
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("POSTGRES_DB", "datasetgen_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "55432")
os.environ.setdefault("POSTGRES_USER", "datasetgen_test")
os.environ.setdefault("POSTGRES_PASSWORD", "datasetgen_test_password")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "56379")
os.environ.setdefault("TEST_REDIS_PREFIX", "tests:")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:19000")
os.environ.setdefault("MINIO_ACCESS_KEY", "testminioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "testminioadmin123")
os.environ.setdefault("MINIO_BUCKET_DOCUMENTS", "documents-test")
os.environ.setdefault("MINIO_BUCKET_OUTPUTS", "outputs-test")
os.environ.setdefault("TEST_MINIO_PREFIX", "tests/")
os.environ.setdefault("TEST_RUN_ID", uuid.uuid4().hex[:8])

from app.config import settings  # noqa: E402
from tests import safety  # noqa: E402

if safety.is_testing() and settings.postgres_db not in safety.ALLOWED_TEST_DATABASE_NAMES:
    raise RuntimeError(f"POSTGRES_DB={settings.postgres_db!r} 不是测试数据库；拒绝运行测试。")

TEST_DB_URL = (
    f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
)
TEST_REDIS_URL = f"redis://{settings.redis_host}:{settings.redis_port}"


@pytest.fixture(autouse=True)
def _isolate_parser_transport():
    """每个测试结束后重置全局 parser transport，避免 fake 泄漏到后续测试。"""
    from parsing.transport import reset_transport

    yield
    reset_transport()


# ---------------------------------------------------------------------------
# 数据库：session 级 engine；schema 创建一次；每个测试结束 TRUNCATE。
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def _test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(TEST_DB_URL, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
async def _prepare_schema(_test_engine) -> AsyncGenerator[None, None]:
    """按当前模型创建全部表（幂等；不覆盖已有表，真实迁移由 smoke test 覆盖）。"""
    from app import models  # noqa: F401  # 确保所有模型已注册
    from app.database import Base

    # 某些测试数据库（如调度器预置的容器）可能缺少 public schema，
    # 导致 CREATE TYPE/表 失败；这里幂等地确保 public schema 存在。
    async with _test_engine.begin() as conn:
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS public'))
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(scope="session")
def _test_session_factory(_test_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


_TRUNCATE_ALL_SQL = """
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> 'alembic_version'
    LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' CASCADE';
    END LOOP;
END $$;
"""


@pytest.fixture
async def db_session(_test_session_factory, _test_engine, _prepare_schema) -> AsyncGenerator[AsyncSession, None]:
    """每个测试开启一个新 session；结束后 TRUNCATE 全部业务表并清理测试 Redis key。"""
    safety.assert_test_environment_ready(require_db=True, require_redis=True, require_minio=False)
    async with _test_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            async with _test_engine.begin() as conn:
                await conn.execute(text(_TRUNCATE_ALL_SQL))


@pytest.fixture(scope="session")
async def _redis() -> AsyncGenerator[aioredis.Redis, None]:
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def clean_redis_keys(_redis) -> AsyncGenerator[aioredis.Redis, None]:
    """清理测试 Redis key（前缀 tests:），并返回可用于断言的 client。"""
    safety.assert_test_environment_ready(require_db=False, require_redis=True, require_minio=False)
    yield _redis
    prefix = os.environ.get("TEST_REDIS_PREFIX", "tests:")
    keys = [k async for k in _redis.scan_iter(match=f"{prefix}*")]
    if keys:
        await _redis.delete(*keys)


@pytest.fixture
def app(_redis) -> Generator:
    """构造使用测试配置的 FastAPI 应用实例（注入测试 Redis）。"""
    from app.main import app as _app

    _app.state.redis = _redis
    yield _app


@pytest.fixture
async def client(app, db_session) -> AsyncGenerator[AsyncClient, None]:
    """可复用 ASGI 测试客户端，自动覆盖 get_db 依赖。"""
    from app.database import get_db

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 认证 helper：显式 access/refresh token、无 token、伪造 token。
# ---------------------------------------------------------------------------


class AuthHelper:
    """封装 Token 结构，供 API 测试直接使用。"""

    def __init__(self, access_token: str, refresh_token: str, user_id: uuid.UUID):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user_id = user_id

    def bearer_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


def create_access_token(user_id: uuid.UUID, *, role: str = "editor", secret: str | None = None) -> str:
    from datetime import datetime, timedelta

    from jose import jwt

    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": datetime.now(UTC) + timedelta(minutes=30),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, secret or settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: uuid.UUID, *, secret: str | None = None) -> str:
    from datetime import datetime, timedelta

    from jose import jwt

    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": datetime.now(UTC) + timedelta(days=7),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, secret or settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


# ---------------------------------------------------------------------------
# 资源工厂：两个 Project、四个角色、ProjectMember、Document 及下游资源。
# ---------------------------------------------------------------------------


class UserFactory:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, username: str, role: str = "editor"):
        from app.services.auth_service import AuthService

        service = AuthService(self.session)
        return await service.register(username, f"{username}@test.local", "password-123", role)


class ProjectFactory:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, created_by: uuid.UUID, description: str | None = None):
        from app.models.project import Project

        project = Project(name=name, description=description, created_by=created_by)
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def add_member(self, project_id: uuid.UUID, user_id: uuid.UUID, role: str = "viewer"):
        from app.models.project import ProjectMember

        member = ProjectMember(project_id=project_id, user_id=user_id, role=role)
        self.session.add(member)
        await self.session.flush()
        return member


class ResourceFactory:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_document(
        self, project_id: uuid.UUID, uploaded_by: uuid.UUID, filename: str = "sample.pdf"
    ):
        from app.models.document import Document

        doc = Document(
            project_id=project_id,
            filename=filename,
            file_size=len(b"%PDF-test"),
            sha256="0" * 64,
            minio_key=f"tests/{os.environ.get('TEST_RUN_ID', 'local')}/{project_id}/sample.pdf",
            page_count=1,
            uploaded_by=uploaded_by,
        )
        self.session.add(doc)
        await self.session.flush()
        await self.session.refresh(doc)
        return doc

    async def create_parser_profile(self, project_id: uuid.UUID, name: str = "PyMuPDF 本地"):
        from app.models.config import ParserProfile

        profile = ParserProfile(project_id=project_id, name=name)
        self.session.add(profile)
        await self.session.flush()
        await self.session.refresh(profile)
        return profile


@pytest.fixture
def make_user(db_session):
    return UserFactory(db_session)


@pytest.fixture
def make_project(db_session):
    return ProjectFactory(db_session)


@pytest.fixture
def make_resource(db_session):
    return ResourceFactory(db_session)


@pytest.fixture
async def org(db_session):
    """预置 admin/reviewer/editor/viewer 四角色用户与两个项目（A 与 B）。

    返回结构：
      users: {role: User}
      projects: {name: Project}
    """
    user_factory = UserFactory(db_session)
    project_factory = ProjectFactory(db_session)

    roles = ["admin", "reviewer", "editor", "viewer"]
    users = {}
    for role in roles:
        users[role] = await user_factory.create(f"{role}_user", role)

    project_a = await project_factory.create("项目 A", users["admin"].id)
    project_b = await project_factory.create("项目 B", users["admin"].id)

    for role in roles:
        await project_factory.add_member(project_a.id, users[role].id, role)
    # 项目 B 仅 admin；用于验证项目隔离（reviewer/editor/viewer 不应访问）。
    await project_factory.add_member(project_b.id, users["admin"].id, "admin")

    await db_session.flush()
    return {
        "users": users,
        "projects": {"a": project_a, "b": project_b},
        "db": db_session,
    }
