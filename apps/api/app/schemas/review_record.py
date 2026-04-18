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
    created_at: datetime
