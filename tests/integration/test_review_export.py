"""R16–R19：真实审批、固定来源及导出门禁回归。"""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.curated import EvidenceLink
from app.models.export import Export, SnapshotManifest
from app.models.parse import ParseJob
from app.models.task import Task
from app.services.candidate_service import CandidateService, EvidenceValidationError
from app.services.evidence_provenance import ProvenanceSnapshotMissingError, resolve_evidence_source
from domain.manifest import EXPORTER_VERSION, MANIFEST_SCHEMA_VERSION
from tests.integration.test_export_snapshot import (
    _login,
    _make_approved_item,
    _make_doc_chain,
    _make_finalized_dataset,
    _make_verified_candidate,
    _run_export_worker,
    _span,
)

pytestmark = pytest.mark.integration


async def request_export(client, db_session, org, dataset, fmt="qa_json"):
    from app.models.config import ExportProfile

    pid = org["projects"]["a"].id
    profile = ExportProfile(project_id=pid, name="review", format=fmt)
    db_session.add(profile)
    await db_session.commit()
    return await client.post(
        f"/api/projects/{pid}/datasets/{dataset.id}/export", headers=await _login(client, "editor_user"),
        json={"export_profile_id": str(profile.id), "expected_source_revision": dataset.composition_revision,
              "expected_source_sha256": dataset.composition_sha256},
    )


@pytest.mark.parametrize(("item_type", "content", "fmt", "code"), [
    ("knowledge_extraction", {"title": "知识", "summary": "摘要"}, "qa_json", "EXPORT_FORMAT_INCOMPATIBLE"),
    ("benchmark_case", {"question": "Q", "reference_answer": "A"}, "sft_jsonl", "EXPORT_FORMAT_INCOMPATIBLE"),
    ("qa_generation", {"question": "Q", "answer": "  "}, "qa_json", "EXPORT_CONTENT_INVALID"),
    ("benchmark_case", {"question": "Q", "answer": "A"}, "benchmark_json", "EXPORT_CONTENT_INVALID"),
])
async def test_export_api_rejects_before_creating_task_or_artifact(client, db_session, org, item_type, content, fmt, code):
    ds = await _make_finalized_dataset(client, db_session, org["projects"]["a"].id, item_type=item_type, content=content)
    count_before = (await db_session.execute(select(func.count()).select_from(Task))).scalar_one()
    response = await request_export(client, db_session, org, ds["dataset"], fmt)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code
    assert response.json()["context"]["items"][0]["curated_item_id"] == str(ds["approved"]["curated_item"].id)
    assert (await db_session.execute(select(func.count()).select_from(Task))).scalar_one() == count_before
    assert (await db_session.execute(select(func.count()).select_from(Export))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(SnapshotManifest))).scalar_one() == 0


async def test_unknown_export_format_is_422(client, db_session, org):
    ds = await _make_finalized_dataset(client, db_session, org["projects"]["a"].id)
    response = await request_export(client, db_session, org, ds["dataset"], "unknown")
    assert response.status_code == 422, response.text


async def test_mixed_batch_rejects_whole_request(client, db_session, org):
    from app.models.dataset import Dataset
    from app.services.composition_service import CompositionService

    pid, uid = org["projects"]["a"].id, org["users"]["reviewer"].id
    qa = await _make_approved_item(client, db_session, pid)
    knowledge = await _make_approved_item(client, db_session, pid, item_type="knowledge_extraction", content={"title": "知识"})
    dataset = Dataset(project_id=pid, name="混合类型", created_by=uid)
    db_session.add(dataset)
    await db_session.flush()
    service = CompositionService(db_session)
    for item in (qa, knowledge):
        await service.add_membership(container_type="dataset", container_id=dataset.id,
                                     curated_item_id=item["curated_item"].id, require_supported=False)
    dataset = await service.finalize(container_type="dataset", container_id=dataset.id,
                                     expected_revision=dataset.composition_revision,
                                     expected_sha256=dataset.composition_sha256, reviewer_id=uid)
    await db_session.commit()
    response = await request_export(client, db_session, org, dataset)
    assert response.status_code == 409
    assert response.json()["code"] == "EXPORT_FORMAT_INCOMPATIBLE"
    assert (await db_session.execute(select(func.count()).select_from(Export))).scalar_one() == 0


