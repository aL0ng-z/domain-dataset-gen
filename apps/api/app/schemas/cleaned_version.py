import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from domain.schemas import BaseSchema


class CleanedDocumentVersionResponse(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    source_cleaning_job_id: uuid.UUID | None
    version: int
    section_count: int
    artifact_key: str | None
    status: str
    created_by: uuid.UUID
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CleanedDocumentVersionDetailResponse(CleanedDocumentVersionResponse):
    merged_markdown: str


class CleanedFinalReviewRequest(BaseModel):
    version_id: uuid.UUID
    action: str = Field(description="'accept' or 'reject'")
    reason: str | None = None
    comment: str | None = None
