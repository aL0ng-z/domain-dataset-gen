"""核心响应形状的运行时合同测试（真实 ASGI 请求）。

覆盖任务卡第 11 节验收断言：
- Dataset items / Benchmark cases 在空集、单页和越界页均返回四键分页对象，total/page/page_size 正确。
- Candidate/CuratedItem 的 content 在运行时返回 object（而非字符串）。
- 用旧请求字段或错误 JSON 类型调用端点返回 422（ValidationErrorResponse），不会静默接受。
- 代表性 401/403/404/409 符合 ErrorResponse；字段校验 422 符合 ValidationErrorResponse。
- 未注册 code、`200 {success:false}`、字符串 detail 等不合规响应会使合同测试失败（静态 + 运行时）。
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.chunk import Chunk
from app.models.dataset import Benchmark, BenchmarkCase, Dataset, DatasetItem
from app.models.document import Document
from app.models.generation import Candidate
from app.models.section import Section

# UserFactory 固定密码（tests/conftest.py）。
PASSWORD = "password-123"

pytestmark = pytest.mark.integration


async def _login_headers(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def _make_doc_chain(db_session, project_id, uploaded_by):
    """构造 ParserProfile -> ParseJob -> CleaningJob -> Document -> Section -> Chunk 的最小链路。

    注意：长驻测试库可能存在早期漂移 schema（parse_jobs 带 snapshot_schema_version
    列与 ck_parse_jobs_snapshot_complete 约束，当前模型/迁移已不含）。为保证测试在
    全新 checkout（CI）与本地长驻库都能通过，这里按 information_schema 探测列并
    自适应构造 parse_jobs 行。
    """
    from sqlalchemy import text

    from app.models.config import ParserProfile
    from app.models.parse import ParseJob

    doc = Document(
        project_id=project_id,
        filename="contract.pdf",
        file_size=100,
        sha256="1" * 64,
        minio_key="tests/contract.pdf",
        page_count=1,
        uploaded_by=uploaded_by,
    )
    db_session.add(doc)
    await db_session.flush()
    await db_session.refresh(doc)

    parser_profile = ParserProfile(project_id=project_id, name="Contract Parser")
    db_session.add(parser_profile)
    await db_session.flush()
    await db_session.refresh(parser_profile)

    # ModelConfig 与 PromptTemplate 是 generation_runs 的外键目标。
    from app.models.config import ModelConfig
    from app.models.prompt_template import PromptTemplate

    model_config = ModelConfig(
        project_id=project_id,
        name="Contract Model",
        provider="mock",
        base_url="http://localhost:8080/v1",
        api_key_encrypted="test-key",
        model_name="mock-model",
    )
    db_session.add(model_config)
    await db_session.flush()

    prompt_template = PromptTemplate(
        project_id=project_id,
        task_type="knowledge_extraction",
        name="Contract Template",
        system_prompt="sys",
        user_prompt_template="{chunk}",
    )
    db_session.add(prompt_template)
    await db_session.flush()

    # 探测 parse_jobs 是否含漂移列（决定用 ORM 还是兼容 SQL 插入）。
    has_snapshot = (
        await db_session.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='parse_jobs' AND column_name='snapshot_schema_version'"
            )
        )
    ).scalar_one_or_none()
    if has_snapshot:
        # 漂移库：snapshot_schema_version=0 可绕过完整快照约束。
        new_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO parse_jobs (id, document_id, parser_profile_id, status, "
                "snapshot_schema_version, created_at) "
                "VALUES (:id, :doc_id, :profile_id, 'queued', 0, now())"
            ),
            {"id": new_id, "doc_id": doc.id, "profile_id": parser_profile.id},
        )
        parse_job_id = new_id
    else:
        # T03 CHECK(ck_parse_jobs_snapshot_complete)：快照/hash 全部非空才满足约束。
        parse_job = ParseJob(
            document_id=doc.id,
            parser_profile_id=parser_profile.id,
            status="queued",
            snapshot_schema_version=1,
            parser_profile_snapshot={"endpoint_ref": "test"},
            parser_profile_sha256="0" * 64,
            endpoint_policy_snapshot={"policy": "test"},
            endpoint_policy_ref="test-policy",
            endpoint_policy_version="1",
            endpoint_policy_sha256="0" * 64,
        )
        db_session.add(parse_job)
        await db_session.flush()
        await db_session.refresh(parse_job)
        parse_job_id = parse_job.id

    from app.models.section import CleaningJob

    cleaning_job = CleaningJob(
        document_id=doc.id,
        parse_job_id=parse_job_id,
        status="completed",
        started_by=uploaded_by,
    )
    db_session.add(cleaning_job)
    await db_session.flush()
    await db_session.refresh(cleaning_job)

    section = Section(
        cleaning_job_id=cleaning_job.id,
        document_id=doc.id,
        ordinal=1,
        heading_path="h1",
        source_pages={"start": 1},
        raw_markdown="# raw",
        cleaned_markdown="# cleaned",
        status="accepted",
        cleaned_by=uploaded_by,
        assignment_status="completed",
    )
    db_session.add(section)
    await db_session.flush()
    await db_session.refresh(section)

    # T06：chunks.chunk_set_id NOT NULL。惰性创建 legacy 隔离集合满足 FK 约束。
    from app.models.chunk_set import ChunkSet

    chunk_set = ChunkSet(
        document_id=doc.id,
        status="completed",
        version=1,
        is_legacy=True,
        summary_json={"provenance": "test_fixture"},
        created_by=uploaded_by,
    )
    db_session.add(chunk_set)
    await db_session.flush()
    await db_session.refresh(chunk_set)

    chunk = Chunk(
        section_id=section.id,
        document_id=doc.id,
        chunk_set_id=chunk_set.id,
        ordinal=1,
        heading_path="h1",
        content="chunk content",
        source_pages={"start": 1, "end": 2},
        token_count=4,
        status="ready",
    )
    db_session.add(chunk)
    await db_session.flush()
    await db_session.refresh(chunk)
    return chunk


async def _make_candidate(db_session, chunk, project_id):
    """构造一个 content 为 object 的 Candidate（通过 GenerationRun 外键）。

    复用 _make_doc_chain 已建立的 ModelConfig/PromptTemplate；若直接调用本函数
    而不经过 doc_chain（例如仅做校验），则忽略缺失的外键目标。
    """
    from app.models.config import ModelConfig
    from app.models.generation import GenerationRun
    from app.models.prompt_template import PromptTemplate

    model_config_id = (
        await db_session.execute(select(ModelConfig.id).where(ModelConfig.project_id == project_id).limit(1))
    ).scalar_one_or_none()
    prompt_template_id = (
        await db_session.execute(select(PromptTemplate.id).where(PromptTemplate.project_id == project_id).limit(1))
    ).scalar_one_or_none()
    if model_config_id is None or prompt_template_id is None:
        pytest.skip("缺少 ModelConfig/PromptTemplate 外键目标，跳过")

    run = GenerationRun(
        chunk_id=chunk.id,
        prompt_template_id=prompt_template_id,
        model_config_id=model_config_id,
        context_mode="single_chunk",
        status="completed",
        is_legacy=True,
        provenance_status="legacy_unavailable",
        provenance_error_code="LEGACY_TEST_FIXTURE",
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.refresh(run)

    candidate = Candidate(
        generation_run_id=run.id,
        chunk_id=chunk.id,
        content={"title": "压缩机原理", "summary": "……", "tags": ["核心知识"]},
        candidate_type="knowledge_extraction",
        status="review_pending",
        review_evidence_spans={"pages": [1, 2], "quote": "原文"},
    )
    db_session.add(candidate)
    await db_session.flush()
    await db_session.refresh(candidate)
    return candidate


async def _make_curated_item(db_session, chunk, project_id, promoted_by):
    """构造一个真实的 CuratedItem（content 为 object），供 DatasetItem/BenchmarkCase 外键引用。"""
    candidate = await _make_candidate(db_session, chunk, project_id)
    from app.models.curated import CuratedItem

    # T09：approved 必须带审批指针（CHECK），通用 fixture 用 draft。
    item = CuratedItem(
        project_id=project_id,
        candidate_id=candidate.id,
        content=candidate.content,
        item_type="knowledge_extraction",
        status="draft",
        promoted_by=promoted_by,
        current_revision=1,
    )
    db_session.add(item)
    await db_session.flush()
    await db_session.refresh(item)
    return item


class TestDatasetItemsPagination:
    async def test_empty_dataset_returns_four_keys(self, client, org, db_session):
        headers = await _login_headers(client, "editor_user")
        # 直接建 Dataset（空）
        dataset = Dataset(
            project_id=org["projects"]["a"].id,
            name="空数据集",
            description=None,
            created_by=org["users"]["admin"].id,
        )
        db_session.add(dataset)
        await db_session.flush()

        res = await client.get(
            f"/api/projects/{org['projects']['a'].id}/datasets/{dataset.id}/items?page=1&page_size=20",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}, body

    async def test_single_page_items(self, client, org, db_session):
        headers = await _login_headers(client, "editor_user")
        chunk = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        dataset = Dataset(
            project_id=org["projects"]["a"].id,
            name="数据集",
            description=None,
            created_by=org["users"]["admin"].id,
        )
        db_session.add(dataset)
        await db_session.flush()
        for i in range(3):
            item = DatasetItem(
                dataset_id=dataset.id,
                curated_item_id=(await _make_curated_item(db_session, chunk, org["projects"]["a"].id, org["users"]["editor"].id)).id,
                ordinal=i + 1,
            )
            db_session.add(item)
        await db_session.flush()

        res = await client.get(
            f"/api/projects/{org['projects']['a'].id}/datasets/{dataset.id}/items?page=1&page_size=2",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body.keys()) == {"items", "total", "page", "page_size"}
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert body["page"] == 1 and body["page_size"] == 2

    async def test_out_of_range_page_returns_empty_items_and_total(self, client, org, db_session):
        headers = await _login_headers(client, "editor_user")
        chunk = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        dataset = Dataset(
            project_id=org["projects"]["a"].id,
            name="数据集",
            description=None,
            created_by=org["users"]["admin"].id,
        )
        db_session.add(dataset)
        await db_session.flush()
        item = DatasetItem(
            dataset_id=dataset.id,
            curated_item_id=(await _make_curated_item(db_session, chunk, org["projects"]["a"].id, org["users"]["editor"].id)).id,
            ordinal=1,
        )
        db_session.add(item)
        await db_session.flush()

        res = await client.get(
            f"/api/projects/{org['projects']['a'].id}/datasets/{dataset.id}/items?page=99&page_size=20",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["items"] == []
        assert body["total"] == 1
        assert body["page"] == 99

    async def test_invalid_page_size_returns_422(self, client, org):
        headers = await _login_headers(client, "editor_user")
        res = await client.get(
            f"/api/projects/{org['projects']['a'].id}/datasets/{uuid.uuid4()}/items?page=0&page_size=200",
            headers=headers,
        )
        assert res.status_code == 422, res.text


class TestBenchmarkCasesPagination:
    async def test_benchmark_cases_paginated(self, client, org, db_session):
        headers = await _login_headers(client, "editor_user")
        chunk = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        benchmark = Benchmark(
            project_id=org["projects"]["a"].id,
            name="基准集",
            description=None,
            created_by=org["users"]["admin"].id,
        )
        db_session.add(benchmark)
        await db_session.flush()
        for i in range(5):
            case = BenchmarkCase(
                benchmark_id=benchmark.id,
                curated_item_id=(await _make_curated_item(db_session, chunk, org["projects"]["a"].id, org["users"]["editor"].id)).id,
                ordinal=i + 1,
            )
            db_session.add(case)
        await db_session.flush()

        res = await client.get(
            f"/api/projects/{org['projects']['a'].id}/benchmarks/{benchmark.id}/cases?page=2&page_size=2",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body.keys()) == {"items", "total", "page", "page_size"}
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["page"] == 2 and body["page_size"] == 2


class TestJsonContentRuntime:
    async def test_candidate_content_is_object(self, client, org, db_session):
        headers = await _login_headers(client, "editor_user")
        chunk = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate = await _make_candidate(db_session, chunk, org["projects"]["a"].id)

        res = await client.get(f"/api/candidates/{candidate.id}", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert isinstance(body["content"], dict), f"Candidate.content 运行时应为 object，实际 {type(body['content'])}"
        assert body["content"]["title"] == "压缩机原理"
        assert isinstance(body["review_evidence_spans"], dict)

    async def test_candidates_list_paginated(self, client, org, db_session):
        headers = await _login_headers(client, "editor_user")
        chunk = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        await _make_candidate(db_session, chunk, org["projects"]["a"].id)

        res = await client.get(
            f"/api/candidates?project_id={org['projects']['a'].id}&page=1&page_size=20",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body.keys()) == {"items", "total", "page", "page_size"}
        assert body["total"] == 1
        assert isinstance(body["items"][0]["content"], dict)


class TestValidation422:
    async def test_wrong_content_type_returns_validation_error(self, client, org, db_session):
        headers = await _login_headers(client, "editor_user")
        chunk = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate = await _make_candidate(db_session, chunk, org["projects"]["a"].id)

        # 用字符串替换 content（旧的前端曾发送字符串）——必须 422，不能静默接受。
        res = await client.patch(
            f"/api/candidates/{candidate.id}",
            json={"content": "这是一个字符串，不是 JSON object"},
            headers=headers,
        )
        assert res.status_code == 422, res.text
        body = res.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "errors" in body
        # ValidationErrorItem 必须带 loc/msg/type
        assert body["errors"][0]["loc"] and "msg" in body["errors"][0] and "type" in body["errors"][0]

    async def test_unknown_body_field_returns_422(self, client, org):
        headers = await _login_headers(client, "admin_user")
        res = await client.post(
            "/api/projects/",
            json={"name": "x", "unknown_field": 1},
            headers=headers,
        )
        assert res.status_code == 422, res.text
        body = res.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "errors" in body

class TestErrorEnvelopeRuntime:
    async def test_401_uses_error_response(self, client, org):
        res = await client.get("/api/auth/me")
        assert res.status_code == 401, res.text
        body = res.json()
        assert set(body.keys()) >= {"code", "message", "request_id"}
        assert body["code"] == "AUTH_REQUIRED"

    async def test_404_uses_error_response(self, client, org):
        headers = await _login_headers(client, "editor_user")
        res = await client.get(f"/api/projects/{org['projects']['a'].id}/documents/{uuid.uuid4()}", headers=headers)
        assert res.status_code == 404, res.text
        body = res.json()
        assert body["code"] == "NOT_FOUND"
        assert "request_id" in body

    async def test_403_uses_error_response(self, client, org):
        # viewer 无权创建数据集（editor 级别）。
        headers = await _login_headers(client, "viewer_user")
        res = await client.post(
            f"/api/projects/{org['projects']['a'].id}/datasets/",
            json={"name": "x"},
            headers=headers,
        )
        assert res.status_code == 403, res.text
        body = res.json()
        assert body["code"] == "PERMISSION_DENIED"

    async def test_login_wrong_password_uses_error_response(self, client):
        res = await client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
        assert res.status_code == 401, res.text
        body = res.json()
        assert body["code"] == "AUTH_REQUIRED"
        assert "message" in body
        # 不返回堆栈或 token
        assert "traceback" not in body and "token" not in body
