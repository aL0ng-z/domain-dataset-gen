import uuid
from datetime import datetime

from pydantic import Field

from domain.schemas import BaseSchema, RequestSchema


class CleanedDocumentVersionResponse(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    source_cleaning_job_id: uuid.UUID | None
    version: int
    section_count: int
    artifact_key: str | None
    source_revision_sha256: str | None
    content_sha256: str | None
    status: str
    created_by: uuid.UUID
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CleanedDocumentVersionDetailResponse(CleanedDocumentVersionResponse):
    merged_markdown: str
    source_revision_map: dict | None


class CleanedFinalReviewRequest(RequestSchema):
    version_id: uuid.UUID
    action: str = Field(description="'accept' or 'reject'")
    reason: str | None = None
    comment: str | None = None
