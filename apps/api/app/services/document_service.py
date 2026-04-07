import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.models.parse import ParseJob
from storage import StorageClient


class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._storage = StorageClient(
            settings.minio_endpoint, settings.minio_access_key,
            settings.minio_secret_key, settings.minio_secure,
        )

    async def upload(
        self, project_id: uuid.UUID, filename: str, file_data: bytes, uploaded_by: uuid.UUID
    ) -> Document:
        # Validate PDF magic bytes
        if not file_data[:4] == b"%PDF":
            raise ValueError("文件不是有效的 PDF 格式")

        sha256 = hashlib.sha256(file_data).hexdigest()

        # Extract page count from PDF
        page_count = None
        try:
            import tempfile
            import pymupdf
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name
            doc_pdf = pymupdf.open(tmp_path)
            page_count = len(doc_pdf)
            doc_pdf.close()
            import os
            os.unlink(tmp_path)
        except Exception:
            pass  # Non-critical, page_count stays None

        # Upload to MinIO (use UUID in key to allow duplicate files)
        doc_id = uuid.uuid4()
        minio_key = f"{project_id}/{doc_id}/{filename}"
        self._storage.upload_file(settings.minio_bucket_documents, minio_key, file_data, "application/pdf")

        doc = Document(
            id=doc_id,
            project_id=project_id,
            filename=filename,
            file_size=len(file_data),
            sha256=sha256,
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
        self._storage.delete_file(settings.minio_bucket_documents, doc.minio_key)
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
