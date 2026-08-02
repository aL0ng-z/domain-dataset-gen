import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


class ChunkResponse(BaseSchema):
    id: uuid.UUID
    section_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    heading_path: str
    content: str
    source_pages: dict | None
    token_count: int
    status: str
    # T06：响应项新增 chunk_set_id 与 chunk_set_version（历史集合不混入默认列表）。
    chunk_set_id: uuid.UUID
    chunk_set_version: int | None = None
    created_at: datetime
    updated_at: datetime


class ChunkUpdate(RequestSchema):
    content: str | None = None


class GenerateRequest(RequestSchema):
    prompt_template_id: uuid.UUID
    model_config_id: uuid.UUID
