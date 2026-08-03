import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset, DatasetItem


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

    async def count_items(self, dataset_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
        )
        return int(result.scalar() or 0)

    async def list_items(
        self, dataset_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[DatasetItem], int]:
        """分页返回 Dataset items，total 为过滤后的总数；排序 ordinal ASC, id ASC。"""
        offset = (page - 1) * page_size
        base = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(
            base.order_by(DatasetItem.ordinal, DatasetItem.id).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total
