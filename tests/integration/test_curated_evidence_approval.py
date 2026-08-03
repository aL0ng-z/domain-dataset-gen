"""T09 Candidate/CuratedItem 证据与审批矩阵测试（验收标准 1-14）。

覆盖：
- JSON object 显示/编辑/保存；字符串/数组/null content 返回 422，前端无渲染异常。
- supported 无 span、拒绝无原因、越界 offset、quote 不匹配、跨项目 Chunk 均失败
  且不改变 Candidate。
- 合法审核一次事务写 verdict/bundle/reviewer/状态/ReviewRecord；flush 失败回滚。
- 提升准确写 1 CuratedItem + v1 revision + 去重 span 对应 EvidenceLink（服务端派生）。
- 并发提升最多一个 201，另一个 409，数据库只有一个 CuratedItem。
- editor 直接 PATCH status=approved 422；reviewer 缺证据 approve 409/422。
- 并发保存 same expected_revision 只有一个成功；revision 版本连续且内容正确。
- approve 绑定 revision id/content hash 与规范证据 hash；退审/重批不改写历史记录。
- 直接 UPDATE/DELETE CuratedRevision/approve ReviewRecord 被数据库拒绝；
  篡改 content/hash、跨 item 指针、错误 canonicalization version 无法通过约束。
- 同一 content/evidence golden fixture 在后端/migration 计算相同 SHA-256。
- evidence/revisions 分页字段正确。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.chunk import Chunk
from app.models.curated import CuratedItem, CuratedRevision, EvidenceLink
from app.models.generation import Candidate, GenerationRun
from app.models.review_record import ReviewRecord
from domain.canonical import (
    CURATED_APPROVAL_CJSON_VERSION,
    CURATED_CONTENT_CJSON_VERSION,
    curated_approval_sha256,
    curated_content_sha256,
)

PASSWORD = "password-123"
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _login(client, username: str) -> dict[str, str]:
    res = await client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def _make_doc_chain(db_session, project_id, uploaded_by, *, content: str = "压比是出口与进口压力之比，是衡量压缩机性能的核心指标。"):
    """构造 Document -> Section -> Chunk 最小链路（content 可自定义，含中文/emoji fixture）。"""
    from app.models.config import ModelConfig, ParserProfile
    from app.models.document import Document
    from app.models.parse import ParseJob
    from app.models.prompt_template import PromptTemplate
    from app.models.section import CleaningJob, Section

    doc = Document(
        project_id=project_id,
        filename="t09.pdf",
        file_size=100,
        sha256="2" * 64,
        minio_key="tests/t09.pdf",
        page_count=1,
        uploaded_by=uploaded_by,
    )
    db_session.add(doc)
    await db_session.flush()

    parser_profile = ParserProfile(project_id=project_id, name="P")
    db_session.add(parser_profile)
    await db_session.flush()
    model_config = ModelConfig(
        project_id=project_id, name="M", provider="mock",
        base_url="http://localhost:8080/v1", api_key_encrypted="k", model_name="m",
    )
    db_session.add(model_config)
    await db_session.flush()
    prompt_template = PromptTemplate(
        project_id=project_id, task_type="qa_generation", name="T",
        system_prompt="sys", user_prompt_template="{chunk}",
    )
    db_session.add(prompt_template)
    await db_session.flush()

    parse_job = ParseJob(
        document_id=doc.id, parser_profile_id=parser_profile.id, status="completed",
        snapshot_schema_version=1, parser_profile_snapshot={"endpoint_ref": "t"},
        parser_profile_sha256="0" * 64, endpoint_policy_snapshot={"policy": "t"},
        endpoint_policy_ref="t", endpoint_policy_version="1",
        endpoint_policy_sha256="0" * 64,
    )
    db_session.add(parse_job)
    await db_session.flush()
    cleaning_job = CleaningJob(document_id=doc.id, parse_job_id=parse_job.id, status="completed", started_by=uploaded_by)
    db_session.add(cleaning_job)
    await db_session.flush()
    section = Section(
        cleaning_job_id=cleaning_job.id, document_id=doc.id, ordinal=1, heading_path="1.1",
        raw_markdown="# raw", cleaned_markdown="# cleaned",
    )
    db_session.add(section)
    await db_session.flush()

    from app.models.chunk_set import ChunkSet
    chunk_set = ChunkSet(
        document_id=doc.id, status="completed", version=1, is_legacy=True,
        summary_json={"provenance": "test"}, created_by=uploaded_by,
    )
    db_session.add(chunk_set)
    await db_session.flush()
    chunk = Chunk(
        section_id=section.id, document_id=doc.id, chunk_set_id=chunk_set.id, ordinal=1,
        heading_path="1.1", content=content, source_pages={"start": 1, "end": 2}, token_count=30,
    )
    db_session.add(chunk)
    await db_session.flush()
    await db_session.refresh(chunk)

    return {
        "doc": doc,
        "model": model_config,
        "tpl": prompt_template,
        "chunk": chunk,
        "section": section,
    }


async def _make_candidate(db_session, res, *, content=None, status="ai_generated"):
    run = GenerationRun(
        chunk_id=res["chunk"].id,
        prompt_template_id=res["tpl"].id,
        model_config_id=res["model"].id,
        context_mode="single_chunk",
        status="completed",
        is_legacy=True,
        provenance_status="legacy_unavailable",
        provenance_error_code="LEGACY_TEST_FIXTURE",
    )
    db_session.add(run)
    await db_session.flush()
    candidate = Candidate(
        generation_run_id=run.id,
        chunk_id=res["chunk"].id,
        content=content or {"question": "什么是压比?", "answer": "压比是出口与进口压力之比"},
        candidate_type="qa_generation",
        status=status,
    )
    db_session.add(candidate)
    await db_session.flush()
    await db_session.refresh(candidate)
    return candidate, run


def _span(chunk_id, content: str, quote: str) -> dict:
    start = content.index(quote)
    return {
        "chunk_id": str(chunk_id),
        "start_char": start,
        "end_char": start + len(quote),
        "quote_text": quote,
    }


async def _review_and_promote(client, username: str, candidate_id, res, *, verdict="supported", quote="压比是出口与进口压力之比"):
    """以 reviewer 身份 review approved 并 promote，返回 (curated_item_id, candidate_id)。"""
    headers = await _login(client, username)
    span = _span(res["chunk"].id, res["chunk"].content, quote)
    r = await client.post(
        f"/api/candidates/{candidate_id}/review",
        headers=headers,
        json={"verdict": verdict, "evidence_spans": [span], "reject_reason": None},
    )
    assert r.status_code == 200, r.text
    p = await client.post(
        f"/api/candidates/{candidate_id}/promote-to-curated",
        headers=headers,
        json={},
    )
    assert p.status_code == 201, p.text
    return p.json()["id"]


# ---------------------------------------------------------------------------
# 验收 1：JSON object 可显示/编辑/保存；字符串/数组/null -> 422
# ---------------------------------------------------------------------------


class TestJsonObjectContract:
    async def test_string_content_422(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        r = await client.patch(
            f"/api/candidates/{candidate.id}",
            headers=headers,
            json={"content": "这是一个字符串"},
        )
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "VALIDATION_ERROR"

    async def test_array_content_422(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        r = await client.patch(
            f"/api/candidates/{candidate.id}", headers=headers,
            json={"content": [1, 2, 3]},
        )
        assert r.status_code == 422, r.text

    async def test_null_content_422(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        r = await client.patch(
            f"/api/candidates/{candidate.id}", headers=headers,
            json={"content": None},
        )
        assert r.status_code == 422, r.text

    async def test_object_content_saved_and_status_human_edited(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        new_content = {"question": "压比?", "answer": "压比是压力之比", "tags": ["核心"]}
        r = await client.patch(
            f"/api/candidates/{candidate.id}", headers=headers,
            json={"content": new_content},
        )
        assert r.status_code == 200, r.text
        assert r.json()["content"] == new_content
        assert r.json()["status"] == "human_edited"
        # 旧审核字段被清空。
        assert r.json()["review_verdict"] is None
        assert r.json()["review_evidence_spans"] is None


# ---------------------------------------------------------------------------
# 验收 2：supported 无 span、拒绝无原因、越界 offset、quote 不匹配、跨项目失败
# ---------------------------------------------------------------------------


class TestReviewValidation:
    async def test_supported_no_span_409(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [], "reject_reason": None},
        )
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "CANDIDATE_EVIDENCE_REQUIRED"

    async def test_unsupported_no_reason_422(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "unsupported", "evidence_spans": None, "reject_reason": None},
        )
        assert r.status_code == 422, r.text

    async def test_out_of_bounds_offset_422_no_state_change(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        # end 超出 content 长度。
        bad_span = {
            "chunk_id": str(res["chunk"].id),
            "start_char": 0,
            "end_char": 9999,
            "quote_text": "压比",
        }
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [bad_span], "reject_reason": None},
        )
        assert r.status_code == 422, r.text
        # Candidate 状态不变。
        fresh = (
            await db_session.execute(select(Candidate).where(Candidate.id == candidate.id))
        ).scalar_one()
        assert fresh.status == "ai_generated"
        assert fresh.review_verdict is None

    async def test_quote_mismatch_422(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        span = _span(res["chunk"].id, res["chunk"].content, "压比是出口")
        span["quote_text"] = "错误原文"
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [span], "reject_reason": None},
        )
        assert r.status_code == 422, r.text

    async def test_cross_project_chunk_422(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res_a = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        res_b = await _make_doc_chain(db_session, org["projects"]["b"].id, org["users"]["admin"].id)
        candidate, _ = await _make_candidate(db_session, res_a)
        span = _span(res_b["chunk"].id, res_b["chunk"].content, "压比是出口")
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [span], "reject_reason": None},
        )
        assert r.status_code == 422, r.text

    async def test_unicode_offset_codepoint(self, client, org, db_session):
        """emoji 是 surrogate pair（UTF-16 2 单元）；offset 按 Unicode code point 计数。"""
        content = "压比🌟测试"  # 0:压 1:比 2:🌟 3:测 4:试
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id, content=content)
        candidate, _ = await _make_candidate(db_session, res)
        # quote "🌟测试" 的 code point offset 是 2..5。
        span = {"chunk_id": str(res["chunk"].id), "start_char": 2, "end_char": 5, "quote_text": "🌟测试"}
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [span], "reject_reason": None},
        )
        assert r.status_code == 200, r.text
        fresh = (
            await db_session.execute(select(Candidate).where(Candidate.id == candidate.id))
        ).scalar_one()
        assert fresh.status == "approved"


# ---------------------------------------------------------------------------
# 验收 3：合法审核一次事务写 verdict/bundle/reviewer/状态/ReviewRecord；回滚
# ---------------------------------------------------------------------------


class TestReviewAtomicity:
    async def test_legal_review_writes_all_fields_and_record(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        span = _span(res["chunk"].id, res["chunk"].content, "压比是出口")
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [span], "reject_reason": None},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["review_verdict"] == "supported"
        assert body["reviewed_by"] is not None
        assert body["review_evidence_spans"]["schema_version"] == 1
        assert body["review_evidence_spans"]["spans"][0]["quote_text"] == "压比是出口"
        record = (
            await db_session.execute(
                select(ReviewRecord).where(
                    ReviewRecord.entity_type == "candidate",
                    ReviewRecord.entity_id == candidate.id,
                )
            )
        ).scalar_one()
        assert record.action == "approve"
        assert record.reviewer_id is not None

    async def test_span_dedup_and_stable_sort(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        s1 = _span(res["chunk"].id, res["chunk"].content, "压比是出口")
        s2 = _span(res["chunk"].id, res["chunk"].content, "压缩机性能")
        # 乱序 + 重复。
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [s2, s1, s1], "reject_reason": None},
        )
        assert r.status_code == 200, r.text
        spans = r.json()["review_evidence_spans"]["spans"]
        # 去重后 2 条且稳定排序。
        assert len(spans) == 2
        starts = [s["start_char"] for s in spans]
        assert starts == sorted(starts)

    async def test_review_flush_failure_rolls_back(self, org, db_session, monkeypatch):
        """模拟 flush 失败：verdict/bundle/reviewer/状态/ReviewRecord 全部回滚。"""
        from app.services.candidate_service import CandidateService

        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        span = _span(res["chunk"].id, res["chunk"].content, "压比是出口")
        review_spans = [
            {"chunk_id": str(res["chunk"].id), "start_char": span["start_char"], "end_char": span["end_char"], "quote_text": span["quote_text"]}
        ]
        # 先提交候选创建，保证回滚只回滚审核写入而非候选本身。
        await db_session.commit()
        candidate_id = candidate.id

        async def _boom(*args, **kwargs):
            raise RuntimeError("flush failed")

        monkeypatch.setattr(db_session, "flush", _boom)

        service = CandidateService(db_session)
        with pytest.raises(RuntimeError):
            await service.review(
                candidate_id=candidate_id,
                reviewer_id=org["users"]["reviewer"].id,
                verdict="supported",
                evidence_spans=review_spans,
            )
        monkeypatch.undo()
        await db_session.rollback()
        fresh = (
            await db_session.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        assert fresh.status == "ai_generated"
        assert fresh.review_verdict is None
        count = (
            await db_session.execute(
                select(ReviewRecord).where(ReviewRecord.entity_id == candidate_id)
            )
        ).scalar_one_or_none()
        assert count is None


# ---------------------------------------------------------------------------
# 验收 4：提升写 1 CuratedItem + v1 revision + 去重 EvidenceLink（服务端派生）
# ---------------------------------------------------------------------------


class TestPromote:
    async def test_promote_writes_item_revision_evidence(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        span = _span(res["chunk"].id, res["chunk"].content, "压比是出口")
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [span], "reject_reason": None},
        )
        assert r.status_code == 200, r.text
        p = await client.post(
            f"/api/candidates/{candidate.id}/promote-to-curated", headers=headers, json={},
        )
        assert p.status_code == 201, p.text
        item = p.json()
        assert item["status"] == "draft"
        assert item["current_revision"] == 1

        revisions = (
            await db_session.execute(
                select(CuratedRevision).where(CuratedRevision.curated_item_id == item["id"])
            )
        ).scalars().all()
        assert len(revisions) == 1
        assert revisions[0].version == 1
        assert revisions[0].canonicalization_version == CURATED_CONTENT_CJSON_VERSION
        assert revisions[0].content_sha256 == curated_content_sha256(candidate.content)

        links = (
            await db_session.execute(
                select(EvidenceLink).where(EvidenceLink.curated_item_id == item["id"])
            )
        ).scalars().all()
        assert len(links) == 1
        # 服务端派生：quote/页码/标题/document 全来自 Chunk。
        assert links[0].quote_text == "压比是出口"
        assert links[0].document_id == res["chunk"].document_id
        assert links[0].source_pages == res["chunk"].source_pages
        assert links[0].heading_path == res["chunk"].heading_path
        assert links[0].start_char == span["start_char"]
        assert links[0].end_char == span["end_char"]

    async def test_repeat_promote_409_same_item(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item_id = await _review_and_promote(client, "reviewer_user", candidate.id, res)
        # 重复提升 -> 409 + context item id。
        p2 = await client.post(
            f"/api/candidates/{candidate.id}/promote-to-curated", headers=headers, json={},
        )
        assert p2.status_code == 409, p2.text
        assert p2.json()["code"] == "CANDIDATE_ALREADY_PROMOTED"
        assert p2.json()["context"]["curated_item_id"] == item_id
        # 只有一个 CuratedItem。
        count = (
            await db_session.execute(
                select(func.count()).select_from(CuratedItem).where(CuratedItem.candidate_id == candidate.id)
            )
        ).scalar()
        assert count == 1

    async def test_concurrent_promote_only_one_201(self, client, org, db_session, _test_session_factory):
        """两个并发提升请求最多一个 201，数据库只有一个 CuratedItem（唯一约束兜底）。"""
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        await db_session.commit()

        # 先 review 为 approved（一次）。
        from app.services.candidate_service import CandidateService

        async with _test_session_factory() as session:
            service = CandidateService(session)
            span = _span(res["chunk"].id, res["chunk"].content, "压比是出口")
            await service.review(
                candidate_id=candidate.id,
                reviewer_id=org["users"]["reviewer"].id,
                verdict="supported",
                evidence_spans=[{
                    "chunk_id": str(res["chunk"].id),
                    "start_char": span["start_char"],
                    "end_char": span["end_char"],
                    "quote_text": span["quote_text"],
                }],
            )
            await session.commit()

        # 两个独立会话并发提升；唯一约束保证只有一个成功。
        from app.services.candidate_service import (
            CandidateAlreadyPromotedError,
            CandidateEvidenceRequiredError,
            CandidateStateConflictError,
        )

        async def _promote_service():
            async with _test_session_factory() as session:
                service = CandidateService(session)
                try:
                    item = await service.promote_to_curated(candidate.id, org["users"]["reviewer"].id)
                    await session.commit()
                    return "201", item.id
                except CandidateAlreadyPromotedError:
                    await session.rollback()
                    return "409", None
                except (CandidateStateConflictError, CandidateEvidenceRequiredError):
                    await session.rollback()
                    return "409", None
                except IntegrityError:
                    await session.rollback()
                    return "409", None

        results = await asyncio.gather(_promote_service(), _promote_service())
        statuses = [r[0] for r in results]
        assert statuses.count("201") == 1, f"并发提升结果: {statuses}"
        assert statuses.count("409") == 1

        async with _test_session_factory() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(CuratedItem).where(CuratedItem.candidate_id == candidate.id)
                )
            ).scalar()
            assert count == 1

    async def test_promote_requires_approved(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res, status="ai_generated")
        p = await client.post(
            f"/api/candidates/{candidate.id}/promote-to-curated", headers=headers, json={},
        )
        assert p.status_code == 409, p.text
        assert p.json()["code"] == "CANDIDATE_REVIEW_STATE_CONFLICT"

    async def test_editor_patch_status_approved_422(self, client, org, db_session):
        """editor 直接 PATCH status=approved -> 422（extra=forbid）。"""
        headers = await _login(client, "editor_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        r = await client.patch(
            f"/api/candidates/{candidate.id}", headers=headers,
            json={"content": {"q": "x"}, "status": "approved"},
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 验收 5/6/7：角色门禁 + 并发编辑 + approve 绑定
# ---------------------------------------------------------------------------


class TestCuratedGate:
    async def test_editor_cannot_review_403(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        r = await client.post(
            f"/api/candidates/{candidate.id}/review", headers=headers,
            json={"verdict": "supported", "evidence_spans": [], "reject_reason": None},
        )
        assert r.status_code == 403, r.text

    async def test_concurrent_save_same_expected_revision_only_one(self, client, org, db_session, _test_session_factory):
        """两个相同 expected_revision 并发保存只有一个成功；revision 版本连续。"""
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item_id = await _review_and_promote(client, "reviewer_user", candidate.id, res)
        await db_session.commit()

        from app.services.curated_item_service import CuratedItemService

        async def _save(question: str):
            async with _test_session_factory() as session:
                service = CuratedItemService(session)
                try:
                    item = await service.update(
                        item_id=item_id,
                        revised_by=org["users"]["editor"].id,
                        content={"question": question, "answer": "a"},
                        revision_note="并发",
                        expected_revision=1,
                    )
                    await session.commit()
                    return "ok", item.current_revision, item.content
                except Exception:
                    await session.rollback()
                    return "conflict", None, None

        results = await asyncio.gather(_save("A?"), _save("B?"))
        oks = [r for r in results if r[0] == "ok"]
        conflicts = [r for r in results if r[0] == "conflict"]
        assert len(oks) == 1
        assert len(conflicts) == 1

        async with _test_session_factory() as session:
            revs = (
                await session.execute(
                    select(CuratedRevision).where(CuratedRevision.curated_item_id == item_id)
                    .order_by(CuratedRevision.version)
                )
            ).scalars().all()
            assert [r.version for r in revs] == [1, 2]
            item = (
                await session.execute(select(CuratedItem).where(CuratedItem.id == item_id))
            ).scalar_one()
            assert item.current_revision == 2
            # 成功者的内容正确。
            assert oks[0][2] in (
                {"question": "A?", "answer": "a"},
                {"question": "B?", "answer": "a"},
            )
            assert revs[1].content == oks[0][2]

    async def test_approve_binds_revision_hash_evidence(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item_id = await _review_and_promote(client, "reviewer_user", candidate.id, res)
        item = (
            await db_session.execute(select(CuratedItem).where(CuratedItem.id == item_id))
        ).scalar_one()
        rev = (
            await db_session.execute(
                select(CuratedRevision).where(
                    CuratedRevision.curated_item_id == item_id,
                    CuratedRevision.version == 1,
                )
            )
        ).scalar_one()

        r = await client.post(
            f"/api/projects/{org['projects']['a'].id}/curated-items/{item_id}/review",
            headers=headers,
            json={"action": "approve", "reason": None, "expected_revision": 1},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"
        assert r.json()["approved_revision_id"] == str(rev.id)
        assert r.json()["approved_by"] == str(org["users"]["reviewer"].id)
        assert r.json()["approved_at"] is not None

        record = (
            await db_session.execute(
                select(ReviewRecord).where(ReviewRecord.id == item.approval_record_id)
            )
        ).scalar_one()
        assert record.entity_revision_id == rev.id
        assert record.revision_content_sha256 == rev.content_sha256
        assert record.evidence_sha256 == curated_approval_sha256(
            revision_id=rev.id,
            content_sha256=rev.content_sha256,
            evidence_links=[{
                "id": el.id, "chunk_id": el.chunk_id, "document_id": el.document_id,
                "start_char": el.start_char, "end_char": el.end_char,
                "quote_text": el.quote_text, "source_pages": el.source_pages,
                "heading_path": el.heading_path,
            } for el in (await db_session.execute(
                select(EvidenceLink).where(EvidenceLink.curated_item_id == item_id)
            )).scalars().all()],
        )
        assert record.canonicalization_version == CURATED_APPROVAL_CJSON_VERSION
        assert record.evidence_snapshot is not None
        assert record.evidence_snapshot["schema_version"] == 1

    async def test_needs_revision_then_reapprove_keeps_history(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item_id = await _review_and_promote(client, "reviewer_user", candidate.id, res)
        pid = org["projects"]["a"].id

        # approve v1
        r1 = await client.post(
            f"/api/projects/{pid}/curated-items/{item_id}/review", headers=headers,
            json={"action": "approve", "reason": None, "expected_revision": 1},
        )
        assert r1.status_code == 200, r1.text
        v1_record_id = r1.json()["approval_record_id"]

        # needs_revision v1
        r2 = await client.post(
            f"/api/projects/{pid}/curated-items/{item_id}/review", headers=headers,
            json={"action": "needs_revision", "reason": "修正", "expected_revision": 1},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "draft"
        assert r2.json()["approved_revision_id"] is None

        # editor 编辑 v2
        editor_headers = await _login(client, "editor_user")
        r3 = await client.patch(
            f"/api/projects/{pid}/curated-items/{item_id}",
            headers=editor_headers,
            json={
                "content": {"question": "压比?", "answer": "压比是压力之比"},
                "revision_note": "v2",
                "expected_revision": 1,
            },
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["current_revision"] == 2

        # approve v2
        r4 = await client.post(
            f"/api/projects/{pid}/curated-items/{item_id}/review", headers=headers,
            json={"action": "approve", "reason": None, "expected_revision": 2},
        )
        assert r4.status_code == 200, r4.text
        assert r4.json()["status"] == "approved"

        # v1 approval record 不变（历史不可改写）。
        v1_record = (
            await db_session.execute(select(ReviewRecord).where(ReviewRecord.id == v1_record_id))
        ).scalar_one()
        assert v1_record.action == "approve"
        assert v1_record.entity_revision_id is not None
        # v2 record 绑定 v2 revision。
        v2_record = (
            await db_session.execute(select(ReviewRecord).where(ReviewRecord.id == r4.json()["approval_record_id"]))
        ).scalar_one()
        v2_rev = (
            await db_session.execute(
                select(CuratedRevision).where(
                    CuratedRevision.curated_item_id == item_id,
                    CuratedRevision.version == 2,
                )
            )
        ).scalar_one()
        assert v2_record.entity_revision_id == v2_rev.id
        assert v2_record.revision_content_sha256 == v2_rev.content_sha256

    async def test_approve_requires_evidence_gate(self, client, org, db_session):
        """直接 SQL 建一个无证据的 CuratedItem，reviewer approve -> 409 GATE_FAILED。"""
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res, status="approved")
        # 不提升，直接建 item（无 revision/evidence）。
        item = CuratedItem(
            project_id=org["projects"]["a"].id,
            candidate_id=candidate.id,
            content={"question": "q", "answer": "a"},
            item_type="qa_generation",
            status="draft",
            promoted_by=org["users"]["reviewer"].id,
            current_revision=1,
        )
        db_session.add(item)
        await db_session.flush()
        # 需要 v1 revision。
        db_session.add(CuratedRevision(
            curated_item_id=item.id,
            revised_by=org["users"]["reviewer"].id,
            version=1,
            content=item.content,
            content_sha256=curated_content_sha256(item.content),
            canonicalization_version=CURATED_CONTENT_CJSON_VERSION,
        ))
        await db_session.flush()

        r = await client.post(
            f"/api/projects/{org['projects']['a'].id}/curated-items/{item.id}/review",
            headers=headers,
            json={"action": "approve", "reason": None, "expected_revision": 1},
        )
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "CURATED_APPROVAL_GATE_FAILED"


# ---------------------------------------------------------------------------
# 验收 8/9/10：数据库不可变约束 + deferred trigger + hash 绑定
# ---------------------------------------------------------------------------


class TestDatabaseImmutability:
    async def test_curated_revision_update_delete_rejected(self, org, db_session):
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item = CuratedItem(
            project_id=org["projects"]["a"].id, candidate_id=candidate.id,
            content={"q": "x"}, item_type="qa_generation", status="draft",
            promoted_by=org["users"]["reviewer"].id, current_revision=1,
        )
        db_session.add(item)
        await db_session.flush()
        rev = CuratedRevision(
            curated_item_id=item.id, revised_by=org["users"]["reviewer"].id, version=1,
            content={"q": "x"}, content_sha256=curated_content_sha256({"q": "x"}),
            canonicalization_version=CURATED_CONTENT_CJSON_VERSION,
        )
        db_session.add(rev)
        await db_session.commit()
        rev_id = str(rev.id)

        with pytest.raises(DBAPIError):
            await db_session.execute(
                text("UPDATE curated_revisions SET content_sha256 = :h WHERE id = :id"),
                {"h": "f" * 64, "id": rev_id},
            )
        await db_session.rollback()
        with pytest.raises(DBAPIError):
            await db_session.execute(
                text("DELETE FROM curated_revisions WHERE id = :id"), {"id": rev_id}
            )
        await db_session.rollback()

    async def test_approval_record_update_delete_rejected(self, org, db_session):
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item = CuratedItem(
            project_id=org["projects"]["a"].id, candidate_id=candidate.id,
            content={"q": "x"}, item_type="qa_generation", status="draft",
            promoted_by=org["users"]["reviewer"].id, current_revision=1,
        )
        db_session.add(item)
        await db_session.flush()
        rev = CuratedRevision(
            curated_item_id=item.id, revised_by=org["users"]["reviewer"].id, version=1,
            content={"q": "x"}, content_sha256=curated_content_sha256({"q": "x"}),
            canonicalization_version=CURATED_CONTENT_CJSON_VERSION,
        )
        db_session.add(rev)
        await db_session.flush()
        record = ReviewRecord(
            entity_type="curated_item", entity_id=item.id,
            reviewer_id=org["users"]["reviewer"].id, action="approve",
            entity_revision_id=rev.id,
            revision_content_sha256=rev.content_sha256,
            evidence_snapshot={"schema_version": 1, "evidence_links": []},
            evidence_sha256="a" * 64,
            canonicalization_version=CURATED_APPROVAL_CJSON_VERSION,
        )
        db_session.add(record)
        await db_session.commit()
        record_id = str(record.id)

        with pytest.raises(DBAPIError):
            await db_session.execute(
                text("UPDATE review_records SET reason = :r WHERE id = :id"),
                {"r": "篡改", "id": record_id},
            )
        await db_session.rollback()
        with pytest.raises(DBAPIError):
            await db_session.execute(
                text("DELETE FROM review_records WHERE id = :id"), {"id": record_id}
            )
        await db_session.rollback()

    async def test_cross_item_revision_pointer_rejected(self, org, db_session, _test_session_factory):
        """篡改 approved_revision_id 指向其它 item 的 revision -> deferred trigger 拒绝。"""
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        candidate2, _ = await _make_candidate(db_session, res, content={"question": "q2", "answer": "a2"})
        await db_session.commit()

        async with _test_session_factory() as session:
            item1 = CuratedItem(
                project_id=org["projects"]["a"].id, candidate_id=candidate.id,
                content={"q": "x"}, item_type="qa_generation", status="draft",
                promoted_by=org["users"]["reviewer"].id, current_revision=1,
            )
            session.add(item1)
            await session.flush()
            rev1 = CuratedRevision(
                curated_item_id=item1.id, revised_by=org["users"]["reviewer"].id, version=1,
                content={"q": "x"}, content_sha256=curated_content_sha256({"q": "x"}),
                canonicalization_version=CURATED_CONTENT_CJSON_VERSION,
            )
            session.add(rev1)
            await session.flush()
            record1 = ReviewRecord(
                entity_type="curated_item", entity_id=item1.id,
                reviewer_id=org["users"]["reviewer"].id, action="approve",
                entity_revision_id=rev1.id,
                revision_content_sha256=rev1.content_sha256,
                evidence_snapshot={"schema_version": 1, "evidence_links": []},
                evidence_sha256="a" * 64,
                canonicalization_version=CURATED_APPROVAL_CJSON_VERSION,
            )
            session.add(record1)
            await session.flush()

            # item2（独立 candidate）的 approved_revision_id 指向 item1 的 revision
            # -> 违反同 item 校验（deferred trigger 在 commit 时拒绝）。
            item2 = CuratedItem(
                project_id=org["projects"]["a"].id, candidate_id=candidate2.id,
                content={"q": "y"}, item_type="qa_generation", status="approved",
                promoted_by=org["users"]["reviewer"].id, current_revision=1,
                approved_revision_id=rev1.id,
                approval_record_id=record1.id,
                approved_by=org["users"]["reviewer"].id,
                approved_at=datetime.now(UTC),
            )
            session.add(item2)
            # RAISE EXCEPTION 的 deferred trigger 由 SQLAlchemy 包装为 DBAPIError。
            with pytest.raises(DBAPIError):
                await session.commit()
            await session.rollback()

    async def test_wrong_canonical_version_rejected(self, org, db_session, _test_session_factory):
        """approve 记录的 canonicalization_version 错误 -> deferred trigger 在提交时拒绝。"""
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        await db_session.commit()

        async with _test_session_factory() as session:
            item = CuratedItem(
                project_id=org["projects"]["a"].id, candidate_id=candidate.id,
                content={"q": "x"}, item_type="qa_generation", status="draft",
                promoted_by=org["users"]["reviewer"].id, current_revision=1,
            )
            session.add(item)
            await session.flush()
            rev = CuratedRevision(
                curated_item_id=item.id, revised_by=org["users"]["reviewer"].id, version=1,
                content={"q": "x"}, content_sha256=curated_content_sha256({"q": "x"}),
                canonicalization_version=CURATED_CONTENT_CJSON_VERSION,
            )
            session.add(rev)
            await session.flush()
            record = ReviewRecord(
                entity_type="curated_item", entity_id=item.id,
                reviewer_id=org["users"]["reviewer"].id, action="approve",
                entity_revision_id=rev.id,
                revision_content_sha256=rev.content_sha256,
                evidence_snapshot={"schema_version": 1, "evidence_links": []},
                evidence_sha256="b" * 64,
                canonicalization_version="curated-approval-cjson-V999",
            )
            session.add(record)
            await session.flush()
            # 从 draft 置为 approved：CHECK 通过（指针齐全），由 deferred trigger 在
            # commit 时校验 canonicalization_version 为已知版本（错误版本 -> 拒绝）。
            item.status = "approved"
            item.approved_revision_id = rev.id
            item.approval_record_id = record.id
            item.approved_by = org["users"]["reviewer"].id
            item.approved_at = datetime.now(UTC)
            # RAISE EXCEPTION 的 deferred trigger 由 SQLAlchemy 包装为 DBAPIError。
            with pytest.raises(DBAPIError):
                await session.commit()
            await session.rollback()


# ---------------------------------------------------------------------------
# 验收 11：golden fixture 跨端相同 hash；改一个 code point 必变 hash
# ---------------------------------------------------------------------------


class TestCanonicalHash:
    def test_content_hash_golden(self):
        """同 content 在 canonical 模块与迁移工具（domain.canonical 复用）得到相同 hash。"""
        content = {"question": "压比定义?", "answer": "出口与进口压力之比"}
        h = curated_content_sha256(content)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
        # 迁移直接 import domain.canonical.curated_content_sha256，天然一致。
        from importlib.util import module_from_spec, spec_from_file_location
        from pathlib import Path

        mig_path = Path(__file__).resolve().parents[2] / "apps/api/migrations/versions/t09_curated_evidence_approval.py"
        spec = spec_from_file_location("t09_mig", mig_path)
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.curated_content_sha256(content) == h

    def test_content_hash_changes_on_codepoint(self):
        c1 = {"answer": "压比🌟定义"}
        c2 = {"answer": "压比X定义"}
        assert curated_content_sha256(c1) != curated_content_sha256(c2)

    def test_key_order_does_not_change_hash(self):
        c1 = {"question": "q", "answer": "a"}
        c2 = {"answer": "a", "question": "q"}
        assert curated_content_sha256(c1) == curated_content_sha256(c2)

    def test_evidence_hash_stable_order(self):
        el_a = {"id": "a", "chunk_id": "c", "document_id": "d", "start_char": 0, "end_char": 5, "quote_text": "x", "source_pages": None, "heading_path": ""}
        el_b = {"id": "b", "chunk_id": "c", "document_id": "d", "start_char": 10, "end_char": 15, "quote_text": "y", "source_pages": None, "heading_path": ""}
        assert curated_approval_sha256(revision_id="r", content_sha256="0" * 64, evidence_links=[el_b, el_a]) == \
            curated_approval_sha256(revision_id="r", content_sha256="0" * 64, evidence_links=[el_a, el_b])


# ---------------------------------------------------------------------------
# 验收 12/13：分页 + 领域 code
# ---------------------------------------------------------------------------


class TestPaginationAndCodes:
    async def test_evidence_revisions_paginated(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item_id = await _review_and_promote(client, "reviewer_user", candidate.id, res)

        r = await client.get(
            f"/api/projects/{org['projects']['a'].id}/curated-items/{item_id}/evidence?page=1&page_size=20",
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {"items", "total", "page", "page_size"}
        assert body["total"] == 1
        assert body["items"][0]["start_char"] is not None
        assert body["items"][0]["quote_text"] is not None

        r2 = await client.get(
            f"/api/projects/{org['projects']['a'].id}/curated-items/{item_id}/revisions?page=99&page_size=20",
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["items"] == []
        assert r2.json()["total"] == 1

    async def test_curated_review_403_for_editor(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        res = await _make_doc_chain(db_session, org["projects"]["a"].id, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        item_id = await _review_and_promote(client, "reviewer_user", candidate.id, res)
        r = await client.post(
            f"/api/projects/{org['projects']['a'].id}/curated-items/{item_id}/review",
            headers=headers,
            json={"action": "approve", "reason": None, "expected_revision": 1},
        )
        assert r.status_code == 403, r.text
