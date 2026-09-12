"""T05 清洗编辑并发与租约集成测试（验收 #1-#14）。

覆盖任务卡 §11 自动化验收：
1. 两个用户并发 acquire 同一 Section，恰好一个成功，另一个 409；数据库最多一条未释放 lease。
2. 同一用户重复 acquire 返回同一 lease_id，不新增有效记录。
3. 旧 lease_id 的 heartbeat/release/save 均不能续期、释放或修改后来者的 lease/内容。
4. 两客户端从 revision 5 保存，只有一个变为 6；另一个 409，正文和修订历史无双写。
5. Redis 断开时合法持租者仍保存；无租约者仍被拒绝（数据库为正确性来源）。
6. submit 携带 revision+lease，成功后释放 lease。
9. 20 个不同幂等键并发合并：version/version UUID/artifact key 唯一；对象 hash 与库一致。
10. 20 个相同幂等键并发合并只得到一个版本和一个正式对象；其余稳定重放，无 500。
11. 内容计算与发布之间修改 Section -> 409 CLEAN_SOURCE_CHANGED，无正式版本。
12. 两个 reviewer 并发 accept/reject 同一 version，只有一个成功；另一个 review conflict。
13/14. active pointer 单调不回退；迟到 reject 不清空刚接受的 active。

并发测试使用 `_test_session_factory` 的独立 session（与现有 test_parse_job_config_snapshot
模式一致），避免共享 AsyncSession 被多个协程并发使用；HTTP 层的 409 错误映射用顺序请求验证。
"""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.section import Section, SectionLease
from app.services.clean_version_service import content_sha256
from app.services.section_service import (
    SectionLeaseHeldError,
    SectionLeaseLostError,
    SectionService,
    SectionVersionConflictError,
)

