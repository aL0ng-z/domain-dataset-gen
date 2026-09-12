"""读取编组固定 revision，禁止用当前可编辑正文替代导出输入。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curated import CuratedItem, CuratedRevision
from app.models.dataset import BenchmarkCase, DatasetItem
from domain.export_content import validate_export_members


async def load_export_memberships(db: AsyncSession, source_type: str, source_id) -> list:
    model = DatasetItem if source_type == "dataset" else BenchmarkCase
    source_column = model.dataset_id if source_type == "dataset" else model.benchmark_id
    return list((await db.execute(select(model).where(source_column == source_id).order_by(model.ordinal))).scalars().all())


async def validate_membership_content(db: AsyncSession, memberships: list, fmt: str) -> None:
    members = []
    for membership in memberships:
        item = await db.get(CuratedItem, membership.curated_item_id)
        revision = await db.get(CuratedRevision, membership.curated_revision_id)
        members.append({
            "curated_item": {"id": str(membership.curated_item_id), "type": item.item_type if item else None},
            "curated_revision": {"content": revision.content if revision and revision.curated_item_id == membership.curated_item_id else None},
        })
    validate_export_members(members, fmt)
