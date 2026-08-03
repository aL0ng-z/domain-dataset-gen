import uuid
from datetime import datetime

from domain.schemas import BaseSchema


class ReviewRecordResponse(BaseSchema):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    reviewer_id: uuid.UUID
    action: str
    reason: str | None
    comment: str | None
    # T09：审批绑定字段。
    entity_revision_id: uuid.UUID | None
    revision_content_sha256: str | None
    evidence_sha256: str | None
    canonicalization_version: str | None
    created_at: datetime