async def test_worker_rechecks_content_before_sealing(client, db_session, org, _test_session_factory):
    from app.models.config import ExportProfile
    from app.services.export_service import ExportService
    from app.services.task_service import TaskService

    pid, uid = org["projects"]["a"].id, org["users"]["editor"].id
    ds = await _make_finalized_dataset(client, db_session, pid, content={"question": "Q", "answer": " "})
    dataset = ds["dataset"]
    profile = ExportProfile(project_id=pid, name="worker guard", format="qa_json")
    db_session.add(profile)
    await db_session.flush()
    # 绕过 HTTP 门禁模拟先前排队的请求；worker 仍必须在快照和上传前独立拒绝。
    service = ExportService(db_session)
    export = await service.create_export_request(
        project_id=pid, dataset_id=dataset.id, benchmark_id=None, export_profile_id=profile.id,
        created_by=uid, request_fingerprint="0" * 64, profile_snapshot=service.build_profile_snapshot(profile), task_id=None,
    )
    task = await TaskService(db_session, None).create_task(
        project_id=pid, task_type="export", entity_type="dataset", entity_id=dataset.id,
        created_by=uid, handler="export_dataset", payload={"export_id": str(export.id),
        "source_type": "dataset", "source_id": str(dataset.id), "export_profile_id": str(profile.id), "created_by": str(uid)},
    )
    export.task_id = task.id
    await db_session.commit()
    await _run_export_worker(_test_session_factory, task.id)
    await db_session.refresh(export)
    assert export.status == "failed" and export.error_code == "EXPORT_CONTENT_INVALID"
    assert export.minio_key is None and export.snapshot_manifest_id is None
    assert (await db_session.execute(select(func.count()).select_from(SnapshotManifest))).scalar_one() == 0


async def test_export_uses_evidence_versions_with_multiple_parses_and_new_active_set(client, db_session, org, _test_session_factory):
    pid = org["projects"]["a"].id
    ds = await _make_finalized_dataset(client, db_session, pid)
    chain = ds["approved"]["res"]
    old_parse = chain["parse_job"]
    new_parse = ParseJob(
        document_id=chain["doc"].id, parser_profile_id=old_parse.parser_profile_id, status="completed",
        snapshot_schema_version=1, parser_profile_snapshot={"new": True}, parser_profile_sha256="4" * 64,
        endpoint_policy_snapshot={"policy": "new"}, endpoint_policy_ref="new", endpoint_policy_version="2",
        endpoint_policy_sha256="5" * 64,
    )
    db_session.add(new_parse)
    await db_session.flush()
    from app.models.chunk_set import ChunkSet
    # 活动集合允许推进；历史证据仍必须沿自身所属集合溯源。
    newer_set = ChunkSet(document_id=chain["doc"].id, status="completed", version=2, is_legacy=True,
                         created_by=org["users"]["admin"].id)
    db_session.add(newer_set)
    await db_session.flush()
    chain["doc"].active_chunk_set_id = newer_set.id
    await db_session.commit()
    # 当前可编辑正文不参与预检和封存；必须继续使用批准的固定 revision。
    ds["approved"]["curated_item"].content = {"question": "过期问题", "answer": ""}
    await db_session.commit()
    response = await request_export(client, db_session, org, ds["dataset"])
    assert response.status_code == 202, response.text
    await _run_export_worker(_test_session_factory, uuid.UUID(response.json()["task_id"]))
    snapshot = (await db_session.execute(select(SnapshotManifest))).scalar_one()
    assert snapshot.schema_version == MANIFEST_SCHEMA_VERSION == 2
    assert snapshot.manifest["exporter_version"] == EXPORTER_VERSION == "exporter-v3"
    assert snapshot.manifest["members"][0]["curated_revision"]["content"]["answer"] == "压比是出口与进口压力之比"
    evidence = snapshot.manifest["members"][0]["evidence"][0]["provenance"]
    assert evidence["parse"]["parse_job_id"] == str(old_parse.id)
    assert evidence["parse"]["parser_profile_snapshot"] == old_parse.parser_profile_snapshot
    assert evidence["chunk_set"]["chunk_set_id"] == str(chain["chunk_set"].id)
    assert evidence["cleaned_document_version"]["cleaned_document_version_id"] == str(chain["clean_version"].id)
    assert evidence["cleaned_document_version"]["merged_markdown"] == chain["chunk"].content
    assert evidence["cleaning_job"]["cleaning_job_id"] == str(chain["cleaning_job"].id)


