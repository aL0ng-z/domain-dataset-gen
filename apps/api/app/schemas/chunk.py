import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


class ChunkResponse(BaseSchema):
    id: uuid.UUID
    section_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    heading_path: str
    content: str
    source_pages: list | None
    token_count: int
    status: str
    created_at: datetime
    updated_at: datetime


class ChunkUpdate(BaseModel):
    content: str | None = None


class GenerateRequest(BaseModel):
    prompt_template_id: uuid.UUID
    model_config_id: uuid.UUID
