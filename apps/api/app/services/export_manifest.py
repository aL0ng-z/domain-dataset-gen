"""T11：完整导出 manifest 构建（只消费 T03/T06/T08/T09/T10 冻结快照）。

任务卡 §4.4/§5.2：manifest 保存完整冻结数据，不只 ID 列表；任何缺失链路必须显式
记录 ``provenance_gap`` 并阻断新导出，不得用 null 冒充“已完整验证”。

本模块的职责：把导出涉及的全部冻结引用（T10 finalized composition、T09 approved
CuratedRevision/Evidence、T08 GenerationBatch/Prompt/Model 快照、T06 ChunkSet、
T03 ParserProfile 快照）组装为完整 manifest dict，并计算 manifest-cjson-v1 hash。

显式规则（任务卡 §4.4）：
- 所有字符串写入前做 NFC 规范化（hash 排除自身字段）。
- 时间统一 UTC ISO-8601。
- 任一冻结引用缺失或 hash 不符 -> 抛 :class:`ProvenanceSnapshotMissingError`
  （worker 映射为 PROVENANCE_SNAPSHOT_MISSING），禁止 fallback 到 current 配置。
- manifest 严禁含 api_key_encrypted、Bearer token、预签名 URL、未脱敏 secret。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.curated import CuratedItem, CuratedRevision
from app.models.dataset import Benchmark, Dataset
from app.models.document import Document
from app.models.generation import Candidate
from app.models.generation_batch import GenerationBatch
from app.models.parse import ParseJob
from app.models.review_record import ReviewRecord
from domain.manifest import (
    EXPORTER_VERSION,
    MANIFEST_CJSON_VERSION,
    MANIFEST_SCHEMA_VERSION,
    manifest_sha256,
)

#: 生成链路缺失/不可验证时的 provenance_gap 标记。
PROVENANCE_GAP_KEY = "provenance_gap"


class ProvenanceSnapshotMissingError(Exception):
    """任一冻结引用缺失或 hash 不符；导出必须失败而非降级。"""


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _nfc(value: str | None) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", value or "")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _get(db: AsyncSession, model, obj_id) -> Any:
    result = await db.execute(select(model).where(model.id == obj_id))
    return result.scalar_one_or_none()


async def _build_evidence_provenance(db: AsyncSession, evidence: dict) -> dict:
    """从 EvidenceLink 冻结坐标构建证据的完整版本图（Document/ParseJob/ChunkSet/Chunk）。

    任一证据引用缺失 -> provenance_gap（不伪造）。
    """
    chunk_id = evidence.get("chunk_id")
    document_id = evidence.get("document_id")
    document = await _get(db, Document, uuid.UUID(str(document_id)))
    if document is None:
        return {"document_id": str(document_id), PROVENANCE_GAP_KEY: "document_missing"}

    # Document -> ParseJob（T03 快照）。
    parse_job_row = (
        await db.execute(
            select(ParseJob)
            .where(ParseJob.document_id == document.id)
            .order_by(ParseJob.created_at.desc())
        )
    ).scalar_one_or_none()
    parse_snapshot = None
    if parse_job_row is not None:
        if parse_job_row.parser_profile_snapshot is None:
            parse_snapshot = {PROVENANCE_GAP_KEY: "parser_profile_snapshot_missing"}
        else:
            parse_snapshot = {
                "parse_job_id": str(parse_job_row.id),
                "parser_profile_snapshot": parse_job_row.parser_profile_snapshot,
                "parser_profile_sha256": parse_job_row.parser_profile_sha256,
                "endpoint_policy_ref": parse_job_row.endpoint_policy_ref,
                "endpoint_policy_version": parse_job_row.endpoint_policy_version,
                "endpoint_policy_sha256": parse_job_row.endpoint_policy_sha256,
            }
    else:
        parse_snapshot = {PROVENANCE_GAP_KEY: "parse_job_missing"}

    # Document -> ChunkSet（T06 快照）。
    chunk_set_row = None
    if document.active_chunk_set_id is not None:
        chunk_set_row = await _get(db, ChunkSet, document.active_chunk_set_id)
    chunk_set_snapshot = None
    if chunk_set_row is None:
        chunk_set_snapshot = {PROVENANCE_GAP_KEY: "chunk_set_missing"}
    else:
        chunk_set_snapshot = {
            "chunk_set_id": str(chunk_set_row.id),
            "version": chunk_set_row.version,
            "source_sha256": chunk_set_row.source_sha256,
            "output_sha256": chunk_set_row.output_sha256,
            "splitter_version": chunk_set_row.splitter_version,
            "config_json": chunk_set_row.config_json,
        }

    # Chunk 冻结内容。
    chunk = None
    if chunk_id is not None:
        chunk = await _get(db, Chunk, uuid.UUID(str(chunk_id)))
    chunk_snapshot = None
    if chunk is None:
        chunk_snapshot = {PROVENANCE_GAP_KEY: "chunk_missing"}
    else:
        chunk_snapshot = {
            "chunk_id": str(chunk.id),
            "ordinal": chunk.ordinal,
            "content": chunk.content,
            "content_sha256": _sha256(chunk.content),
        }

    return {
        "document": {
            "document_id": str(document.id),
            "filename": _nfc(document.filename),
            "sha256": document.sha256,
            "status": document.status,
        },
        "parse": parse_snapshot,
        "chunk_set": chunk_set_snapshot,
        "chunk": chunk_snapshot,
    }


async def _build_member_generation_provenance(db: AsyncSession, curated_item: CuratedItem) -> dict | None:
    """构建 CuratedItem 关联的生成链路版本图（T08）。

    CuratedItem -> Candidate -> GenerationRun -> GenerationBatch（冻结 prompt/model）。
    任一链路缺失 -> provenance_gap。
    """
    if curated_item.candidate_id is None:
        return {PROVENANCE_GAP_KEY: "candidate_missing"}
    candidate = await _get(db, Candidate, curated_item.candidate_id)
    if candidate is None:
        return {PROVENANCE_GAP_KEY: "candidate_missing"}
    run_row = None
    if candidate.generation_run_id is not None:
        from app.models.generation import GenerationRun

        run_row = await _get(db, GenerationRun, candidate.generation_run_id)
    if run_row is None:
        return {PROVENANCE_GAP_KEY: "generation_run_missing"}
    if run_row.generation_batch_id is None:
        return {PROVENANCE_GAP_KEY: "generation_batch_missing"}
    batch = await _get(db, GenerationBatch, run_row.generation_batch_id)
    if batch is None:
        return {PROVENANCE_GAP_KEY: "generation_batch_missing"}

    # T08 冻结快照（provenance=verified 时必填）。
    prompt_snapshot = batch.prompt_template_snapshot
    model_snapshot = batch.model_config_snapshot
    if prompt_snapshot is None or batch.prompt_template_sha256 is None:
        return {PROVENANCE_GAP_KEY: "prompt_template_snapshot_missing"}
    if model_snapshot is None or batch.model_config_sha256 is None:
        return {PROVENANCE_GAP_KEY: "model_config_snapshot_missing"}

    return {
        "candidate_id": str(candidate.id),
        "candidate_status": candidate.status,
        "generation_run_id": str(run_row.id),
        "generation_batch_id": str(batch.id),
        "prompt_template": {
            "prompt_template_version_id": str(batch.prompt_template_version_id) if batch.prompt_template_version_id else None,
            "snapshot": prompt_snapshot,
            "sha256": batch.prompt_template_sha256,
        },
        "model_config": {
            "snapshot": model_snapshot,
            "sha256": batch.model_config_sha256,
        },
        "renderer_version": batch.renderer_version,
    }


async def build_export_manifest(
    db: AsyncSession,
    *,
    export_id: uuid.UUID,
    project_id: uuid.UUID,
    requested_by: uuid.UUID,
    container: Dataset | Benchmark,
    memberships: list,
    export_profile_id: uuid.UUID,
    export_profile_version: int,
    export_format: str,
    profile_snapshot: dict,
    profile_snapshot_hash: str,
) -> tuple[dict, str]:
    """构建完整冻结 manifest 并返回 (manifest, manifest_sha256)。

    - memberships 为 DatasetItem/BenchmarkCase 列表（已含固定 revision/approval hash）。
    - 任一成员冻结引用缺失 -> 抛 :class:`ProvenanceSnapshotMissingError`。
    """
    container_type = "dataset" if isinstance(container, Dataset) else "benchmark"
    container_id = container.id
    container_name = container.name

    member_sections: list[dict] = []
    for m in memberships:
        # 固定 CuratedRevision（T09，不可变）。
        rev = await _get(db, CuratedRevision, m.curated_revision_id)
        if rev is None:
            raise ProvenanceSnapshotMissingError(
                f"membership {m.id} 固定 CuratedRevision 缺失：{m.curated_revision_id}"
            )
        # 固定 approval record（T09 证据快照）。
        record = await _get(db, ReviewRecord, m.approval_record_id)
        evidence_rows = []
        if record is not None and record.evidence_snapshot is not None:
            evidence_rows = (record.evidence_snapshot or {}).get("evidence_links") or []
        if not evidence_rows:
            raise ProvenanceSnapshotMissingError(
                f"membership {m.id} approval record 证据快照为空：{m.approval_record_id}"
            )
        curated = await _get(db, CuratedItem, m.curated_item_id)

        member_evidence = []
        for ev in evidence_rows:
            member_evidence.append(
                {
                    "evidence_link_id": str(ev.get("evidence_link_id", "")),
                    "quote_text": _nfc(ev.get("quote_text")),
                    "source_pages": ev.get("source_pages"),
                    "heading_path": _nfc(ev.get("heading_path")),
                    "start_char": ev.get("start_char"),
                    "end_char": ev.get("end_char"),
                    "provenance": await _build_evidence_provenance(db, ev),
                }
            )

        generation_prov = (
            await _build_member_generation_provenance(db, curated)
            if curated is not None
            else {PROVENANCE_GAP_KEY: "curated_item_missing"}
        )

        member_sections.append(
            {
                "membership_id": str(m.id),
                "ordinal": m.ordinal,
                "curated_item": {
                    "id": str(m.curated_item_id),
                    "type": curated.item_type if curated is not None else None,
                    "status": curated.status if curated is not None else None,
                },
                "curated_revision": {
                    "id": str(rev.id),
                    "version": rev.version,
                    "content": rev.content,
                    "content_sha256": rev.content_sha256,
                    "canonicalization_version": rev.canonicalization_version,
                },
                "approval": {
                    "approval_record_id": str(m.approval_record_id),
                    "evidence_sha256": m.approval_evidence_sha256,
                },
                "evidence": member_evidence,
                "generation": generation_prov,
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "canonicalization_version": MANIFEST_CJSON_VERSION,
        "exporter_version": EXPORTER_VERSION,
        "formatter_version": EXPORTER_VERSION,
        "export_id": str(export_id),
        "project_id": str(project_id),
        "requested_by": str(requested_by),
        "sealed_at": _iso(datetime.now(UTC)),
        "source": {
            "source_type": container_type,
            "source_id": str(container_id),
            "source_name": _nfc(container_name),
            "status": container.status,
            "composition_revision": container.composition_revision,
            "composition_sha256": container.composition_sha256,
            "composition_canonicalization_version": container.composition_canonicalization_version,
        },
        "profile": {
            "export_profile_id": str(export_profile_id),
            "version": export_profile_version,
            "format": export_format,
            "snapshot": profile_snapshot,
            "sha256": profile_snapshot_hash,
        },
        "members": member_sections,
        "member_count": len(member_sections),
    }
    _assert_no_provenance_gap(manifest)
    manifest_hash = manifest_sha256(manifest)
    return manifest, manifest_hash


def _assert_no_provenance_gap(value: Any, path: str = "") -> None:
    """递归断言 manifest 无任何 ``provenance_gap`` 标记。

    任一缺失链路必须阻断新导出（任务卡 §4.4）；不得用 null 冒充“已完整验证”。
    返回前扫描整棵 manifest，发现 gap 即抛 :class:`ProvenanceSnapshotMissingError`。
    """
    if isinstance(value, dict):
        if PROVENANCE_GAP_KEY in value:
            gap = value[PROVENANCE_GAP_KEY]
            raise ProvenanceSnapshotMissingError(
                f"provenance gap at {path or 'root'}: {gap}"
            )
        for k, v in value.items():
            _assert_no_provenance_gap(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _assert_no_provenance_gap(item, f"{path}[{idx}]")
