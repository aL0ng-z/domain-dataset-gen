import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curated import CuratedItem
from app.models.dataset import Benchmark, BenchmarkCase, Dataset, DatasetItem


class DatasetService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_dataset(
        self, project_id: uuid.UUID, name: str, description: str | None, created_by: uuid.UUID
    ) -> Dataset:
        dataset = Dataset(
            project_id=project_id, name=name, description=description, created_by=created_by
        )
        self.db.add(dataset)
        await self.db.flush()
        await self.db.refresh(dataset)
        return dataset

    async def list_datasets(
        self, project_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Dataset], int]:
        offset = (page - 1) * page_size
        base = select(Dataset).where(Dataset.project_id == project_id)

        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(
            base.order_by(Dataset.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get_dataset(self, dataset_id: uuid.UUID) -> Dataset | None:
        result = await self.db.execute(select(Dataset).where(Dataset.id == dataset_id))
        return result.scalar_one_or_none()

    async def update_dataset(self, dataset_id: uuid.UUID, **kwargs) -> Dataset | None:
        dataset = await self.get_dataset(dataset_id)
        if dataset is None:
            return None
        for key, value in kwargs.items():
            if value is not None:
                setattr(dataset, key, value)
        await self.db.flush()
        await self.db.refresh(dataset)
        return dataset

    async def delete_dataset(self, dataset_id: uuid.UUID) -> bool:
        dataset = await self.get_dataset(dataset_id)
        if dataset is None:
            return False
        await self.db.delete(dataset)
        await self.db.flush()
        return True

    async def list_items(self, dataset_id: uuid.UUID) -> list[DatasetItem]:
        result = await self.db.execute(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == dataset_id)
            .order_by(DatasetItem.ordinal)
        )
        return list(result.scalars().all())

    async def add_item(self, dataset_id: uuid.UUID, curated_item_id: uuid.UUID) -> DatasetItem:
        # Validate curated item exists and is approved
        curated = (
            await self.db.execute(select(CuratedItem).where(CuratedItem.id == curated_item_id))
        ).scalar_one_or_none()
        if curated is None:
            raise ValueError("知识条目不存在")
        if curated.status != "approved":
            raise ValueError("知识条目状态必须为已审核通过(approved)")

        # Auto-assign ordinal as max+1
        max_result = await self.db.execute(
            select(func.coalesce(func.max(DatasetItem.ordinal), 0)).where(
                DatasetItem.dataset_id == dataset_id
            )
        )
        next_ordinal = (max_result.scalar() or 0) + 1

        item = DatasetItem(
            dataset_id=dataset_id, curated_item_id=curated_item_id, ordinal=next_ordinal
        )
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def remove_item(self, dataset_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(DatasetItem).where(
                DatasetItem.id == item_id, DatasetItem.dataset_id == dataset_id
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            return False
        await self.db.delete(item)
        await self.db.flush()
        return True
