import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.export import Export, SnapshotManifest


class ExportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_exports(
        self, project_id: uuid.UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Export], int]:
        offset = (page - 1) * page_size
        base = select(Export).where(Export.project_id == project_id)

        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(
            base.order_by(Export.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get_export(self, export_id: uuid.UUID) -> Export | None:
        result = await self.db.execute(select(Export).where(Export.id == export_id))
        return result.scalar_one_or_none()

    async def get_manifest(self, snapshot_manifest_id: uuid.UUID) -> SnapshotManifest | None:
        result = await self.db.execute(
            select(SnapshotManifest).where(SnapshotManifest.id == snapshot_manifest_id)
        )
        return result.scalar_one_or_none()

    async def create_export(
        self,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID | None,
        benchmark_id: uuid.UUID | None,
        export_profile_id: uuid.UUID,
        minio_key: str,
        format: str,
        item_count: int,
        snapshot_manifest_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> Export:
        export = Export(
            project_id=project_id,
            dataset_id=dataset_id,
            benchmark_id=benchmark_id,
            export_profile_id=export_profile_id,
            minio_key=minio_key,
            format=format,
            item_count=item_count,
            snapshot_manifest_id=snapshot_manifest_id,
            created_by=created_by,
        )
        self.db.add(export)
        await self.db.flush()
        await self.db.refresh(export)
        return export

    async def create_snapshot_manifest(self, manifest: dict) -> SnapshotManifest:
        snapshot = SnapshotManifest(manifest=manifest)
        self.db.add(snapshot)
        await self.db.flush()
        await self.db.refresh(snapshot)
        return snapshot
