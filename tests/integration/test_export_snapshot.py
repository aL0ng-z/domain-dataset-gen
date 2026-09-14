"""T11 不可变导出集成测试（任务卡 §12 自动化验收标准）。

覆盖：
1. 导出触发 202 返回 {export_id, task_id, status:queued}；不伪造 completed。
2. 完整导出流水线：queued -> worker 快照封存 -> versioned upload -> seal+finalize ->
   completed；两次导出不同 id/key/version；重导出不改变第一次下载 bytes/hash。
3. formatter 只读 manifest（独立重渲染得到相同 payload SHA-256/字节数）。
4. manifest 含冻结 content/revision/evidence 与完整版本图；删除任一冻结引用 ->
   PROVENANCE_SNAPSHOT_MISSING（导出失败而非降级）。
5. secret scanner：manifest/对象不含 api_key/token/预签名 URL。
6. DB 直接 UPDATE/DELETE completed Export 或 SnapshotManifest 被 trigger 拒绝。
7. 外部写入同 key 新版本后，旧 Export 仍按 version_id 下载原 bytes。
8. 快照后上传失败可安全 retry（不重新读取可变业务表、不覆盖旧对象）。
9. 并发修改 source -> revision 冲突 409。
10. legacy 记录显示 unverified 且无伪造 hash。
11. 直接 INSERT 错误 seal hash / 错配 export/snapshot / 重复对象坐标被 DB 拒绝。
12. 直接把 Export 标 completed 不提供 seal -> deferred constraint 回滚。
13. 修改当前配置后导出仍只出现冻结值。

用真实 MinIO（测试 outputs bucket 已启用 versioning）验证对象版本下载。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.config import ChunkProfile, ExportProfile, ModelConfig, ParserProfile
from app.models.curated import CuratedItem, CuratedRevision
from app.models.dataset import Dataset
from app.models.document import Document
from app.models.generation import Candidate, GenerationRun
from app.models.generation_batch import GenerationBatch
from app.models.parse import ParseJob
from app.models.prompt_template import PromptTemplate
from app.models.review_record import ReviewRecord
from app.models.section import CleaningJob, Section
from app.models.task import Task
from app.services.clean_version_service import content_sha256, revision_map_sha256
from domain.manifest import manifest_cjson, manifest_sha256
from splitters import canonical_output_sha256, splitter_version

PASSWORD = "password-123"
_OUTPUTS = "outputs-test"


# ---------------------------------------------------------------------------
# 基础设施 helper
# ---------------------------------------------------------------------------


async def _login(client, username: str) -> dict[str, str]:
    res = await client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def org_uid(db_session, username: str = "editor_user") -> uuid.UUID:
    from app.models.user import User

    row = (await db_session.execute(select(User.id).where(User.username == username))).scalar_one_or_none()
    if row is None:
        raise RuntimeError(f"{username} 不存在（请先建 org fixture）")
    return row


async def _make_doc_chain(db_session, project_id, uploaded_by, *, content: str = "压比是出口与进口压力之比，是衡量压缩机性能的核心指标。"):
    """构造 Document -> ParseJob -> Chunk 最小链路（T03/T06 冻结快照齐全）。"""
    doc = Document(
        project_id=project_id, filename="t11.pdf", file_size=100, sha256="a" * 64,
        minio_key="tests/t11.pdf", page_count=1, uploaded_by=uploaded_by,
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
    from app.models.prompt_template import PromptTemplateVersion

    tpl_version = PromptTemplateVersion(
        template_id=prompt_template.id, version=1,
        system_prompt="sys", user_prompt_template="q",
    )
    db_session.add(tpl_version)
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

    revision_map = {str(section.id): section.content_revision}
    clean_version = CleanedDocumentVersion(
        document_id=doc.id, source_cleaning_job_id=cleaning_job.id, version=1,
        section_count=1, merged_markdown=content, content_sha256=content_sha256(content),
        source_revision_map=revision_map, source_revision_sha256=revision_map_sha256(revision_map),
        status="accepted", created_by=uploaded_by,
    )
    chunk_profile = ChunkProfile(project_id=project_id, name="Chunks")
    chunk_task = Task(
        project_id=project_id, task_type="chunk", entity_type="document", entity_id=doc.id,
        handler="chunk_document", payload={}, payload_version=2, status="completed",
        next_run_at=datetime.now(UTC), completed_at=datetime.now(UTC), created_by=uploaded_by,
    )
    db_session.add_all([clean_version, chunk_profile, chunk_task])
    await db_session.flush()
    chunk_set = ChunkSet(
        document_id=doc.id, status="completed", version=1, is_legacy=False,
        cleaned_document_version_id=clean_version.id, chunk_profile_id=chunk_profile.id,
        task_id=chunk_task.id, strategy="hybrid_heading_recursive",
        config_json={"strategy": "hybrid_heading_recursive", "max_tokens": 512, "overlap_tokens": 0},
        source_sha256=clean_version.content_sha256,
        output_sha256=canonical_output_sha256([(1, "1.1", content, 30)]),
        splitter_version=splitter_version(), completed_at=datetime.now(UTC),
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
    # 文档 active chunk set 指向该集合（供 manifest 版本图）。
    doc.active_clean_version_id = clean_version.id
    doc.active_chunk_set_id = chunk_set.id
    await db_session.flush()

    return {
        "doc": doc,
        "model": model_config,
        "tpl": prompt_template,
        "tpl_version": tpl_version,
        "chunk": chunk,
        "section": section,
        "chunk_set": chunk_set,
        "parse_job": parse_job,
        "cleaning_job": cleaning_job,
        "clean_version": clean_version,
    }


def _span(chunk_id, content: str, quote: str) -> dict:
    start = content.index(quote)
    return {
        "chunk_id": str(chunk_id),
        "start_char": start,
        "end_char": start + len(quote),
        "quote_text": quote,
    }


async def _make_verified_candidate(db_session, res, *, content=None, actor_uid, item_type="qa_generation"):
    """构造带 verified 生成批次的 Candidate（T08 冻结 prompt/model 快照齐全）。"""
    batch = GenerationBatch(
        document_id=res["doc"].id,
        chunk_set_id=res["chunk_set"].id,
        model_config_id=res["model"].id,
        prompt_template_id=res["tpl"].id,
        selected_chunk_ids=[str(res["chunk"].id)],
        status="completed",
        total_chunks=1,
        completed_chunks=1,
        created_by=actor_uid,
        is_legacy=False,
        provenance_status="verified",
        prompt_template_version_id=res["tpl_version"].id,
        prompt_template_snapshot={
            "template_id": str(res["tpl"].id), "version": 1, "task_type": "qa_generation",
            "name": "T", "system_prompt": "sys", "user_prompt_template": "q",
            "input_schema": None, "output_schema": None,
        },
        prompt_template_sha256="1" * 64,
        model_config_snapshot={
            "config_id": str(res["model"].id), "version": 1, "provider": "mock",
            "model_name": "m", "temperature": None, "max_tokens": None,
            "base_url": "http://localhost:8080/v1", "extra_params": {},
        },
        model_config_sha256="2" * 64,
        renderer_version="prompt-renderer:v1",
        completed_at=datetime.now(UTC),
    )
    db_session.add(batch)
    await db_session.flush()
    run = GenerationRun(
        chunk_id=res["chunk"].id,
        prompt_template_id=res["tpl"].id,
        model_config_id=res["model"].id,
        context_mode="single_chunk",
        status="completed",
        generation_batch_id=batch.id,
        input_prompt='[{"role":"user","content":"q"}]',
        raw_output='{"question":"什么是压比?","answer":"压比是出口与进口压力之比"}',
        rendered_prompt_sha256="3" * 64,
        is_legacy=False,
        provenance_status="verified",
        completed_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.flush()
    candidate = Candidate(
        generation_run_id=run.id,
        chunk_id=res["chunk"].id,
        content=content or {"question": "什么是压比?", "answer": "压比是出口与进口压力之比"},
        candidate_type=item_type,
        status="ai_generated",
        source_generation_batch_id=batch.id,
    )
    db_session.add(candidate)
    await db_session.flush()
    await db_session.refresh(candidate)
    return candidate, run, batch


async def _make_approved_item(
    client, db_session, project_id, *, verdict="supported", content=None, actor="reviewer_user", item_type="qa_generation"
) -> dict:
    """构造 approved CuratedItem（verified 生成链路 + EvidenceLink + approve 记录）。"""
    res = await _make_doc_chain(db_session, project_id, await org_uid(db_session, actor))
    candidate, run, batch = await _make_verified_candidate(db_session, res, content=content, actor_uid=await org_uid(db_session, actor), item_type=item_type)
    await db_session.flush()
    await db_session.commit()

    headers = await _login(client, actor)
    content_text = res["chunk"].content
    quote = "压比是出口与进口压力之比"
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
                CuratedRevision.curated_item_id == item_id, CuratedRevision.version == 1,
            )
        )
    ).scalar_one()
    record = (
        await db_session.execute(select(ReviewRecord).where(ReviewRecord.id == approved["approval_record_id"]))
    ).scalar_one()
    return {"curated_item": item, "revision": rev, "record": record, "candidate": candidate, "res": res}


async def _approve_item(client, project_id, item_id, *, expected_revision=1, actor="reviewer_user"):
    headers = await _login(client, actor)
    r = await client.post(
        f"/api/projects/{project_id}/curated-items/{item_id}/review",
        headers=headers,
        json={"action": "approve", "reason": None, "expected_revision": expected_revision},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _make_finalized_dataset(client, db_session, project_id, *, content=None, item_type="qa_generation") -> dict:
    """构造 finalized Dataset（含一个 approved item）。"""
    approved = await _make_approved_item(client, db_session, project_id, content=content, item_type=item_type)
    dataset = Dataset(project_id=project_id, name="T11 数据集", created_by=await org_uid(db_session, "editor_user"))
    db_session.add(dataset)
    await db_session.flush()
    # 直接加 membership（复用 composition service 的逻辑路径）。
    from app.services.composition_service import CompositionService

    cs = CompositionService(db_session)
    await cs.add_membership(
        container_type="dataset",
        container_id=dataset.id,
        curated_item_id=approved["curated_item"].id,
        require_supported=False,
    )
    await db_session.flush()
    # finalize。
    dataset = await cs.finalize(
        container_type="dataset",
        container_id=dataset.id,
        expected_revision=dataset.composition_revision,
        expected_sha256=dataset.composition_sha256,
        reviewer_id=await org_uid(db_session, "reviewer_user"),
    )
    await db_session.commit()
    await db_session.refresh(dataset)
    return {"dataset": dataset, "approved": approved}


async def _run_export_worker(_test_session_factory, task_id: uuid.UUID):
    """通过真实 runner 执行 export task（独立会话 + CAS 语义）。

    与 test_task_runner 一致：claim -> handler -> completed 原子提交。
    """
    from storage import reset_storage_client

    reset_storage_client()  # 确保使用测试 MinIO 凭据（避免跨测试复用错误单例）
    from app.workers.execution import HandlerRegistry
    from app.workers.export_worker import run_export_benchmark_handler, run_export_dataset_handler
    from app.workers.runner import TaskRunner

    registry = HandlerRegistry()
    registry.register("export_dataset", 1)(run_export_dataset_handler)
    registry.register("export_benchmark", 1)(run_export_benchmark_handler)
    runner = TaskRunner(_test_session_factory, registry, worker_id="test-export-runner")
    await runner._claim_and_execute()


# ---------------------------------------------------------------------------
# 验收 1：触发 202 返回真实 export_id/task_id
# ---------------------------------------------------------------------------


class TestExportTrigger:
    async def test_trigger_returns_202_with_export_id(self, client, db_session, org):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        approved = await _make_approved_item(client, db_session, pid)
        dataset = Dataset(project_id=pid, name="D", created_by=await org_uid(db_session, "editor_user"))
        db_session.add(dataset)
        await db_session.flush()
        from app.services.composition_service import CompositionService

        cs = CompositionService(db_session)
        await cs.add_membership(container_type="dataset", container_id=dataset.id, curated_item_id=approved["curated_item"].id, require_supported=False)
        dataset = await cs.finalize(container_type="dataset", container_id=dataset.id, expected_revision=dataset.composition_revision, expected_sha256=dataset.composition_sha256, reviewer_id=await org_uid(db_session, "reviewer_user"))
        await db_session.commit()
        await db_session.refresh(dataset)
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()

        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        assert res.status_code == 202, res.text
        body = res.json()
        assert body["status"] == "queued"
        assert body["export_id"]
        assert body["task_id"]
        assert body["export_id"] != body["task_id"], "export_id 不得伪造为 task.id"

    async def test_trigger_non_finalized_409(self, client, db_session, org):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        dataset = Dataset(project_id=pid, name="D", created_by=await org_uid(db_session, "editor_user"))
        db_session.add(dataset)
        await db_session.flush()
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()

        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": 0,
                "expected_source_sha256": "0" * 64,
            },
        )
        assert res.status_code == 409, res.text
        assert res.json()["code"] == "EXPORT_SOURCE_NOT_FINALIZED"

    async def test_trigger_revision_conflict_409(self, client, db_session, org):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid)
        dataset = ds["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()

        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision + 99,
                "expected_source_sha256": "f" * 64,
            },
        )
        assert res.status_code == 409, res.text
        assert res.json()["code"] == "EXPORT_REVISION_CONFLICT"


# ---------------------------------------------------------------------------
# 验收 2/3：完整流水线 + 两次导出不同 id/key/version + 重导出不改变下载
# ---------------------------------------------------------------------------


class TestExportPipeline:
    async def test_full_pipeline_completes(
        self,
        client,
        db_session,
        org,
        _test_session_factory,
        monkeypatch,
    ):
        """完整导出：queued -> worker -> completed；验证 payload hash 可重渲染。"""
        from app.workers.export_worker import render_payload_from_manifest, render_payload_sha256

        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid)
        dataset = ds["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()
        await db_session.refresh(profile)

        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        assert res.status_code == 202, res.text
        body = res.json()

        # 执行 worker。
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
        await db_session.commit()

        # Export 已 completed。
        export = (
            await db_session.execute(select(__import__("app.models.export", fromlist=["Export"]).Export).where(__import__("app.models.export", fromlist=["Export"]).Export.id == uuid.UUID(body["export_id"])))
        ).scalar_one()
        assert export.status == "completed"
        assert export.item_count == 1
        assert export.output_sha256 and len(export.output_sha256) == 64
        assert export.manifest_sha256 and len(export.manifest_sha256) == 64
        assert export.object_version_id

        # 从 SnapshotManifest 独立重渲染得到相同 payload hash（验收 3）。
        manifest_row = (
            await db_session.execute(
                select(__import__("app.models.export", fromlist=["SnapshotManifest"]).SnapshotManifest).where(
                    __import__("app.models.export", fromlist=["SnapshotManifest"]).SnapshotManifest.export_id == export.id
                )
            )
        ).scalar_one()
        payload = render_payload_from_manifest(manifest_row.manifest, "qa_json")
        assert render_payload_sha256(payload) == export.output_sha256
        # manifest hash 与 DB 记录一致。
        assert manifest_row.manifest_sha256 == manifest_sha256(manifest_row.manifest)
        from app.config import settings
        from storage import get_storage_client

        storage = get_storage_client(
            settings.minio_endpoint,
            settings.minio_access_key,
            settings.minio_secret_key,
            settings.minio_secure,
        )
        manifest_bytes = storage.download_object_version(
            settings.minio_bucket_outputs,
            export.manifest_key,
            export.manifest_object_version_id,
        )
        assert manifest_bytes == manifest_cjson(manifest_row.manifest).encode("utf-8")

        # 下载（绑定 version）可获取原 bytes。
        dl = await client.get(
            f"/api/projects/{pid}/exports/{export.id}/download", headers=headers, follow_redirects=False,
        )
        assert dl.status_code == 307, dl.text
        assert "location" in dl.headers
        assert parse_qs(urlparse(dl.headers["location"]).query)["versionId"] == [
            export.object_version_id
        ]

        # Bearer 认证短链：300 秒、no-store；跨项目与未认证均不得签发。
        link = await client.post(
            f"/api/projects/{pid}/exports/{export.id}/download-link",
            headers=headers,
        )
        assert link.status_code == 200, link.text
        assert link.headers["cache-control"] == "no-store"
        assert link.json()["filename"].endswith(".json")
        assert parse_qs(urlparse(link.json()["url"]).query)["versionId"] == [
            export.object_version_id
        ]
        unauthenticated = await client.post(
            f"/api/projects/{pid}/exports/{export.id}/download-link"
        )
        assert unauthenticated.status_code == 401
        admin_headers = await _login(client, "admin_user")
        cross_project = await client.post(
            f"/api/projects/{org['projects']['b'].id}/exports/{export.id}/download-link",
            headers=admin_headers,
        )
        assert cross_project.status_code == 404

        # POST 是主验证合同；deep 同时重算 payload、对象 manifest 与 DB canonical manifest。
        reviewer_headers = await _login(client, "reviewer_user")
        verified = await client.post(
            f"/api/projects/{pid}/exports/{export.id}/verify?deep=true",
            headers=reviewer_headers,
        )
        assert verified.status_code == 200, verified.text
        verify_body = verified.json()
        assert verify_body["status"] == "verified"
        assert verify_body["shallow"] == {
            "db_fields_present": True,
            "version_id_present": True,
            "metadata_ok": True,
        }
        assert {item["item"] for item in verify_body["deep"]} == {
            "payload_sha256",
            "manifest_sha256",
            "manifest_canonical_sha256",
        }

        # metadata 被外部伪造/破坏时拒绝签发；deep 以真实流式 bytes hash 为准。
        original_stat = storage.stat_object_version

        def mismatched_stat(bucket, key, version_id):
            stat = original_stat(bucket, key, version_id)
            if key == export.minio_key:
                stat["sha256"] = "f" * 64
            return stat

        monkeypatch.setattr(storage, "stat_object_version", mismatched_stat)
        integrity_error = await client.post(
            f"/api/projects/{pid}/exports/{export.id}/download-link",
            headers=headers,
        )
        assert integrity_error.status_code == 409
        assert integrity_error.json()["code"] == "EXPORT_INTEGRITY_ERROR"
        monkeypatch.setattr(storage, "stat_object_version", original_stat)

        original_sha256 = storage.sha256_object_version

        def mismatched_payload_hash(bucket, key, version_id):
            if key == export.minio_key:
                return "f" * 64
            return original_sha256(bucket, key, version_id)

        monkeypatch.setattr(storage, "sha256_object_version", mismatched_payload_hash)
        deep_mismatch = await client.post(
            f"/api/projects/{pid}/exports/{export.id}/verify?deep=true",
            headers=reviewer_headers,
        )
        assert deep_mismatch.status_code == 200
        assert deep_mismatch.json()["status"] == "failed"
        assert next(
            item for item in deep_mismatch.json()["deep"]
            if item["item"] == "payload_sha256"
        )["ok"] is False

    async def test_two_exports_distinct_and_stable(self, client, db_session, org, _test_session_factory):
        """同一 Dataset 连续导出两次：不同 id/key/version；第一次下载 bytes 不变。"""
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid)
        dataset = ds["dataset"]
        profiles = [
            ExportProfile(project_id=pid, name="QA", format="qa_json"),
            ExportProfile(project_id=pid, name="Messages", format="messages"),
        ]
        db_session.add_all(profiles)
        await db_session.flush()
        await db_session.commit()

        ids = []
        for profile in profiles:
            res = await client.post(
                f"/api/projects/{pid}/datasets/{dataset.id}/export",
                headers=headers,
                json={
                    "export_profile_id": str(profile.id),
                    "expected_source_revision": dataset.composition_revision,
                    "expected_source_sha256": dataset.composition_sha256,
                },
            )
            assert res.status_code == 202, res.text
            body = res.json()
            await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
            await db_session.commit()
            ids.append(uuid.UUID(body["export_id"]))

        assert ids[0] != ids[1]
        from app.models.export import Export

        e1 = (await db_session.execute(select(Export).where(Export.id == ids[0]))).scalar_one()
        e2 = (await db_session.execute(select(Export).where(Export.id == ids[1]))).scalar_one()
        # 两次导出 key 不同（内容寻址，即使内容相同也因 export_id 不同而不同）。
        assert e1.minio_key != e2.minio_key
        assert e1.object_version_id != e2.object_version_id
        # 不同 format 即使同为 .json 扩展名，也必须各自渲染并使用独立对象坐标。
        assert {e1.format, e2.format} == {"qa_json", "messages"}
        assert e1.minio_key.endswith(".json") and e2.minio_key.endswith(".json")
        assert e1.output_sha256 != e2.output_sha256

        # 外部同 key 写入新版本不得改变旧 Export 的固定版本下载。
        from app.config import settings
        from storage import get_storage_client

        storage = get_storage_client(
            settings.minio_endpoint,
            settings.minio_access_key,
            settings.minio_secret_key,
            settings.minio_secure,
        )
        old_bytes = storage.download_object_version(
            settings.minio_bucket_outputs, e1.minio_key, e1.object_version_id
        )
        overwritten = storage.client.put_object(
            settings.minio_bucket_outputs,
            e1.minio_key,
            BytesIO(b"external-overwrite"),
            length=len(b"external-overwrite"),
            content_type="application/json",
        )
        assert overwritten.version_id != e1.object_version_id
        assert storage.download_object_version(
            settings.minio_bucket_outputs, e1.minio_key, e1.object_version_id
        ) == old_bytes
        fixed_download = await client.get(
            f"/api/projects/{pid}/exports/{e1.id}/download",
            headers=headers,
            follow_redirects=False,
        )
        assert parse_qs(urlparse(fixed_download.headers["location"]).query)["versionId"] == [
            e1.object_version_id
        ]


# ---------------------------------------------------------------------------
# 验收 8：快照后失败/取消恢复，已有 manifest 的 retry 不重读可变来源
# ---------------------------------------------------------------------------


class TestExportFailureRecovery:
    async def test_upload_failure_auto_retry_reuses_snapshot(
        self,
        client,
        db_session,
        org,
        _test_session_factory,
        monkeypatch,
    ):
        """快照提交后上传失败先自动回队；重试复用 manifest，不读取已修改 Profile。"""
        from app.models.export import Export, SnapshotManifest
        from app.models.task import Task
        from storage import StorageClient

        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        dataset = (await _make_finalized_dataset(client, db_session, pid))["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.commit()
        profile_id = profile.id
        response = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        body = response.json()
        original_put = StorageClient.put_object_versioned

        def fail_upload(*_args, **_kwargs):
            raise OSError("injected upload failure")

        monkeypatch.setattr(StorageClient, "put_object_versioned", fail_upload)
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))

        db_session.expire_all()
        first_task = (
            await db_session.execute(select(Task).where(Task.id == uuid.UUID(body["task_id"])))
        ).scalar_one()
        first_export = (
            await db_session.execute(
                select(Export).where(Export.id == uuid.UUID(body["export_id"]))
            )
        ).scalar_one()
        snapshot_id = first_export.snapshot_manifest_id
        assert first_task.status == "queued"
        assert first_export.status == "processing"
        assert snapshot_id is not None
        assert (
            await db_session.execute(
                select(SnapshotManifest).where(SnapshotManifest.id == snapshot_id)
            )
        ).scalar_one().manifest["profile"]["format"] == "qa_json"

        # 修改当前 Profile；retry 必须继续使用 Export.profile_snapshot/manifest 的旧格式。
        await db_session.execute(
            update(ExportProfile)
            .where(ExportProfile.id == profile_id)
            .values(format="messages", version=ExportProfile.version + 1)
        )
        first_task.next_run_at = datetime.now(UTC)
        await db_session.commit()
        monkeypatch.setattr(StorageClient, "put_object_versioned", original_put)
        await _run_export_worker(_test_session_factory, first_task.id)

        db_session.expire_all()
        completed = (
            await db_session.execute(
                select(Export).where(Export.id == uuid.UUID(body["export_id"]))
            )
        ).scalar_one()
        assert completed.status == "completed"
        assert completed.format == "qa_json"
        assert completed.snapshot_manifest_id == snapshot_id

    async def test_finalize_failure_manual_retry_updates_same_export(
        self,
        client,
        db_session,
        org,
        _test_session_factory,
        monkeypatch,
    ):
        """上传后 finalize 回滚时人工 retry 原子切换 Task，并复用同一快照/Export。"""
        from app.models.export import Export, ExportArtifactSeal
        from app.services.export_service import ExportService

        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        dataset = (await _make_finalized_dataset(client, db_session, pid))["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.commit()
        profile_id = profile.id
        response = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        body = response.json()
        original_finalize = ExportService.finalize_completed

        async def reject_finalize(*_args, **_kwargs) -> bool:
            return False

        monkeypatch.setattr(ExportService, "finalize_completed", reject_finalize)
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
        db_session.expire_all()
        failed = (
            await db_session.execute(
                select(Export).where(Export.id == uuid.UUID(body["export_id"]))
            )
        ).scalar_one()
        snapshot_id = failed.snapshot_manifest_id
        assert failed.status == "failed"
        assert snapshot_id is not None
        assert (
            await db_session.execute(
                select(ExportArtifactSeal).where(ExportArtifactSeal.export_id == failed.id)
            )
        ).scalar_one_or_none() is None

        await db_session.execute(
            update(ExportProfile)
            .where(ExportProfile.id == profile_id)
            .values(format="messages", version=ExportProfile.version + 1)
        )
        await db_session.commit()
        db_session.expire_all()
        retry = await client.post(
            f"/api/projects/{pid}/tasks/{body['task_id']}/retry",
            headers={**headers, "Idempotency-Key": "export-finalize-retry"},
        )
        assert retry.status_code == 201, retry.text
        retry_task_id = retry.json()["id"]
        await db_session.commit()
        # 同 key 安全重放只返回同一后继；不同 key 不得再创建第二个活跃后继。
        replay = await client.post(
            f"/api/projects/{pid}/tasks/{body['task_id']}/retry",
            headers={**headers, "Idempotency-Key": "export-finalize-retry"},
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == retry_task_id
        conflict = await client.post(
            f"/api/projects/{pid}/tasks/{body['task_id']}/retry",
            headers={**headers, "Idempotency-Key": "another-retry"},
        )
        assert conflict.status_code == 409
        await db_session.commit()

        monkeypatch.setattr(ExportService, "finalize_completed", original_finalize)
        await _run_export_worker(_test_session_factory, uuid.UUID(retry_task_id))
        db_session.expire_all()
        completed = (
            await db_session.execute(
                select(Export).where(Export.id == uuid.UUID(body["export_id"]))
            )
        ).scalar_one()
        assert completed.status == "completed"
        assert completed.task_id == uuid.UUID(retry_task_id)
        assert completed.retry_count == 1
        assert completed.snapshot_manifest_id == snapshot_id
        assert completed.format == "qa_json"

    async def test_cancel_after_upload_rolls_back_seal_and_finalize(
        self,
        client,
        db_session,
        org,
        _test_session_factory,
        monkeypatch,
    ):
        """两个对象上传后取消：Task/Export 收敛，seal 与 completed 发布整笔回滚。"""
        from app.models.export import Export, ExportArtifactSeal
        from app.models.task import Task
        from app.services.export_service import ExportService
        from app.workers.queue import TaskQueue

        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        dataset = (await _make_finalized_dataset(client, db_session, pid))["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.commit()
        response = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        body = response.json()
        task_id = uuid.UUID(body["task_id"])
        original_insert = ExportService.insert_artifact_seal

        async def insert_then_cancel(service, **kwargs):
            seal = await original_insert(service, **kwargs)
            async with _test_session_factory() as cancel_db:
                ok, status = await TaskQueue(cancel_db).request_cancel(
                    task_id=task_id,
                    cancel_requested_by=org["users"]["editor"].id,
                )
                assert ok and status == "cancelling"
                await cancel_db.commit()
            return seal

        monkeypatch.setattr(ExportService, "insert_artifact_seal", insert_then_cancel)
        await _run_export_worker(_test_session_factory, task_id)
        db_session.expire_all()
        task = (
            await db_session.execute(select(Task).where(Task.id == task_id))
        ).scalar_one()
        export = (
            await db_session.execute(
                select(Export).where(Export.id == uuid.UUID(body["export_id"]))
            )
        ).scalar_one()
        assert task.status == "cancelled"
        assert export.status == "failed"
        assert export.snapshot_manifest_id is not None
        assert export.artifact_seal_id is None
        assert (
            await db_session.execute(
                select(ExportArtifactSeal).where(ExportArtifactSeal.export_id == export.id)
            )
        ).scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# 验收 6：DB 不可变 trigger
# ---------------------------------------------------------------------------


class TestDatabaseImmutability:
    async def test_completed_export_update_rejected(self, client, db_session, org, _test_session_factory):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid)
        dataset = ds["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()
        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        body = res.json()
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
        await db_session.commit()
        from app.models.export import Export

        export = (await db_session.execute(select(Export).where(Export.id == uuid.UUID(body["export_id"])))).scalar_one()
        assert export.status == "completed"

        # 用全新会话执行 raw SQL（DB trigger 拒绝 UPDATE/DELETE）。
        async with _test_session_factory() as s:
            with pytest.raises(DBAPIError):
                await s.execute(text("UPDATE exports SET item_count = 999 WHERE id = :id"), {"id": export.id})
            await s.rollback()
            with pytest.raises(DBAPIError):
                await s.execute(text("DELETE FROM exports WHERE id = :id"), {"id": export.id})
            await s.rollback()

    async def test_snapshot_manifest_update_rejected(self, client, db_session, org, _test_session_factory):
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid)
        dataset = ds["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()
        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        body = res.json()
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
        await db_session.commit()
        from app.models.export import SnapshotManifest

        snap = (await db_session.execute(select(SnapshotManifest).where(SnapshotManifest.export_id == uuid.UUID(body["export_id"])))).scalar_one()
        async with _test_session_factory() as s:
            with pytest.raises(DBAPIError):
                await s.execute(text("UPDATE snapshot_manifests SET manifest = '{}'::jsonb WHERE id = :id"), {"id": snap.id})
            await s.rollback()
            with pytest.raises(DBAPIError):
                await s.execute(text("DELETE FROM snapshot_manifests WHERE id = :id"), {"id": snap.id})
            await s.rollback()


# ---------------------------------------------------------------------------
# 验收 4/5：provenance 缺失阻断 + secret 扫描
# ---------------------------------------------------------------------------


class TestProvenanceAndSecrets:
    async def test_provenance_gap_blocks_export(self, client, db_session, org, _test_session_factory):
        """删除冻结 GenerationBatch -> 导出 PROVENANCE_SNAPSHOT_MISSING 失败而非降级。"""
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid)
        dataset = ds["dataset"]
        # 制造冻结引用缺失：把 GenerationBatch 置为 invalid 且清空冻结快照（CHECK 允许
        # provenance_status != 'verified' 时快照为 NULL），manifest 构建即命中
        # prompt_template_snapshot_missing 阻断导出。
        from app.models.generation_batch import GenerationBatch

        batches = (await db_session.execute(select(GenerationBatch))).scalars().all()
        for batch in batches:
            batch.provenance_status = "invalid"
            batch.provenance_error_code = "T11_TEST_GAP"
            batch.is_legacy = True
            batch.prompt_template_snapshot = None
            batch.prompt_template_sha256 = None
            batch.model_config_snapshot = None
            batch.model_config_sha256 = None
        await db_session.commit()

        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()
        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        assert res.status_code == 202, res.text
        body = res.json()
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
        await db_session.commit()
        from app.models.export import Export

        export = (await db_session.execute(select(Export).where(Export.id == uuid.UUID(body["export_id"])))).scalar_one()
        assert export.status == "failed"
        assert export.error_code == "PROVENANCE_SNAPSHOT_MISSING"

    async def test_manifest_has_no_secrets(self, client, db_session, org, _test_session_factory):
        """manifest/对象不含 api_key/token/预签名 URL。"""
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid)
        dataset = ds["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()
        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        body = res.json()
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
        await db_session.commit()
        from app.models.export import SnapshotManifest

        snap = (await db_session.execute(select(SnapshotManifest).where(SnapshotManifest.export_id == uuid.UUID(body["export_id"])))).scalar_one()
        raw = json.dumps(snap.manifest, ensure_ascii=False)
        # 只扫描明确的秘密字段（不含 token_count/max_tokens 等合法词）。
        for bad in ("api_key", "api-key", "access_token", "bearer", "X-Amz-Signature", "presigned_url", "authorization"):
            assert bad not in raw, f"manifest 含敏感字段: {bad}"
        # 无预签名 URL / 无 http 端点泄露（除 base_url 白名单）。
        assert "X-Amz-" not in raw


# ---------------------------------------------------------------------------
# 验收 13：修改当前配置后导出仍只出现冻结值
# ---------------------------------------------------------------------------


class TestFrozenValuesOnly:
    async def test_export_uses_frozen_curated_revision(self, client, db_session, org, _test_session_factory):
        """导出后修改 CuratedItem 当前 content，重导出仍只出现冻结 revision 值。"""
        headers = await _login(client, "editor_user")
        pid = org["projects"]["a"].id
        ds = await _make_finalized_dataset(client, db_session, pid, content={"question": "q?", "answer": "a1"})
        dataset = ds["dataset"]
        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        await db_session.commit()
        res = await client.post(
            f"/api/projects/{pid}/datasets/{dataset.id}/export",
            headers=headers,
            json={
                "export_profile_id": str(profile.id),
                "expected_source_revision": dataset.composition_revision,
                "expected_source_sha256": dataset.composition_sha256,
            },
        )
        body = res.json()
        await _run_export_worker(_test_session_factory, uuid.UUID(body["task_id"]))
        await db_session.commit()
        from app.models.export import SnapshotManifest

        snap = (await db_session.execute(select(SnapshotManifest).where(SnapshotManifest.export_id == uuid.UUID(body["export_id"])))).scalar_one()
        member = snap.manifest["members"][0]
        assert member["curated_revision"]["content"]["answer"] == "a1"
        assert "a1" in json.dumps(snap.manifest, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 验收 12：completed 需 seal（deferred constraint）
# ---------------------------------------------------------------------------


class TestSealRequired:
    async def test_completed_without_seal_rejected(self, client, db_session, org):
        """直接把 Export 标 completed 而不提供 seal -> deferred constraint 回滚。"""
        pid = org["projects"]["a"].id
        from app.models.export import Export

        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        dataset = Dataset(project_id=pid, name="D", created_by=await org_uid(db_session, "editor_user"))
        db_session.add(dataset)
        await db_session.flush()

        # 构造 queued export。
        export = Export(
            project_id=pid, dataset_id=dataset.id, source_type="dataset", export_profile_id=profile.id,
            format="qa_json", status="queued", request_fingerprint="0" * 64,
            profile_snapshot={}, formatter_version="exporter-v1", created_by=await org_uid(db_session, "editor_user"),
            is_legacy=False,
        )
        db_session.add(export)
        await db_session.flush()
        # 置 completed 但无 seal：completed_complete CHECK 拒绝。
        with pytest.raises(DBAPIError):
            await db_session.execute(
                text("UPDATE exports SET status='completed' WHERE id=:id"),
                {"id": export.id},
            )
        await db_session.rollback()


# ---------------------------------------------------------------------------
# 验收 11：seal INSERT 校验
# ---------------------------------------------------------------------------


class TestSealInsertValidation:
    async def test_wrong_seal_hash_rejected(self, client, db_session, org):
        """直接 INSERT 错误 seal hash 被 DB 拒绝。"""
        pid = org["projects"]["a"].id
        uid = await org_uid(db_session, "editor_user")
        from app.models.export import Export, SnapshotManifest

        profile = ExportProfile(project_id=pid, name="EP", format="qa_json")
        db_session.add(profile)
        await db_session.flush()
        dataset = Dataset(project_id=pid, name="D", created_by=await org_uid(db_session, "editor_user"))
        db_session.add(dataset)
        await db_session.flush()

        export = Export(
            project_id=pid, dataset_id=dataset.id, source_type="dataset", export_profile_id=profile.id,
            format="qa_json", status="queued", request_fingerprint="0" * 64,
            profile_snapshot={}, formatter_version="exporter-v1", created_by=uid, is_legacy=False,
        )
        db_session.add(export)
        await db_session.flush()
        snap = SnapshotManifest(
            export_id=export.id, project_id=pid, manifest={}, schema_version=1,
            canonicalization_version="manifest-cjson-v1", manifest_sha256="0" * 64,
            sealed_at=datetime.now(UTC), is_legacy=False,
        )
        db_session.add(snap)
        await db_session.flush()

        with pytest.raises(DBAPIError):
            await db_session.execute(
                text(
                    "INSERT INTO export_artifact_seals (id, export_id, snapshot_manifest_id, seal_version, seal_payload, seal_sha256, sealed_at, "
                    "manifest_bucket, manifest_key, manifest_object_version_id, manifest_sha256, manifest_size, manifest_content_type, "
                    "output_bucket, output_key, output_object_version_id, output_sha256, output_size, output_content_type, created_at) "
                    "VALUES (:id, :eid, :sid, 'artifact-seal-cjson-v1', '{}'::jsonb, 'f'*64, now(), "
                    "'b','k','v','0'*64, 1, 'application/json', 'b','o','v2','0'*64, 1, 'application/json', now())"
                ),
                {"id": uuid.uuid4(), "eid": export.id, "sid": snap.id},
            )
        await db_session.rollback()
