import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Benchmark, BenchmarkCase


class BenchmarkService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_benchmark(
        self, project_id: uuid.UUID, name: str, description: str | None, created_by: uuid.UUID
    ) -> Benchmark:
        benchmark = Benchmark(
            project_id=project_id, name=name, description=description, created_by=created_by
        )
        self.db.add(benchmark)
        await self.db.flush()
        await self.db.refresh(benchmark)
        return benchmark

    async def list_benchmarks(
        self, project_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Benchmark], int]:
        offset = (page - 1) * page_size
        base = select(Benchmark).where(Benchmark.project_id == project_id)

        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(
            base.order_by(Benchmark.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get_benchmark(self, benchmark_id: uuid.UUID) -> Benchmark | None:
        result = await self.db.execute(select(Benchmark).where(Benchmark.id == benchmark_id))
        return result.scalar_one_or_none()

    async def update_benchmark(self, benchmark_id: uuid.UUID, **kwargs) -> Benchmark | None:
        benchmark = await self.get_benchmark(benchmark_id)
        if benchmark is None:
            return None
        for key, value in kwargs.items():
            if value is not None:
                setattr(benchmark, key, value)
        await self.db.flush()
        await self.db.refresh(benchmark)
        return benchmark

    async def delete_benchmark(self, benchmark_id: uuid.UUID) -> bool:
        benchmark = await self.get_benchmark(benchmark_id)
        if benchmark is None:
            return False
        await self.db.delete(benchmark)
        await self.db.flush()
        return True

    async def count_cases(self, benchmark_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(BenchmarkCase).where(BenchmarkCase.benchmark_id == benchmark_id)
        )
        return int(result.scalar() or 0)

    async def list_cases(
        self, benchmark_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[BenchmarkCase], int]:
        """分页返回 Benchmark cases，total 为过滤后的总数；排序 ordinal ASC, id ASC。"""
        offset = (page - 1) * page_size
        base = select(BenchmarkCase).where(BenchmarkCase.benchmark_id == benchmark_id)
        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(
            base.order_by(BenchmarkCase.ordinal, BenchmarkCase.id).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total
