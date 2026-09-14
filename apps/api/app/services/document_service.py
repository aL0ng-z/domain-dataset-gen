import asyncio
import hashlib
import logging
import uuid

# Pre-load pymupdf at module level to avoid slow first-call initialization
import pymupdf  # noqa: F401
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.parse import ParseJob
from app.models.section import CleaningJob
from app.storage_keys import build_storage_key
from storage import get_storage_client

logger = logging.getLogger(__name__)


def _extract_page_count(file_data: bytes) -> int | None:
    """Extract page count from PDF bytes (runs in thread pool)."""
    try:
        doc_pdf = pymupdf.open(stream=file_data, filetype="pdf")
        page_count = len(doc_pdf)
        doc_pdf.close()
        return page_count
    except Exception:
        return None


class DocumentInUseError(Exception):
    """文档删除被数据库 RESTRICT 外键阻止。"""


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
        minio_key = build_storage_key(project_id, doc_id, filename)
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

        # 收集所有派生对象坐标，但绝不能在数据库删除已提交前清理它们。
        # EvidenceLink 等 RESTRICT 外键会拒绝数据库删除；此前先删 MinIO 会把
        # 仍存在的文档变成不可恢复的悬挂记录。
        storage_objects: list[tuple[str, str]] = [
            (settings.minio_bucket_documents, doc.minio_key),
        ]
        parse_jobs = await self.db.execute(
            select(ParseJob).where(ParseJob.document_id == document_id)
        )
        for job in parse_jobs.scalars().all():
            for key in (job.raw_markdown_key, job.structured_json_key):
                if key:
                    storage_objects.append((settings.minio_bucket_outputs, key))
        cleaned_versions = await self.db.execute(
            select(CleanedDocumentVersion.artifact_key).where(
                CleanedDocumentVersion.document_id == document_id
            )
        )
        storage_objects.extend(
            (settings.minio_bucket_outputs, key)
            for key in cleaned_versions.scalars().all()
            if key
        )
        chunk_sets = await self.db.execute(
            select(ChunkSet.artifact_key).where(ChunkSet.document_id == document_id)
        )
        storage_objects.extend(
            (settings.minio_bucket_outputs, key)
            for key in chunk_sets.scalars().all()
            if key
        )

        # DB cascade handles parse jobs, cleaning jobs, sections and chunks. Commit
        # is intentionally here rather than in get_db: storage cleanup must happen
        # only after the irreversible database decision has succeeded.
        try:
            await self.db.delete(doc)
            await self.db.flush()
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise DocumentInUseError("文档已被证据或其他受保护记录引用，不能删除") from exc
        except Exception:
            await self.db.rollback()
            raise

        for bucket, key in dict.fromkeys(storage_objects):
            try:
                await asyncio.to_thread(self._storage.delete_file, bucket, key)
            except Exception:  # noqa: BLE001 - DB 已提交，只记录待后续清理的具体对象
                logger.exception("文档删除后清理对象失败: bucket=%s key=%s", bucket, key)
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

        # 与文档删除相同：先做数据库删除并提交，再清理解析产物。清洗版本等
        # RESTRICT 依赖会在 flush 时阻止删除，此时对象存储必须保持不变。
        storage_objects = [
            (settings.minio_bucket_outputs, key)
            for key in (job.raw_markdown_key, job.structured_json_key)
            if key
        ]
        cleaning_jobs = await self.db.execute(
            select(CleaningJob).where(CleaningJob.parse_job_id == job_id)
        )
        try:
            for cleaning_job in cleaning_jobs.scalars().all():
                await self.db.delete(cleaning_job)
            await self.db.delete(job)
            await self.db.flush()

            # If no parse jobs remain, revert document status to "uploaded".
            remaining = await self.db.scalar(
                select(func.count()).select_from(ParseJob).where(ParseJob.document_id == document_id)
            )
            if remaining == 0:
                doc = await self.get_document(document_id)
                if doc and doc.status in ("parsed", "parsing"):
                    doc.status = "uploaded"
                    await self.db.flush()
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise DocumentInUseError("解析任务仍被清洗版本或其他受保护记录引用，不能删除") from exc
        except Exception:
            await self.db.rollback()
            raise

        for bucket, key in storage_objects:
            try:
                await asyncio.to_thread(self._storage.delete_file, bucket, key)
            except Exception:  # noqa: BLE001 - 数据库已提交，只记录待后续清理对象
                logger.exception("解析任务删除后清理对象失败: bucket=%s key=%s", bucket, key)

        return True
