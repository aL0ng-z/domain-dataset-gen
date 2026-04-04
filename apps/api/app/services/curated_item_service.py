import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curated import CuratedItem, CuratedRevision, EvidenceLink
from app.models.dataset import BenchmarkCase, DatasetItem


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

    async def update(
        self,
        item_id: uuid.UUID,
        revised_by: uuid.UUID,
        revision_note: str | None = None,
        **kwargs,
    ) -> CuratedItem | None:
        """Update a curated item. Automatically saves a CuratedRevision before applying changes."""
        item = await self.get(item_id)
        if item is None:
            return None

        # Auto-save revision of current state
        revision = CuratedRevision(
            curated_item_id=item.id,
            revised_by=revised_by,
            content=item.content,
            revision_note=revision_note,
        )
        self.db.add(revision)

        # Apply updates
        for key, value in kwargs.items():
            if value is not None:
                setattr(item, key, value)

        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def list_revisions(
        self, item_id: uuid.UUID
    ) -> list[CuratedRevision]:
        result = await self.db.execute(
            select(CuratedRevision)
            .where(CuratedRevision.curated_item_id == item_id)
            .order_by(CuratedRevision.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_evidence_links(
        self, item_id: uuid.UUID
    ) -> list[EvidenceLink]:
        result = await self.db.execute(
            select(EvidenceLink).where(EvidenceLink.curated_item_id == item_id)
        )
        return list(result.scalars().all())

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