PASSWORD = "password-123"


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _RecorderStorage:
    """记录上传/删除对象；充当 fake MinIO（不依赖真实服务）。"""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.upload_calls: list[tuple[str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def upload_file(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.objects[f"{bucket}/{key}"] = data
        self.upload_calls.append((bucket, key))
        return key

    def download_file(self, bucket: str, key: str) -> bytes:
        return self.objects.get(f"{bucket}/{key}", b"")

    def delete_file(self, bucket: str, key: str) -> None:
        self.delete_calls.append((bucket, key))
        self.objects.pop(f"{bucket}/{key}", None)


# ---------------------------------------------------------------------------
# 并发 acquire（验收 1）：独立 session，真并发，数据库至多一条未释放 lease。
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_concurrent_acquire_exactly_one_success(
    full_resources, _test_session_factory, monkeypatch
):
    a = full_resources["projects"]["a"]
    section_id = a["section"].id
    editor_id = full_resources["users"]["editor"].id
    reviewer_id = full_resources["users"]["reviewer"].id

    async def acquire(session, user_id):
        service = SectionService(session, redis_client=None)
        return await service.acquire_lease(section_id, user_id)

    async def run(user_id):
        async with _test_session_factory() as session:
            try:
                lease = await acquire(session, user_id)
                await session.commit()
                return ("ok", lease)
            except SectionLeaseHeldError:
                await session.rollback()
                return ("held", None)

    results = await asyncio.gather(run(editor_id), run(reviewer_id))
    codes = sorted(r[0] for r in results)
    assert codes == ["held", "ok"], f"期望一成功一冲突，实际 {codes}"

    # 数据库至多一条未释放 lease。
    async with _test_session_factory() as session:
        rows = (
            await session.execute(
                select(SectionLease).where(
                    SectionLease.section_id == section_id,
                    SectionLease.released_at.is_(None),
                )
            )
        ).scalars().all()
        assert len(rows) <= 1, f"未释放 lease 数量应为 ≤1，实际 {len(rows)}"


@pytest.mark.integration
async def test_repeat_acquire_idempotent_same_lease(full_resources, _test_session_factory):
    """验收 2：同一用户重复 acquire 返回同一 lease_id，不新增有效记录。"""
    a = full_resources["projects"]["a"]
    section_id = a["section"].id
    editor_id = full_resources["users"]["editor"].id

    async with _test_session_factory() as session:
        service = SectionService(session, redis_client=None)
        lease1 = await service.acquire_lease(section_id, editor_id)
        await session.commit()
        lease2 = await service.acquire_lease(section_id, editor_id)
        await session.commit()
        assert lease1.id == lease2.id, "重复 acquire 应返回同一 lease_id"

        rows = (
            await session.execute(
                select(SectionLease).where(
                    SectionLease.section_id == section_id,
                    SectionLease.released_at.is_(None),
                )
            )
        ).scalars().all()
        assert len(rows) == 1, f"有效 lease 应为 1，实际 {len(rows)}"


@pytest.mark.integration
async def test_old_lease_cannot_touch_new_lease(
    client: AsyncClient, full_resources, db_session: AsyncSession
):
    """验收 3：旧 lease_id 的 heartbeat/release/save 均不能续期、释放或修改后来者的 lease/内容。"""
    a = full_resources["projects"]["a"]
    section_id = a["section"].id
    tokens_editor = await _login(client, "editor_user")
    tokens_reviewer = await _login(client, "reviewer_user")
    he = _bearer(tokens_editor["access_token"])
    hr = _bearer(tokens_reviewer["access_token"])

    # editor 先 acquire 然后 release（模拟旧客户端）。
    r = await client.post(f"/api/sections/{section_id}/lease/acquire", headers=he)
    old_lease_id = r.json()["id"]
    rel = await client.post(f"/api/sections/{section_id}/lease/release", json={"lease_id": old_lease_id}, headers=he)
    assert rel.status_code == 204, rel.text

    # reviewer 后来 acquire（新 lease）。
    r = await client.post(f"/api/sections/{section_id}/lease/acquire", headers=hr)
    assert r.status_code == 200, r.text
    new_lease_id = r.json()["id"]
    assert new_lease_id != old_lease_id

    # 旧 lease 的 heartbeat 必须 409（不能续期）。
    hb = await client.post(
        f"/api/sections/{section_id}/lease/heartbeat", json={"lease_id": old_lease_id}, headers=he
    )
    assert hb.status_code == 409, hb.text
    assert hb.json().get("code") == "SECTION_LEASE_LOST", hb.text

    # 旧 lease 的 release 不影响新 lease（重复释放 204）。
    rel2 = await client.post(
        f"/api/sections/{section_id}/lease/release", json={"lease_id": old_lease_id}, headers=he
    )
    assert rel2.status_code == 204, rel2.text
    # 新 lease 仍然有效。
    hb_new = await client.post(
        f"/api/sections/{section_id}/lease/heartbeat", json={"lease_id": new_lease_id}, headers=hr
    )
    assert hb_new.status_code == 200, hb_new.text

    # 旧 lease 的 save 必须 409（不能修改内容）。
    patch = await client.patch(
        f"/api/sections/{section_id}",
        json={"cleaned_markdown": "旧客户端写入", "expected_revision": 0, "lease_id": old_lease_id},
        headers=he,
    )
    assert patch.status_code == 409, patch.text
    assert patch.json().get("code") == "SECTION_LEASE_LOST", patch.text


# ---------------------------------------------------------------------------
# 并发保存 revision 5 -> 6（验收 4）：独立 session，行锁串行化，无双写。
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_two_clients_revision_5_only_one_becomes_6(
    full_resources, _test_session_factory
):
    """验收 4：两客户端从 revision 5 保存，只有一个变为 6；另一个 409，无双写。"""
    a = full_resources["projects"]["a"]
    section_id = a["section"].id
    editor_id = full_resources["users"]["editor"].id
    reviewer_id = full_resources["users"]["reviewer"].id

    # 先把 section 推到 revision 5。
    async with _test_session_factory() as session:
        service = SectionService(session, redis_client=None)
        lease = await service.acquire_lease(section_id, editor_id)
        for i in range(5):
            section = await service.update_section(
                section_id, f"v{i}", editor_id, expected_revision=i, lease_id=lease.id
            )
        await session.commit()
        assert section.content_revision == 5

    # 释放旧 lease（reviewer acquire 新 lease）。
    async with _test_session_factory() as session:
        service = SectionService(session, redis_client=None)
        await service.release_lease(section_id, editor_id, lease.id)
        await session.commit()
        lease2 = await service.acquire_lease(section_id, reviewer_id)
        await session.commit()

    async def save_from(session, user_id, lid, text):
        service = SectionService(session, redis_client=None)
        try:
            section = await service.update_section(
                section_id, text, user_id, expected_revision=5, lease_id=lid
            )
            await session.commit()
            return ("ok", section)
        except SectionVersionConflictError:
            await session.rollback()
            return ("conflict", None)
        except SectionLeaseLostError:
            await session.rollback()
            return ("leaselost", None)

    async def run(user_id, lid):
        async with _test_session_factory() as session:
            return await save_from(session, user_id, lid, f"client-{user_id}")

    # 同一 lease 下两个请求并发（客户端 A/B 共享同一编辑会话也竞争 revision）。
    results = await asyncio.gather(
        run(reviewer_id, lease2.id),
        run(reviewer_id, lease2.id),
    )
    codes = sorted(r[0] for r in results)
    assert codes == ["conflict", "ok"], f"期望一成功一 conflict，实际 {codes}"

    # 数据库 revision 只能为 6，且只有一条修订历史（无双写）。
    async with _test_session_factory() as session:
        fresh = (
            await session.execute(select(Section).where(Section.id == section_id))
        ).scalar_one()
        assert fresh.content_revision == 6, f"revision 应为 6，实际 {fresh.content_revision}"
        from app.models.section import SectionRevision

        rev_count = (
            await session.execute(
                select(func.count()).select_from(SectionRevision).where(
                    SectionRevision.section_id == section_id,
                    SectionRevision.to_revision == 6,
                )
            )
        ).scalar_one()
        assert rev_count == 1, "revision 6 的修订记录应只有一条"


@pytest.mark.integration
async def test_redis_down_lease_holder_saves_but_no_lease_rejected(
    full_resources, _test_session_factory
):
    """验收 5：Redis 断开时合法持租者仍保存；无租约者仍被拒绝（数据库为正确性来源）。"""
    a = full_resources["projects"]["a"]
    section_id = a["section"].id
    editor_id = full_resources["users"]["editor"].id
    reviewer_id = full_resources["users"]["reviewer"].id

    async with _test_session_factory() as session:
        # 不注入 redis（redis_client=None 即 Redis 故障），数据库路径仍正常。
        service = SectionService(session, redis_client=None)
        lease = await service.acquire_lease(section_id, editor_id)
        section = await service.update_section(
            section_id, "redis-down-save", editor_id, expected_revision=0, lease_id=lease.id
        )
        assert section is not None
        assert section.content_revision == 1
        await session.commit()

    async with _test_session_factory() as session:
        # 无租约者（reviewer 无 lease）保存被拒绝。
        service = SectionService(session, redis_client=None)
        with pytest.raises(SectionLeaseLostError):
            await service.update_section(
                section_id, "no-lease", reviewer_id, expected_revision=1, lease_id=uuid.uuid4()
            )
        await session.rollback()


@pytest.mark.integration
async def test_section_save_and_submit_release_lease(
    client: AsyncClient, full_resources
):
    """验收 6 服务端部分：submit 携带 revision+lease，成功后释放 lease。"""
    a = full_resources["projects"]["a"]
    section_id = a["section"].id
    tokens = await _login(client, "editor_user")
    headers = _bearer(tokens["access_token"])

    lease = (await client.post(f"/api/sections/{section_id}/lease/acquire", headers=headers)).json()
    res = await client.patch(
        f"/api/sections/{section_id}",
        json={"cleaned_markdown": "final", "expected_revision": 0, "lease_id": lease["id"]},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["content_revision"] == 1

    sub = await client.post(
        f"/api/sections/{section_id}/submit",
        json={"expected_revision": 1, "lease_id": lease["id"]},
        headers=headers,
    )
    assert sub.status_code == 200, sub.text

    # submit 成功后 lease 被释放：heartbeat 应 409。
    hb = await client.post(
        f"/api/sections/{section_id}/lease/heartbeat", json={"lease_id": lease["id"]}, headers=headers
    )
    assert hb.status_code == 409, hb.text


@pytest.mark.integration
async def test_empty_section_survives_http_merge_preview_and_revision(
    client: AsyncClient, full_resources, _test_session_factory, monkeypatch
):
    """R08：显式删除经 API 保存、合并预览、再次编辑的历史修订都不能恢复原文。"""
    from app.services import clean_version_service as cvs

    a = full_resources["projects"]["a"]
    section_id = a["section"].id
    editor = _bearer((await _login(client, "editor_user"))["access_token"])
    reviewer = _bearer((await _login(client, "reviewer_user"))["access_token"])
    async with _test_session_factory() as session:
        session.add(Section(
            document_id=a["document"].id, cleaning_job_id=a["cleaning_job"].id,
            ordinal=1, heading_path="保留章节", raw_markdown="保留正文", cleaned_markdown="保留正文",
        ))
        await session.commit()
    lease_response = await client.post(f"/api/sections/{section_id}/lease/acquire", headers=editor)
    assert lease_response.status_code == 200, lease_response.text
    lease_id = lease_response.json()["id"]
    cleared = await client.patch(f"/api/sections/{section_id}", headers=editor, json={
        "cleaned_markdown": "", "expected_revision": 0, "lease_id": lease_id,
    })
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cleaned_markdown"] == ""

    recorder = _RecorderStorage()
    monkeypatch.setattr(cvs, "get_storage_client", lambda *args, **kwargs: recorder)
    merged = await client.post(
        f"/api/projects/{a['pid']}/documents/{a['document'].id}/cleaning/merge",
        params={"cleaning_job_id": str(a["cleaning_job"].id)},
        headers={**reviewer, "Idempotency-Key": "empty-section-regression"},
    )
    assert merged.status_code == 201, merged.text
    preview = await client.get(f"/api/cleaned-versions/{merged.json()['id']}", headers=editor)
    assert preview.status_code == 200, preview.text
    assert preview.json()["merged_markdown"] == "保留正文\n"
    assert next(iter(recorder.objects.values())).decode("utf-8") == "保留正文\n"

    changed_again = await client.patch(f"/api/sections/{section_id}", headers=editor, json={
        "cleaned_markdown": "新的正文", "expected_revision": 1, "lease_id": lease_id,
    })
    assert changed_again.status_code == 200, changed_again.text
    revisions = await client.get(f"/api/sections/{section_id}/revisions", headers=editor)
    assert revisions.status_code == 200, revisions.text
    before_second_edit = next(row for row in revisions.json() if row["from_revision"] == 1)
    assert before_second_edit["cleaned_markdown"] == ""


# ---------------------------------------------------------------------------
# 合并并发（验收 9/10）：独立 session 并发调 create_merged_version。
# ---------------------------------------------------------------------------


async def _merge_ctx(full_resources, monkeypatch, client):
    a = full_resources["projects"]["a"]
    recorder = _RecorderStorage()
    from app.services import clean_version_service as cvs

    monkeypatch.setattr(cvs, "get_storage_client", lambda *a, **k: recorder)
    return {
        "pid": a["pid"],
        "doc_id": a["document"].id,
        "cjid": a["cleaning_job"].id,
        "reviewer_id": full_resources["users"]["reviewer"].id,
        "recorder": recorder,
    }


@pytest.mark.integration
async def test_merge_unique_idempotency_keys_all_versions_unique(
    full_resources, _test_session_factory, monkeypatch
):
    """验收 9：20 个不同幂等键并发合并，version/version UUID/artifact key 唯一；对象 hash 与库一致。"""
    ctx = await _merge_ctx(full_resources, monkeypatch, None)
    from app.services.clean_version_service import CleanVersionService

    async def do_merge(key):
        async with _test_session_factory() as session:
            service = CleanVersionService(session)
            version = await service.create_merged_version(
                ctx["doc_id"], ctx["cjid"], ctx["reviewer_id"], idempotency_key=key
            )
            await session.commit()
            return version

    results = await asyncio.gather(*[do_merge(f"k-{i}") for i in range(20)])
    ids = {r.id for r in results}
    vers = {r.version for r in results}
    keys = {r.artifact_key for r in results}
    assert len(ids) == 20, "version UUID 必须唯一"
    assert len(vers) == 20, "version 必须唯一"
    assert len(keys) == 20, "artifact key 必须唯一"

    for v in results:
        blob = ctx["recorder"].objects.get(f"outputs-test/{v.artifact_key}")
        assert blob is not None, f"对象缺失 {v.artifact_key}"
        assert content_sha256(blob.decode("utf-8")) == v.content_sha256, "对象内容 hash 与库不一致"
        assert v.source_revision_sha256, "source revision hash 必须非空"
        assert v.merge_idempotency_key, "幂等键必须非空"


@pytest.mark.integration
async def test_merge_same_idempotency_key_single_version(
    full_resources, _test_session_factory, monkeypatch
):
    """验收 10：20 个相同幂等键并发合并只得到一个版本和一个正式对象；其余稳定重放。"""
    ctx = await _merge_ctx(full_resources, monkeypatch, None)
    from app.services.clean_version_service import CleanVersionService

    async def do_merge():
        async with _test_session_factory() as session:
            service = CleanVersionService(session)
            version = await service.create_merged_version(
                ctx["doc_id"], ctx["cjid"], ctx["reviewer_id"], idempotency_key="same-key"
            )
            await session.commit()
            return version.id

    ids = await asyncio.gather(*[do_merge() for _ in range(20)])
    assert len(set(ids)) == 1, f"相同幂等键应只产生一个版本，实际 {len(set(ids))}"
    assert len(ctx["recorder"].upload_calls) == 1, f"应只有一次对象上传，实际 {len(ctx['recorder'].upload_calls)}"


@pytest.mark.integration
async def test_merge_source_changed_returns_409(
    full_resources, _test_session_factory, monkeypatch, client: AsyncClient
):
    """验收 11：内容计算与发布之间修改 Section -> 409 CLEAN_SOURCE_CHANGED，无正式版本。

    在 compute 阶段（无锁读 Section）与 publish 阶段（Document 锁内锁定并复核 revision
    向量）之间，用独立 session 修改 Section revision，使发布阶段复核不一致 -> 409。
    实现：monkeypatch _load_locked_sections，先在其他连接提交 revision 变化，再执行原锁定
    复核（Document 锁不阻塞 Section 行锁，无死锁）。
    """
    ctx = await _merge_ctx(full_resources, monkeypatch, client)
    from app.services.clean_version_service import CleanSourceChangedError, CleanVersionService

    a = full_resources["projects"]["a"]
    section = a["section"]
    editor_id = full_resources["users"]["editor"].id
    section_id = section.id

    # 第一次正常 merge 建立版本基线。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        await service.create_merged_version(
            ctx["doc_id"], ctx["cjid"], ctx["reviewer_id"], idempotency_key="base-merge"
        )
        await session.commit()

    original_lock_sections = CleanVersionService._load_locked_sections
    bumped = {"done": False}

    async def _bump_then_lock(self, document_id, cleaning_job_id):
        # compute 阶段已完成（合并内容/hash 已计算），发布加锁前修改 Section revision。
        if not bumped["done"]:
            bumped["done"] = True
            async with _test_session_factory() as session:
                service = SectionService(session, redis_client=None)
                lease = await service.acquire_lease(section_id, editor_id)
                await service.update_section(
                    section_id, "changed-mid-merge", editor_id, expected_revision=0, lease_id=lease.id
                )
                await session.commit()
        return await original_lock_sections(self, document_id, cleaning_job_id)

    monkeypatch.setattr(CleanVersionService, "_load_locked_sections", _bump_then_lock)

    # 第二次 merge：发布阶段锁定复核 revision 向量不一致 -> CLEAN_SOURCE_CHANGED。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        with pytest.raises(CleanSourceChangedError):
            await service.create_merged_version(
                ctx["doc_id"], ctx["cjid"], ctx["reviewer_id"], idempotency_key="second-merge"
            )
        await session.rollback()

    # 无正式版本产生：full_resources 预置 1 个 cleaned_version + base-merge 1 个 = 2，
    # 第二次 merge 未新增（count 仍为 2）。
    async with _test_session_factory() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(CleanedDocumentVersion).where(
                    CleanedDocumentVersion.document_id == ctx["doc_id"]
                )
            )
        ).scalar_one()
        assert count == 2, f"应只有 2 个版本（预置 + 第一次 merge），实际 {count}"


# ---------------------------------------------------------------------------
# 终审并发（验收 12/13/14）。
# ---------------------------------------------------------------------------


async def _mk_version(full_resources, _test_session_factory, monkeypatch, idem_key):
    ctx = await _merge_ctx(full_resources, monkeypatch, None)
    from app.services.clean_version_service import CleanVersionService

    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        version = await service.create_merged_version(
            ctx["doc_id"], ctx["cjid"], ctx["reviewer_id"], idempotency_key=idem_key
        )
        await session.commit()
        return version


@pytest.mark.integration
async def test_final_review_concurrent_accept_reject_exactly_one(
    full_resources, _test_session_factory, monkeypatch
):
    """验收 12：两个 reviewer 并发 accept/reject 同一 version，只有一个成功；另一个 review conflict。"""
    version = await _mk_version(full_resources, _test_session_factory, monkeypatch, "fr-key")
    reviewer_id = full_resources["users"]["reviewer"].id
    admin_id = full_resources["users"]["admin"].id
    from app.services.clean_version_service import (
        CleanVersionReviewConflictError,
        CleanVersionService,
    )

    async def review(session, user_id, action):
        service = CleanVersionService(session)
        try:
            v = await service.final_review(
                version.id, user_id, action,
                document_id=version.document_id,
                cleaning_job_id=version.source_cleaning_job_id,
            )
            await session.commit()
            return ("ok", v.status)
        except CleanVersionReviewConflictError:
            await session.rollback()
            return ("conflict", None)
        except Exception:  # noqa: BLE001
            await session.rollback()
            return ("error", None)

    async def run(user_id, action):
        async with _test_session_factory() as session:
            return await review(session, user_id, action)

    results = await asyncio.gather(
        run(reviewer_id, "accept"),
        run(admin_id, "reject"),
    )
    codes = sorted(r[0] for r in results)
    assert codes == ["conflict", "ok"], f"期望一成功一 conflict，实际 {codes}"

    # 终态唯一一致。
    async with _test_session_factory() as session:
        final = (
            await session.execute(
                select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == version.id)
            )
        ).scalar_one()
        assert final.status in ("accepted", "rejected")
        # review record 唯一。
        from app.models.review_record import ReviewRecord

        rec_count = (
            await session.execute(
                select(func.count()).select_from(ReviewRecord).where(
                    ReviewRecord.entity_type == "cleaned_document_version",
                    ReviewRecord.entity_id == version.id,
                )
            )
        ).scalar_one()
        assert rec_count == 1, f"review record 应只有 1 条，实际 {rec_count}"


