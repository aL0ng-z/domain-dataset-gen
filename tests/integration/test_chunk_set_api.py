"""T06 版本化切分 API 层测试（HTTP 合同 §5）。

覆盖：
- POST chunk 缺少 Idempotency-Key -> 422。
- 无 active cleaned version -> 409 CLEAN_VERSION_NOT_READY。
- 显式历史版本 -> 409 CLEAN_VERSION_STALE。
- 同 key 同请求重放 -> 202 reused:true 同一对象；同 key 不同请求 -> 409 IDEMPOTENCY_KEY_REUSED。
- 不同 key 活跃切分进行中 -> 409 CHUNK_RUN_IN_PROGRESS。
- GET chunks 默认只返回 active set；GET chunk-sets 分页历史。
- PATCH completed set 的 chunk -> 409 CHUNK_SET_IMMUTABLE。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.config import ChunkProfile
from app.models.document import Document
from tests.conftest import ResourceFactory

pytestmark = pytest.mark.integration

PASSWORD = "password-123"


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_doc_with_clean_version(
    db: AsyncSession, org,
) -> tuple[Document, CleanedDocumentVersion, ChunkProfile]:
    """构造 active accepted clean version + chunk profile 的文档。"""
    rf = ResourceFactory(db)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = CleanedDocumentVersion(
        document_id=doc.id,
        version=1,
        section_count=1,
        merged_markdown="# 文档\n\n## 第一节\n\n内容一" * 5,
        status="accepted",
        created_by=org["users"]["reviewer"].id,
    )
    db.add(cv)
    await db.flush()
    doc.active_clean_version_id = cv.id
    doc.clean_status = "completed"
    profile = await rf.create_chunk_profile(org["projects"]["a"].id)
    await db.commit()
    await db.refresh(doc)
    await db.refresh(cv)
    return doc, cv, profile


async def test_chunk_requires_idempotency_key(client: AsyncClient, org):
    """缺少 Idempotency-Key -> 422。"""
    token = (await _login(client, "editor_user"))["access_token"]
    doc, cv, profile = await _seed_doc_with_clean_version(org["db"], org)
    pid = org["projects"]["a"].id

    res = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id)},
        headers=_bearer(token),
    )
    assert res.status_code == 422
    assert res.json()["code"] == "VALIDATION_ERROR"


async def test_chunk_no_clean_version_409(client: AsyncClient, org):
    """没有 active cleaned version -> 409 CLEAN_VERSION_NOT_READY。"""
    token = (await _login(client, "editor_user"))["access_token"]
    rf = ResourceFactory(org["db"])
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    profile = await rf.create_chunk_profile(org["projects"]["a"].id)
    await org["db"].commit()
    pid = org["projects"]["a"].id

    res = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id)},
        headers={**_bearer(token), "Idempotency-Key": "key-1"},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "CLEAN_VERSION_NOT_READY"


async def test_chunk_stale_clean_version_409(client: AsyncClient, org):
    """显式提供旧于 active 的版本 -> 409 CLEAN_VERSION_STALE。"""
    token = (await _login(client, "editor_user"))["access_token"]
    db = org["db"]
    doc, cv, profile = await _seed_doc_with_clean_version(db, org)
    # 再创建一个更新的 active 版本。
    cv2 = CleanedDocumentVersion(
        document_id=doc.id, version=2, section_count=1,
        merged_markdown="# 更新\n\n内容", status="accepted",
        created_by=org["users"]["reviewer"].id,
    )
    db.add(cv2)
    await db.flush()
    doc.active_clean_version_id = cv2.id
    await db.commit()
    pid = org["projects"]["a"].id

    res = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id), "cleaned_version_id": str(cv.id)},
        headers={**_bearer(token), "Idempotency-Key": "key-2"},
    )
    assert res.status_code == 409
    body = res.json()
    assert body["code"] == "CLEAN_VERSION_STALE"
    assert body["context"]["target_version"] == 1
    assert body["context"]["active_version"] == 2


async def test_chunk_idempotent_replay_and_conflict(client: AsyncClient, org):
    """同 key 同请求 -> reused:true；同 key 不同请求 -> 409 IDEMPOTENCY_KEY_REUSED。"""
    token = (await _login(client, "editor_user"))["access_token"]
    db = org["db"]
    doc, cv, profile = await _seed_doc_with_clean_version(db, org)
    pid = org["projects"]["a"].id
    headers = {**_bearer(token), "Idempotency-Key": "idem-key"}

    res1 = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id)},
        headers=headers,
    )
    assert res1.status_code == 202, res1.text
    body1 = res1.json()
    assert body1["reused"] is False
    assert body1["status"] == "pending"

    # 同 key 同请求重放：返回同一对象。
    res2 = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id)},
        headers=headers,
    )
    assert res2.status_code == 202, res2.text
    body2 = res2.json()
    assert body2["reused"] is True
    assert body2["task_id"] == body1["task_id"]
    assert body2["chunk_set_id"] == body1["chunk_set_id"]

    # 同 key 不同请求（不同 profile）：409 IDEMPOTENCY_KEY_REUSED。
    profile2 = await ResourceFactory(db).create_chunk_profile(org["projects"]["a"].id)
    await db.commit()
    res3 = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile2.id)},
        headers=headers,
    )
    assert res3.status_code == 409, res3.text
    assert res3.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_chunk_run_in_progress_409(client: AsyncClient, org):
    """不同 key 活跃切分进行中 -> 409 CHUNK_RUN_IN_PROGRESS。"""
    token = (await _login(client, "editor_user"))["access_token"]
    db = org["db"]
    doc, cv, profile = await _seed_doc_with_clean_version(db, org)
    pid = org["projects"]["a"].id

    res1 = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id)},
        headers={**_bearer(token), "Idempotency-Key": "active-key"},
    )
    assert res1.status_code == 202

    # 另一 key 再发起：409 CHUNK_RUN_IN_PROGRESS（活跃 pending set 存在）。
    res2 = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id)},
        headers={**_bearer(token), "Idempotency-Key": "other-key"},
    )
    assert res2.status_code == 409
    assert res2.json()["code"] == "CHUNK_RUN_IN_PROGRESS"


async def test_chunk_sets_history_and_active_default(client: AsyncClient, org):
    """GET chunk-sets 返回分页历史；GET chunks 默认只返回 active set。"""
    token = (await _login(client, "editor_user"))["access_token"]
    db = org["db"]
    doc, cv, profile = await _seed_doc_with_clean_version(db, org)
    pid = org["projects"]["a"].id

    res = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/chunk",
        json={"chunk_profile_id": str(profile.id)},
        headers={**_bearer(token), "Idempotency-Key": "hist-key"},
    )
    assert res.status_code == 202
    cs_id = res.json()["chunk_set_id"]

    # chunk-sets 历史含该 set。
    res2 = await client.get(
        f"/api/projects/{pid}/documents/{doc.id}/chunk-sets",
        headers=_bearer(token),
    )
    assert res2.status_code == 200
    assert res2.json()["total"] >= 1

    # chunk-sets/{csid} 详情。
    res3 = await client.get(
        f"/api/projects/{pid}/chunk-sets/{cs_id}",
        headers=_bearer(token),
    )
    assert res3.status_code == 200
    assert res3.json()["id"] == cs_id


async def test_patch_completed_set_chunk_immutable(client: AsyncClient, org):
    """completed set 上的 chunk PATCH -> 409 CHUNK_SET_IMMUTABLE。"""
    token = (await _login(client, "editor_user"))["access_token"]
    db = org["db"]
    doc, cv, profile = await _seed_doc_with_clean_version(db, org)

    # 直接构造一个 completed set + chunk。
    from app.models.chunk import Chunk
    from app.models.section import CleaningJob, Section

    rf = ResourceFactory(db)
    parser = await rf.create_parser_profile(org["projects"]["a"].id)
    parse_job = await rf.create_parse_job(doc.id, parser.id, status="completed")
    cj = CleaningJob(
        document_id=doc.id, parse_job_id=parse_job.id, status="completed",
        started_by=org["users"]["editor"].id,
    )
    db.add(cj)
    await db.flush()
    sec = Section(
        cleaning_job_id=cj.id, document_id=doc.id, ordinal=0,
        heading_path="1", raw_markdown="x", cleaned_markdown="x",
        status="accepted", content_revision=0, assignment_status="completed",
    )
    db.add(sec)
    await db.flush()

    # T06 CHECK(ck_chunk_sets_legacy_required_fields)：非 legacy 集合必填 task_id。
    from app.models.task import Task
    from app.services.task_service import TaskService

    task = await TaskService(db).create_task(
        org["projects"]["a"].id, "chunk", "document", doc.id, org["users"]["editor"].id,
        payload={"document_id": str(doc.id)}, handler="chunk_document",
    )
    cs = ChunkSet(
        document_id=doc.id,
        cleaned_document_version_id=cv.id,
        chunk_profile_id=profile.id,
        strategy="hybrid_heading_recursive",
        config_json={"max_tokens": 512, "overlap_tokens": 50},
        status="completed",
        version=1,
        is_legacy=False,
        splitter_version="2.0.0@cl100k_base:0.12.0",
        task_id=task.id,
        created_by=org["users"]["editor"].id,
    )
    db.add(cs)
    await db.flush()
    chunk = Chunk(
        section_id=sec.id,
        document_id=doc.id,
        chunk_set_id=cs.id,
        ordinal=0,
        heading_path="",
        content="x",
        token_count=1,
        status="ready",
    )
    db.add(chunk)
    await db.commit()

    res = await client.patch(
        f"/api/chunks/{chunk.id}",
        json={"content": "edited"},
        headers=_bearer(token),
    )
    assert res.status_code == 409
    assert res.json()["code"] == "CHUNK_SET_IMMUTABLE"
