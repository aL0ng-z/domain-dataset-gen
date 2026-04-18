import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


class SectionResponse(BaseSchema):
    id: uuid.UUID
    cleaning_job_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    heading_path: str
    source_pages: list | None
    raw_markdown: str
    cleaned_markdown: str | None
    status: str
    cleaned_by: uuid.UUID | None
    assignment_status: str
    assigned_to: uuid.UUID | None
    assigned_by: uuid.UUID | None
    assigned_at: datetime | None
    completed_at: datetime | None
    return_reason: str | None
    created_at: datetime
    updated_at: datetime


class SectionUpdate(BaseModel):
    cleaned_markdown: str


class SectionReview(BaseModel):
    action: str  # "accept" or "reject"
    note: str | None = None


class SectionCommentCreate(BaseModel):
    comment_type: str = "general"
    content: str


class SectionCommentResponse(BaseSchema):
    id: uuid.UUID
    section_id: uuid.UUID
    user_id: uuid.UUID
    comment_type: str
    content: str
    created_at: datetime


class SectionRevisionResponse(BaseSchema):
    id: uuid.UUID
    section_id: uuid.UUID
    revised_by: uuid.UUID
    cleaned_markdown: str
    revision_note: str | None
    created_at: datetime


class SectionLeaseResponse(BaseSchema):
    id: uuid.UUID
    section_id: uuid.UUID
    user_id: uuid.UUID
    acquired_at: datetime
    expires_at: datetime
    released_at: datetime | None


class SectionAssignRequest(BaseModel):
    assignee_id: uuid.UUID


class BulkAssignmentItem(BaseModel):
    section_ids: list[uuid.UUID]
    assignee_id: uuid.UUID


class BulkAssignRequest(BaseModel):
    assignments: list[BulkAssignmentItem]


class SectionReturnRequest(BaseModel):
    reason: str
