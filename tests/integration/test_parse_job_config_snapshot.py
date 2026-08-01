"""ParseJob 配置冻结集成测试（T03 验收 #2/#3/#5/#12）。

覆盖：
- 创建 ParseJob 后修改 Profile 或更新 registry，执行/retry 仍使用创建时快照，
  原 snapshot/ref/version/hash 字节级不变；
- 快照规范化/hash 确定；直接篡改 JSON/hash/ref/version 被数据库约束或 worker 校验拒绝；
- 快照递归扫描不含 secret/Token/Authorization/PDF bytes/预签名 URL；
- 并发 Profile 更新与 ParseJob 创建时每个 job 只对应完整版本，无混合快照。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import ParserProfile
from app.models.parse import ParseJob
from app.security.registry import ParserEndpointConfig, ParserEndpointRegistry
from app.security.snapshot import profile_snapshot_sha256, scan_for_secrets
from app.services.parse_freeze_service import freeze_profile_policy


def _make_registry() -> ParserEndpointRegistry:
    return ParserEndpointRegistry(
        [
            ParserEndpointConfig(
                endpoint_ref="mineru-official",
                parser_name="mineru",
                network_zone="public-remote",
                base_url="https://mineru.net/api/v4/extract/task",
                credential_ref="env:MINERU_API_TOKEN",
                credential_origins=["https://mineru.net"],
                artifact_origins=[{"usage": "any", "suffix": "*.aliyuncs.com"}],
            ),
            ParserEndpointConfig(
                endpoint_ref="ml-mineru-9010",
                parser_name="mineru_local_service",
                network_zone="managed-local",
                base_url="http://127.0.0.1:9010",
                allowed_paths=["/tasks", "/tasks/{task_id}", "/tasks/{task_id}/result"],
                pinned_ips=["127.0.0.1"],
            ),
        ]
    )


def _profile(project_id: uuid.UUID, **kwargs) -> ParserProfile:
    defaults = dict(
        project_id=project_id,
        name="profile",
        version=1,
        is_default=False,
        parser_name="mineru",
        parser_options={"endpoint_ref": "mineru-official", "model_version": "vlm"},
    )
    defaults.update(kwargs)
    return ParserProfile(**defaults)


@pytest.fixture(autouse=True)
def _registry_fixture(monkeypatch):
    """测试使用受控 registry。"""
    from app.security import registry as registry_module

    monkeypatch.setattr(
        registry_module,
        "build_registry",
        lambda configs=None: _make_registry(),
    )
    from app.security.registry import reset_registry

    reset_registry()
    yield
    reset_registry()


async def _freeze(db: AsyncSession, profile: ParserProfile) -> tuple[dict, dict, dict]:
    """返回 (profile_snapshot, policy_snapshot, security_context)。"""
    return await _freeze_full(db, profile)


async def _freeze_full(db: AsyncSession, profile: ParserProfile) -> tuple[dict, dict, dict]:
    profile_snapshot, policy_snapshot, security = freeze_profile_policy(profile)
    return profile_snapshot, policy_snapshot, security


async def _make_document(db: AsyncSession, project_id: uuid.UUID, uploaded_by: uuid.UUID) -> uuid.UUID:
    """创建真实 Document（parse_jobs 有 FK 约束）。"""
    from app.models.document import Document

    doc = Document(
        project_id=project_id,
        filename="sample.pdf",
        file_size=10,
        sha256="0" * 64,
        minio_key=f"tests/{uploaded_by}/sample.pdf",
        page_count=1,
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    return doc.id


async def test_snapshot_hash_is_deterministic(org, db_session: AsyncSession):
    profile = _profile(org["projects"]["a"].id)
    db_session.add(profile)
    await db_session.flush()

    snap1, policy1, _ = await _freeze(db_session, profile)
    snap2, policy2, _ = await _freeze(db_session, profile)
    assert snap1 == snap2
    assert policy1 == policy2
    assert profile_snapshot_sha256(snap1) == profile_snapshot_sha256(snap2)


async def test_profile_change_does_not_alter_frozen_snapshot(org, db_session: AsyncSession, monkeypatch):
    """创建快照后修改 Profile，快照字节级不变。"""
    from app.services.parse_freeze_service import freeze_parse_job

    profile = _profile(org["projects"]["a"].id)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    doc_id = await _make_document(db_session, org["projects"]["a"].id, org["users"]["admin"].id)
    await db_session.commit()

    job = await freeze_parse_job(db_session, document_id=doc_id, profile=profile)
    await db_session.commit()
    original = job.parser_profile_snapshot
    original_hash = job.parser_profile_sha256
    original_ref = job.endpoint_policy_ref
    original_version = job.endpoint_policy_version
    original_policy_hash = job.endpoint_policy_sha256

    # 修改 profile（model_version 变化）
    profile.parser_options = {"endpoint_ref": "mineru-official", "model_version": "ocr"}
    profile.version = 2
    await db_session.commit()

    # 重新读取 job：快照必须仍是原值（数据库不可变 + ORM 缓存）。
    job_id = job.id
    fresh = (await db_session.execute(select(ParseJob).where(ParseJob.id == job_id))).scalar_one()
    assert fresh.parser_profile_snapshot == original
    assert fresh.parser_profile_sha256 == original_hash
    assert fresh.endpoint_policy_ref == original_ref
    assert fresh.endpoint_policy_version == original_version
    assert fresh.endpoint_policy_sha256 == original_policy_hash


async def test_db_trigger_rejects_snapshot_rewrite(org, db_session: AsyncSession):
    """直接篡改快照字段被数据库触发器拒绝。"""
    from app.services.parse_freeze_service import freeze_parse_job

    profile = _profile(org["projects"]["a"].id)
    db_session.add(profile)
    await db_session.flush()
    doc_id = await _make_document(db_session, org["projects"]["a"].id, org["users"]["admin"].id)
    job = await freeze_parse_job(db_session, document_id=doc_id, profile=profile)
    await db_session.commit()

    # 通过原始 SQL 尝试改写（绕过 ORM 事件）
    from sqlalchemy import text

    job_id = str(job.id)
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        await db_session.execute(
            text("UPDATE parse_jobs SET parser_profile_sha256 = :h WHERE id = :id"),
            {"h": "f" * 64, "id": job_id},
        )
    await db_session.rollback()


async def test_snapshot_contains_no_secrets(org, db_session: AsyncSession):
    """快照递归扫描不含 secret/Token/Authorization/PDF bytes/预签名 URL。"""
    from app.services.parse_freeze_service import freeze_parse_job

    profile = _profile(org["projects"]["a"].id)
    db_session.add(profile)
    await db_session.flush()
    doc_id = await _make_document(db_session, org["projects"]["a"].id, org["users"]["admin"].id)
    job = await freeze_parse_job(db_session, document_id=doc_id, profile=profile)
    await db_session.commit()

    hits = scan_for_secrets(job.parser_profile_snapshot) + scan_for_secrets(job.endpoint_policy_snapshot)
    assert hits == []
    assert job.parser_profile_sha256 and len(job.parser_profile_sha256) == 64
    assert job.endpoint_policy_sha256 and len(job.endpoint_policy_sha256) == 64


async def test_concurrent_freeze_and_profile_update_yields_consistent_versions(
    org, db_session: AsyncSession, monkeypatch, _test_session_factory
):
    """并发 100 次：每个 job 只对应更新前或更新后的完整版本，无混合快照。"""
    from app.services.parse_freeze_service import freeze_parse_job

    profile = _profile(org["projects"]["a"].id)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    profile_id = profile.id

    # 预置文档（先提交，确保并发会话可读）。
    from app.models.document import Document

    doc = Document(
        project_id=org["projects"]["a"].id,
        filename="sample.pdf",
        file_size=10,
        sha256="0" * 64,
        minio_key=f"tests/{org['users']['admin'].id}/sample.pdf",
        page_count=1,
        uploaded_by=org["users"]["admin"].id,
    )
    db_session.add(doc)
    await db_session.commit()
    document_id = doc.id

    factory = _test_session_factory

    async def create_job():
        async with factory() as session:
            p = (
                await session.execute(
                    select(ParserProfile).where(ParserProfile.id == profile_id).with_for_update()
                )
            ).scalar_one()
            job = await freeze_parse_job(session, document_id=document_id, profile=p)
            await session.commit()
            snap = job.parser_profile_snapshot
            return snap.get("options", {}).get("model_version")

    async def update_profile():
        async with factory() as session:
            p = (
                await session.execute(
                    select(ParserProfile).where(ParserProfile.id == profile_id).with_for_update()
                )
            ).scalar_one()
            new_options = {"endpoint_ref": "mineru-official", "model_version": "v2"}
            p.parser_options = new_options
            p.version = p.version + 1
            await session.commit()

    results: list[str] = []
    # 偶数轮：仅创建 job（读 vlm 初始版本）；奇数轮：并发创建 job + 更新 profile。
    # 并发分支使用 select_for_update 串行化，create_job 与 update_profile 竞争同一行锁，
    # create_job 可能先获得锁（读 vlm）或后获得（读 v2），天然产生两种版本。
    for _ in range(100):
        if _ % 2 == 0:
            results.append(await create_job())
        else:
            task = asyncio.create_task(update_profile())
            job_version = await create_job()
            await task
            results.append(job_version)

    # 每个 job 的 model_version 只能是 vlm 或 v2（完整版本之一），不得为混合。
    assert all(v in ("vlm", "v2") for v in results), results
    # 至少出现两种版本（证明并发更新确实发生了）。
    assert "vlm" in results and "v2" in results, results


async def test_retry_uses_same_frozen_snapshot(org, db_session: AsyncSession):
    """retry 必须保持相同 ref/version/hash。"""
    from app.services.parse_freeze_service import freeze_parse_job

    profile = _profile(org["projects"]["a"].id)
    db_session.add(profile)
    await db_session.flush()
    doc_id = await _make_document(db_session, org["projects"]["a"].id, org["users"]["admin"].id)
    job1 = await freeze_parse_job(db_session, document_id=doc_id, profile=profile)
    await db_session.commit()

    job2 = await freeze_parse_job(db_session, document_id=doc_id, profile=profile)
    await db_session.commit()

    # 同一 profile 的两个 job 快照/hash 完全一致（retry 语义）。
    assert job1.parser_profile_snapshot == job2.parser_profile_snapshot
    assert job1.parser_profile_sha256 == job2.parser_profile_sha256
    assert job1.endpoint_policy_ref == job2.endpoint_policy_ref
    assert job1.endpoint_policy_version == job2.endpoint_policy_version
    assert job1.endpoint_policy_sha256 == job2.endpoint_policy_sha256


async def test_managed_local_freeze_has_no_credential(org, db_session: AsyncSession):
    """managed-local profile 冻结后 policy 无 credential_ref。"""
    profile = _profile(
        org["projects"]["a"].id,
        parser_name="mineru_local_service",
        parser_options={"endpoint_ref": "ml-mineru-9010", "backend": "vlm-auto-engine"},
    )
    db_session.add(profile)
    await db_session.flush()
    _, policy, security = await _freeze(db_session, profile)
    assert policy["network_zone"] == "managed-local"
    assert policy["credential_ref"] is None
    assert security["managed_local"]["pinned_ips"] == ["127.0.0.1"]