@pytest.mark.integration
async def test_active_version_monotonic_no_rollback(
    full_resources, _test_session_factory, monkeypatch
):
    """验收 13/14：active pointer 单调不回退；迟到 reject 不清空刚接受的 active。"""
    from app.services.clean_version_service import (
        CleanVersionReviewConflictError,
        CleanVersionService,
    )

    admin_id = full_resources["users"]["admin"].id

    v1 = await _mk_version(full_resources, _test_session_factory, monkeypatch, "v1")
    # accept v1 -> active = v1。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        await service.final_review(
            v1.id, admin_id, "accept",
            document_id=v1.document_id, cleaning_job_id=v1.source_cleaning_job_id,
        )
        await session.commit()

    v2 = await _mk_version(full_resources, _test_session_factory, monkeypatch, "v2")
    assert v2.version > v1.version, "v2 的版本号必须大于 v1"

    # 旧版本（v1）已 accepted：再次 accept -> review conflict（不再是 review_pending）。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        with pytest.raises(CleanVersionReviewConflictError):
            await service.final_review(
                v1.id, admin_id, "accept",
                document_id=v1.document_id, cleaning_job_id=v1.source_cleaning_job_id,
            )
        await session.rollback()

    # accept v2 -> active = v2（向前）。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        await service.final_review(
            v2.id, admin_id, "accept",
            document_id=v2.document_id, cleaning_job_id=v2.source_cleaning_job_id,
        )
        await session.commit()

    async with _test_session_factory() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == v2.document_id))
        ).scalar_one()
        assert doc.active_clean_version_id == v2.id, "active 应指向 v2"

    # 迟到 reject v1：已被 accepted，得到 review conflict，绝不清空 active。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        with pytest.raises(CleanVersionReviewConflictError):
            await service.final_review(
                v1.id, admin_id, "reject",
                document_id=v1.document_id, cleaning_job_id=v1.source_cleaning_job_id,
            )
        await session.rollback()

    async with _test_session_factory() as session:
        doc_after = (
            await session.execute(select(Document).where(Document.id == v2.document_id))
        ).scalar_one()
        assert doc_after.active_clean_version_id == v2.id, "迟到 reject 不得清空 active"


