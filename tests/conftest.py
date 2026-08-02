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

import contextlib
import os
import sys
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
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
# 禁用 asyncpg 语句缓存：每次测试结束会 TRUNCATE 全表（涉及 catalog 锁），
# 而 PREPARE 语句与 TRUNCATE 会竞争 AccessExclusiveLock，导致间歇性
# DeadlockDetectedError。statement_cache_size=0 关闭 PREPARE，从根上消除该竞态。
TEST_ENGINE_OPTIONS = {"connect_args": {"statement_cache_size": 0}}
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
    engine = create_async_engine(TEST_DB_URL, echo=False, **TEST_ENGINE_OPTIONS)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
async def _prepare_schema(_test_engine) -> AsyncGenerator[None, None]:
    """按当前模型确保 schema 一致（缺列的表重建，不覆盖其他表）。

    多 worktree 并发复用同一测试库时，另一 worktree 的 create_all 可能用其旧模型
    创建了缺少新列的 tasks 表（如 T07 的 handler/state_version）。这里检测关键表
    的 schema 漂移：仅当 `tasks.handler` 列缺失时 drop tasks/task_attempts 并重建，
    让本 worktree 的模型补齐新列；其余表保持不动，尽量减小对并发 worktree 的干扰。
    真实迁移链路由 run-migration-smoke 覆盖。
    """
    from app import models  # noqa: F401  # 确保所有模型已注册
    from app.database import Base

    # 某些测试数据库（如调度器预置的容器）可能缺少 public schema，
    # 导致 CREATE TYPE/表 失败；这里幂等地确保 public schema 存在。
    async with _test_engine.begin() as conn:
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS public'))
        # 检测 tasks 表是否缺 T07 新列（handler/state_version）。
        drift = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='tasks' AND column_name IN ('handler','state_version')"
            )
        )
        existing_cols = {row[0] for row in drift.fetchall()}
        if existing_cols is not None and "handler" not in existing_cols:
            # tasks 存在但缺 T07 列（旧 worktree 模型创建）：重建以补齐新列。
            # 同时删除旧 task_status 枚举（缺 cancelling），create_all 会用当前模型
            # 重建含 6 值（含 cancelling）的枚举类型。
            await conn.execute(text("DROP TABLE IF EXISTS task_attempts CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS tasks CASCADE"))
            await conn.execute(text("DROP TYPE IF EXISTS task_status"))
        await conn.run_sync(Base.metadata.create_all)
    async with _test_engine.begin() as conn:
        await conn.execute(text(_TRUNCATE_ALL_SQL))
    yield


