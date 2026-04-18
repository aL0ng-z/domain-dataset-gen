import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.review_record import ReviewRecord
from app.models.section import CleaningJob, Section
from storage import get_storage_client


class CleanVersionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._storage = get_storage_client(
            settings.minio_endpoint, settings.minio_access_key,
            settings.minio_secret_key, settings.minio_secure,
        )

    async def _next_version(self, document_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.coalesce(func.max(CleanedDocumentVersion.version), 0))
            .where(CleanedDocumentVersion.document_id == document_id)
        )
        return (result.scalar() or 0) + 1

    async def _latest_cleaning_job(self, document_id: uuid.UUID) -> CleaningJob | None:
        result = await self.db.execute(
            select(CleaningJob)
            .where(CleaningJob.document_id == document_id)
            .order_by(CleaningJob.created_at.desc())
        )
        return result.scalars().first()

    async def create_merged_version(self, document_id: uuid.UUID, user_id: uuid.UUID) -> CleanedDocumentVersion:
        doc = (await self.db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
        if doc is None:
            raise ValueError("document not found")

        sections_result = await self.db.execute(
            select(Section).where(Section.document_id == document_id).order_by(Section.ordinal)
        )
        sections = list(sections_result.scalars().all())
        if not sections:
            raise ValueError("no sections to merge")

        parts: list[str] = []
        for s in sections:
            body = s.cleaned_markdown if s.cleaned_markdown else s.raw_markdown
            if not body:
                continue
            parts.append(body.strip())
        merged = "\n\n".join(parts) + "\n"

        version = await self._next_version(document_id)
        cleaning_job = await self._latest_cleaning_job(document_id)

        artifact_key = f"cleaned/{document_id}/v{version}.md"
        await asyncio.to_thread(
            self._storage.upload_file,
            settings.minio_bucket_outputs, artifact_key, merged.encode("utf-8"), "text/markdown",
        )

        row = CleanedDocumentVersion(
            document_id=document_id,
            source_cleaning_job_id=cleaning_job.id if cleaning_job else None,
            version=version,
            section_count=len(sections),
            merged_markdown=merged,
            artifact_key=artifact_key,
            status="review_pending",
            created_by=user_id,
        )
        self.db.add(row)

        doc.clean_status = "review_pending"

        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def final_review(
        self, version_id: uuid.UUID, user_id: uuid.UUID,
        action: str, reason: str | None = None, comment: str | None = None,
    ) -> CleanedDocumentVersion:
        if action not in ("accept", "reject"):
            raise ValueError("action must be 'accept' or 'reject'")

        version = (
            await self.db.execute(select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == version_id))
        ).scalar_one_or_none()
        if version is None:
            raise ValueError("version not found")

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
        version.reviewed_at = datetime.now(timezone.utc)

        doc = (await self.db.execute(select(Document).where(Document.id == version.document_id))).scalar_one()
        if action == "accept":
            doc.clean_status = "completed"
            doc.active_clean_version_id = version.id
        else:
            doc.clean_status = "review_pending"  # stays; user can merge again

        await self.db.flush()
        await self.db.refresh(version)
        return version

    async def list_versions(self, document_id: uuid.UUID) -> list[CleanedDocumentVersion]:
        result = await self.db.execute(
            select(CleanedDocumentVersion)
            .where(CleanedDocumentVersion.document_id == document_id)
            .order_by(CleanedDocumentVersion.version.desc())
        )
        return list(result.scalars().all())

    async def get_version(self, version_id: uuid.UUID) -> CleanedDocumentVersion | None:
        result = await self.db.execute(
            select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == version_id)
        )
        return result.scalar_one_or_none()