@pytest.mark.integration
async def test_final_review_stale_older_than_active(
    full_resources, _test_session_factory, monkeypatch
):
    """验收 13 补充：较旧 pending version 在较新版本 accepted 后 accept -> CLEAN_VERSION_STALE。"""
    from app.services.clean_version_service import (
        CleanVersionService,
        CleanVersionStaleError,
    )

    admin_id = full_resources["users"]["admin"].id

    v1 = await _mk_version(full_resources, _test_session_factory, monkeypatch, "s1")
    v2 = await _mk_version(full_resources, _test_session_factory, monkeypatch, "s2")
    assert v2.version > v1.version, "v2 的版本号必须大于 v1"

    # 新版本先 accept -> active = v2。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        await service.final_review(
            v2.id, admin_id, "accept",
            document_id=v2.document_id, cleaning_job_id=v2.source_cleaning_job_id,
        )
        await session.commit()

    # 旧版本 v1 仍是 review_pending，但旧于 active v2 -> CLEAN_VERSION_STALE。
    async with _test_session_factory() as session:
        service = CleanVersionService(session)
        with pytest.raises(CleanVersionStaleError) as exc_info:
            await service.final_review(
                v1.id, admin_id, "accept",
                document_id=v1.document_id, cleaning_job_id=v1.source_cleaning_job_id,
            )
        assert exc_info.value.target_version == v1.version
        assert exc_info.value.active_version == v2.version
        await session.rollback()

    # v1 保持 review_pending，active 不变。
    async with _test_session_factory() as session:
        v1_fresh = (
            await session.execute(
                select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == v1.id)
            )
        ).scalar_one()
        assert v1_fresh.status == "review_pending", "stale 后目标仍为 review_pending"
        doc = (
            await session.execute(select(Document).where(Document.id == v1.document_id))
        ).scalar_one()
        assert doc.active_clean_version_id == v2.id, "active pointer 不变"
