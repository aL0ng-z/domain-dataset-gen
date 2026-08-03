"""T11：导出服务（§4.1-§4.3 生命周期 + §5 一致性/上传/发布合同）。

职责：
- ``create_export_request``：创建 queued Export + Task（触发 API 调用）。
- ``build_and_insert_snapshot``：构建完整 manifest 并 INSERT SnapshotManifest（不可变）。
- ``insert_seal``：上传完成后 INSERT ExportArtifactSeal（BEFORE INSERT trigger 复算 hash）。
- ``finalize_completed``：fenced CAS 把 Export 置 completed（deferred constraint 复核）。

一致性（任务卡 §5.2）：formatter 只从 SnapshotManifest 渲染 payload；本服务不重新
读取当前 CuratedItem/Evidence/ParserProfile/PromptTemplate/ModelConfig。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import ExportProfile
from app.models.export import Export, ExportArtifactSeal, SnapshotManifest
from app.models.task import Task
from domain.manifest import EXPORTER_VERSION, seal_sha256

#: 导出状态。
EXPORT_QUEUED = "queued"
EXPORT_PROCESSING = "processing"
EXPORT_COMPLETED = "completed"
EXPORT_FAILED = "failed"


class ExportImmutableError(Exception):
    """completed Export 不可重跑/改格式。"""


class ExportSealError(Exception):
    """seal 插入或 Export finalize 失败（seal 已发布但 Export 未完成时整体回滚）。"""


def _now() -> datetime:
    return datetime.now(UTC)


class ExportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # 列表/查询
    # ------------------------------------------------------------------

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

    async def get_manifest_by_export(self, export_id: uuid.UUID) -> SnapshotManifest | None:
        result = await self.db.execute(
            select(SnapshotManifest).where(SnapshotManifest.export_id == export_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # 创建请求（queued Export + Task 已在路由/任务服务创建）
    # ------------------------------------------------------------------

    async def create_export_request(
        self,
        *,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID | None,
        benchmark_id: uuid.UUID | None,
        export_profile_id: uuid.UUID,
        created_by: uuid.UUID,
        request_fingerprint: str,
        profile_snapshot: dict,
        task_id: uuid.UUID | None,
        formatter_version: str = EXPORTER_VERSION,
    ) -> Export:
        """创建 queued Export 记录（T11：不再返回伪造 completed）。"""
        export = Export(
            project_id=project_id,
            dataset_id=dataset_id,
            benchmark_id=benchmark_id,
            source_type="dataset" if dataset_id else "benchmark",
            export_profile_id=export_profile_id,
            format="",  # 由 worker 从 profile 解析后填充
            status=EXPORT_QUEUED,
            request_fingerprint=request_fingerprint,
            profile_snapshot=profile_snapshot,
            formatter_version=formatter_version,
            task_id=task_id,
            retry_count=0,
            is_legacy=False,
            created_by=created_by,
        )
        self.db.add(export)
        await self.db.flush()
        await self.db.refresh(export)
        return export

    # ------------------------------------------------------------------
    # Snapshot 构建 + INSERT（不可变）
    # ------------------------------------------------------------------

    async def insert_snapshot_manifest(
        self,
        *,
        export_id: uuid.UUID,
        project_id: uuid.UUID,
        manifest: dict,
        manifest_hash: str,
    ) -> SnapshotManifest:
        from domain.manifest import MANIFEST_CJSON_VERSION

        snapshot = SnapshotManifest(
            export_id=export_id,
            project_id=project_id,
            manifest=manifest,
            schema_version=1,
            canonicalization_version=MANIFEST_CJSON_VERSION,
            manifest_sha256=manifest_hash,
            sealed_at=_now(),
            is_legacy=False,
        )
        self.db.add(snapshot)
        await self.db.flush()
        await self.db.refresh(snapshot)
        # Export -> snapshot 指针（同一事务）。
        await self.db.execute(
            update(Export)
            .where(Export.id == export_id, Export.is_legacy.is_(False))
            .values(snapshot_manifest_id=snapshot.id)
        )
        return snapshot

    # ------------------------------------------------------------------
    # Seal（上传后 INSERT，trigger 复算 hash）
    # ------------------------------------------------------------------

    async def insert_artifact_seal(
        self,
        *,
        export_id: uuid.UUID,
        snapshot_manifest_id: uuid.UUID,
        manifest_bucket: str,
        manifest_key: str,
        manifest_object_version_id: str,
        manifest_sha256: str,
        manifest_size: int,
        manifest_content_type: str,
        output_bucket: str,
        output_key: str,
        output_object_version_id: str,
        output_sha256: str,
        output_size: int,
        output_content_type: str,
        schema_version: int = 1,
        formatter_version: str = EXPORTER_VERSION,
    ) -> ExportArtifactSeal:
        """构建精确 seal_payload（不含 seal_sha256 自身）并 INSERT。

        BEFORE INSERT trigger 用共享 canonical helper 复算 hash 并核对 payload/列
        与关联 Export/Snapshot；不一致即拒绝。seal 一旦插入即不可变。
        """
        seal_payload = {
            "seal_version": "artifact-seal-cjson-v1",
            "schema_version": schema_version,
            "formatter_version": formatter_version,
            "export_id": str(export_id),
            "snapshot_manifest_id": str(snapshot_manifest_id),
            "manifest": {
                "bucket": manifest_bucket,
                "key": manifest_key,
                "object_version_id": manifest_object_version_id,
                "sha256": manifest_sha256,
                "size": manifest_size,
                "content_type": manifest_content_type,
            },
            "output": {
                "bucket": output_bucket,
                "key": output_key,
                "object_version_id": output_object_version_id,
                "sha256": output_sha256,
                "size": output_size,
                "content_type": output_content_type,
            },
        }
        computed = seal_sha256(seal_payload)
        seal = ExportArtifactSeal(
            export_id=export_id,
            snapshot_manifest_id=snapshot_manifest_id,
            seal_version="artifact-seal-cjson-v1",
            seal_payload=seal_payload,
            seal_sha256=computed,
            sealed_at=_now(),
            manifest_bucket=manifest_bucket,
            manifest_key=manifest_key,
            manifest_object_version_id=manifest_object_version_id,
            manifest_sha256=manifest_sha256,
            manifest_size=manifest_size,
            manifest_content_type=manifest_content_type,
            output_bucket=output_bucket,
            output_key=output_key,
            output_object_version_id=output_object_version_id,
            output_sha256=output_sha256,
            output_size=output_size,
            output_content_type=output_content_type,
        )
        self.db.add(seal)
        await self.db.flush()
        await self.db.refresh(seal)
        return seal

    # ------------------------------------------------------------------
    # Fenced finalize（seal INSERT + Export completed 同事务）
    # ------------------------------------------------------------------

    async def finalize_completed(
        self,
        *,
        export_id: uuid.UUID,
        run_token: uuid.UUID,
        expected_state_version: int,
        artifact_seal_id: uuid.UUID,
        snapshot_manifest_id: uuid.UUID,
        output_bucket: str,
        output_key: str,
        output_object_version_id: str,
        output_sha256: str,
        output_size: int,
        content_type: str,
        manifest_key: str,
        manifest_object_version_id: str,
        manifest_sha256: str,
        format: str,
        item_count: int,
        task_id: uuid.UUID | None,
    ) -> bool:
        """fenced CAS 更新 Export 为 completed（携带 run token/state_version）。

        与 seal INSERT 同一数据库事务；失败整体回滚，不留“seal 已发布、Export 未
        完成”的半状态（任务卡 §4.3）。deferred constraint 在 commit 前复核一对一关系。
        """
        now = _now()
        res = await self.db.execute(
            update(Export)
            .where(
                Export.id == export_id,
                Export.is_legacy.is_(False),
                Export.status == EXPORT_PROCESSING,
                Export.task_id == task_id,
                exists(
                    select(Task.id).where(
                        Task.id == task_id,
                        Task.run_token == run_token,
                        Task.status == "processing",
                        Task.state_version == expected_state_version,
                    )
                ),
            )
            .values(
                status=EXPORT_COMPLETED,
                artifact_seal_id=artifact_seal_id,
                snapshot_manifest_id=snapshot_manifest_id,
                bucket_name=output_bucket,
                minio_key=output_key,
                object_version_id=output_object_version_id,
                output_sha256=output_sha256,
                file_size=output_size,
                content_type=content_type,
                manifest_key=manifest_key,
                manifest_object_version_id=manifest_object_version_id,
                manifest_sha256=manifest_sha256,
                format=format,
                item_count=item_count,
                completed_at=now,
                updated_at=now,
            )
        )
        return (res.rowcount or 0) == 1

    async def mark_failed(
        self,
        *,
        export_id: uuid.UUID,
        error_code: str,
        error_message: str,
        task_id: uuid.UUID | None,
    ) -> None:
        """把 queued/processing Export 标 failed（completed 由 trigger 拒绝）。"""
        await self.db.execute(
            update(Export)
            .where(
                Export.id == export_id,
                Export.is_legacy.is_(False),
                Export.status.in_((EXPORT_QUEUED, EXPORT_PROCESSING)),
            )
            .values(
                status=EXPORT_FAILED,
                error_code=error_code,
                error_message=error_message,
                updated_at=_now(),
            )
        )

    # ------------------------------------------------------------------
    # Profile 快照
    # ------------------------------------------------------------------

    @staticmethod
    def build_profile_snapshot(profile: ExportProfile) -> dict:
        """ExportProfile 非敏感配置快照（含 format/版本/template_options）。"""
        return {
            "export_profile_id": str(profile.id),
            "version": profile.version,
            "name": profile.name,
            "format": profile.format,
            "template_options": profile.template_options,
        }

    @staticmethod
    def profile_snapshot_sha256(snapshot: dict) -> str:
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def build_request_fingerprint(
        *,
        expected_source_revision: int,
        expected_source_sha256: str,
        export_profile_id: uuid.UUID,
    ) -> str:
        """导出请求摘要（expected revision/hash + profile），用于一致性校验。"""
        import hashlib
        import json

        payload = {
            "expected_source_revision": expected_source_revision,
            "expected_source_sha256": expected_source_sha256,
            "export_profile_id": str(export_profile_id),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
