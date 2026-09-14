import asyncio
import contextlib
import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.review_record import ReviewRecord
from app.models.section import CleaningJob, Section
from app.storage_keys import build_storage_key
from storage import get_storage_client


class CleanSourceChangedError(Exception):
    """参与合并的 Section 在计算与发布之间发生变化。"""


class CleanVersionReviewConflictError(Exception):
    """目标版本已离开 review_pending 状态。"""


class CleanVersionStaleError(Exception):
    """approve 的目标版本旧于当前 active version。"""

    def __init__(self, target_version: int, active_version: int):
        super().__init__("目标版本旧于当前 active 版本")
        self.target_version = target_version
        self.active_version = active_version


def canonical_revision_map(sections: list[Section]) -> dict[str, int]:
    """按 section id（稳定字符串排序）生成规范化 revision 向量。"""
    return {str(s.id): s.content_revision for s in sections}


def revision_map_sha256(rev_map: dict[str, int]) -> str:
    raw = "\n".join(f"{sid}:{rev}" for sid, rev in sorted(rev_map.items()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _trusted_pages(value: object) -> list[int]:
    """只接受清洗分节器写入的物理页码数组；其余来源一律标为未知。"""
    if not isinstance(value, list):
        return []
    return sorted({
        page for page in value
        if isinstance(page, int) and not isinstance(page, bool) and page > 0
    })


def merge_sections_with_source_intervals(sections: list[Section]) -> tuple[str, list[dict]]:
    """合并 Section，并冻结每段正文在 merged_markdown 中的来源区间。

    页码仅来自 Section 已保存的物理页码数组；正文中的 ``Page 888`` 等文字绝不会
    参与推断。没有可信映射时保留空集合并显式标记为 unknown。
    """
    parts: list[str] = []
    intervals: list[dict] = []
    cursor = 0
    for section in sections:
        body = section.raw_markdown if section.cleaned_markdown is None else section.cleaned_markdown
        body = body.strip() if body else ""
        if not body:
            continue
        if parts:
            cursor += 2  # ``\n\n`` between adjacent merged sections.
        start = cursor
        cursor += len(body)
        pages = _trusted_pages(getattr(section, "source_pages", None))
        intervals.append({
            "section_id": str(section.id),
            "start_char": start,
            "end_char": cursor,
            "source_pages": pages,
            "page_mapping_status": "trusted" if pages else "unknown",
        })
        parts.append(body)
    return "\n\n".join(parts) + "\n", intervals


class CleanVersionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._storage = get_storage_client(
            settings.minio_endpoint,
            settings.minio_access_key,
            settings.minio_secret_key,
            settings.minio_secure,
        )

    async def _load_locked_sections(
        self, document_id: uuid.UUID, cleaning_job_id: uuid.UUID
    ) -> list[Section]:
        """按稳定顺序（ordinal, id）锁定参与合并的 Sections。"""
        result = await self.db.execute(
            select(Section)
            .where(
                Section.document_id == document_id,
                Section.cleaning_job_id == cleaning_job_id,
            )
            .order_by(Section.ordinal, Section.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def create_merged_version(
        self,
        document_id: uuid.UUID,
        cleaning_job_id: uuid.UUID,
        user_id: uuid.UUID,
        idempotency_key: str | None = None,
    ) -> CleanedDocumentVersion:
        """创建合并版本：冻结 revision/hash、UUID 对象 key、Document 行锁分配版本、幂等发布。

        任务卡 §4.4 顺序：
        - 计算阶段（无锁）：按 ordinal 读取 Section id/revision/正文，生成规范化
          revision map、合并内容与 hash，预生成 version UUID 与对象 key。
        - 发布阶段（Document 行锁）：锁内幂等复核（同 key 命中直接重放，不上传）、
          稳定顺序锁定 Sections 复核 revision 向量、分配下一 version、create-only
          上传唯一 key 对象、插入版本行并更新文档状态。
        对象 key 为 cleaned/{document_id}/{version_id}.md，与可竞争的显示版本号解耦；
        来源变化/文档缺失时不发布版本并同步删除已上传的孤儿对象。
        """
        # 1. 幂等快路径：同 key 已有版本直接返回（发布阶段锁内再复核）。
        if idempotency_key is not None:
            existing = await self._find_by_idempotency(document_id, idempotency_key)
            if existing is not None:
                return existing

        cleaning_job = (
            await self.db.execute(
                select(CleaningJob).where(
                    CleaningJob.id == cleaning_job_id,
                    CleaningJob.document_id == document_id,
                )
            )
        ).scalar_one_or_none()
        if cleaning_job is None:
            raise ValueError("cleaning job not found")

        # ---- 计算阶段（无锁）----
        sections_result = await self.db.execute(
            select(Section)
            .where(
                Section.document_id == document_id,
                Section.cleaning_job_id == cleaning_job_id,
            )
            .order_by(Section.ordinal, Section.id)
        )
        sections = list(sections_result.scalars().all())
        if not sections:
            raise ValueError("no sections to merge")

        rev_map = canonical_revision_map(sections)
        source_rev_sha = revision_map_sha256(rev_map)

        merged, source_intervals = merge_sections_with_source_intervals(sections)
        merged_sha = content_sha256(merged)

        version_id = uuid.uuid4()
        artifact_key = build_storage_key("cleaned", document_id, f"{version_id}.md")

        # ---- 发布阶段（Document 行锁）----
        doc = (
            await self.db.execute(select(Document).where(Document.id == document_id).with_for_update())
        ).scalar_one_or_none()
        if doc is None:
            raise ValueError("document not found")

        # 锁内幂等复核：并发同 key 请求在前一个提交后命中已有版本 -> 直接重放，不重复上传。
        if idempotency_key is not None:
            existing = await self._find_by_idempotency(document_id, idempotency_key)
            if existing is not None:
                return existing

        # 稳定顺序锁定参与合并的 Sections，并复核 revision 向量是否仍与计算输入一致。
        locked_sections = await self._load_locked_sections(document_id, cleaning_job_id)
        locked_merged, locked_source_intervals = merge_sections_with_source_intervals(locked_sections)
        if (
            canonical_revision_map(locked_sections) != rev_map
            or locked_merged != merged
            or locked_source_intervals != source_intervals
        ):
            # 来源在计算与发布之间变化：不发布版本、不改 Document 状态。
            raise CleanSourceChangedError("来源 Section 在合并期间已变化")

        # Document 锁保护下分配下一 version（无锁 MAX(version)+1 已被行锁取代）。
        max_version = (
            await self.db.execute(
                select(func.coalesce(func.max(CleanedDocumentVersion.version), 0)).where(
                    CleanedDocumentVersion.document_id == document_id
                )
            )
        ).scalar_one()
        version = int(max_version) + 1

        # 对象写入：唯一 key + create-only 语义；写失败则整个请求失败、不产生版本行。
        await asyncio.to_thread(
            self._storage.upload_file,
            settings.minio_bucket_outputs,
            artifact_key,
            merged.encode("utf-8"),
            "text/markdown",
        )

        row = CleanedDocumentVersion(
            id=version_id,
            document_id=document_id,
            source_cleaning_job_id=cleaning_job.id,
            version=version,
            section_count=len(sections),
            merged_markdown=merged,
            artifact_key=artifact_key,
            source_revision_map=rev_map,
            source_intervals=source_intervals,
            source_revision_sha256=source_rev_sha,
            content_sha256=merged_sha,
            merge_idempotency_key=idempotency_key,
            status="review_pending",
            created_by=user_id,
        )
        self.db.add(row)

        doc.clean_status = "review_pending"

        try:
            await self.db.flush()
        except Exception:
            # 数据库提交前失败：同步删除刚上传的唯一 key 对象，避免留下孤儿对象。
            # （提交后失败由部署期孤儿扫描 + 审计脚本兜底，历史对象绝不覆盖复用。）
            with contextlib.suppress(Exception):  # noqa: BLE001 - 清理失败不影响主错误上报
                await asyncio.to_thread(self._storage.delete_file, settings.minio_bucket_outputs, artifact_key)
            raise
        await self.db.refresh(row)
        return row

    async def _find_by_idempotency(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> CleanedDocumentVersion | None:
        result = await self.db.execute(
            select(CleanedDocumentVersion)
            .where(
                CleanedDocumentVersion.document_id == document_id,
                CleanedDocumentVersion.merge_idempotency_key == idempotency_key,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # 并发终审：Document -> Version 固定锁序、review_pending CAS、active 单调门禁。
    # ------------------------------------------------------------------

    async def final_review(
        self,
        version_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        reason: str | None = None,
        comment: str | None = None,
        document_id: uuid.UUID | None = None,
        cleaning_job_id: uuid.UUID | None = None,
    ) -> CleanedDocumentVersion:
        if action not in ("accept", "reject"):
            raise ValueError("action must be 'accept' or 'reject'")

        # 固定锁序：先锁定 Document 行，再锁定目标 Version 行。
        query = select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == version_id)
        if document_id is not None:
            query = query.where(CleanedDocumentVersion.document_id == document_id)
        if cleaning_job_id is not None:
            query = query.where(CleanedDocumentVersion.source_cleaning_job_id == cleaning_job_id)

        version = (await self.db.execute(query)).scalar_one_or_none()
        if version is None:
            raise ValueError("version not found")

        # 先锁 Document（固定锁序，避免死锁）。
        doc = (
            await self.db.execute(
                select(Document).where(Document.id == version.document_id).with_for_update()
            )
        ).scalar_one()

        # 再锁目标 Version；行锁串行化并发 accept/reject。
        version = (
            await self.db.execute(
                select(CleanedDocumentVersion)
                .where(CleanedDocumentVersion.id == version_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

        # review_pending CAS：锁内检查状态；只有仍为 review_pending 才允许写入终态。
        # 行锁保证并发 accept/reject 恰有一个成功，失败方得到 review conflict。
        # populate_existing 强制刷新 identity map，避免读到加锁前缓存的旧状态。
        if version.status != "review_pending":
            raise CleanVersionReviewConflictError("该版本已被其他评审者处理")

        if action == "accept" and doc.active_clean_version_id is not None:
            # active 单调门禁：目标旧于当前 active 版本则返回 stale。
            active_ver = (
                await self.db.execute(
                    select(CleanedDocumentVersion.version).where(
                        CleanedDocumentVersion.id == doc.active_clean_version_id
                    )
                )
            ).scalar_one_or_none()
            if active_ver is not None and version.version < active_ver:
                raise CleanVersionStaleError(version.version, int(active_ver))

        record_action = "approve" if action == "accept" else "reject"
        record = ReviewRecord(
            entity_type="cleaned_document_version",
            entity_id=version_id,
            reviewer_id=user_id,
            action=record_action,
            reason=reason,
            comment=comment,
        )
        self.db.add(record)

        version.status = "accepted" if action == "accept" else "rejected"
        version.reviewed_by = user_id
        version.reviewed_at = datetime.now(UTC)

        if action == "accept":
            doc.clean_status = "completed"
            doc.active_clean_version_id = version.id
        # reject 不清空、不回退、不改写已有 active pointer。

        await self.db.flush()
        await self.db.refresh(version)
        return version

    async def list_versions(
        self, document_id: uuid.UUID, cleaning_job_id: uuid.UUID | None = None
    ) -> list[CleanedDocumentVersion]:
        query = select(CleanedDocumentVersion).where(CleanedDocumentVersion.document_id == document_id)
        if cleaning_job_id is not None:
            query = query.where(CleanedDocumentVersion.source_cleaning_job_id == cleaning_job_id)
        result = await self.db.execute(query.order_by(CleanedDocumentVersion.version.desc()))
        return list(result.scalars().all())

    async def get_version(self, version_id: uuid.UUID) -> CleanedDocumentVersion | None:
        result = await self.db.execute(select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == version_id))
        return result.scalar_one_or_none()
