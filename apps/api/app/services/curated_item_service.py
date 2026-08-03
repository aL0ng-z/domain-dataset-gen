import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curated import CuratedItem, CuratedRevision, EvidenceLink
from app.models.dataset import BenchmarkCase, DatasetItem
from app.models.review_record import ReviewRecord
from domain.canonical import (
    CURATED_APPROVAL_CJSON_VERSION,
    CURATED_CONTENT_CJSON_VERSION,
    build_evidence_snapshot,
    curated_approval_sha256,
    curated_content_sha256,
)


class CuratedRevisionConflictError(Exception):
    """expected_revision 不等于当前版本（409 CURATED_REVISION_CONFLICT）。"""

    def __init__(self, message: str, current_revision: int):
        super().__init__(message)
        self.current_revision = current_revision


class CuratedApprovalGateFailedError(Exception):
    """source/证据/内容/审批 hash 门禁不满足（409 CURATED_APPROVAL_GATE_FAILED）。"""


class CuratedReviewStateConflictError(Exception):
    """当前状态不允许 approve/needs_revision 或并发 review 已胜出（409 CURATED_REVIEW_STATE_CONFLICT）。"""

    def __init__(self, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision


class CuratedItemService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, item_id: uuid.UUID) -> CuratedItem | None:
        result = await self.db.execute(
            select(CuratedItem).where(CuratedItem.id == item_id)
        )
        return result.scalar_one_or_none()

    async def list_items(
        self,
        project_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        item_type: str | None = None,
    ) -> tuple[list[CuratedItem], int]:
        offset = (page - 1) * page_size

        base = select(CuratedItem).where(CuratedItem.project_id == project_id)
        count_base = select(func.count()).select_from(CuratedItem).where(
            CuratedItem.project_id == project_id
        )

        if status is not None:
            base = base.where(CuratedItem.status == status)
            count_base = count_base.where(CuratedItem.status == status)
        if item_type is not None:
            base = base.where(CuratedItem.item_type == item_type)
            count_base = count_base.where(CuratedItem.item_type == item_type)

        count_result = await self.db.execute(count_base)
        total = count_result.scalar() or 0

        result = await self.db.execute(
            base.order_by(CuratedItem.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def _load_current_revision(self, item_id: uuid.UUID, version: int) -> CuratedRevision | None:
        result = await self.db.execute(
            select(CuratedRevision).where(
                CuratedRevision.curated_item_id == item_id,
                CuratedRevision.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        item_id: uuid.UUID,
        revised_by: uuid.UUID,
        content: dict,
        revision_note: str | None = None,
        expected_revision: int | None = None,
    ) -> CuratedItem | None:
        """乐观修订（CAS）：只允许修改 draft；expected_revision 锁 + INSERT vN+1 原子提交。

        - 请求不存在 status（schema extra=forbid 已拒绝）。
        - 已 approved 条目不可编辑（改内容需先退审）。
        - 条件 UPDATE（current_revision == expected AND status == draft）保证并发
          保存最多一个成功；失败者返回 409 CURATED_REVISION_CONFLICT，本地草稿保留。
        - revision (item, version) 唯一约束兜底并发；revision 内容为该版本完整快照。
        """
        # 先做幂等/状态预检（不触发锁，仅快速失败）。
        existing = await self.get(item_id)
        if existing is None:
            return None

        expected = expected_revision if expected_revision is not None else existing.current_revision

        # CAS：原子把 current_revision 从 expected 递增到 expected+1（独占行锁）。
        # synchronize_session="fetch"：让 ORM identity map 同步新 content，避免后续
        # get() 读到旧值。
        result = await self.db.execute(
            update(CuratedItem)
            .where(
                CuratedItem.id == item_id,
                CuratedItem.status == "draft",
                CuratedItem.current_revision == expected,
            )
            .values(
                content=content,
                current_revision=expected + 1,
                updated_at=func.now(),
            )
            .returning(CuratedItem.id)
            .execution_options(synchronize_session="fetch")
        )
        claimed = result.scalar_one_or_none()
        if claimed is None:
            fresh = await self.get(item_id)
            raise CuratedRevisionConflictError(
                "expected_revision 不等于当前版本", fresh.current_revision if fresh else expected
            )

        revision = CuratedRevision(
            curated_item_id=item_id,
            revised_by=revised_by,
            version=expected + 1,
            content=content,
            content_sha256=curated_content_sha256(content),
            canonicalization_version=CURATED_CONTENT_CJSON_VERSION,
            revision_note=revision_note,
        )
        self.db.add(revision)
        await self.db.flush()
        # CAS 用原生 UPDATE 直接写库，identity map 仍是旧 content；expire 后重读。
        self.db.expire(existing, ["content", "current_revision", "updated_at"])
        item = await self.get(item_id)
        return item

    async def review(
        self,
        item_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        action: str,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> CuratedItem | None:
        """reviewer 审批 action（approve|needs_revision），CAS 保证并发最多一个成功。

        approve 原子绑定当前 revision 的 id/content hash + 证据快照/审批 hash，
        写不可变 ReviewRecord；needs_revision 退回 draft 并清空当前批准指针，
        历史 revision/approval record/evidence/membership 保持不可变。
        """
        existing = await self.get(item_id)
        if existing is None:
            return None
        expected = expected_revision if expected_revision is not None else existing.current_revision

        if action == "needs_revision":
            if not (reason and reason.strip()):
                raise CuratedApprovalGateFailedError("退审必须提供非空原因")
            # CAS：仅 approved 且 revision 匹配时退回 draft 并清空批准指针。
            result = await self.db.execute(
                update(CuratedItem)
                .where(
                    CuratedItem.id == item_id,
                    CuratedItem.status == "approved",
                    CuratedItem.current_revision == expected,
                )
                .values(
                    status="draft",
                    approved_revision_id=None,
                    approval_record_id=None,
                    approved_by=None,
                    approved_at=None,
                    updated_at=func.now(),
                )
                .returning(CuratedItem.id)
            )
            claimed = result.scalar_one_or_none()
            if claimed is None:
                fresh = await self.get(item_id)
                raise CuratedReviewStateConflictError(
                    "仅 approved 条目可退审或 revision 已过期",
                    current_revision=fresh.current_revision if fresh else expected,
                )
            self.db.add(
                ReviewRecord(
                    entity_type="curated_item",
                    entity_id=item_id,
                    reviewer_id=reviewer_id,
                    action="needs_revision",
                    reason=reason,
                )
            )
            await self.db.flush()
            return await self.get(item_id)

        # action == "approve"
        if existing.status == "approved":
            raise CuratedReviewStateConflictError("条目已 approved，不可重复批准")
        if existing.status != "draft":
            raise CuratedReviewStateConflictError("仅 draft 条目可批准")

        # 门禁预检：source Candidate approved + content 非空 object。
        from app.models.generation import Candidate

        candidate = (
            await self.db.execute(
                select(Candidate).where(Candidate.id == existing.candidate_id)
            )
        ).scalar_one_or_none()
        if candidate is None or candidate.status != "approved":
            raise CuratedApprovalGateFailedError("source Candidate 必须已 approved")
        if not isinstance(existing.content, dict) or not existing.content:
            raise CuratedApprovalGateFailedError("批准要求 content 为非空 object")

        evidence_links = await self._list_evidence_links_plain(item_id)
        if not evidence_links:
            raise CuratedApprovalGateFailedError("批准要求至少一个有效 EvidenceLink")

        revision = await self._load_current_revision(item_id, expected)
        if revision is None:
            raise CuratedApprovalGateFailedError("当前 revision 缺失，无法批准")

        content_hash = curated_content_sha256(existing.content)
        if content_hash != revision.content_sha256:
            raise CuratedApprovalGateFailedError("revision 内容 hash 与当前 content 不一致")

        evidence_rows = [
            {
                "id": el.id,
                "chunk_id": el.chunk_id,
                "document_id": el.document_id,
                "start_char": el.start_char,
                "end_char": el.end_char,
                "quote_text": el.quote_text,
                "source_pages": el.source_pages,
                "heading_path": el.heading_path,
            }
            for el in evidence_links
        ]
        approval_hash = curated_approval_sha256(
            revision_id=revision.id,
            content_sha256=content_hash,
            evidence_links=evidence_rows,
        )
        evidence_snapshot = build_evidence_snapshot(evidence_rows)

        record = ReviewRecord(
            entity_type="curated_item",
            entity_id=item_id,
            reviewer_id=reviewer_id,
            action="approve",
            reason=reason,
            entity_revision_id=revision.id,
            revision_content_sha256=content_hash,
            evidence_snapshot=evidence_snapshot,
            evidence_sha256=approval_hash,
            canonicalization_version=CURATED_APPROVAL_CJSON_VERSION,
        )
        self.db.add(record)
        await self.db.flush()

        # CAS：仅 draft 且 revision 匹配时置为 approved（并发编辑/另一 approve 失败）。
        claimed = await self.db.execute(
            update(CuratedItem)
            .where(
                CuratedItem.id == item_id,
                CuratedItem.status == "draft",
                CuratedItem.current_revision == expected,
            )
            .values(
                status="approved",
                approved_revision_id=revision.id,
                approval_record_id=record.id,
                approved_by=reviewer_id,
                approved_at=datetime.now(UTC),
                updated_at=func.now(),
            )
            .returning(CuratedItem.id)
        )
        if claimed.scalar_one_or_none() is None:
            fresh = await self.get(item_id)
            raise CuratedReviewStateConflictError(
                "并发 review/编辑已胜出，approve 未生效",
                current_revision=fresh.current_revision if fresh else expected,
            )

        await self.db.flush()
        return await self.get(item_id)

    async def _list_evidence_links_plain(self, item_id: uuid.UUID) -> list[EvidenceLink]:
        result = await self.db.execute(
            select(EvidenceLink)
            .where(EvidenceLink.curated_item_id == item_id)
            .order_by(EvidenceLink.chunk_id, EvidenceLink.start_char)
        )
        return list(result.scalars().all())

    async def list_revisions(
        self,
        item_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CuratedRevision], int]:
        offset = (page - 1) * page_size
        count_result = await self.db.execute(
            select(func.count())
            .select_from(CuratedRevision)
            .where(CuratedRevision.curated_item_id == item_id)
        )
        total = count_result.scalar() or 0
        result = await self.db.execute(
            select(CuratedRevision)
            .where(CuratedRevision.curated_item_id == item_id)
            .order_by(CuratedRevision.version.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_evidence_links(
        self,
        item_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvidenceLink], int]:
        offset = (page - 1) * page_size
        count_result = await self.db.execute(
            select(func.count())
            .select_from(EvidenceLink)
            .where(EvidenceLink.curated_item_id == item_id)
        )
        total = count_result.scalar() or 0
        result = await self.db.execute(
            select(EvidenceLink)
            .where(EvidenceLink.curated_item_id == item_id)
            .order_by(EvidenceLink.chunk_id, EvidenceLink.start_char)
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def add_to_dataset(
        self, item_id: uuid.UUID, dataset_id: uuid.UUID
    ) -> DatasetItem:
        """Add a curated item to a dataset. Auto-assigns ordinal."""
        item = await self.get(item_id)
        if item is None:
            raise ValueError("知识条目不存在")

        # Get max ordinal in dataset
        max_ord_result = await self.db.execute(
            select(func.max(DatasetItem.ordinal)).where(
                DatasetItem.dataset_id == dataset_id
            )
        )
        max_ord = max_ord_result.scalar() or 0

        dataset_item = DatasetItem(
            dataset_id=dataset_id,
            curated_item_id=item_id,
            ordinal=max_ord + 1,
        )
        self.db.add(dataset_item)
        await self.db.flush()
        await self.db.refresh(dataset_item)
        return dataset_item

    async def add_to_benchmark(
        self, item_id: uuid.UUID, benchmark_id: uuid.UUID
    ) -> BenchmarkCase:
        """Add a curated item to a benchmark. Auto-assigns ordinal."""
        item = await self.get(item_id)
        if item is None:
            raise ValueError("知识条目不存在")

        # Get max ordinal in benchmark
        max_ord_result = await self.db.execute(
            select(func.max(BenchmarkCase.ordinal)).where(
                BenchmarkCase.benchmark_id == benchmark_id
            )
        )
        max_ord = max_ord_result.scalar() or 0

        benchmark_case = BenchmarkCase(
            benchmark_id=benchmark_id,
            curated_item_id=item_id,
            ordinal=max_ord + 1,
        )
        self.db.add(benchmark_case)
        await self.db.flush()
        await self.db.refresh(benchmark_case)
        return benchmark_case
