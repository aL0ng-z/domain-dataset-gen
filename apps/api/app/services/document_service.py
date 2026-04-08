import asyncio
import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.models.parse import ParseJob
from storage import get_storage_client

# Pre-load pymupdf at module level to avoid slow first-call initialization
import pymupdf  # noqa: F401


def _extract_page_count(file_data: bytes) -> int | None:
    """Extract page count from PDF bytes (runs in thread pool)."""
    try:
        doc_pdf = pymupdf.open(stream=file_data, filetype="pdf")
        page_count = len(doc_pdf)
        doc_pdf.close()
        return page_count
    except Exception:
        return None


class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._storage = get_storage_client(
            settings.minio_endpoint, settings.minio_access_key,
            settings.minio_secret_key, settings.minio_secure,
        )

    async def _deduplicate_filename(self, project_id: uuid.UUID, filename: str) -> str:
        """If filename already exists in project, append (1), (2), etc."""
        import os
        base, ext = os.path.splitext(filename)
        result = await self.db.execute(
            select(func.count()).select_from(Document).where(
                Document.project_id == project_id,
                Document.filename == filename,
            )
        )
        if result.scalar() == 0:
            return filename
        for i in range(1, 100):
            candidate = f"{base}({i}){ext}"
            result = await self.db.execute(
                select(func.count()).select_from(Document).where(
                    Document.project_id == project_id,
                    Document.filename == candidate,
                )
            )
            if result.scalar() == 0:
                return candidate
        return f"{base}({uuid.uuid4().hex[:6]}){ext}"

    async def upload(
        self, project_id: uuid.UUID, filename: str, file_data: bytes, uploaded_by: uuid.UUID
    ) -> Document:
        # Validate PDF magic bytes
        if not file_data[:4] == b"%PDF":
            raise ValueError("文件不是有效的 PDF 格式")

        # Run CPU/IO-bound operations in thread pool to avoid blocking event loop
        sha256 = await asyncio.to_thread(hashlib.sha256, file_data)
        sha256_hex = sha256.hexdigest()

        filename = await self._deduplicate_filename(project_id, filename)

        page_count = await asyncio.to_thread(_extract_page_count, file_data)

        # Upload to MinIO in thread pool
        doc_id = uuid.uuid4()
        minio_key = f"{project_id}/{doc_id}/{filename}"
        await asyncio.to_thread(
            self._storage.upload_file,
            settings.minio_bucket_documents, minio_key, file_data, "application/pdf",
        )

        doc = Document(
            id=doc_id,
            project_id=project_id,
            filename=filename,
            file_size=len(file_data),
            sha256=sha256_hex,
            minio_key=minio_key,
            page_count=page_count,
            uploaded_by=uploaded_by,
        )
        self.db.add(doc)
        await self.db.flush()
        await self.db.refresh(doc)
        return doc

    async def list_documents(
        self, project_id: uuid.UUID, page: int = 1, page_size: int = 20, status: str | None = None
    ) -> tuple[list[Document], int]:
        offset = (page - 1) * page_size
        base = select(Document).where(Document.project_id == project_id)
        if status:
            base = base.where(Document.status == status)
        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(base.order_by(Document.created_at.desc()).offset(offset).limit(page_size))
        return list(result.scalars().all()), total

    async def get_document(self, document_id: uuid.UUID) -> Document | None:
        result = await self.db.execute(select(Document).where(Document.id == document_id))
        return result.scalar_one_or_none()

    async def delete_document(self, document_id: uuid.UUID) -> bool:
        doc = await self.get_document(document_id)
        if doc is None:
            return False
        # Run MinIO delete in thread pool
        await asyncio.to_thread(
            self._storage.delete_file, settings.minio_bucket_documents, doc.minio_key,
        )
        await self.db.delete(doc)
        await self.db.flush()
        return True

    async def list_parse_jobs(self, document_id: uuid.UUID) -> list[ParseJob]:
        result = await self.db.execute(
            select(ParseJob).where(ParseJob.document_id == document_id).order_by(ParseJob.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_parse_job(self, job_id: uuid.UUID) -> ParseJob | None:
        result = await self.db.execute(select(ParseJob).where(ParseJob.id == job_id))
        return result.scalar_one_or_none()

    async def delete_parse_job(self, job_id: uuid.UUID) -> bool:
        job = await self.get_parse_job(job_id)
        if job is None:
            return False
        document_id = job.document_id
        # Delete associated files from MinIO outputs bucket
        for key in (job.raw_markdown_key, job.structured_json_key):
            if key:
                try:
                    await asyncio.to_thread(
                        self._storage.delete_file, settings.minio_bucket_outputs, key,
                    )
                except Exception:
                    pass  # file may already be gone
        await self.db.delete(job)
        await self.db.flush()

        # If no parse jobs remain, revert document status to "uploaded"
        remaining = await self.db.execute(
            select(func.count()).select_from(ParseJob).where(ParseJob.document_id == document_id)
        )
        if remaining.scalar() == 0:
            doc = await self.get_document(document_id)
            if doc and doc.status in ("parsed", "parsing"):
                doc.status = "uploaded"
                await self.db.flush()

        return True