@pytest.fixture(scope="session")
def _test_session_factory(_test_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


# 单语句 TRUNCATE 所有业务表：先拼接表名列表，再一次性执行一条 TRUNCATE，
# 相比逐表 TRUNCATE 的循环一次性获取全部表锁，避免多 worktree 并发访问同一
# 测试库时因表间加锁顺序不一致而死锁（T01/T02 并行实施中确认同一问题并采用同方案）。
_TRUNCATE_ALL_SQL = """
DO $$
DECLARE tbls text;
BEGIN
    SELECT string_agg(quote_ident(tablename), ', ') INTO tbls
      FROM pg_tables
     WHERE schemaname = 'public' AND tablename <> 'alembic_version';
    IF tbls IS NOT NULL THEN
        EXECUTE 'TRUNCATE TABLE ' || tbls || ' CASCADE';
    END IF;
END $$;
"""


async def _truncate_with_retry(_test_engine) -> None:
    """TRUNCATE 带死锁重试。

    多 worktree 并发访问同一测试库时，两个独立 pytest 进程可能同时 TRUNCATE/INSERT，
    触发 PostgreSQL 死锁（异步事务竞争）。deadlock 由 PG 检测后自动回滚受害方，
    这里按 sqlstate 40P01（deadlock_detected）捕获并稍作退避后重试，直至成功。
    """
    import asyncio

    for attempt in range(6):
        try:
            async with _test_engine.begin() as conn:
                await conn.execute(text(_TRUNCATE_ALL_SQL))
            return
        except Exception as exc:  # noqa: BLE001 - 需要统一捕获并判断是否死锁
            sqlstate = getattr(exc, "sqlstate", None) or getattr(getattr(exc, "orig", None), "sqlstate", None)
            if sqlstate != "40P01":
                raise
            if attempt == 5:
                raise
            await asyncio.sleep(0.2 * (attempt + 1))


@pytest.fixture
async def db_session(_test_session_factory, _test_engine, _prepare_schema) -> AsyncGenerator[AsyncSession, None]:
    """每个测试开启一个新 session；结束后 TRUNCATE 全部业务表并清理测试 Redis key。"""
    safety.assert_test_environment_ready(require_db=True, require_redis=True, require_minio=False)
    async with _test_session_factory() as session:
        try:
            yield session
        finally:
            # 死锁/连接被对端终止时 rollback 可能抛 InterfaceError，清理阶段吞掉即可。
            with contextlib.suppress(DBAPIError):
                await session.rollback()
            await _truncate_with_retry(_test_engine)


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
        "type": "access",
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

    async def create_model_config(self, project_id: uuid.UUID, name: str = "GPT-4o-mini"):
        from app.models.config import ModelConfig

        cfg = ModelConfig(
            project_id=project_id,
            name=name,
            provider="openai",
            base_url="http://localhost:9999/v1",
            api_key_encrypted="test-key",
            model_name="gpt-4o-mini",
        )
        self.session.add(cfg)
        await self.session.flush()
        await self.session.refresh(cfg)
        return cfg

    async def create_prompt_template(self, project_id: uuid.UUID, task_type: str = "qa_generation"):
        from app.models.prompt_template import PromptTemplate

        tpl = PromptTemplate(
            project_id=project_id,
            task_type=task_type,
            name="QA 模板",
            system_prompt="你是专家",
            user_prompt_template="请回答：{{content}}",
        )
        self.session.add(tpl)
        await self.session.flush()
        await self.session.refresh(tpl)
        return tpl

    async def create_chunk_profile(self, project_id: uuid.UUID):
        from app.models.config import ChunkProfile

        profile = ChunkProfile(project_id=project_id, name="默认切分")
        self.session.add(profile)
        await self.session.flush()
        await self.session.refresh(profile)
        return profile

    async def create_export_profile(self, project_id: uuid.UUID):
        from app.models.config import ExportProfile

        profile = ExportProfile(project_id=project_id, name="SFT")
        self.session.add(profile)
        await self.session.flush()
        await self.session.refresh(profile)
        return profile

    async def create_parse_job(self, document_id: uuid.UUID, parser_profile_id: uuid.UUID, status: str = "queued"):
        from app.models.parse import ParseJob

        # T03 CHECK(ck_parse_jobs_snapshot_complete)：snapshot_schema_version>=1 时
        # snapshot/hash/ref/version 必须全部非空。填默认快照，使 fixture 在迁移后
        # schema（含约束）与 create_all schema 下都合法。
        job = ParseJob(
            document_id=document_id,
            parser_profile_id=parser_profile_id,
            status=status,
            snapshot_schema_version=1,
            parser_profile_snapshot={"endpoint_ref": "test"},
            parser_profile_sha256="0" * 64,
            endpoint_policy_snapshot={"policy": "test"},
            endpoint_policy_ref="test-policy",
            endpoint_policy_version="1",
            endpoint_policy_sha256="0" * 64,
            frozen_at=datetime.now(UTC),
        )
        self.session.add(job)
        await self.session.flush()
        await self.session.refresh(job)
        return job

    async def create_cleaning_job(self, document_id: uuid.UUID, parse_job_id: uuid.UUID, started_by: uuid.UUID):
        from app.models.section import CleaningJob

        job = CleaningJob(
            document_id=document_id,
            parse_job_id=parse_job_id,
            started_by=started_by,
            status="completed",
        )
        self.session.add(job)
        await self.session.flush()
        await self.session.refresh(job)
        return job

    async def create_section(self, cleaning_job_id: uuid.UUID, document_id: uuid.UUID, ordinal: int = 0):
        from app.models.section import Section

        section = Section(
            cleaning_job_id=cleaning_job_id,
            document_id=document_id,
            ordinal=ordinal,
            heading_path="1.1",
            raw_markdown="# 测试",
            cleaned_markdown="# 清洗后",
        )
        self.session.add(section)
        await self.session.flush()
        await self.session.refresh(section)
        return section

    async def create_chunk(self, section_id: uuid.UUID, document_id: uuid.UUID, ordinal: int = 0):
        from app.models.chunk import Chunk

        # T06：chunks.chunk_set_id NOT NULL。为文档惰性创建 legacy 隔离集合，
        # 保证既有测试/下游 FK 结构不变（Chunk id 不变）。
        chunk_set_id = await self._ensure_legacy_chunk_set(document_id)
        chunk = Chunk(
            section_id=section_id,
            document_id=document_id,
            chunk_set_id=chunk_set_id,
            ordinal=ordinal,
            heading_path="1.1",
            content="测试内容",
            token_count=1,
        )
        self.session.add(chunk)
        await self.session.flush()
        await self.session.refresh(chunk)
        return chunk

    async def _ensure_legacy_chunk_set(self, document_id: uuid.UUID) -> uuid.UUID:
        """为文档创建/复用 is_legacy=true 隔离集合（确定性 UUID）。"""
        from app.models.chunk_set import ChunkSet
        from app.models.document import Document

        result = await self.session.execute(
            select(ChunkSet).where(
                ChunkSet.document_id == document_id,
                ChunkSet.is_legacy.is_(True),
            ).limit(1)
        )
        existing = result.scalars().first()
        if existing is not None:
            return existing.id

        doc = (
            await self.session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            raise RuntimeError("document not found")
        cs = ChunkSet(
            document_id=document_id,
            status="completed",
            version=1,
            is_legacy=True,
            summary_json={"provenance": "test_fixture"},
            created_by=doc.uploaded_by,
        )
        self.session.add(cs)
        await self.session.flush()
        return cs.id

    async def create_generation_run(
        self,
        chunk_id: uuid.UUID,
        prompt_template_id: uuid.UUID,
        model_config_id: uuid.UUID,
    ):
        from app.models.generation import GenerationRun

        # T08：fixture 生成的是迁移前合成历史 run，诚实标记为 legacy_unavailable
        # （无 input_prompt/hash/raw_output，无法通过 verified CHECK）。
        run = GenerationRun(
            chunk_id=chunk_id,
            prompt_template_id=prompt_template_id,
            model_config_id=model_config_id,
            context_mode="single_chunk",
            status="completed",
            is_legacy=True,
            provenance_status="legacy_unavailable",
            provenance_error_code="LEGACY_TEST_FIXTURE",
        )
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def create_candidate(
        self, generation_run_id: uuid.UUID, chunk_id: uuid.UUID, status: str = "ai_generated"
    ):
        from app.models.generation import Candidate

        candidate = Candidate(
            generation_run_id=generation_run_id,
            chunk_id=chunk_id,
            content={"question": "q", "answer": "a"},
            candidate_type="qa_generation",
            status=status,
        )
        self.session.add(candidate)
        await self.session.flush()
        await self.session.refresh(candidate)
        return candidate

    async def create_curated_item(
        self, project_id: uuid.UUID, candidate_id: uuid.UUID, promoted_by: uuid.UUID, status: str = "approved"
    ):
        from app.models.curated import CuratedItem

        item = CuratedItem(
            project_id=project_id,
            candidate_id=candidate_id,
            content={"question": "q", "answer": "a"},
            item_type="qa_generation",
            status=status,
            promoted_by=promoted_by,
        )
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def create_dataset(self, project_id: uuid.UUID, created_by: uuid.UUID):
        from app.models.dataset import Dataset

        ds = Dataset(project_id=project_id, name="数据集", created_by=created_by)
        self.session.add(ds)
        await self.session.flush()
        await self.session.refresh(ds)
        return ds

    async def create_benchmark(self, project_id: uuid.UUID, created_by: uuid.UUID):
        from app.models.dataset import Benchmark

        bm = Benchmark(project_id=project_id, name="基准集", created_by=created_by)
        self.session.add(bm)
        await self.session.flush()
        await self.session.refresh(bm)
        return bm

    async def create_cleaned_version(
        self, document_id: uuid.UUID, cleaning_job_id: uuid.UUID, created_by: uuid.UUID, version: int = 1
    ):
        from app.models.cleaned_document_version import CleanedDocumentVersion

        v = CleanedDocumentVersion(
            document_id=document_id,
            source_cleaning_job_id=cleaning_job_id,
            version=version,
            merged_markdown="# 合并",
            created_by=created_by,
        )
        self.session.add(v)
        await self.session.flush()
        await self.session.refresh(v)
        return v

    async def create_task(self, project_id: uuid.UUID, task_type: str, entity_type: str, entity_id: uuid.UUID, created_by: uuid.UUID):
        from app.models.task import Task

        task = Task(
            project_id=project_id,
            task_type=task_type,
            entity_type=entity_type,
            entity_id=entity_id,
            status="queued",
            created_by=created_by,
            # T07：持久队列必需字段（handler/payload/next_run_at）。
            handler=f"{task_type}:v1",
            payload={},
            payload_version=1,
            max_attempts=1,
            timeout_seconds=300,
            next_run_at=datetime.now(UTC),
        )
        self.session.add(task)
        await self.session.flush()
        await self.session.refresh(task)
        return task


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


@pytest.fixture
async def full_resources(db_session, org, make_resource):
    """为两个项目各构建一套真实完整的资源链（T02 负向矩阵的数据基础）。

    两个项目都含完整资源链，而非随机不存在的 UUID；项目 B 也预置四角色成员，
    以便验证“同角色下跨项目资源一律 404”（非成员语义另有 org 覆盖）。

    返回：
      projects: {"a": {...资源}, "b": {...资源}}
      每项目含 document/parse_job/cleaning_job/section/chunk/generation_run/
      candidate/curated_item/dataset/benchmark/cleaned_version/task 及各类 profile。
    """
    users = org["users"]
    pids = {"a": org["projects"]["a"].id, "b": org["projects"]["b"].id}

    # 项目 B 保持 org 预置的仅 admin 成员关系：跨项目负向矩阵依赖
    # “A 成员 + B 对象 -> 404/403”，若补齐 B 成员会破坏隔离语义。

    res = {}
    for key in ("a", "b"):
        pid = pids[key]
        admin = users["admin"].id
        editor = users["editor"].id
        reviewer = users["reviewer"].id

        doc = await make_resource.create_document(pid, admin)
        parser = await make_resource.create_parser_profile(pid)
        model = await make_resource.create_model_config(pid)
        tpl = await make_resource.create_prompt_template(pid)
        chunk_prof = await make_resource.create_chunk_profile(pid)
        export_prof = await make_resource.create_export_profile(pid)
        parse_job = await make_resource.create_parse_job(doc.id, parser.id)
        cleaning_job = await make_resource.create_cleaning_job(doc.id, parse_job.id, admin)
        section = await make_resource.create_section(cleaning_job.id, doc.id)
        chunk = await make_resource.create_chunk(section.id, doc.id)
        gen_run = await make_resource.create_generation_run(chunk.id, tpl.id, model.id)
        candidate = await make_resource.create_candidate(gen_run.id, chunk.id, status="approved")
        curated = await make_resource.create_curated_item(pid, candidate.id, reviewer)
        dataset = await make_resource.create_dataset(pid, editor)
        benchmark = await make_resource.create_benchmark(pid, editor)
        cleaned_version = await make_resource.create_cleaned_version(doc.id, cleaning_job.id, reviewer)
        task = await make_resource.create_task(pid, "parse", "document", doc.id, admin)

        res[key] = {
            "pid": pid,
            "document": doc,
            "parser_profile": parser,
            "model_config": model,
            "prompt_template": tpl,
            "chunk_profile": chunk_prof,
            "export_profile": export_prof,
            "parse_job": parse_job,
            "cleaning_job": cleaning_job,
            "section": section,
            "chunk": chunk,
            "generation_run": gen_run,
            "candidate": candidate,
            "curated_item": curated,
            "dataset": dataset,
            "benchmark": benchmark,
            "cleaned_version": cleaned_version,
            "task": task,
        }

    await db_session.commit()  # WS/独立会话可见
    return {"projects": res, "users": users, "db": db_session}

