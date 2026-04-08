import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.curated import CuratedItem, EvidenceLink
from app.models.document import Document
from app.models.generation import Candidate, CandidateComment


class CandidateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, candidate_id: uuid.UUID) -> Candidate | None:
        result = await self.db.execute(
            select(Candidate).where(Candidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def update_content(
        self, candidate_id: uuid.UUID, content: dict
    ) -> Candidate | None:
        """Update candidate content and set status to human_edited."""
        candidate = await self.get(candidate_id)
        if candidate is None:
            return None
        candidate.content = content
        candidate.status = "human_edited"
        await self.db.flush()
        await self.db.refresh(candidate)
        return candidate

    async def review(
        self,
        candidate_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        verdict: str,
        evidence_spans: dict | None = None,
        reject_reason: str | None = None,
    ) -> Candidate | None:
        """Set review verdict on a candidate. Status becomes approved or rejected based on verdict."""
        candidate = await self.get(candidate_id)
        if candidate is None:
            return None

        candidate.reviewed_by = reviewer_id
        candidate.review_verdict = verdict
        candidate.review_evidence_spans = evidence_spans
        candidate.reject_reason = reject_reason

        # Determine status from verdict
        if verdict in ("supported", "partially_supported"):
            candidate.status = "approved"
        else:
            candidate.status = "rejected"

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
        self, candidate_id: uuid.UUID, promoted_by: uuid.UUID
    ) -> CuratedItem:
        """Promote an approved candidate to a CuratedItem with EvidenceLink."""
        candidate = await self.get(candidate_id)
        if candidate is None:
            raise ValueError("候选项不存在")
        if candidate.status != "approved":
            raise ValueError("仅已审核通过的候选项可提升为知识条目")

        # Resolve project_id and chunk info via chunk -> document
        chunk_result = await self.db.execute(
            select(Chunk).where(Chunk.id == candidate.chunk_id)
        )
        chunk = chunk_result.scalar_one_or_none()
        if chunk is None:
            raise ValueError("关联Chunk不存在")

        doc_result = await self.db.execute(
            select(Document.project_id).where(Document.id == chunk.document_id)
        )
        project_id = doc_result.scalar_one_or_none()
        if project_id is None:
            raise ValueError("关联文档不存在")

        # Create CuratedItem
        curated_item = CuratedItem(
            project_id=project_id,
            candidate_id=candidate_id,
            content=candidate.content,
            item_type=candidate.candidate_type,
            status="draft",
            promoted_by=promoted_by,
        )
        self.db.add(curated_item)
        await self.db.flush()

        # Create EvidenceLink
        evidence_link = EvidenceLink(
            curated_item_id=curated_item.id,
            document_id=chunk.document_id,
            chunk_id=chunk.id,
            source_pages=chunk.source_pages,
            heading_path=chunk.heading_path,
        )
        self.db.add(evidence_link)
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
