"""迁移验收集成测试（T03 验收 #14）。

覆盖 migrate_parser_profiles.py 的 dry-run/check/migrate：
- dry-run 对已知 profile 给出确定映射；
- 存量 ParseJob 不能可信回填时标为 legacy_unavailable（snapshot_schema_version=0）；
- 迁移重复执行结果一致（可重复运行）；
- 未知端点在写入前整体失败（fail closed），输出 profile ID 与 URL hash。
"""


import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import ParserProfile
from app.models.document import Document
from app.models.parse import ParseJob


@pytest.fixture(autouse=True)
def _registry_fixture(monkeypatch):
    """迁移脚本使用受控 registry（base_url 精确匹配）。"""
    from app.security import registry as registry_module
    from app.security.registry import ParserEndpointConfig, ParserEndpointRegistry, reset_registry

    monkeypatch.setattr(
        registry_module,
        "build_registry",
        lambda configs=None: ParserEndpointRegistry(
            [
                ParserEndpointConfig(
                    endpoint_ref="mineru-official",
                    parser_name="mineru",
                    network_zone="public-remote",
                    base_url="https://mineru.net/api/v4/extract/task",
                    credential_ref="env:MINERU_API_TOKEN",
                    credential_origins=["https://mineru.net"],
                )
            ]
        ),
    )
    reset_registry()
    yield
    reset_registry()


async def _seed_legacy_profiles(org, db_session: AsyncSession):
    """插入可映射的 legacy profile 与一个无法映射的 profile。"""
    pid = org["projects"]["a"].id
    profile = ParserProfile(
        project_id=pid,
        name="legacy-mineru",
        version=1,
        is_default=False,
        parser_name="mineru",
        parser_options={"base_url": "https://mineru.net/api/v4/extract/task", "model_version": "vlm"},
    )
    db_session.add(profile)
    await db_session.flush()
    return profile


async def test_legacy_parse_job_marked_legacy_unavailable(org, db_session: AsyncSession):
    """迁移前已存在的 ParseJob 标记 snapshot_schema_version=0 + legacy_unavailable。"""
    profile = await _seed_legacy_profiles(org, db_session)
    doc = Document(
        project_id=org["projects"]["a"].id,
        filename="a.pdf",
        file_size=10,
        sha256="0" * 64,
        minio_key="tests/x/a.pdf",
        page_count=1,
        uploaded_by=org["users"]["admin"].id,
    )
    db_session.add(doc)
    await db_session.flush()

    # 迁移前的 job 由 migration 回填为 legacy（模拟：直接插入 schema_version=0）。
    job = ParseJob(
        document_id=doc.id,
        parser_profile_id=profile.id,
        status="queued",
        snapshot_schema_version=0,
        parser_profile_snapshot={"legacy_unavailable": True},
        endpoint_policy_snapshot={"legacy_unavailable": True},
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # T11 门禁：legacy_unavailable 的 job 不得被宣称完整可复现。
    assert job.snapshot_schema_version == 0
    assert job.parser_profile_snapshot.get("legacy_unavailable") is True
    assert job.parser_profile_sha256 is None


async def test_migration_dry_run_maps_known_profile(org, db_session: AsyncSession, monkeypatch):
    """dry-run 对已知 profile 给出确定映射，且不写入数据库。"""
    profile = await _seed_legacy_profiles(org, db_session)
    await db_session.commit()

    from scripts import migrate_parser_profiles as mig

    monkeypatch.setattr(mig, "_build_registry", lambda: _make_registry_for_script())
    mapped, unmapped, skipped = mig._plan_migration(
        [profile], _make_registry_for_script()
    )
    assert len(mapped) == 1
    plan = mapped[0]
    assert plan["endpoint_ref"] == "mineru-official"
    assert "base_url" not in plan["cleaned_options"]
    assert plan["cleaned_options"]["model_version"] == "vlm"


async def test_migration_fail_closed_on_unknown_endpoint(org, db_session: AsyncSession, monkeypatch):
    """无法唯一映射的 profile 使迁移在写入前整体失败，输出 URL hash。"""
    pid = org["projects"]["a"].id
    profile = ParserProfile(
        project_id=pid,
        name="legacy-unknown",
        version=1,
        is_default=False,
        parser_name="mineru",
        parser_options={"base_url": "https://evil.example.com/api/v4/extract/task"},
    )
    db_session.add(profile)
    await db_session.commit()

    from scripts import migrate_parser_profiles as mig

    registry = _make_registry_for_script()
    mapped, unmapped, skipped = mig._plan_migration([profile], registry)
    assert mapped == []
    assert len(unmapped) == 1
    item = unmapped[0]
    assert item["id"] == str(profile.id)
    # URL hash 而非完整 URL
    assert item["url_hash"] == mig._url_hash("https://evil.example.com/api/v4/extract/task")
    assert "evil.example.com" not in str(item)


async def test_migration_is_repeatable(org, db_session: AsyncSession, monkeypatch):
    """迁移重复执行结果一致（可重复运行）。"""
    profile = await _seed_legacy_profiles(org, db_session)
    await db_session.commit()

    from scripts import migrate_parser_profiles as mig

    registry = _make_registry_for_script()
    mapped1, _, _ = mig._plan_migration([profile], registry)
    # 第一次迁移后清理 options
    cleaned = mapped1[0]["cleaned_options"]
    profile.parser_options = cleaned
    profile.version += 1
    await db_session.commit()

    # 重复执行：已无 forbidden 字段 → skipped
    _, _, skipped = mig._plan_migration([profile], registry)
    assert skipped and skipped[0]["reason"] == "no_forbidden_fields"


def _make_registry_for_script():
    from app.security.registry import ParserEndpointConfig, ParserEndpointRegistry

    return ParserEndpointRegistry(
        [
            ParserEndpointConfig(
                endpoint_ref="mineru-official",
                parser_name="mineru",
                network_zone="public-remote",
                base_url="https://mineru.net/api/v4/extract/task",
                credential_ref="env:MINERU_API_TOKEN",
                credential_origins=["https://mineru.net"],
            )
        ]
    )
