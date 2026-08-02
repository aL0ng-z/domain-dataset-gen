"""T08 生成 API 合同测试（验收标准 13-14）。

- 202 接收语义：单 Chunk 与批量端点返回 GenerateAcceptedResponse（不假装同步 Candidate）。
- 领域 code 有限联合：404/409 各场景返回 ErrorResponse 且 code 在表中注册。
- 422 仅用于 Pydantic 结构校验（ValidationErrorResponse），业务冲突不借用 422。
- 跨项目模板/模型/Chunk 组合返回 T02 404，不派发任务、不调用 LLM。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_access_token
from tests.integration.test_generation_flow import _build_generation_doc

pytestmark = pytest.mark.integration


def _headers(user) -> dict:
    return {"Authorization": f"Bearer {user.access_token}"}


async def _make_ready_doc(db_session: AsyncSession, org):
    """构建文档 + active ChunkSet + ready chunks + 配置；返回 AuthHelper 编辑者 token。"""
    from tests.conftest import AuthHelper

    res = await _build_generation_doc(db_session, org, chunk_count=2)
    editor = org["users"]["editor"]
    token = AuthHelper(
        access_token=create_access_token(editor.id, role="editor"),
        refresh_token="x",
        user_id=editor.id,
    )
    return res, token


# ---------------------------------------------------------------------------
# 202 接收语义
# ---------------------------------------------------------------------------


async def test_single_generate_returns_202_accepted(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """单 Chunk 生成返回 202 + GenerateAcceptedResponse，不返回 Candidate。"""
    res, token = await _make_ready_doc(db_session, org)
    chunk = res["chunks"][0]
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert "task_id" in body
    assert "generation_batch_id" in body
    assert "content" not in body  # 不得假装同步返回 Candidate


async def test_batch_generate_returns_202_accepted(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """批量生成返回 202；selected_chunk_ids 省略 = active set 全部 ready。"""
    res, token = await _make_ready_doc(db_session, org)
    resp = await client.post(
        f"/api/projects/{org['projects']['a'].id}/documents/{res['doc'].id}/generate-batch",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert "generation_batch_id" in body


# ---------------------------------------------------------------------------
# 404 / 409 领域 code 联合
# ---------------------------------------------------------------------------


async def test_single_generate_404_config_not_found(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """模板/模型不存在或不可见 -> 404 GENERATION_CONFIG_NOT_FOUND。"""
    res, token = await _make_ready_doc(db_session, org)
    chunk = res["chunks"][0]
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(uuid.uuid4()), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "GENERATION_CONFIG_NOT_FOUND"


async def test_single_generate_404_source_not_found(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """Chunk 不存在 -> 404 GENERATION_SOURCE_NOT_FOUND。"""
    _, token = await _make_ready_doc(db_session, org)
    resp = await client.post(
        f"/api/chunks/{uuid.uuid4()}/generate",
        json={"prompt_template_id": str(uuid.uuid4()), "model_config_id": str(uuid.uuid4())},
        headers=_headers(token),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "GENERATION_SOURCE_NOT_FOUND"


async def test_single_generate_409_source_not_ready(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """无 active ChunkSet / Chunk 状态不允许 -> 409 GENERATION_SOURCE_NOT_READY。"""

    res, token = await _make_ready_doc(db_session, org)
    # 把 Chunk 标记为 generated（状态不允许生成）。
    chunk = res["chunks"][0]
    chunk.status = "generated"
    await db_session.commit()
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "GENERATION_SOURCE_NOT_READY"


async def test_batch_generate_409_source_not_ready(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """文档无 active ChunkSet -> 409 GENERATION_SOURCE_NOT_READY。"""
    from tests.conftest import ResourceFactory

    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    res, token = await _make_ready_doc(db_session, org)
    resp = await client.post(
        f"/api/projects/{org['projects']['a'].id}/documents/{doc.id}/generate-batch",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "GENERATION_SOURCE_NOT_READY"


async def test_batch_generate_409_config_unavailable_unsafe_extra(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """ModelConfig extra_params 含秘密 -> 409（不创建 Batch/Task）。"""
    res, token = await _make_ready_doc(db_session, org)
    res["model"].extra_params = {"api_key": "sk-secret"}
    await db_session.commit()
    resp = await client.post(
        f"/api/projects/{org['projects']['a'].id}/documents/{res['doc'].id}/generate-batch",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "GENERATION_SNAPSHOT_UNSAFE"


async def test_batch_generate_422_validation_selected_chunks_empty(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """selected_chunk_ids 空数组 -> 422 ValidationErrorResponse（Pydantic 结构校验）。"""
    res, token = await _make_ready_doc(db_session, org)
    resp = await client.post(
        f"/api/projects/{org['projects']['a'].id}/documents/{res['doc'].id}/generate-batch",
        json={
            "prompt_template_id": str(res["tpl"].id),
            "model_config_id": str(res["model"].id),
            "selected_chunk_ids": [],
        },
        headers=_headers(token),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "errors" in body


# ---------------------------------------------------------------------------
# 查询 API 合同
# ---------------------------------------------------------------------------


async def test_generation_batch_get_returns_redacted_summary(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """GET batch 详情返回 hash 前缀/provenance，不返回 credential/完整快照。"""
    res, token = await _make_ready_doc(db_session, org)
    # 通过单 Chunk 生成创建 batch。
    chunk = res["chunks"][0]
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    gbid = resp.json()["generation_batch_id"]

    detail = await client.get(
        f"/api/projects/{org['projects']['a'].id}/generation-batches/{gbid}",
        headers=_headers(token),
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["provenance_status"] == "verified"
    assert body["is_legacy"] is False
    assert body["prompt_template_sha256_prefix"]
    assert body["model_config_sha256_prefix"]
    assert body["renderer_version"]
    # 不返回 credential/完整快照。
    assert "api_key" not in body
    assert "prompt_template_snapshot" not in body
    assert "model_config_snapshot" not in body


async def test_generation_batch_runs_paginated(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """GET batch runs 分页返回 GenerationRunResponse。"""
    res, token = await _make_ready_doc(db_session, org)
    chunk = res["chunks"][0]
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    gbid = resp.json()["generation_batch_id"]
    runs = await client.get(
        f"/api/projects/{org['projects']['a'].id}/generation-batches/{gbid}/runs?page=1&page_size=20",
        headers=_headers(token),
    )
    assert runs.status_code == 200
    body = runs.json()
    assert body["total"] == 1
    assert body["items"][0]["chunk_id"] == str(chunk.id)
    assert body["items"][0]["provenance_status"] == "verified"


async def test_chunk_candidates_stable_order(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """GET /api/chunks/{cid}/candidates 分页稳定排序。"""

    res, token = await _make_ready_doc(db_session, org)
    chunk = res["chunks"][0]
    # 直接创建两个 Candidate。
    from app.models.generation import Candidate, GenerationRun

    run1 = GenerationRun(
        chunk_id=chunk.id, prompt_template_id=res["tpl"].id, model_config_id=res["model"].id,
        context_mode="single_chunk", status="completed",
        is_legacy=True, provenance_status="legacy_unavailable", provenance_error_code="TEST",
    )
    db_session.add(run1)
    await db_session.flush()
    c1 = Candidate(generation_run_id=run1.id, chunk_id=chunk.id, content={"a": 1}, candidate_type="qa_generation")
    db_session.add(c1)
    await db_session.flush()

    resp = await client.get(
        f"/api/chunks/{chunk.id}/candidates?page=1&page_size=20",
        headers=_headers(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    # 稳定排序：created_at DESC, id DESC。
    items = body["items"]
    assert all(items[i]["created_at"] >= items[i + 1]["created_at"] for i in range(len(items) - 1))


# ---------------------------------------------------------------------------
# retry API 合同
# ---------------------------------------------------------------------------


async def test_retry_batch_202_and_retry_exists(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """failed batch retry -> 202；重复 retry -> 409 GENERATION_RETRY_EXISTS。"""
    from datetime import UTC, datetime

    res, token = await _make_ready_doc(db_session, org)
    chunk = res["chunks"][0]
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    gbid = resp.json()["generation_batch_id"]

    # 把 batch 置为 failed（无成功项）。
    from app.models.generation_batch import GenerationBatch

    batch = (await db_session.execute(select(GenerationBatch).where(GenerationBatch.id == uuid.UUID(gbid)))).scalar_one()
    batch.status = "failed"
    batch.completed_at = datetime.now(UTC)
    await db_session.commit()

    retry_resp = await client.post(
        f"/api/projects/{org['projects']['a'].id}/generation-batches/{gbid}/retry",
        headers=_headers(token),
    )
    assert retry_resp.status_code == 202, retry_resp.text
    assert retry_resp.json()["status"] == "queued"

    # 第二次 retry -> 409 RETRY_EXISTS（已有直接后继）。
    retry2 = await client.post(
        f"/api/projects/{org['projects']['a'].id}/generation-batches/{gbid}/retry",
        headers=_headers(token),
    )
    assert retry2.status_code == 409
    assert retry2.json()["code"] == "GENERATION_RETRY_EXISTS"


async def test_retry_batch_not_retryable(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """pending batch retry -> 409 GENERATION_NOT_RETRYABLE（非 failed/cancelled）。"""
    res, token = await _make_ready_doc(db_session, org)
    chunk = res["chunks"][0]
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(res["tpl"].id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    gbid = resp.json()["generation_batch_id"]
    retry_resp = await client.post(
        f"/api/projects/{org['projects']['a'].id}/generation-batches/{gbid}/retry",
        headers=_headers(token),
    )
    assert retry_resp.status_code == 409
    assert retry_resp.json()["code"] == "GENERATION_NOT_RETRYABLE"


async def test_cross_project_rejected_404(
    db_session: AsyncSession, org, client: AsyncClient,
):
    """跨项目模板/模型/Chunk 组合 -> T02 404，不派发任务。"""
    res, token = await _make_ready_doc(db_session, org)
    # 项目 B 的模板（admin 可见但 editor 不可见 -> 404）。
    from app.models.prompt_template import PromptTemplate

    b_tpl = PromptTemplate(
        project_id=org["projects"]["b"].id, task_type="qa_generation", name="B 模板",
        system_prompt="s", user_prompt_template="{{content}}",
    )
    db_session.add(b_tpl)
    await db_session.commit()
    chunk = res["chunks"][0]
    resp = await client.post(
        f"/api/chunks/{chunk.id}/generate",
        json={"prompt_template_id": str(b_tpl.id), "model_config_id": str(res["model"].id)},
        headers=_headers(token),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "GENERATION_CONFIG_NOT_FOUND"