async def test_review_rejects_span_outside_frozen_generation_batch(db_session, org):
    pid, uid = org["projects"]["a"].id, org["users"]["reviewer"].id
    first = await _make_doc_chain(db_session, pid, uid)
    second = await _make_doc_chain(db_session, pid, uid, content="第二文档第七页的证据正文")
    second["chunk"].heading_path = "第二章"
    second["chunk"].source_pages = {"start": 7, "end": 7}
    candidate, _, _ = await _make_verified_candidate(db_session, first, actor_uid=uid)
    service = CandidateService(db_session)
    with pytest.raises(EvidenceValidationError, match="冻结来源"):
        await service.review(
            candidate.id,
            uid,
            "supported",
            expected_revision=candidate.content_revision,
            evidence_spans=[
                _span(first["chunk"].id, first["chunk"].content, first["chunk"].content[:3]),
                _span(second["chunk"].id, second["chunk"].content, "证据正文"),
            ],
        )
    assert (await db_session.execute(select(EvidenceLink))).scalars().all() == []


async def test_promote_revalidates_reviewed_quote_before_writes(db_session, org):
    pid, uid = org["projects"]["a"].id, org["users"]["reviewer"].id
    chain = await _make_doc_chain(db_session, pid, uid)
    candidate, _, _ = await _make_verified_candidate(db_session, chain, actor_uid=uid)
    service = CandidateService(db_session)
    await service.review(
        candidate.id,
        uid,
        "supported",
        expected_revision=candidate.content_revision,
        evidence_spans=[_span(chain["chunk"].id, chain["chunk"].content, "压比")],
    )
    # 模拟审核后存储中出现过期/非法 span，不能继续产生错误的证据链接。
    candidate.review_evidence_spans = {"spans": [{"chunk_id": str(chain["chunk"].id), "start_char": 0, "end_char": 9999, "quote_text": "压比"}]}
    with pytest.raises(EvidenceValidationError):
        await service.promote_to_curated(candidate.id, uid, expected_revision=candidate.content_revision)
    assert (await db_session.execute(select(func.count()).select_from(EvidenceLink))).scalar_one() == 0


async def test_missing_fixed_clean_version_is_not_replaced_by_active(db_session, org):
    pid, uid = org["projects"]["a"].id, org["users"]["reviewer"].id
    chain = await _make_doc_chain(db_session, pid, uid)
    from app.models.chunk_set import ChunkSet
    missing = ChunkSet(document_id=chain["doc"].id, version=2, status="completed", is_legacy=True, created_by=uid)
    db_session.add(missing)
    await db_session.flush()
    chain["chunk"].chunk_set_id = missing.id
    await db_session.flush()
    with pytest.raises(ProvenanceSnapshotMissingError, match="固定清洗版本"):
        await resolve_evidence_source(db_session, chunk_id=chain["chunk"].id, document_id=chain["doc"].id, project_id=pid)
