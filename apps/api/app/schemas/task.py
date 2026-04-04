import uuid
from datetime import datetime

from domain.schemas import BaseSchema


class TaskResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    task_type: str
    entity_type: str
    entity_id: uuid.UUID
    parent_task_id: uuid.UUID | None
    status: str
    progress: int
    error_message: str | None
    created_by: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
