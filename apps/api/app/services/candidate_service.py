import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.curated import CuratedItem, CuratedRevision, EvidenceLink
from app.models.document import Document
from app.models.generation import Candidate, CandidateComment
from app.models.generation_batch import GenerationBatch
from domain.canonical import (
    CURATED_CONTENT_CJSON_VERSION,
    curated_content_sha256,
)


class CandidateStateConflictError(Exception):
    """Candidate 已处于不允许当前 review/edit 的状态（409 CANDIDATE_REVIEW_STATE_CONFLICT）。"""


class CandidateEvidenceRequiredError(Exception):
    """supported/partially_supported 缺少有效证据（409 CANDIDATE_EVIDENCE_REQUIRED）。"""


class CandidateAlreadyPromotedError(Exception):
    """Candidate 已有 CuratedItem（409 CANDIDATE_ALREADY_PROMOTED）。"""

    def __init__(self, message: str, item_id: uuid.UUID | None = None):
        super().__init__(message)
        self.item_id = item_id


class EvidenceValidationError(Exception):
    """证据 span 校验失败（越界/quote 不匹配/跨项目或批次 Chunk，422）。"""


class CandidateRevisionConflictError(Exception):
    """客户端基于旧内容版本进行编辑、审核或提升（409）。"""

    def __init__(self, current_revision: int):
        super().__init__("候选内容已更新，请重新加载后再操作")
        self.current_revision = current_revision


class CandidateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, candidate_id: uuid.UUID) -> Candidate | None:
        result = await self.db.execute(
            select(Candidate).where(Candidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def _get_for_update(self, candidate_id: uuid.UUID) -> Candidate | None:
        """读取并锁住 Candidate，避免审核/编辑/提升基于不同内容版本执行。"""
        return await self.db.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _require_revision(candidate: Candidate, expected_revision: int) -> None:
        if candidate.content_revision != expected_revision:
            raise CandidateRevisionConflictError(candidate.content_revision)

    async def _existing_curated_item(self, candidate_id: uuid.UUID) -> CuratedItem | None:
        return await self.db.scalar(
            select(CuratedItem).where(CuratedItem.candidate_id == candidate_id).limit(1)
        )

    async def update_content(
        self, candidate_id: uuid.UUID, content: dict, *, expected_revision: int
    ) -> Candidate | None:
        """更新 Candidate 内容并置 status=human_edited；已提升 Candidate 不可再改。

        已提升（存在 CuratedItem）默认返回 None 由路由映射 409；同事务清空旧审核字段。
        """
        candidate = await self._get_for_update(candidate_id)
        if candidate is None:
            return None
        self._require_revision(candidate, expected_revision)
        if await self._existing_curated_item(candidate_id) is not None:
            raise CandidateAlreadyPromotedError("候选已提升，内容不可再修改；后续编辑走 CuratedItem revision")

        candidate.content = content
        candidate.status = "human_edited"
        # 内容编辑后旧审核结论失效：清空审核字段（任务卡 §5.1）。
        candidate.reviewed_by = None
        candidate.review_verdict = None
        candidate.review_evidence_spans = None
        candidate.reject_reason = None
        candidate.reviewed_content_revision = None
        candidate.content_revision += 1
        await self.db.flush()
        await self.db.refresh(candidate)
        return candidate

    async def _resolve_span(
        self, candidate: Candidate, span: dict
    ) -> dict:
        """校验单个证据 span 并返回标准化 span。

        - Chunk 属于 Candidate 的 source batch/项目（跨项目/批次 Chunk -> 422）。
        - offset 以 Unicode code point 左闭右开计数；越界 -> 422。
        - chunk.content[start:end] == quote_text（NFC 比较）-> 否则 422。
        """
        try:
            chunk_id = uuid.UUID(str(span["chunk_id"]))
            start, end, quote = span["start_char"], span["end_char"], span["quote_text"]
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceValidationError("证据 span 字段缺失或非法") from exc
        if type(start) is not int or type(end) is not int or not isinstance(quote, str):
            raise EvidenceValidationError("证据 span 范围必须为整数，引用必须为字符串")

        chunk = (
            await self.db.execute(select(Chunk).where(Chunk.id == chunk_id))
        ).scalar_one_or_none()
        if chunk is None:
            raise EvidenceValidationError("证据 Chunk 不存在")

        candidate_source_chunk = await self.db.get(Chunk, candidate.chunk_id)
        if candidate_source_chunk is None:
            raise EvidenceValidationError("Candidate 关联 Chunk 不存在")
        candidate_project = await self.db.scalar(
            select(Document.project_id).where(Document.id == candidate_source_chunk.document_id)
        )
        chunk_project = await self.db.scalar(
            select(Document.project_id).where(Document.id == chunk.document_id)
        )
        if candidate_project is None or chunk_project is None or candidate_project != chunk_project:
            raise EvidenceValidationError("证据 Chunk 不属于 Candidate 所在项目")

        # 证据集合属于生成时冻结的 selected_chunk_ids，而不是当前 active ChunkSet。
        # 历史 batch 仍按自己的选择集校验，active 指针变化不能扩大可引用来源。
        if candidate.source_generation_batch_id is None:
            raise EvidenceValidationError("Candidate 缺少冻结的生成批次来源")
        batch = await self.db.get(GenerationBatch, candidate.source_generation_batch_id)
        if batch is None or batch.document_id != candidate_source_chunk.document_id:
            raise EvidenceValidationError("Candidate 生成批次来源无效")
        selected_chunk_ids = batch.selected_chunk_ids
        if not isinstance(selected_chunk_ids, list) or str(chunk.id) not in {
            str(selected_id) for selected_id in selected_chunk_ids
        }:
            raise EvidenceValidationError("证据 Chunk 不属于 Candidate 生成批次的冻结来源")

        content_len = len(chunk.content)
        if start < 0 or end > content_len or start >= end:
            raise EvidenceValidationError("证据 span 超出 Chunk 文本范围")

        actual = chunk.content[start:end]
        import unicodedata

        if unicodedata.normalize("NFC", actual) != unicodedata.normalize("NFC", quote):
            raise EvidenceValidationError("证据 quote_text 与 Chunk 原文不匹配")

        return {
            "chunk_id": str(chunk_id),
            "start_char": start,
            "end_char": end,
            "quote_text": quote,
        }

    async def review(
        self,
        candidate_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        verdict: str,
        *,
        expected_revision: int,
        evidence_spans: list[dict] | None = None,
        reject_reason: str | None = None,
    ) -> Candidate | None:
        """审核 Candidate：校验 span -> 写 verdict/evidence bundle/reject reason/状态。

        supported/partially_supported 必须至少一个 span（否则抛 EvidenceRequired）；
        unsupported/out_of_scope 必须非空 reject_reason（schema 已强制，服务层兜底）。
        status=approved|rejected 由 verdict 映射；同事务写一条 ReviewRecord。
        """
        candidate = await self._get_for_update(candidate_id)
        if candidate is None:
            return None
        self._require_revision(candidate, expected_revision)
        # 已提升的 Candidate 不可再改（冻结）。
        if await self._existing_curated_item(candidate_id) is not None:
            raise CandidateAlreadyPromotedError("候选已提升，审核结论不可再修改")

        normalized_spans: list[dict] = []
        if evidence_spans:
            for span in evidence_spans:
                normalized_spans.append(await self._resolve_span(candidate, span))
        normalized_spans = _dedupe_spans(normalized_spans)

        if verdict in ("supported", "partially_supported") and not normalized_spans:
            raise CandidateEvidenceRequiredError("supported/partially_supported 必须提供有效证据 span")
        if verdict in ("unsupported", "out_of_scope") and not (reject_reason and reject_reason.strip()):
            raise EvidenceValidationError("unsupported/out_of_scope 必须提供非空拒绝原因")

        bundle = (
            {"schema_version": 1, "spans": normalized_spans}
            if normalized_spans
            else None
        )

        candidate.reviewed_by = reviewer_id
        candidate.review_verdict = verdict
        candidate.review_evidence_spans = bundle
        candidate.reject_reason = reject_reason
        candidate.reviewed_content_revision = candidate.content_revision
        candidate.status = "approved" if verdict in ("supported", "partially_supported") else "rejected"

        from app.models.review_record import ReviewRecord

        self.db.add(
            ReviewRecord(
                entity_type="candidate",
                entity_id=candidate_id,
                reviewer_id=reviewer_id,
                action="approve" if candidate.status == "approved" else "reject",
                reason=reject_reason,
            )
        )

        await self.db.flush()
        await self.db.refresh(candidate)
        return candidate

    async def add_comment(
        self,
        candidate_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
    ) -> CandidateComment:
        comment = CandidateComment(
            candidate_id=candidate_id,
            user_id=user_id,
            content=content,
        )
        self.db.add(comment)
        await self.db.flush()
        await self.db.refresh(comment)
        return comment

    async def list_comments(
        self, candidate_id: uuid.UUID
    ) -> list[CandidateComment]:
        result = await self.db.execute(
            select(CandidateComment)
            .where(CandidateComment.candidate_id == candidate_id)
            .order_by(CandidateComment.created_at)
        )
        return list(result.scalars().all())

    async def promote_to_curated(
        self, candidate_id: uuid.UUID, promoted_by: uuid.UUID, *, expected_revision: int
    ) -> CuratedItem:
        """幂等提升：approved 且证据有效的 Candidate -> 1 CuratedItem + v1 revision + EvidenceLink。

        并发重复提升最多一个成功：候选先查既有 CuratedItem（返回已有），再以条件插入
        （数据库 candidate_id 唯一约束兜底并发）。
        """
        candidate = await self._get_for_update(candidate_id)
        if candidate is None:
            raise ValueError("候选项不存在")
        self._require_revision(candidate, expected_revision)
        if candidate.status != "approved":
            raise CandidateStateConflictError("仅已审核通过的候选项可提升为知识条目")
        if candidate.reviewed_content_revision != candidate.content_revision:
            raise CandidateStateConflictError("候选内容已在审核后变更，请重新审核")

        # 幂等：已有 CuratedItem -> 返回既有（路由映射 409 + context item id）。
        existing = await self._existing_curated_item(candidate_id)
        if existing is not None:
            raise CandidateAlreadyPromotedError("该候选项已提升", existing.id)

        # 证据有效：至少一个 span 且 quote 校验通过（审核时已验证，此处复核）。
        spans = _extract_spans(candidate.review_evidence_spans)
        if not spans:
            raise CandidateEvidenceRequiredError("提升要求候选项有有效证据 span")

        chunk = (
            await self.db.execute(select(Chunk).where(Chunk.id == candidate.chunk_id))
        ).scalar_one_or_none()
        if chunk is None:
            raise ValueError("关联Chunk不存在")

        doc_result = await self.db.execute(
            select(Document.project_id).where(Document.id == chunk.document_id)
        )
        project_id = doc_result.scalar_one_or_none()
        if project_id is None:
            raise ValueError("关联文档不存在")

        # 审核后至提升前也可能发生数据变化；先复核全部 span，再写入产物。
        resolved_spans = []
        for span in spans:
            resolved = await self._resolve_span(candidate, span)
            source_chunk = (
                await self.db.execute(select(Chunk).where(Chunk.id == uuid.UUID(resolved["chunk_id"])))
            ).scalar_one()
            resolved_spans.append((resolved, source_chunk))

        # 物化 CuratedItem（v1 revision 在同一事务）。
        curated_item = CuratedItem(
            project_id=project_id,
            candidate_id=candidate_id,
            content=candidate.content,
            item_type=candidate.candidate_type,
            status="draft",
            promoted_by=promoted_by,
            current_revision=1,
        )
        self.db.add(curated_item)
        await self.db.flush()

        revision = CuratedRevision(
            curated_item_id=curated_item.id,
            revised_by=promoted_by,
            version=1,
            content=candidate.content,
            content_sha256=curated_content_sha256(candidate.content),
            canonicalization_version=CURATED_CONTENT_CJSON_VERSION,
            revision_note="提升自 Candidate",
        )
        self.db.add(revision)

        # EvidenceLink 服务端派生：document/页码/标题/quote 全来自 Chunk 与已验证 span。
        for span, source_chunk in resolved_spans:
            self.db.add(
                EvidenceLink(
                    curated_item_id=curated_item.id,
                    document_id=source_chunk.document_id,
                    chunk_id=source_chunk.id,
                    start_char=span["start_char"],
                    end_char=span["end_char"],
                    source_pages=source_chunk.source_pages,
                    heading_path=source_chunk.heading_path,
                    quote_text=source_chunk.content[span["start_char"]:span["end_char"]],
                )
            )
        await self.db.flush()
        await self.db.refresh(curated_item)
        return curated_item

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Candidate], int]:
        offset = (page - 1) * page_size

        base = (
            select(Candidate)
            .join(Chunk, Candidate.chunk_id == Chunk.id)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.project_id == project_id)
        )
        if status:
            base = base.where(Candidate.status == status)

        count_result = await self.db.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar() or 0

        result = await self.db.execute(
            base.order_by(Candidate.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_by_chunk(
        self, chunk_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Candidate], int]:
        offset = (page - 1) * page_size

        count_result = await self.db.execute(
            select(func.count()).select_from(Candidate).where(Candidate.chunk_id == chunk_id)
        )
        total = count_result.scalar() or 0

        result = await self.db.execute(
            select(Candidate)
            .where(Candidate.chunk_id == chunk_id)
            .order_by(Candidate.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total


def _extract_spans(bundle: dict | None) -> list[dict]:
    """从 review_evidence_spans bundle 提取 span 列表（兼容旧 dict 结构）。"""
    if not bundle or not isinstance(bundle, dict):
        return []
    spans = bundle.get("spans") or []
    if not isinstance(spans, list):
        return []
    return [s for s in spans if isinstance(s, dict)]


def _dedupe_spans(spans: list[dict]) -> list[dict]:
    """按 (chunk_id, start_char, end_char) 去重并稳定排序。"""
    seen: dict = {}
    for s in spans:
        key = (s["chunk_id"], s["start_char"], s["end_char"])
        seen[key] = s
    return sorted(
        seen.values(),
        key=lambda s: (s["chunk_id"], s["start_char"], s["end_char"]),
    )
