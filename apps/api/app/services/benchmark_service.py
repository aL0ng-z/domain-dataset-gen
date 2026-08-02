import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curated import CuratedItem
from app.models.dataset import Benchmark, BenchmarkCase
from app.models.generation import Candidate


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

    async def list_cases(self, benchmark_id: uuid.UUID) -> list[BenchmarkCase]:
        result = await self.db.execute(
            select(BenchmarkCase)
            .where(BenchmarkCase.benchmark_id == benchmark_id)
            .order_by(BenchmarkCase.ordinal)
        )
        return list(result.scalars().all())

    async def list_cases_paginated(
        self, benchmark_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[BenchmarkCase], int]:
        """分页返回 Benchmark cases，total 为过滤后的总数。"""
        offset = (page - 1) * page_size
        base = select(BenchmarkCase).where(BenchmarkCase.benchmark_id == benchmark_id)
        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(
            base.order_by(BenchmarkCase.ordinal).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def add_case(self, benchmark_id: uuid.UUID, curated_item_id: uuid.UUID) -> BenchmarkCase:
        # Validate curated item exists and is approved
        curated = (
            await self.db.execute(select(CuratedItem).where(CuratedItem.id == curated_item_id))
        ).scalar_one_or_none()
        if curated is None:
            raise ValueError("知识条目不存在")
        if curated.status != "approved":
            raise ValueError("知识条目状态必须为已审核通过(approved)")

        # Validate source candidate's review_verdict == "supported"
        candidate = (
            await self.db.execute(select(Candidate).where(Candidate.id == curated.candidate_id))
        ).scalar_one_or_none()
        if candidate is None:
            raise ValueError("源候选条目不存在")
        if candidate.review_verdict != "supported":
            raise ValueError("源候选条目的评审结论必须为完全支持(supported)")

        # Auto-assign ordinal as max+1
        max_result = await self.db.execute(
            select(func.coalesce(func.max(BenchmarkCase.ordinal), 0)).where(
                BenchmarkCase.benchmark_id == benchmark_id
            )
        )
        next_ordinal = (max_result.scalar() or 0) + 1

        case = BenchmarkCase(
            benchmark_id=benchmark_id, curated_item_id=curated_item_id, ordinal=next_ordinal
        )
        self.db.add(case)
        await self.db.flush()
        await self.db.refresh(case)
        return case

    async def remove_case(self, benchmark_id: uuid.UUID, case_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(BenchmarkCase).where(
                BenchmarkCase.id == case_id, BenchmarkCase.benchmark_id == benchmark_id
            )
        )
        case = result.scalar_one_or_none()
        if case is None:
            return False
        await self.db.delete(case)
        await self.db.flush()
        return True
