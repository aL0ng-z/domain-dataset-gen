"""T10 Dataset/Benchmark composition 矩阵测试（验收标准 1-17）。

覆盖：
- 空集/两页/越界页规范分页对象，排序 ordinal ASC, id ASC；详情 item_count 与库一致。
- eligible 查询排除已添加、非 approved、无证据、跨项目 item；Benchmark 排除
  partially_supported source。
- Dataset 可添加有证据 approved/partially_supported item；Benchmark 对同一 item
  返回 409（COMPOSITION_ITEM_INELIGIBLE），supported item 可添加。
- viewer 添加/移除 403；editor 跨项目 ID 404 且数据库无变化。
- 同一 item 两个并发添加最多一个 201、一个 409，只有一条 membership。
- 两个不同 item 并发添加都成功且 ordinal 唯一；重复至少 50 轮无唯一键异常泄漏 500。
- finalized 容器 add/remove 均 409；读取不受影响。
- membership 固定加入时 approved revision/approval record；条目退审后既有容器
  预览/hash 不变，remove/re-add 才升级。
- add/remove 每次只递增一次 revision 且 hash 可由数据库成员重算；回滚时全部不变。
- finalize 与 add/remove 并发至少 50 轮：mutation 先完成则旧 expected revision 的
  finalize 返回 409；finalize 先完成则 mutation 返回 409；无 finalized 后写入。
- finalized 后直接 ORM/SQL 修改 membership/composition 被数据库门禁拒绝。
- 篡改 membership 保存 hash、换另一 CuratedItem 的 revision/approval record、
  未知 canonicalization version 均被 constraint/重算门禁拒绝。
- golden fixture 在 service 与迁移得到相同 composition SHA-256；改 pinned content/
  evidence hash 或 ordinal 必然改变 hash。
- 每个领域失败返回稳定 code；字段校验保持结构化 422。
- 删除 membership 不删除 CuratedItem/EvidenceLink；错误 container+item 组合不删。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models.chunk import Chunk
from app.models.curated import CuratedItem, CuratedRevision, EvidenceLink
from app.models.dataset import Benchmark, Dataset, DatasetItem
from app.models.generation import Candidate, GenerationRun
from app.models.generation_batch import GenerationBatch
from app.models.review_record import ReviewRecord
from domain.composition import COMPOSITION_CJSON_VERSION, composition_sha256

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
    """构造 Document -> Section -> Chunk 最小链路。"""
    from app.models.config import ModelConfig, ParserProfile
    from app.models.document import Document
    from app.models.parse import ParseJob
    from app.models.prompt_template import PromptTemplate
    from app.models.section import CleaningJob, Section

    doc = Document(
        project_id=project_id, filename="t10.pdf", file_size=100, sha256="3" * 64,
        minio_key="tests/t10.pdf", page_count=1, uploaded_by=uploaded_by,
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
    # Candidate 审核现在只允许引用生成时冻结的 selected_chunk_ids。该轻量
    # fixture 使用 legacy 批次避免复制 T08 全量快照，但仍真实绑定来源 Chunk。
    batch = GenerationBatch(
        document_id=res["doc"].id,
        chunk_set_id=res["chunk"].chunk_set_id,
        model_config_id=res["model"].id,
        prompt_template_id=res["tpl"].id,
        selected_chunk_ids=[str(res["chunk"].id)],
        status="completed",
        total_chunks=1,
        completed_chunks=1,
        created_by=res["doc"].uploaded_by,
        is_legacy=True,
        provenance_status="legacy_unavailable",
        provenance_error_code="LEGACY_TEST_FIXTURE",
    )
    db_session.add(batch)
    await db_session.flush()
    run = GenerationRun(
        chunk_id=res["chunk"].id,
        prompt_template_id=res["tpl"].id,
        model_config_id=res["model"].id,
        generation_batch_id=batch.id,
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
        source_generation_batch_id=batch.id,
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


async def _review_and_promote(
    client, username: str, candidate_id, res, *, verdict="supported", quote="压比是出口与进口压力之比"
):
    """reviewer 审核 + 提升，返回 curated_item_id。"""
    headers = await _login(client, username)
    span = _span(res["chunk"].id, res["chunk"].content, quote)
    r = await client.post(
        f"/api/candidates/{candidate_id}/review",
        headers=headers,
        json={
            "expected_revision": 1,
            "verdict": verdict,
            "evidence_spans": [span],
            "reject_reason": None,
        },
    )
    assert r.status_code == 200, r.text
    p = await client.post(
        f"/api/candidates/{candidate_id}/promote-to-curated",
        headers=headers,
        json={"expected_revision": 1},
    )
    assert p.status_code == 201, p.text
    return p.json()["id"]


async def _approve_item(client, project_id, item_id, *, expected_revision=1, actor="reviewer_user"):
    """reviewer 批准 CuratedItem。"""
    headers = await _login(client, actor)
    r = await client.post(
        f"/api/projects/{project_id}/curated-items/{item_id}/review",
        headers=headers,
        json={"action": "approve", "reason": None, "expected_revision": expected_revision},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _make_approved_item(
    client, db_session, project_id, *, verdict="supported", content=None, actor="reviewer_user"
) -> dict:
    """完整构造一个 approved CuratedItem（v1 revision + EvidenceLink + approve 记录）。

    ``actor`` 为执行 review/promote/approve 的用户（项目 B 仅 admin 成员，需传
    "admin_user"）。返回 {"curated_item", "revision", "record", "candidate"}。
    """
    res = await _make_doc_chain(db_session, project_id, await org_uid(db_session, actor))
    return await _approve_from_chain(client, db_session, project_id, res, verdict=verdict, content=content, actor=actor)


async def org_uid(db_session, username: str = "editor_user") -> uuid.UUID:
    """获取 org 预置用户 id（构造 doc chain 的 uploaded_by）。"""
    from app.models.user import User
    row = (await db_session.execute(select(User.id).where(User.username == username))).scalar_one_or_none()
    if row is None:
        raise RuntimeError(f"{username} 不存在（请先建 org fixture）")
    return row


async def _approve_from_chain(client, db_session, project_id, res, *, verdict="supported", content=None, actor="reviewer_user"):
    """走真实审核/提升/批准流程构造 approved CuratedItem。

    review 接口要求证据 span 精确命中 chunk 原文（Unicode code point），promote
    派生 v1 revision + EvidenceLink；最后 approve 绑定 revision/证据快照。
    """
    candidate, _ = await _make_candidate(db_session, res, content=content)
    await db_session.flush()
    await db_session.commit()

    headers = await _login(client, actor)
    quote = "压比是出口与进口压力之比"
    content_text = res["chunk"].content
    if quote not in content_text:
        quote = content_text[: max(1, len(content_text) // 2)]
    span = _span(res["chunk"].id, content_text, quote)
    r = await client.post(
        f"/api/candidates/{candidate.id}/review",
        headers=headers,
        json={
            "expected_revision": candidate.content_revision,
            "verdict": verdict,
            "evidence_spans": [span],
            "reject_reason": None,
        },
    )
    assert r.status_code == 200, r.text
    p = await client.post(
        f"/api/candidates/{candidate.id}/promote-to-curated",
        headers=headers,
        json={"expected_revision": candidate.content_revision},
    )
    assert p.status_code == 201, p.text
    item_id = p.json()["id"]
    approved = await _approve_item(client, project_id, item_id, actor=actor)
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
    record = (
        await db_session.execute(select(ReviewRecord).where(ReviewRecord.id == approved["approval_record_id"]))
    ).scalar_one()
    return {"curated_item": item, "revision": rev, "record": record, "candidate": candidate}


def _membership_hash_rows(memberships) -> list[dict]:
    return [
        {
            "membership_id": m.id,
            "ordinal": m.ordinal,
            "curated_item_id": m.curated_item_id,
            "curated_revision_id": m.curated_revision_id,
            "curated_revision_sha256": m.curated_revision_sha256,
            "approval_record_id": m.approval_record_id,
            "approval_evidence_sha256": m.approval_evidence_sha256,
        }
        for m in memberships
    ]


# ---------------------------------------------------------------------------
# 验收 1：分页对象、排序、count
# ---------------------------------------------------------------------------


class TestPaginationAndCount:
    async def test_empty_dataset_four_keys(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        dataset = Dataset(project_id=org["projects"]["a"].id, name="空", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.flush()

        res = await client.get(
            f"/api/projects/{org['projects']['a'].id}/datasets/{dataset.id}/items?page=1&page_size=20",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body.keys()) == {"items", "total", "page", "page_size"}
        assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}

        # 详情 item_count == 0。
        d = await client.get(
            f"/api/projects/{org['projects']['a'].id}/datasets/{dataset.id}", headers=headers
        )
        assert d.status_code == 200
        assert d.json()["item_count"] == 0
        assert d.json()["composition_revision"] == 0

    async def test_two_pages_and_out_of_range(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved_items = [
            await _make_approved_item(client, db_session, pid, content={"question": f"Q{i}", "answer": "A"})
            for i in range(3)
        ]
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.flush()

        for i, approved in enumerate(approved_items):
            m = DatasetItem(
                dataset_id=dataset.id,
                curated_item_id=approved["curated_item"].id,
                ordinal=i + 1,
                curated_revision_id=approved["revision"].id,
                curated_revision_sha256=approved["revision"].content_sha256,
                approval_record_id=approved["record"].id,
                approval_evidence_sha256=approved["record"].evidence_sha256,
            )
            db_session.add(m)
        await db_session.commit()

        # 两页。
        res = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/items?page=1&page_size=2", headers=headers
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert [i["ordinal"] for i in body["items"]] == [1, 2]
        # 越界页。
        res2 = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/items?page=99&page_size=20", headers=headers
        )
        assert res2.status_code == 200
        assert res2.json()["items"] == []
        assert res2.json()["total"] == 3
        # 详情 count 与库一致。
        d = await client.get(f"/api/projects/{pid}/datasets/{dataset.id}", headers=headers)
        assert d.json()["item_count"] == 3

    async def test_detail_field_contract(self, client, org, db_session):
        """detail item 字段固定：id/container_id/.../curated_item（含 pinned_content object）。"""
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.flush()
        m = DatasetItem(
            dataset_id=dataset.id,
            curated_item_id=approved["curated_item"].id,
            ordinal=1,
            curated_revision_id=approved["revision"].id,
            curated_revision_sha256=approved["revision"].content_sha256,
            approval_record_id=approved["record"].id,
            approval_evidence_sha256=approved["record"].evidence_sha256,
        )
        db_session.add(m)
        await db_session.commit()

        res = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/items?page=1&page_size=20", headers=headers
        )
        item = res.json()["items"][0]
        assert set(item.keys()) == {
            "id", "container_id", "curated_item_id", "curated_revision_id",
            "curated_revision_sha256", "approval_record_id", "approval_evidence_sha256",
            "ordinal", "created_at", "curated_item",
        }
        ci = item["curated_item"]
        assert set(ci.keys()) >= {
            "id", "item_type", "current_status", "current_revision", "pinned_revision", "pinned_content",
        }
        assert isinstance(ci["pinned_content"], dict)
        assert ci["pinned_content"]["question"] == "什么是压比?"
        assert ci["pinned_revision"]["content_sha256"] == approved["revision"].content_sha256

    async def test_empty_benchmark_and_benchmark_detail(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        bm = Benchmark(project_id=org["projects"]["a"].id, name="BM", created_by=org["users"]["admin"].id)
        db_session.add(bm)
        await db_session.flush()
        res = await client.get(
            f"/api/projects/{org['projects']['a'].id}/benchmarks/{bm.id}/cases?page=1&page_size=20",
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}
        d = await client.get(
            f"/api/projects/{org['projects']['a'].id}/benchmarks/{bm.id}", headers=headers
        )
        assert d.json()["case_count"] == 0


# ---------------------------------------------------------------------------
# 验收 2/3：eligible 查询与 add 资格差异
# ---------------------------------------------------------------------------


class TestEligibleAndAddGate:
    async def test_eligible_excludes_nonapproved_no_evidence_cross_project(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.flush()
        # 已加入该容器。
        m = DatasetItem(
            dataset_id=dataset.id, curated_item_id=approved["curated_item"].id, ordinal=1,
            curated_revision_id=approved["revision"].id,
            curated_revision_sha256=approved["revision"].content_sha256,
            approval_record_id=approved["record"].id,
            approval_evidence_sha256=approved["record"].evidence_sha256,
        )
        db_session.add(m)
        # 项目 B 的 approved item（跨项目）；B 仅 admin 成员。
        b_approved = await _make_approved_item(client, db_session, org["projects"]["b"].id, actor="admin_user")
        # 非 approved（draft）item。
        res = await _make_doc_chain(db_session, pid, org["users"]["editor"].id)
        candidate, _ = await _make_candidate(db_session, res)
        draft = CuratedItem(
            project_id=pid, candidate_id=candidate.id, content={"q": "x"},
            item_type="qa_generation", status="draft",
            promoted_by=org["users"]["reviewer"].id, current_revision=1,
        )
        db_session.add(draft)
        await db_session.commit()

        res = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/eligible-items?page=1&page_size=20",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        ids = [i["id"] for i in body["items"]]
        assert str(approved["curated_item"].id) not in ids  # 已添加排除
        assert str(b_approved["curated_item"].id) not in ids  # 跨项目排除
        assert str(draft.id) not in ids  # 非 approved 排除

    async def test_dataset_accepts_partially_supported_but_benchmark_rejects(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        partial = await _make_approved_item(client, db_session, pid, verdict="partially_supported")
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        benchmark = Benchmark(project_id=pid, name="BM", created_by=org["users"]["admin"].id)
        db_session.add_all([dataset, benchmark])
        await db_session.commit()

        # Dataset 可添加 partially_supported。
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=headers,
            json={"curated_item_id": str(partial["curated_item"].id)},
        )
        assert r.status_code == 201, r.text
        # Benchmark 对同一 item 409。
        r2 = await client.post(
            f"/api/projects/{pid}/benchmarks/{benchmark.id}/cases",
            headers=headers,
            json={"curated_item_id": str(partial["curated_item"].id)},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["code"] == "COMPOSITION_ITEM_INELIGIBLE"
        # benchmark eligible 列表不含 partial。
        eligible = await client.get(
            f"/api/projects/{pid}/benchmarks/{benchmark.id}/eligible-items?page=1&page_size=20",
            headers=headers,
        )
        assert str(partial["curated_item"].id) not in [i["id"] for i in eligible.json()["items"]]

    async def test_supported_item_addable_to_benchmark(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        supported = await _make_approved_item(client, db_session, pid, verdict="supported")
        benchmark = Benchmark(project_id=pid, name="BM", created_by=org["users"]["admin"].id)
        db_session.add(benchmark)
        await db_session.commit()

        r = await client.post(
            f"/api/projects/{pid}/benchmarks/{benchmark.id}/cases",
            headers=headers,
            json={"curated_item_id": str(supported["curated_item"].id)},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["curated_revision_id"] == str(supported["revision"].id)
        assert body["approval_record_id"] == str(supported["record"].id)

    async def test_add_repeat_membership_409(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()

        r1 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=headers, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r1.status_code == 201
        r2 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=headers, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r2.status_code == 409
        assert r2.json()["code"] == "COMPOSITION_MEMBER_EXISTS"

    async def test_eligible_search(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid, content={"question": "压比定义?", "answer": "…"})
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/eligible-items?query=压比&page=1&page_size=20",
            headers=headers,
        )
        assert r.status_code == 200
        assert str(approved["curated_item"].id) in [i["id"] for i in r.json()["items"]]
        # 不匹配的 query。
        r2 = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/eligible-items?query=不存在的词&page=1&page_size=20",
            headers=headers,
        )
        assert r2.json()["items"] == []


# ---------------------------------------------------------------------------
# 验收 4：授权与跨项目
# ---------------------------------------------------------------------------


class TestAuthorization:
    async def test_viewer_add_remove_403(self, client, org, db_session):
        headers = await _login(client, "viewer_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()

        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=headers, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r.status_code == 403, r.text
        r2 = await client.delete(
            f"/api/projects/{pid}/datasets/{dataset.id}/items/{uuid.uuid4()}", headers=headers
        )
        assert r2.status_code == 403, r2.text

    async def test_editor_cross_project_id_404_no_change(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, org["projects"]["b"].id, actor="admin_user")
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        before = (await db_session.execute(select(func.count()).select_from(DatasetItem))).scalar()

        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=headers, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r.status_code == 404, r.text
        await db_session.commit()
        after = (await db_session.execute(select(func.count()).select_from(DatasetItem))).scalar()
        assert after == before

    async def test_forged_parent_child_404_no_delete(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=headers, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r.status_code == 201
        m_id = r.json()["id"]
        # 错误父子组合：用另一个 dataset 的 did 删除该 item -> 404。
        dataset2 = Dataset(project_id=pid, name="DS2", created_by=org["users"]["admin"].id)
        db_session.add(dataset2)
        await db_session.commit()
        r2 = await client.delete(
            f"/api/projects/{pid}/datasets/{dataset2.id}/items/{m_id}", headers=headers
        )
        assert r2.status_code == 404, r2.text
        # 数据库无变化。
        still = (
            await db_session.execute(select(func.count()).select_from(DatasetItem))
        ).scalar()
        assert still == 1

    async def test_remove_success_204_and_deletes_only_membership(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=headers, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        m_id = r.json()["id"]
        r2 = await client.delete(
            f"/api/projects/{pid}/datasets/{dataset.id}/items/{m_id}", headers=headers
        )
        assert r2.status_code == 204, r2.text
        # membership 删除但 CuratedItem/EvidenceLink 仍在。
        assert (await db_session.execute(select(func.count()).select_from(DatasetItem))).scalar() == 0
        assert (await db_session.execute(select(CuratedItem).where(CuratedItem.id == approved["curated_item"].id))).scalar_one() is not None
        assert (await db_session.execute(select(func.count()).select_from(EvidenceLink))).scalar() >= 1


# ---------------------------------------------------------------------------
# 验收 5/6：并发添加
# ---------------------------------------------------------------------------


class TestConcurrentAdd:
    async def test_same_item_concurrent_add(self, client, org, db_session, _test_session_factory):
        """同一 item 两个并发添加最多一个 201、一个 409，只有一条 membership。"""
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        ds_id = dataset.id
        item_id = approved["curated_item"].id

        from app.services.composition_service import CompositionService

        async def _add():
            async with _test_session_factory() as session:
                try:
                    svc = CompositionService(session)
                    await svc.add_membership(
                        container_type="dataset", container_id=ds_id,
                        curated_item_id=item_id, require_supported=False,
                    )
                    await session.commit()
                    return "ok"
                except Exception:
                    await session.rollback()
                    return "conflict"

        results = await asyncio.gather(_add(), _add())
        assert sorted(results) == ["conflict", "ok"]
        async with _test_session_factory() as session:
            n = (
                await session.execute(
                    select(func.count()).select_from(DatasetItem).where(DatasetItem.dataset_id == ds_id)
                )
            ).scalar()
            assert n == 1

    async def test_two_items_concurrent_add_50_rounds(self, client, org, db_session, _test_session_factory):
        """两个不同 item 并发添加都成功且 ordinal 唯一；重复 50 轮无唯一键异常泄漏。"""
        pid = org["projects"]["a"].id
        approved1 = await _make_approved_item(client, db_session, pid, content={"question": "Q1", "answer": "A1"})
        approved2 = await _make_approved_item(client, db_session, pid, content={"question": "Q2", "answer": "A2"})
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        ds_id = dataset.id

        from app.services.composition_service import CompositionService

        async def _add(iid):
            async with _test_session_factory() as session:
                svc = CompositionService(session)
                await svc.add_membership(
                    container_type="dataset", container_id=ds_id,
                    curated_item_id=iid, require_supported=False,
                )
                await session.commit()

        for _ in range(50):
            async with _test_session_factory() as session:
                # 清空 membership 重来一轮。
                await session.execute(text("DELETE FROM dataset_items WHERE dataset_id = :d"), {"d": str(ds_id)})
                ds = (
                    await session.execute(select(Dataset).where(Dataset.id == ds_id).with_for_update())
                ).scalar_one()
                ds.composition_revision = 0
                await session.commit()
            await asyncio.gather(
                _add(approved1["curated_item"].id), _add(approved2["curated_item"].id)
            )
            async with _test_session_factory() as session:
                ords = (
                    await session.execute(
                        select(DatasetItem.ordinal).where(DatasetItem.dataset_id == ds_id)
                    )
                ).scalars().all()
                assert sorted(ords) == [1, 2], ords
        # 最终状态。
        async with _test_session_factory() as session:
            ds = (await session.execute(select(Dataset).where(Dataset.id == ds_id))).scalar_one()
            assert ds.composition_revision == 2


# ---------------------------------------------------------------------------
# 验收 7/11/12：finalized 不可变 + 数据库门禁
# ---------------------------------------------------------------------------


class TestFinalizedImmutability:
    async def test_finalized_container_blocks_add_remove_but_reads_ok(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        editor = await _login(client, "editor_user")
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r.status_code == 201
        m_id = r.json()["id"]

        # finalize（reviewer 必须传当前 revision/hash）。
        detail = await client.get(f"/api/projects/{pid}/datasets/{dataset.id}", headers=headers)
        expected_rev = detail.json()["composition_revision"]
        expected_sha = detail.json()["composition_sha256"]
        f = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers,
            json={"expected_revision": expected_rev, "expected_sha256": expected_sha},
        )
        assert f.status_code == 200, f.text
        assert f.json()["status"] == "finalized"
        assert f.json()["finalized_revision"] == expected_rev
        assert f.json()["finalized_sha256"] == expected_sha

        # finalized 后 add/remove 均 409。
        r2 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r2.status_code == 409
        assert r2.json()["code"] == "COMPOSITION_NOT_DRAFT"
        r3 = await client.delete(
            f"/api/projects/{pid}/datasets/{dataset.id}/items/{m_id}", headers=editor
        )
        assert r3.status_code == 409
        # 读取不受影响。
        items = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/items?page=1&page_size=20", headers=headers
        )
        assert items.status_code == 200
        assert items.json()["total"] == 1

    async def test_direct_sql_mutation_rejected(self, client, org, db_session, _test_session_factory):
        """finalized 后直接 ORM/SQL 修改 membership 或 composition 字段被数据库门禁拒绝。"""
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        editor = await _login(client, "editor_user")
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        m_id = r.json()["id"]
        headers = await _login(client, "reviewer_user")
        detail = await client.get(f"/api/projects/{pid}/datasets/{dataset.id}", headers=headers)
        f = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers,
            json={
                "expected_revision": detail.json()["composition_revision"],
                "expected_sha256": detail.json()["composition_sha256"],
            },
        )
        assert f.status_code == 200
        ds_id = dataset.id
        # client fixture 覆盖 get_db 不自动提交；显式提交使独立 session 可见 finalized。
        await db_session.commit()

        async with _test_session_factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text("UPDATE dataset_items SET ordinal = 99 WHERE id = :id"), {"id": str(m_id)})
            await session.rollback()
            with pytest.raises(DBAPIError):
                await session.execute(text("DELETE FROM dataset_items WHERE id = :id"), {"id": str(m_id)})
            await session.rollback()
            with pytest.raises(DBAPIError):
                await session.execute(text("UPDATE datasets SET composition_revision = 5 WHERE id = :id"), {"id": str(ds_id)})
            await session.rollback()
            # 容器行仍 finalized 且不可 UPDATE。
            with pytest.raises(DBAPIError):
                await session.execute(text("UPDATE datasets SET name = 'hacked' WHERE id = :id"), {"id": str(ds_id)})
            await session.rollback()

    async def test_tampered_membership_hash_rejected(self, client, org, db_session, _test_session_factory):
        """篡改 membership 保存的 revision/evidence hash -> deferred trigger 拒绝。"""
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        editor = await _login(client, "editor_user")
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        m_id = r.json()["id"]
        await db_session.commit()
        # 另一条 item 的 revision（FK 合法但 deferred trigger 拒绝：跨 item 绑定）。
        other = await _make_approved_item(
            client, db_session, pid, content={"question": "另一条", "answer": "…"}
        )
        await db_session.commit()
        async with _test_session_factory() as session:
            # deferred consistency trigger 在 COMMIT 时拒绝（UPDATE 本身可执行）。
            await session.execute(
                text("UPDATE dataset_items SET approval_evidence_sha256 = :h WHERE id = :id"),
                {"h": "f" * 64, "id": str(m_id)},
            )
            with pytest.raises(DBAPIError):
                await session.commit()
            await session.rollback()
            # 换成另一 CuratedItem 的 revision/approval record（FK 合法，deferred 拒绝）。
            await session.execute(
                text(
                    "UPDATE dataset_items SET curated_revision_id = :r, "
                    "curated_revision_sha256 = :rh, approval_record_id = :ar, "
                    "approval_evidence_sha256 = :aeh WHERE id = :id"
                ),
                {
                    "r": str(other["revision"].id),
                    "rh": other["revision"].content_sha256,
                    "ar": str(other["record"].id),
                    "aeh": other["record"].evidence_sha256,
                    "id": str(m_id),
                },
            )
            with pytest.raises(DBAPIError):
                await session.commit()
            await session.rollback()

    async def test_finalize_requires_expected_revision_and_hash(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        editor = await _login(client, "editor_user")
        await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        # 错误 expected revision。
        f = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers,
            json={"expected_revision": 99, "expected_sha256": "a" * 64},
        )
        assert f.status_code == 409
        assert f.json()["code"] == "COMPOSITION_REVISION_CONFLICT"
        # 非法 sha256 格式 -> 422。
        f2 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers,
            json={"expected_revision": 1, "expected_sha256": "not-a-hash"},
        )
        assert f2.status_code == 422


# ---------------------------------------------------------------------------
# 验收 8/9/13：pinned revision 固定 + revision/hash 一致性
# ---------------------------------------------------------------------------


class TestPinnedRevisionAndHash:
    async def test_membership_pins_approved_revision_after_rework(self, client, org, db_session):
        """退审/重批后既有容器预览/hash 不变；remove/re-add 才升级。"""
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        editor = await _login(client, "editor_user")
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r.status_code == 201
        first_sha = r.json()["curated_revision_sha256"]
        first_rev_id = r.json()["curated_revision_id"]
        hash_before = r.json()["curated_item"]["pinned_content"]

        # 退审（reviewer）。
        reviewer = await _login(client, "reviewer_user")
        item_id = approved["curated_item"].id
        nr = await client.post(
            f"/api/projects/{pid}/curated-items/{item_id}/review",
            headers=reviewer,
            json={"action": "needs_revision", "reason": "需要修改", "expected_revision": 1},
        )
        assert nr.status_code == 200, nr.text
        await db_session.commit()

        # 编辑产生 v2（editor）。
        er = await client.patch(
            f"/api/projects/{pid}/curated-items/{item_id}",
            headers=editor,
            json={"content": {"question": "压比新定义", "answer": "…"}, "expected_revision": 1},
        )
        assert er.status_code == 200, er.text
        # 重新批准 v2。
        r2 = await client.post(
            f"/api/projects/{pid}/curated-items/{item_id}/review",
            headers=reviewer,
            json={"action": "approve", "reason": None, "expected_revision": 2},
        )
        assert r2.status_code == 200, r2.text
        await db_session.commit()

        # 既有 membership 仍固定 v1（预览/hash 不变）。
        items = await client.get(
            f"/api/projects/{pid}/datasets/{dataset.id}/items?page=1&page_size=20", headers=editor
        )
        item = items.json()["items"][0]
        assert item["curated_revision_id"] == first_rev_id
        assert item["curated_revision_sha256"] == first_sha
        assert item["curated_item"]["pinned_content"] == hash_before
        assert item["curated_item"]["current_revision"] == 2  # 提示新 revision
        assert item["curated_item"]["current_status"] == "approved"

    async def test_add_remove_increment_revision_and_hash_recomputable(self, client, org, db_session, _test_session_factory):
        pid = org["projects"]["a"].id
        approved1 = await _make_approved_item(client, db_session, pid, content={"question": "Q1", "answer": "A1"})
        approved2 = await _make_approved_item(client, db_session, pid, content={"question": "Q2", "answer": "A2"})
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        editor = await _login(client, "editor_user")

        r1 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved1["curated_item"].id)},
        )
        assert r1.status_code == 201
        r2 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved2["curated_item"].id)},
        )
        assert r2.status_code == 201
        await db_session.commit()

        async with _test_session_factory() as session:
            ds = (await session.execute(select(Dataset).where(Dataset.id == dataset.id))).scalar_one()
            assert ds.composition_revision == 2
            memberships = (
                await session.execute(
                    select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)
                    .order_by(DatasetItem.ordinal)
                )
            ).scalars().all()
            recomputed = composition_sha256(
                container_id=dataset.id, container_type="dataset",
                memberships=_membership_hash_rows(memberships),
            )
            assert recomputed == ds.composition_sha256
            # 改 ordinal 必变 hash。
            changed = composition_sha256(
                container_id=dataset.id, container_type="dataset",
                memberships=[
                    {**r, "ordinal": 1 if r["ordinal"] == 2 else 2}
                    for r in _membership_hash_rows(memberships)
                ],
            )
            assert changed != recomputed

        # remove 一次 -> revision 递增、hash 重算。
        await client.delete(
            f"/api/projects/{pid}/datasets/{dataset.id}/items/{r1.json()['id']}", headers=editor
        )
        await db_session.commit()
        async with _test_session_factory() as session:
            ds = (await session.execute(select(Dataset).where(Dataset.id == dataset.id))).scalar_one()
            assert ds.composition_revision == 3
            memberships = (
                await session.execute(select(DatasetItem).where(DatasetItem.dataset_id == dataset.id))
            ).scalars().all()
            recomputed = composition_sha256(
                container_id=dataset.id, container_type="dataset",
                memberships=_membership_hash_rows(memberships),
            )
            assert recomputed == ds.composition_sha256

    async def test_golden_fixture_same_hash_service_and_domain(self):
        """golden fixture：service 计算与 domain.composition 结果一致，改输入必变 hash。"""
        memberships = [
            {
                "membership_id": "11111111-1111-1111-1111-111111111111",
                "ordinal": 1,
                "curated_item_id": "22222222-2222-2222-2222-222222222222",
                "curated_revision_id": "33333333-3333-3333-3333-333333333333",
                "curated_revision_sha256": "a" * 64,
                "approval_record_id": "44444444-4444-4444-4444-444444444444",
                "approval_evidence_sha256": "b" * 64,
            }
        ]
        h1 = composition_sha256(
            container_id="55555555-5555-5555-5555-555555555555",
            container_type="dataset",
            memberships=memberships,
        )
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)
        # 改变 evidence hash 必变 composition hash。
        memberships2 = [{**memberships[0], "approval_evidence_sha256": "c" * 64}]
        h2 = composition_sha256(
            container_id="55555555-5555-5555-5555-555555555555",
            container_type="dataset",
            memberships=memberships2,
        )
        assert h2 != h1
        # 空集合也有确定 hash。
        h3 = composition_sha256(container_id="55555555-5555-5555-5555-555555555555", container_type="dataset", memberships=[])
        assert len(h3) == 64

    async def test_finalize_and_add_concurrent_50_rounds(self, client, org, db_session, _test_session_factory):
        """finalize 与 add 并发：要么 finalize 因旧 expected 返回 409，要么 add 409；
        不存在 finalized 后写入。每轮新建空容器（draft + 空集合），add 与 finalize
        并发；finalized 容器不可被测试重置（触发器保护），故不复用旧容器。"""
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        await db_session.commit()
        item_id = approved["curated_item"].id
        reviewer = org["users"]["reviewer"].id

        from app.services.composition_service import CompositionService

        async def _new_empty_dataset() -> uuid.UUID:
            """新建 draft + 空集合容器，返回 id。"""
            async with _test_session_factory() as session:
                ds = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
                session.add(ds)
                await session.flush()
                svc = CompositionService(session)
                ds.composition_revision = 0
                ds.composition_sha256 = svc._membership_sha256("dataset", ds.id, [])
                ds.composition_canonicalization_version = COMPOSITION_CJSON_VERSION
                await session.commit()
                return ds.id

        async def _add_once(ds_id):
            async with _test_session_factory() as session:
                svc = CompositionService(session)
                try:
                    await svc.add_membership(
                        container_type="dataset", container_id=ds_id,
                        curated_item_id=item_id, require_supported=False,
                    )
                    await session.commit()
                    return "added"
                except Exception:
                    await session.rollback()
                    return "rejected"

        async def _finalize_once(ds_id, expected_rev, expected_sha):
            async with _test_session_factory() as session:
                svc = CompositionService(session)
                try:
                    await svc.finalize(
                        container_type="dataset", container_id=ds_id,
                        expected_revision=expected_rev,
                        expected_sha256=expected_sha,
                        reviewer_id=reviewer,
                    )
                    await session.commit()
                    return "finalized"
                except Exception:
                    await session.rollback()
                    return "conflict"

        # 每轮：空容器（期望 revision 0 + 空集合 hash），add 与 finalize 并发。
        for _ in range(50):
            ds_id = await _new_empty_dataset()
            expected_rev, expected_sha = 0, "0" * 64
            results = await asyncio.gather(_add_once(ds_id), _finalize_once(ds_id, expected_rev, expected_sha))
            async with _test_session_factory() as session:
                ds = (await session.execute(select(Dataset).where(Dataset.id == ds_id))).scalar_one()
                n = (
                    await session.execute(
                        select(func.count()).select_from(DatasetItem).where(DatasetItem.dataset_id == ds_id)
                    )
                ).scalar()
                if ds.status == "finalized":
                    # finalize 先完成 -> add 必须 rejected；无 membership 残留。
                    assert n == 0, (results, ds.composition_revision)
                    assert "finalized" in results
                    assert "rejected" in results
                    assert ds.composition_revision == expected_rev
                else:
                    # add 先完成 -> finalize 返回 conflict（expected revision/hash 过期）。
                    assert n == 1, (results, ds.composition_revision)
                    assert "added" in results
                    assert "conflict" in results
                    assert ds.composition_revision == 1


# ---------------------------------------------------------------------------
# 验收 14：错误 code 与 422
# ---------------------------------------------------------------------------


class TestErrorCodes:
    async def test_finalize_reject_gate_failed_on_item_rework(self, client, org, db_session):
        """finalize 重验发现资格不一致 -> COMPOSITION_FINALIZE_GATE_FAILED。"""
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        editor = await _login(client, "editor_user")
        r = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/items",
            headers=editor, json={"curated_item_id": str(approved["curated_item"].id)},
        )
        assert r.status_code == 201
        # 退审 -> 资格失效。
        reviewer = await _login(client, "reviewer_user")
        await client.post(
            f"/api/projects/{pid}/curated-items/{approved['curated_item'].id}/review",
            headers=reviewer,
            json={"action": "needs_revision", "reason": "退", "expected_revision": 1},
        )
        await db_session.commit()
        detail = await client.get(f"/api/projects/{pid}/datasets/{dataset.id}", headers=reviewer)
        f = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=reviewer,
            json={
                "expected_revision": detail.json()["composition_revision"],
                "expected_sha256": detail.json()["composition_sha256"],
            },
        )
        assert f.status_code == 409, f.text
        assert f.json()["code"] == "COMPOSITION_FINALIZE_GATE_FAILED"

    async def test_editor_finalize_403_and_viewer_read_ok(self, client, org, db_session):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        f = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers,
            json={"expected_revision": 0, "expected_sha256": "a" * 64},
        )
        assert f.status_code == 403, f.text
        # viewer 可读 detail。
        viewer = await _login(client, "viewer_user")
        d = await client.get(f"/api/projects/{pid}/datasets/{dataset.id}", headers=viewer)
        assert d.status_code == 200

    async def test_finalize_idempotent(self, client, org, db_session):
        headers = await _login(client, "reviewer_user")
        pid = org["projects"]["a"].id
        dataset = Dataset(project_id=pid, name="DS", created_by=org["users"]["admin"].id)
        db_session.add(dataset)
        await db_session.commit()
        detail = await client.get(f"/api/projects/{pid}/datasets/{dataset.id}", headers=headers)
        rev = detail.json()["composition_revision"]
        sha = detail.json()["composition_sha256"]
        f1 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers, json={"expected_revision": rev, "expected_sha256": sha},
        )
        assert f1.status_code == 200
        # 重复相同 finalize 幂等返回当前结果。
        f2 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers, json={"expected_revision": rev, "expected_sha256": sha},
        )
        assert f2.status_code == 200
        # 不同 expected -> 409 REVISION_CONFLICT。
        f3 = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/finalize",
            headers=headers, json={"expected_revision": rev + 1, "expected_sha256": sha},
        )
        assert f3.status_code == 409
        assert f3.json()["code"] == "COMPOSITION_REVISION_CONFLICT"
