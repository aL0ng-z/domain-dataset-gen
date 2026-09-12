"""证据的固定来源链解析；不读取活动版本指针或最新解析任务。"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.parse import ParseJob
from app.models.section import CleaningJob


class ProvenanceSnapshotMissingError(Exception):
    """固定来源缺失或属于其他文档/项目，禁止猜测和回退。"""


@dataclass(frozen=True)
class EvidenceSource:
    document: Document
    chunk: Chunk
    chunk_set: ChunkSet
    clean_version: CleanedDocumentVersion
    cleaning_job: CleaningJob
    parse_job: ParseJob


async def resolve_evidence_source(
    db: AsyncSession, *, chunk_id, document_id, project_id: uuid.UUID
) -> EvidenceSource:
    try:
        chunk_uuid, document_uuid = uuid.UUID(str(chunk_id)), uuid.UUID(str(document_id))
    except (TypeError, ValueError) as exc:
        raise ProvenanceSnapshotMissingError("证据 Chunk/Document ID 缺失或非法") from exc
    chunk = await db.get(Chunk, chunk_uuid)
    document = await db.get(Document, document_uuid)
    if chunk is None or document is None:
        raise ProvenanceSnapshotMissingError("证据 Chunk/Document 缺失")
    if chunk.document_id != document.id or document.project_id != project_id:
        raise ProvenanceSnapshotMissingError("证据 Chunk 与 Document/Project 不一致")
    chunk_set = await db.get(ChunkSet, chunk.chunk_set_id) if chunk.chunk_set_id else None
    if chunk_set is None or chunk_set.document_id != document.id or chunk_set.status != "completed":
        raise ProvenanceSnapshotMissingError("证据所属 ChunkSet 缺失、不一致或未完成")
    clean_version = await db.get(CleanedDocumentVersion, chunk_set.cleaned_document_version_id) if chunk_set.cleaned_document_version_id else None
    if clean_version is None or clean_version.document_id != document.id or clean_version.status != "accepted":
        raise ProvenanceSnapshotMissingError("证据固定清洗版本缺失、不一致或未接受")
    if any(value is None for value in (
        clean_version.content_sha256, clean_version.source_revision_map, clean_version.source_revision_sha256,
        chunk_set.config_json, chunk_set.source_sha256, chunk_set.output_sha256, chunk_set.splitter_version,
    )):
        raise ProvenanceSnapshotMissingError("证据清洗版本或切分配置冻结快照缺失")
    content_hash = hashlib.sha256(clean_version.merged_markdown.encode("utf-8")).hexdigest()
    if clean_version.content_sha256 != content_hash or chunk_set.source_sha256 != content_hash:
        raise ProvenanceSnapshotMissingError("证据清洗正文或切分来源 hash 不一致")
    cleaning_job = await db.get(CleaningJob, clean_version.source_cleaning_job_id) if clean_version.source_cleaning_job_id else None
    if cleaning_job is None or cleaning_job.document_id != document.id:
        raise ProvenanceSnapshotMissingError("证据清洗任务缺失或不一致")
    parse_job = await db.get(ParseJob, cleaning_job.parse_job_id)
    if parse_job is None or parse_job.document_id != document.id:
        raise ProvenanceSnapshotMissingError("证据解析任务缺失或不一致")
    if any(value is None for value in (
        parse_job.parser_profile_snapshot, parse_job.parser_profile_sha256,
        parse_job.endpoint_policy_snapshot, parse_job.endpoint_policy_ref,
        parse_job.endpoint_policy_version, parse_job.endpoint_policy_sha256,
    )):
        raise ProvenanceSnapshotMissingError("证据解析配置冻结快照缺失")
    return EvidenceSource(document, chunk, chunk_set, clean_version, cleaning_job, parse_job)
